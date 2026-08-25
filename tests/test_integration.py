"""End-to-end tests against a real Ollama server.

Skipped automatically when Ollama is unreachable, so the rest of the suite
stays offline and fast. Run just these with:  pytest -m ollama
"""

from __future__ import annotations

import pytest

from cortex.capture import capture
from cortex.chunk import chunk_text
from cortex.config import Config
from cortex.db import connect
from cortex.embed import EmbeddingError, OllamaEmbedder
from cortex.migrations import ensure_vector_index, migrate
from cortex.retrieve import search
from cortex.store import create_record

pytestmark = pytest.mark.ollama


@pytest.fixture
def real_embedder(ollama_available):
    if not ollama_available:
        pytest.skip("Ollama is not reachable")
    config = Config.from_env()
    embedder = OllamaEmbedder(config.ollama_host, config.embed_model)
    try:
        assert embedder.dim > 0
    except EmbeddingError as exc:
        pytest.skip(f"embedding model unavailable: {exc}")
    return embedder


@pytest.fixture
def real_vault(tmp_path, real_embedder):
    conn = connect(tmp_path / "cortex.db")
    migrate(conn)
    ensure_vector_index(conn, real_embedder.model, real_embedder.dim)
    yield conn
    conn.close()


def test_embedding_dimension_is_probed_not_assumed(real_embedder):
    assert real_embedder.dim > 0
    vectors = real_embedder.embed(["one", "two", "three"])
    assert len(vectors) == 3
    assert all(len(v) == real_embedder.dim for v in vectors)


def test_every_chunk_of_a_huge_note_is_accepted_by_the_real_embedder(real_embedder):
    """The prototype's hard failure: over ~13,000 characters, Ollama returns a
    500 rather than truncating, and the note is lost. Chunking must keep every
    piece inside the model's window."""
    body = "\n\n".join(
        f"Section {i}. " + ("The keeper walked the cliff path at dusk. " * 20) for i in range(60)
    )
    assert len(body) > 45_000

    chunks = chunk_text(body, target_tokens=400, max_tokens=512, overlap_tokens=60)
    vectors = real_embedder.embed([c.text for c in chunks])

    assert len(vectors) == len(chunks)
    assert all(len(v) == real_embedder.dim for v in vectors)


def test_the_whole_note_at_once_still_fails_which_is_why_we_chunk(real_embedder):
    """Documents the ceiling this design exists to work around."""
    body = "The keeper walked the cliff path at dusk. " * 1200
    with pytest.raises(EmbeddingError):
        real_embedder.embed([body])


def test_real_semantic_search_finds_a_paraphrase(real_vault, real_embedder):
    """The thing keyword search cannot do: match on meaning, not words."""
    create_record(
        real_vault, real_embedder, project="Echoes", title="The Lighthouse Keeper",
        body="Wexler tends the copper lantern on the northern cliffs. "
             "He has not spoken to another person in eleven years.",
    )
    create_record(
        real_vault, real_embedder, project="Work", title="Sprint Retrospective",
        body="The deployment pipeline keeps timing out. We agreed to split "
             "the integration suite across two runners.",
    )

    hits = search(real_vault, real_embedder, "a man living in total isolation by the sea")

    assert hits
    assert hits[0].record.title == "The Lighthouse Keeper"
    assert hits[0].vector_rank is not None


def test_real_capture_through_the_librarian(real_vault, real_embedder):
    from cortex.llm import LibrarianError, OllamaLibrarian

    config = Config.from_env()
    librarian = OllamaLibrarian(config.ollama_host, config.librarian_model)

    try:
        result = capture(
            real_vault, real_embedder,
            "Reminder to myself: the deployment pipeline keeps timing out because "
            "the integration suite runs on one machine. Split it across two runners.",
            librarian=librarian, project="Work Notes",
        )
    except LibrarianError as exc:
        pytest.skip(f"librarian model unavailable: {exc}")

    assert result.record.project_name == "Work Notes"
    assert result.record.title
    assert result.record.category
    assert result.chunks >= 1
    assert "runners" in result.record.body


