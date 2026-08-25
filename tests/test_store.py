from __future__ import annotations

from datetime import datetime

import pytest

from cortex.store import (
    DuplicateRecordError,
    RecordNotFoundError,
    content_hash,
    count_records,
    create_record,
    delete_record,
    find_by_idempotency_key,
    get_or_create_project,
    get_record,
    integrity_report,
    list_projects,
    list_records,
    reindex,
    slugify,
    update_record,
)

# --- projects -------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Echoes", "echoes"),
        ("  Echoes  ", "echoes"),
        ("ECHOES", "echoes"),
        ("Cal's Improvement Plan", "cal-s-improvement-plan"),
        ("Shattered — Master Design", "shattered-master-design"),
        ("!!!", "untitled"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_project_names_that_differ_only_in_case_or_spacing_do_not_fork(conn):
    """The prototype stored the project as free text, so 'Echoes' and 'echoes '
    were two different projects forever."""
    first = get_or_create_project(conn, "Echoes")
    second = get_or_create_project(conn, "  echoes ")
    third = get_or_create_project(conn, "ECHOES")

    assert first.id == second.id == third.id
    assert len(list_projects(conn)) == 1
    # The first spelling wins as the display name.
    assert first.name == "Echoes"


def test_blank_project_name_becomes_untitled(conn):
    project = get_or_create_project(conn, "   ")
    assert project.name == "Untitled Project"


# --- create ---------------------------------------------------------------


def test_create_record_indexes_chunks_and_vectors(conn, embedder):
    record = create_record(
        conn, embedder, project="Echoes", title="Keeper", body="Wexler tends the lantern."
    )

    assert record.id > 0
    assert record.project_name == "Echoes"
    chunks = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE record_id = ?", (record.id,)
    ).fetchone()["n"]
    vectors = conn.execute("SELECT COUNT(*) AS n FROM vec_chunks").fetchone()["n"]
    assert chunks == 1
    assert vectors == 1


def test_long_note_that_broke_the_prototype_now_stores(conn, embedder):
    """~25,000 characters. The old single-shot embed rejected anything over ~13,000."""
    body = "\n\n".join(f"Section {i}. " + ("The keeper walked at dusk. " * 15) for i in range(50))
    assert len(body) > 20_000

    record = create_record(conn, embedder, project="Echoes", title="Long", body=body)

    chunk_count = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE record_id = ?", (record.id,)
    ).fetchone()["n"]
    assert chunk_count > 1
    assert get_record(conn, record.id).body == body.rstrip()


def test_timestamps_are_timezone_aware_utc(conn, embedder):
    """The prototype stored naive UTC and rendered it as local, so every note
    displayed hours in the future."""
    record = create_record(conn, embedder, project="P", title="T", body="B")
    parsed = datetime.fromisoformat(record.created_at)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_identical_note_in_same_project_is_refused(conn, embedder):
    create_record(conn, embedder, project="Echoes", title="Keeper", body="Same body.")
    with pytest.raises(DuplicateRecordError) as caught:
        create_record(conn, embedder, project="Echoes", title="Keeper", body="Same body.")
    assert caught.value.existing_id > 0


def test_identical_note_can_be_forced(conn, embedder):
    create_record(conn, embedder, project="Echoes", title="Keeper", body="Same body.")
    second = create_record(
        conn, embedder, project="Echoes", title="Keeper", body="Same body.", allow_duplicate=True
    )
    assert second.id > 0
    assert count_records(conn) == 2


def test_same_text_in_a_different_project_is_not_a_duplicate(conn, embedder):
    create_record(conn, embedder, project="Echoes", title="Keeper", body="Same body.")
    other = create_record(conn, embedder, project="Other", title="Keeper", body="Same body.")
    assert other.id > 0


def test_idempotency_key_makes_a_replayed_capture_safe(conn, embedder):
    """The phone re-sends a queued note it isn't sure landed."""
    first = create_record(
        conn, embedder, project="Echoes", title="From phone", body="Text.", idempotency_key="abc-1"
    )
    second = create_record(
        conn, embedder, project="Echoes", title="From phone", body="Text.", idempotency_key="abc-1"
    )
    assert first.id == second.id
    assert count_records(conn) == 1
    assert find_by_idempotency_key(conn, "abc-1").id == first.id


