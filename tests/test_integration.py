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
