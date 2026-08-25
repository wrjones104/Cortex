from __future__ import annotations

import pytest

from cortex.db import StoreError, connect, get_meta
from cortex.migrations import SCHEMA_VERSION, current_version, ensure_vector_index, migrate


def test_migrate_brings_a_fresh_vault_to_current(tmp_path):
    conn = connect(tmp_path / "v.db")
    assert current_version(conn) == 0
    applied = migrate(conn)
    assert applied == SCHEMA_VERSION
    assert current_version(conn) == SCHEMA_VERSION
    conn.close()


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "v.db")
    migrate(conn)
    assert migrate(conn) == 0
    assert current_version(conn) == SCHEMA_VERSION
    conn.close()


def test_migrate_survives_reopening(tmp_path):
    path = tmp_path / "v.db"
    first = connect(path)
    migrate(first)
    first.close()

    second = connect(path)
    assert current_version(second) == SCHEMA_VERSION
    assert migrate(second) == 0
    second.close()


def test_a_vault_from_a_newer_build_is_refused_not_mangled(tmp_path):
    """Opening a future vault must fail loudly rather than half-migrate it."""
    conn = connect(tmp_path / "v.db")
    migrate(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    with pytest.raises(StoreError, match="Upgrade Cortex"):
        migrate(conn)
    conn.close()


def test_fts_triggers_track_records(tmp_path, embedder):
    from cortex.store import create_record, delete_record, update_record

    conn = connect(tmp_path / "v.db")
    migrate(conn)
    ensure_vector_index(conn, embedder.model, embedder.dim)

    record = create_record(
        conn, embedder, project="P", title="Lighthouse", body="A copper lantern burns."
    )

    def fts_ids(term):
        rows = conn.execute(
            "SELECT rowid FROM records_fts WHERE records_fts MATCH ?", (term,)
        )
        return [r["rowid"] for r in rows]

    assert fts_ids("copper") == [record.id]

    update_record(conn, embedder, record.id, body="A brass bell rings.")
    assert fts_ids("copper") == []
    assert fts_ids("brass") == [record.id]

    delete_record(conn, record.id)
    assert fts_ids("brass") == []
    conn.close()


def test_vector_index_records_the_model_it_was_built_with(tmp_path, embedder):
    conn = connect(tmp_path / "v.db")
    migrate(conn)
    ensure_vector_index(conn, embedder.model, embedder.dim)
    assert get_meta(conn, "embed_model") == "fake-embed"
    assert get_meta(conn, "embed_dim") == "256"
    conn.close()


def test_switching_embedding_model_is_refused_until_reindex(tmp_path, embedder):
    """Mixing two vector spaces in one index degrades search silently. Don't."""
    conn = connect(tmp_path / "v.db")
    migrate(conn)
    ensure_vector_index(conn, embedder.model, embedder.dim)

    with pytest.raises(StoreError, match="cortex reindex"):
        ensure_vector_index(conn, "some-other-model", 256)

    with pytest.raises(StoreError, match="cortex reindex"):
        ensure_vector_index(conn, embedder.model, 768)

    conn.close()


def test_reopening_with_the_same_model_is_fine(tmp_path, embedder):
    conn = connect(tmp_path / "v.db")
    migrate(conn)
    ensure_vector_index(conn, embedder.model, embedder.dim)
    ensure_vector_index(conn, embedder.model, embedder.dim)
    conn.close()
