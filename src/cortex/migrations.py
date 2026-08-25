"""Schema definition and forward migrations.

Every schema change is a numbered step applied in order and tracked in
PRAGMA user_version. There is no `CREATE TABLE IF NOT EXISTS` anywhere: a
vault either is at a known version or gets migrated to one, so an older vault
can never silently keep an older shape.
"""

from __future__ import annotations

import sqlite3

from .db import StoreError, get_meta, set_meta, transaction

MIGRATION_1 = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE projects (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    prompt_override TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE records (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    category        TEXT NOT NULL DEFAULT '',
    subcategory     TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'capture',
    content_hash    TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX idx_records_project ON records(project_id);
CREATE INDEX idx_records_hash    ON records(content_hash);
CREATE INDEX idx_records_created ON records(created_at DESC);

-- AUTOINCREMENT so a chunk id is never reused. vec_chunks is keyed by
-- chunk id, so a recycled id could silently pair a new chunk with a stale
-- vector - wrong search results with nothing raising an error.
CREATE TABLE chunks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    ordinal   INTEGER NOT NULL,
    text      TEXT NOT NULL,
    tokens    INTEGER NOT NULL,
    UNIQUE (record_id, ordinal)
);

CREATE INDEX idx_chunks_record ON chunks(record_id);

-- External-content FTS: the index points at records rather than copying it,
-- so there is exactly one copy of every note in the file.
CREATE VIRTUAL TABLE records_fts USING fts5(
    title,
    body,
    content='records',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER records_fts_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, title, body)
    VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER records_fts_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER records_fts_au AFTER UPDATE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO records_fts(rowid, title, body)
    VALUES (new.id, new.title, new.body);
END;
"""

MIGRATION_2 = """
CREATE TABLE threads (
    id              INTEGER PRIMARY KEY,
    title           TEXT NOT NULL,
    project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    model           TEXT,
    summary         TEXT NOT NULL DEFAULT '',
    summarised_upto INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX idx_threads_updated ON threads(updated_at DESC);

-- AUTOINCREMENT because summarised_upto is a message id watermark: a recycled
-- id would make already-summarised turns look unread, or hide new ones.
CREATE TABLE messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'marker')),
    content      TEXT NOT NULL,
    sources_json TEXT,
    tokens       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_messages_thread ON messages(thread_id, id);

-- The part that stops a long thread forgetting. Facts are extracted from
-- turns as they are summarised away, and are never summarised themselves.
CREATE TABLE thread_facts (
    id         INTEGER PRIMARY KEY,
    thread_id  INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    fact       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (thread_id, fact)
);

CREATE INDEX idx_facts_thread ON thread_facts(thread_id, id);
"""

MIGRATION_3 = """
-- Generations are kept so a second attempt cannot destroy a batch you had
-- not finished mining, and so banking one idea is a small request rather than
-- posting the whole batch back.
CREATE TABLE generations (
    id         INTEGER PRIMARY KEY,
    prompt     TEXT NOT NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    model      TEXT NOT NULL,
    mode       TEXT NOT NULL CHECK (mode IN ('options', 'freeform')),
    output     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_generations_created ON generations(created_at DESC);

CREATE TABLE generation_ideas (
    id               INTEGER PRIMARY KEY,
    generation_id    INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    ordinal          INTEGER NOT NULL,
    title            TEXT NOT NULL,
    pitch            TEXT NOT NULL DEFAULT '',
    detail           TEXT NOT NULL,
    -- Set once an idea is banked, so the same one cannot be filed twice and
    -- the UI can show which of a batch you already took.
    banked_record_id INTEGER REFERENCES records(id) ON DELETE SET NULL,
    UNIQUE (generation_id, ordinal)
);

CREATE INDEX idx_ideas_generation ON generation_ideas(generation_id, ordinal);
"""

MIGRATION_4 = """
-- What a project is about, in the author's own words. Used as grounding for
-- everything filed or generated under it, so it is foundational rather than
-- decorative.
ALTER TABLE projects ADD COLUMN description TEXT NOT NULL DEFAULT '';

-- prompt_override was added in migration 1 and never read. Dead columns are
-- how a schema starts lying about itself.
ALTER TABLE projects DROP COLUMN prompt_override;
"""

# (version, description, sql)
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial schema", MIGRATION_1),
    (2, "chat threads, messages and the facts ledger", MIGRATION_2),
    (3, "creative generations and their ideas", MIGRATION_3),
    (4, "project descriptions", MIGRATION_4),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Bring a vault up to SCHEMA_VERSION. Returns the number of steps applied."""
    version = current_version(conn)

    if version > SCHEMA_VERSION:
        raise StoreError(
            f"This vault is at schema version {version}, but this build of Cortex only "
            f"understands up to {SCHEMA_VERSION}. Upgrade Cortex to open it."
        )

    applied = 0
    for number, _description, sql in MIGRATIONS:
        if number <= version:
            continue
        _apply(conn, number, sql)
        applied += 1

    return applied


def _apply(conn: sqlite3.Connection, number: int, sql: str) -> None:
    """Run one migration atomically.

    The transaction has to live inside the script rather than around it:
    sqlite3.executescript() commits any open transaction before it starts, so
    wrapping it in our own BEGIN would leave nothing to commit at the end.
    SQLite's DDL is transactional, so a failure halfway rolls the whole schema
    step back rather than leaving a half-built vault.

    PRAGMA does not accept bound parameters; number comes from MIGRATIONS.
    """
    try:
        conn.executescript(
            f"BEGIN;\n{sql}\nPRAGMA user_version = {int(number)};\nCOMMIT;"
        )
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def ensure_vector_index(conn: sqlite3.Connection, model: str, dim: int) -> None:
    """Create the vec0 index, or verify the existing one still matches the model.

    The vector table's dimension is fixed at creation, so switching embedding
    models is a reindex rather than a migration. Catching the mismatch here
    stops two incompatible vector spaces being mixed in one index, which would
    degrade search silently rather than loudly.
    """
    stored_model = get_meta(conn, "embed_model")
    stored_dim = get_meta(conn, "embed_dim")

    if stored_model is None:
        with transaction(conn):
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
                f"USING vec0(embedding float[{int(dim)}] distance_metric=cosine)"
            )
            set_meta(conn, "embed_model", model)
            set_meta(conn, "embed_dim", str(int(dim)))
        return

    if stored_model != model or int(stored_dim or 0) != int(dim):
        raise StoreError(
            f"This vault was indexed with {stored_model} ({stored_dim} dimensions) but "
            f"Cortex is configured for {model} ({dim} dimensions). Run "
            f"`cortex reindex` to rebuild the index with the new model."
        )