def test_a_failed_embedding_leaves_nothing_behind(conn):
    """The write must be all-or-nothing: no orphan record, no orphan chunks."""
    from conftest import ExplodingEmbedder

    with pytest.raises(RuntimeError, match="model server is down"):
        create_record(conn, ExplodingEmbedder(), project="Echoes", title="T", body="Body text.")

    assert count_records(conn) == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 0
    assert integrity_report(conn) == {
        "orphan_chunks": 0,
        "chunks_without_vectors": 0,
        "vectors_without_chunks": 0,
        "records_without_chunks": 0,
    }


# --- update / delete ------------------------------------------------------


def test_update_body_reindexes_and_stamps_updated_at(conn, embedder):
    record = create_record(conn, embedder, project="P", title="T", body="Original body here.")
    before = conn.execute(
        "SELECT id, text FROM chunks WHERE record_id = ?", (record.id,)
    ).fetchall()

    updated = update_record(conn, embedder, record.id, body="Completely different body now.")

    assert updated.body == "Completely different body now."
    assert updated.content_hash == content_hash(updated.title, updated.body)

    after = conn.execute(
        "SELECT id, text FROM chunks WHERE record_id = ?", (record.id,)
    ).fetchall()
    assert [r["text"] for r in after] == ["Completely different body now."]
    # Chunk ids must not be recycled, or a stale vector could pair with a new chunk.
    assert not ({r["id"] for r in before} & {r["id"] for r in after})
    assert integrity_report(conn)["vectors_without_chunks"] == 0


def test_update_without_body_change_does_not_re_embed(conn, embedder):
    record = create_record(conn, embedder, project="P", title="T", body="Body.")
    calls_before = embedder.calls
    update_record(conn, embedder, record.id, title="New Title")
    assert embedder.calls == calls_before


def test_update_can_move_a_record_between_projects(conn, embedder):
    record = create_record(conn, embedder, project="Echoes", title="T", body="Body.")
    moved = update_record(conn, embedder, record.id, project="Work Notes")
    assert moved.project_name == "Work Notes"
    assert count_records(conn, project="Echoes") == 0
    assert count_records(conn, project="Work Notes") == 1


def test_delete_removes_the_record_its_chunks_and_its_vectors(conn, embedder):
    body = "\n\n".join(f"Paragraph {i} of some length here. " * 8 for i in range(30))
    record = create_record(conn, embedder, project="P", title="T", body=body)
    assert conn.execute("SELECT COUNT(*) AS n FROM vec_chunks").fetchone()["n"] > 1

    delete_record(conn, record.id)

    assert count_records(conn) == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 0
    # vec_chunks has no cascade from the foreign key — it must be cleaned by hand.
    assert conn.execute("SELECT COUNT(*) AS n FROM vec_chunks").fetchone()["n"] == 0


def test_delete_missing_record_raises(conn):
    with pytest.raises(RecordNotFoundError):
        delete_record(conn, 999)


# --- listing --------------------------------------------------------------


def test_list_records_is_newest_first_and_filterable(conn, sample_notes):
    everything = list_records(conn)
    assert [r.id for r in everything] == sorted((r.id for r in sample_notes), reverse=True)

    echoes = list_records(conn, project="Echoes")
    assert {r.project_name for r in echoes} == {"Echoes"}
    assert count_records(conn, project="echoes") == 2


def test_list_records_paginates(conn, sample_notes):
    first = list_records(conn, limit=2, offset=0)
    second = list_records(conn, limit=2, offset=2)
    assert len(first) == 2
    assert len(second) == 1
    assert not ({r.id for r in first} & {r.id for r in second})


# --- reindex --------------------------------------------------------------


def test_reindex_rebuilds_everything_from_the_records(conn, embedder, sample_notes):
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM vec_chunks")
    assert integrity_report(conn)["records_without_chunks"] == 3

    count = reindex(conn, embedder)

    assert count == 3
    report = integrity_report(conn)
    assert report["records_without_chunks"] == 0
    assert report["chunks_without_vectors"] == 0
    assert report["vectors_without_chunks"] == 0


def test_reindex_lets_you_switch_embedding_models(conn, sample_notes):
    """A different model means a different vector width — reindex must handle it."""
    from conftest import FakeEmbedder
    from cortex.db import get_meta

    wider = FakeEmbedder(dim=128)
    wider.model = "fake-embed-wide"

    reindex(conn, wider)

    assert get_meta(conn, "embed_model") == "fake-embed-wide"
    assert get_meta(conn, "embed_dim") == "128"
    assert integrity_report(conn)["chunks_without_vectors"] == 0