def test_the_api_works_against_real_models(tmp_path, real_embedder):
    """One end-to-end pass over HTTP with nothing faked."""
    from fastapi.testclient import TestClient

    from cortex.api import deps
    from cortex.api.app import create_app

    token = "integration-token"
    config = Config(data_dir=tmp_path)
    deps.configure(config, token)

    with TestClient(create_app()) as client:
        client.headers.update({"Authorization": f"Bearer {token}"})

        created = client.post(
            "/api/records",
            json={
                "text": "The ferryman's ledger lists every passenger since 1811, but three "
                "names recur every decade with no explanation.",
                "project": "Echoes",
            },
        )
        assert created.status_code == 201, created.text
        record = created.json()["record"]
        assert record["title"]
        assert record["category"]

        # Semantic match with almost no shared vocabulary.
        hits = client.get("/api/search", params={"q": "a record of travellers"}).json()["hits"]
        assert any(h["record"]["id"] == record["id"] for h in hits)

        # And the relevance floor holds with real vectors.
        absent = client.get(
            "/api/search", params={"q": "sourdough starter hydration ratios"}
        ).json()["hits"]
        assert absent == []


def test_real_ollama_reports_capabilities(ollama_available):
    """Catches the silent-drop bug against the real server.

    The fake-backed unit test pins the parsing; this pins the assumption that
    Ollama actually sends capabilities in the first place.
    """
    if not ollama_available:
        pytest.skip("Ollama is not reachable")

    from cortex.settings import installed_models

    models = installed_models(Config.from_env().ollama_host)

    assert models, "no models installed"
    assert any(m["can_chat"] for m in models), "no chat-capable model reported"
    assert all(isinstance(m["capabilities"], list) for m in models)
    # An embedding model must never be offered as a chat model.
    for model in models:
        if model["can_embed"] and "completion" not in model["capabilities"]:
            assert not model["can_chat"]


def test_real_condensation_resolves_a_pronoun(real_vault, real_embedder):
    """The follow-up case, against a real model.

    'how does she find her way?' contains no noun at all - if condensation
    does not resolve it, retrieval searches for the word 'she' and the model
    is handed nothing, which is when it starts inventing.
    """
    from cortex.chat import answer, create_thread, list_messages
    from cortex.llm import OllamaChat
    from cortex.store import create_record

    config = Config.from_env()
    chatter = OllamaChat(config.ollama_host, config.librarian_model)

    create_record(
        real_vault, real_embedder, project="Echoes", title="The Lighthouse Keeper",
        body="Wexler tends the copper lantern on the northern cliffs. His daughter "
             "Mireille was born the winter he stopped speaking.",
    )
    create_record(
        real_vault, real_embedder, project="Echoes", title="Mireille's Inheritance",
        body="Mireille cannot see the lantern light. She navigates by the sound of "
             "the harbour bell instead, and knows the coast better than her father.",
    )

    thread = create_thread(real_vault, project="Echoes")

    for _ in answer(real_vault, real_embedder, chatter, thread.id, "who tends the lighthouse?"):
        pass

    sources = []
    for kind, payload in answer(
        real_vault, real_embedder, chatter, thread.id, "how does she find her way?"
    ):
        if kind == "sources":
            sources = payload

    assert sources, "a pronoun-only follow-up must still retrieve something"
    assert "Mireille's Inheritance" in sources

    reply = [m for m in list_messages(real_vault, thread.id) if m.role == "assistant"][-1]
    assert "bell" in reply.content.lower()


def test_real_compaction_summarises_and_extracts_facts(real_vault, real_embedder, monkeypatch):
    """Compaction against real models, with the window shrunk to force it."""
    from cortex.chat import add_message, compact, create_thread, get_thread, list_facts
    from cortex.llm import OllamaChat

    config = Config.from_env()
    chatter = OllamaChat(config.ollama_host, config.utility_model or config.librarian_model)

    thread = create_thread(real_vault)
    add_message(real_vault, thread.id, "user", "The keeper is called Wexler and he is mute.")
    add_message(real_vault, thread.id, "assistant", "Understood - Wexler, mute.")
    add_message(real_vault, thread.id, "user", "His daughter is Mireille and she is blind.")
    add_message(real_vault, thread.id, "assistant", "Noted: Mireille, blind.")
    for i in range(6):
        add_message(real_vault, thread.id, "user", f"unrelated point {i}")
        add_message(real_vault, thread.id, "assistant", f"acknowledged {i}")

    assert compact(real_vault, chatter, thread.id) is True

    updated = get_thread(real_vault, thread.id)
    assert updated.summary
    assert updated.summarised_upto > 0

    # The names must survive into the ledger, since the turns holding them are
    # now folded into prose.
    facts = " ".join(list_facts(real_vault, thread.id)).lower()
    assert "wexler" in facts or "wexler" in updated.summary.lower()