def test_integrity_report_works_before_the_vector_index_exists(tmp_path):
    """`cortex doctor` runs without Ollama, so it can meet a vault whose vec0
    table has not been created yet."""
    from cortex.db import connect
    from cortex.migrations import migrate
    from cortex.store import has_vector_index

    conn = connect(tmp_path / "fresh.db")
    migrate(conn)

    assert not has_vector_index(conn)
    assert integrity_report(conn) == {
        "orphan_chunks": 0,
        "chunks_without_vectors": 0,
        "vectors_without_chunks": 0,
        "records_without_chunks": 0,
    }
    conn.close()


def test_integrity_report_flags_a_record_whose_chunks_went_missing(conn, embedder, sample_notes):
    conn.execute("DELETE FROM chunks")
    report = integrity_report(conn)
    assert report["records_without_chunks"] == 3
    assert report["vectors_without_chunks"] > 0


def test_settings_fall_back_to_config_then_prefer_the_vault(conn):
    from cortex.config import Config
    from cortex.settings import get_settings, set_settings

    config = Config(librarian_model="from-config", creative_model="also-config")

    assert get_settings(conn, config).librarian_model == "from-config"

    set_settings(conn, librarian_model="from-vault")
    assert get_settings(conn, config).librarian_model == "from-vault"
    assert get_settings(conn, config).creative_model == "also-config"


def test_setting_an_unknown_key_is_refused(conn):
    from cortex.settings import set_settings

    with pytest.raises(ValueError, match="Not a changeable setting"):
        set_settings(conn, embed_model="sneaky")


def test_blank_settings_are_ignored_rather_than_stored(conn):
    from cortex.config import Config
    from cortex.settings import get_settings, set_settings

    config = Config(librarian_model="from-config")
    set_settings(conn, librarian_model="   ")
    assert get_settings(conn, config).librarian_model == "from-config"


# --- model capabilities ----------------------------------------------------


def test_normalise_model_treats_a_bare_name_as_latest():
    from cortex.settings import normalise_model

    assert normalise_model("embeddinggemma") == "embeddinggemma:latest"
    assert normalise_model("qwen2.5:14b") == "qwen2.5:14b"
    assert normalise_model("  gemma4:12b  ") == "gemma4:12b"


def test_installed_models_reads_capabilities(monkeypatch):
    """The regression guard for a silent failure.

    /api/tags returns a `capabilities` array, but ollama.Client.list() maps
    the response onto a typed Model that has no such field, so going through
    the client drops it and every model looks incapable of everything - which
    left the settings UI offering nothing at all.
    """
    import httpx

    payload = {
        "models": [
            {
                "model": "qwen2.5:14b",
                "details": {"parameter_size": "14.8B"},
                "capabilities": ["completion", "tools"],
            },
            {
                "model": "embeddinggemma:latest",
                "details": {"parameter_size": "307M"},
                "capabilities": ["embedding"],
            },
            {"model": "mystery:1b", "details": {}},
        ]
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: FakeResponse())

    from cortex.settings import installed_models

    models = {m["name"]: m for m in installed_models("http://x")}

    assert models["qwen2.5:14b"]["can_chat"] is True
    assert models["qwen2.5:14b"]["can_embed"] is False
    assert models["embeddinggemma:latest"]["can_embed"] is True
    assert models["embeddinggemma:latest"]["can_chat"] is False
    # A model reporting nothing is treated as capable of nothing, not everything.
    assert models["mystery:1b"]["capabilities"] == []
    assert models["mystery:1b"]["can_chat"] is False


# --- concurrent edits ------------------------------------------------------


def test_an_edit_over_someone_elses_change_is_refused(conn, embedder):
    """Two clients editing one note is normal once captures arrive from a
    phone as well as a desktop."""
    from cortex.store import StaleEditError

    record = create_record(conn, embedder, project="P", title="T", body="Original.")
    seen_at = record.updated_at

    update_record(conn, embedder, record.id, body="Edited on the desktop.")

    with pytest.raises(StaleEditError) as caught:
        update_record(
            conn, embedder, record.id, body="Edited on the phone.",
            expected_updated_at=seen_at,
        )

    # Neither edit is lost: the desktop's stands, and the caller is handed the
    # current version to decide with.
    assert caught.value.record.body == "Edited on the desktop."
    assert get_record(conn, record.id).body == "Edited on the desktop."


def test_an_edit_from_the_current_version_succeeds(conn, embedder):
    record = create_record(conn, embedder, project="P", title="T", body="Original.")
    updated = update_record(
        conn, embedder, record.id, body="Changed.", expected_updated_at=record.updated_at
    )
    assert updated.body == "Changed."