def test_real_token_calibration_converges_on_the_model(real_vault, real_embedder):
    """prompt_eval_count is ground truth; the estimator should move towards it."""
    from cortex.chat import answer, create_thread
    from cortex.llm import OllamaChat
    from cortex.tokens import DEFAULT_CHARS_PER_TOKEN, chars_per_token

    config = Config.from_env()
    chatter = OllamaChat(config.ollama_host, config.librarian_model)
    thread = create_thread(real_vault)

    done = None
    for kind, payload in answer(
        real_vault, real_embedder, chatter, thread.id, "say hello briefly"
    ):
        if kind == "done":
            done = payload

    assert done["prompt_tokens"] > 0, "the model should report its prompt size"
    ratio = chars_per_token(real_vault, chatter.model)
    assert 1.5 <= ratio <= 8.0
    # It should have moved off the default, in some direction.
    assert ratio != DEFAULT_CHARS_PER_TOKEN


def test_real_options_generation_produces_separate_ideas(real_vault, real_embedder):
    """Asking for the shape up front, against a real model.

    The prototype produced one prose blob and banked it whole; the point of
    the structured request is that each alternative arrives separable.
    """
    from cortex.creative import bank, generate, get_generation
    from cortex.llm import OllamaChat

    config = Config.from_env()
    creative = OllamaChat(config.ollama_host, config.creative_model)

    generation_id = None
    for kind, payload in generate(
        real_vault,
        real_embedder,
        creative,
        "ways a harbour bell might be rung by the tide rather than by hand",
        mode="options",
        count=3,
        project="Echoes",
        use_context=False,
    ):
        if kind == "done":
            generation_id = payload["generation_id"]

    generation = get_generation(real_vault, generation_id)
    assert generation.mode == "options", "the model should have produced usable JSON"
    assert len(generation.ideas) >= 2

    titles = [i.title for i in generation.ideas]
    assert len(set(titles)) == len(titles), "alternatives should be distinct"
    assert all(i.detail.strip() for i in generation.ideas)

    # Bank exactly one of them - the thing the prototype could not do.
    chosen = generation.ideas[1].ordinal
    result = bank(real_vault, real_embedder, generation_id, [chosen], project="Echoes")

    assert len(result.banked) == 1
    assert result.banked[0].title == generation.ideas[1].title
    assert get_generation(real_vault, generation_id).ideas[1].banked is True
    assert sum(1 for i in get_generation(real_vault, generation_id).ideas if i.banked) == 1


def test_a_banked_idea_is_findable_on_its_own(real_vault, real_embedder):
    """Separate records mean separate embeddings, which is the payoff."""
    from cortex.creative import bank, generate, get_generation
    from cortex.llm import OllamaChat
    from cortex.retrieve import search

    config = Config.from_env()
    creative = OllamaChat(config.ollama_host, config.creative_model)

    generation_id = None
    for kind, payload in generate(
        real_vault, real_embedder, creative,
        "two different mechanisms that could ring a bell using seawater",
        mode="options", count=2, use_context=False,
    ):
        if kind == "done":
            generation_id = payload["generation_id"]

    generation = get_generation(real_vault, generation_id)
    if len(generation.ideas) < 2:
        pytest.skip("model did not return two options")

    bank(real_vault, real_embedder, generation_id, [0], project="Echoes")

    kept = generation.ideas[0]
    hits = search(real_vault, real_embedder, kept.pitch or kept.title)
    assert any(h.record.title == kept.title for h in hits)