def test_omitting_the_precondition_overwrites_deliberately(conn, embedder):
    record = create_record(conn, embedder, project="P", title="T", body="Original.")
    update_record(conn, embedder, record.id, body="First.")
    forced = update_record(conn, embedder, record.id, body="Second.")
    assert forced.body == "Second."


# --- managing projects -----------------------------------------------------


def test_renaming_a_project_moves_its_notes_with_it(conn, embedder):
    """Records point at the project by id, so nothing has to be rewritten."""
    from cortex.store import find_project, update_project

    record = create_record(conn, embedder, project="Echos", title="T", body="B")

    renamed = update_project(conn, "Echos", new_name="Echoes")

    assert renamed.name == "Echoes"
    assert renamed.slug == "echoes"
    assert get_record(conn, record.id).project_name == "Echoes"
    assert count_records(conn, project="Echoes") == 1
    assert find_project(conn, "Echos") is None


def test_a_rename_onto_an_existing_project_is_refused(conn, embedder):
    from cortex.store import ProjectNameTakenError, update_project

    create_record(conn, embedder, project="Echoes", title="A", body="a")
    create_record(conn, embedder, project="Work Notes", title="B", body="b")

    with pytest.raises(ProjectNameTakenError, match="collide"):
        update_project(conn, "Work Notes", new_name="echoes")

    # Nothing moved.
    assert count_records(conn, project="Echoes") == 1
    assert count_records(conn, project="Work Notes") == 1


def test_a_description_can_be_set_and_cleared(conn):
    from cortex.store import get_or_create_project, update_project

    get_or_create_project(conn, "Echoes")

    described = update_project(conn, "Echoes", description="  A coastal town that forgets.  ")
    assert described.description == "A coastal town that forgets."

    assert update_project(conn, "Echoes", description="").description == ""


def test_renaming_does_not_disturb_the_description(conn):
    from cortex.store import get_or_create_project, update_project

    get_or_create_project(conn, "Echoes")
    update_project(conn, "Echoes", description="A coastal town.")

    renamed = update_project(conn, "Echoes", new_name="Echoes Reborn")
    assert renamed.description == "A coastal town."


def test_updating_a_project_that_does_not_exist(conn):
    from cortex.store import ProjectNotFoundError, update_project

    with pytest.raises(ProjectNotFoundError):
        update_project(conn, "Nowhere", description="x")


def test_deleting_a_project_that_still_holds_notes_is_refused(conn, embedder):
    """The foreign key cascades, so an unguarded delete would silently take
    every note in the project."""
    from cortex.store import ProjectNotEmptyError, delete_project

    create_record(conn, embedder, project="Echoes", title="T", body="B")

    with pytest.raises(ProjectNotEmptyError, match="still holds 1 note"):
        delete_project(conn, "Echoes")

    assert count_records(conn) == 1


def test_an_empty_project_can_be_deleted(conn):
    from cortex.store import delete_project, get_or_create_project, list_projects

    get_or_create_project(conn, "Spare")
    assert delete_project(conn, "Spare") == 0
    assert list_projects(conn) == []


def test_forcing_a_delete_takes_the_notes_and_their_index(conn, embedder):
    from cortex.store import delete_project, integrity_report

    body = "\n\n".join(f"Paragraph {i} of some length here. " * 8 for i in range(20))
    create_record(conn, embedder, project="Echoes", title="A", body=body)
    create_record(conn, embedder, project="Echoes", title="B", body="short")
    create_record(conn, embedder, project="Keep", title="C", body="kept")

    assert delete_project(conn, "Echoes", force=True) == 2

    assert count_records(conn) == 1
    # Vectors are not cascaded by the foreign key, so they must go by hand.
    assert integrity_report(conn) == {
        "orphan_chunks": 0,
        "chunks_without_vectors": 0,
        "vectors_without_chunks": 0,
        "records_without_chunks": 0,
    }


# --- the description as grounding -----------------------------------------


def test_the_brief_is_empty_without_a_description(conn):
    from cortex.store import get_or_create_project, project_brief

    get_or_create_project(conn, "Echoes")
    assert project_brief(conn, "Echoes") == ""
    assert project_brief(conn, None) == ""
    assert project_brief(conn, "Nowhere") == ""


def test_the_brief_carries_the_description(conn):
    from cortex.store import get_or_create_project, project_brief, update_project

    get_or_create_project(conn, "Echoes")
    update_project(conn, "Echoes", description="A coastal town that forgets its own history.")

    brief = project_brief(conn, "Echoes")
    assert "Echoes" in brief
    assert "forgets its own history" in brief
