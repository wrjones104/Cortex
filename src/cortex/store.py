"""Record storage: create, read, update, delete, reindex.

Every write that touches more than one table goes through a single
transaction, so a record, its chunks, its vectors and its full-text entry
either all land or none of them do.

Network calls (embedding) happen *before* the transaction opens. Holding a
write lock open across a model call would block every other reader for the
duration of the inference.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime

from .chunk import Chunk, chunk_text
from .db import serialize_vector, transaction
from .embed import Embedder
from .models import Project, Record

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

_RECORD_SELECT = """
SELECT r.*, p.name AS project_name
FROM records r
JOIN projects p ON p.id = r.project_id
"""


class DuplicateRecordError(RuntimeError):
    """Raised when an identical note already exists in the same project."""

    def __init__(self, existing_id: int, title: str) -> None:
        super().__init__(
            f"An identical note is already in this project: #{existing_id} - {title}"
        )
        self.existing_id = existing_id
        self.title = title


class RecordNotFoundError(LookupError):
    pass


def utcnow() -> str:
    """Timezone-aware UTC, ISO 8601, with the offset kept.

    The prototype stored naive UTC via CURRENT_TIMESTAMP and rendered it as if
    it were local time, so every note displayed hours in the future. Keeping
    the offset makes the conversion unambiguous at the render layer.

    Microseconds, not seconds: conversations are ordered by when they were
    last touched, and at coarse resolution two threads touched close together
    tie. The thread list does not rely on this alone (see list_threads), but
    there is no reason to throw the precision away.
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


def slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    return slug or "untitled"


def content_hash(title: str, body: str) -> str:
    payload = f"{title.strip()}\n\n{body.strip()}".encode()
    return hashlib.sha256(payload).hexdigest()


# --- projects -------------------------------------------------------------


def get_or_create_project(conn: sqlite3.Connection, name: str) -> Project:
    """Look a project up by slug so casing and spacing can't fork it in two.

    In the prototype a project was a free-text string on every record, which
    meant "Echoes" and "echoes " were different projects forever.
    """
    clean = name.strip() or "Untitled Project"
    slug = slugify(clean)

    row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    if row:
        return Project.from_row(row)

    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO projects (name, slug, created_at) VALUES (?, ?, ?)",
            (clean, slug, utcnow()),
        )
        project_id = cursor.lastrowid

    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return Project.from_row(row)


def list_projects(conn: sqlite3.Connection) -> list[Project]:
    rows = conn.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
    return [Project.from_row(r) for r in rows]


def find_project(conn: sqlite3.Connection, name: str) -> Project | None:
    row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slugify(name),)).fetchone()
    return Project.from_row(row) if row else None


# --- records --------------------------------------------------------------


def get_record(conn: sqlite3.Connection, record_id: int) -> Record:
    row = conn.execute(f"{_RECORD_SELECT} WHERE r.id = ?", (record_id,)).fetchone()
    if row is None:
        raise RecordNotFoundError(f"No record with id {record_id}")
    return Record.from_row(row)


def list_records(
    conn: sqlite3.Connection,
    *,
    project: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Record]:
    sql = _RECORD_SELECT
    params: list[object] = []
    if project:
        sql += " WHERE p.slug = ?"
        params.append(slugify(project))
    sql += " ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [Record.from_row(r) for r in conn.execute(sql, params).fetchall()]


def count_records(conn: sqlite3.Connection, *, project: str | None = None) -> int:
    if project:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM records r JOIN projects p ON p.id = r.project_id "
            "WHERE p.slug = ?",
            (slugify(project),),
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()
    return int(row["n"])


def find_by_idempotency_key(conn: sqlite3.Connection, key: str) -> Record | None:
    row = conn.execute(f"{_RECORD_SELECT} WHERE r.idempotency_key = ?", (key,)).fetchone()
    return Record.from_row(row) if row else None


def _write_chunks(
    conn: sqlite3.Connection,
    record_id: int,
    chunks: Sequence[Chunk],
    vectors: Sequence[Sequence[float]],
) -> None:
    """Insert chunk rows and their vectors. Caller owns the transaction."""
    for chunk, vector in zip(chunks, vectors, strict=True):
        cursor = conn.execute(
            "INSERT INTO chunks (record_id, ordinal, text, tokens) VALUES (?, ?, ?, ?)",
            (record_id, chunk.ordinal, chunk.text, chunk.tokens),
        )
        conn.execute(
            "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
            (cursor.lastrowid, serialize_vector(vector)),
        )


def _drop_chunks(conn: sqlite3.Connection, record_id: int) -> None:
    """Remove a record's chunks and vectors.

    vec_chunks is a virtual table, so it gets no ON DELETE CASCADE from the
    chunks foreign key - its rows have to be deleted by hand or they linger as
    orphans that still match searches.
    """
    ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE record_id = ?", (record_id,))]
    for chunk_id in ids:
        conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (chunk_id,))
    conn.execute("DELETE FROM chunks WHERE record_id = ?", (record_id,))


def create_record(
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    project: str,
    title: str,
    body: str,
    category: str = "",
    subcategory: str = "",
    source: str = "capture",
    idempotency_key: str | None = None,
    allow_duplicate: bool = False,
    chunk_options: dict | None = None,
) -> Record:
    """Store a note, chunked and indexed, in one transaction.

    Raises DuplicateRecordError when an identical note is already in the same
    project, unless allow_duplicate is set.
    """
    title = title.strip() or "Untitled"
    body = body.rstrip()

    if idempotency_key:
        existing = find_by_idempotency_key(conn, idempotency_key)
        if existing is not None:
            # The phone replayed a capture it wasn't sure had landed.
            return existing

    project_row = get_or_create_project(conn, project)
    digest = content_hash(title, body)

    if not allow_duplicate:
        dupe = conn.execute(
            "SELECT id, title FROM records WHERE content_hash = ? AND project_id = ?",
            (digest, project_row.id),
        ).fetchone()
        if dupe:
            raise DuplicateRecordError(dupe["id"], dupe["title"])

    chunks = chunk_text(body, **(chunk_options or {}))
    vectors = embedder.embed([c.text for c in chunks]) if chunks else []

    now = utcnow()
    with transaction(conn):
        cursor = conn.execute(
            """
            INSERT INTO records
                (project_id, category, subcategory, title, body, source,
                 content_hash, idempotency_key, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_row.id,
                category.strip(),
                subcategory.strip(),
                title,
                body,
                source,
                digest,
                idempotency_key,
                now,
                now,
            ),
        )
        record_id = cursor.lastrowid
        _write_chunks(conn, record_id, chunks, vectors)

    return get_record(conn, record_id)


def update_record(
    conn: sqlite3.Connection,
    embedder: Embedder,
    record_id: int,
    *,
    project: str | None = None,
    title: str | None = None,
    body: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    chunk_options: dict | None = None,
) -> Record:
    """Update a record, re-chunking and re-embedding only when the body moved."""
    existing = get_record(conn, record_id)

    new_title = existing.title if title is None else (title.strip() or "Untitled")
    new_body = existing.body if body is None else body.rstrip()
    body_changed = new_body != existing.body

    project_id = existing.project_id
    if project is not None and slugify(project) != slugify(existing.project_name):
        project_id = get_or_create_project(conn, project).id

    chunks: list[Chunk] = []
    vectors: list[list[float]] = []
    if body_changed:
        chunks = chunk_text(new_body, **(chunk_options or {}))
        vectors = embedder.embed([c.text for c in chunks]) if chunks else []

    with transaction(conn):
        conn.execute(
            """
            UPDATE records
               SET project_id = ?, title = ?, body = ?, category = ?,
                   subcategory = ?, content_hash = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                project_id,
                new_title,
                new_body,
                existing.category if category is None else category.strip(),
                existing.subcategory if subcategory is None else subcategory.strip(),
                content_hash(new_title, new_body),
                utcnow(),
                record_id,
            ),
        )
        if body_changed:
            _drop_chunks(conn, record_id)
            _write_chunks(conn, record_id, chunks, vectors)

    return get_record(conn, record_id)


def delete_record(conn: sqlite3.Connection, record_id: int) -> None:
    get_record(conn, record_id)
    with transaction(conn):
        _drop_chunks(conn, record_id)
        conn.execute("DELETE FROM records WHERE id = ?", (record_id,))


def reindex(
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    chunk_options: dict | None = None,
    progress=None,
) -> int:
    """Rebuild every chunk and vector from the record bodies.

    Records are the source of truth, so this is always safe to run: after a
    crash, after switching embedding models, or after changing chunk settings.
    Returns the number of records reindexed.
    """
    from .db import set_meta

    rows = conn.execute("SELECT id FROM records ORDER BY id").fetchall()
    record_ids = [r["id"] for r in rows]

    with transaction(conn):
        conn.execute("DROP TABLE IF EXISTS vec_chunks")
        conn.execute(
            f"CREATE VIRTUAL TABLE vec_chunks "
            f"USING vec0(embedding float[{int(embedder.dim)}] distance_metric=cosine)"
        )
        conn.execute("DELETE FROM chunks")
        set_meta(conn, "embed_model", embedder.model)
        set_meta(conn, "embed_dim", str(int(embedder.dim)))

    for position, record_id in enumerate(record_ids, start=1):
        record = get_record(conn, record_id)
        chunks = chunk_text(record.body, **(chunk_options or {}))
        vectors = embedder.embed([c.text for c in chunks]) if chunks else []
        with transaction(conn):
            _write_chunks(conn, record_id, chunks, vectors)
        if progress is not None:
            progress(position, len(record_ids), record)

    return len(record_ids)


def has_vector_index(conn: sqlite3.Connection) -> bool:
    """Whether the vec0 table exists yet.

    It is created on first use, which needs the embedding model to report its
    width - so a vault opened without Ollama running has records and chunks
    but no vector table.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vec_chunks'"
    ).fetchone()
    return row is not None


def integrity_report(conn: sqlite3.Connection) -> dict[str, int]:
    """Cheap consistency check. All four counts should be zero in a healthy vault."""
    orphan_chunks = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks c "
        "LEFT JOIN records r ON r.id = c.record_id WHERE r.id IS NULL"
    ).fetchone()["n"]

    chunk_ids = {r["id"] for r in conn.execute("SELECT id FROM chunks")}
    vec_ids = (
        {r["rowid"] for r in conn.execute("SELECT rowid FROM vec_chunks")}
        if has_vector_index(conn)
        else set()
    )

    unindexed = conn.execute(
        "SELECT COUNT(*) AS n FROM records r "
        "WHERE TRIM(r.body) != '' "
        "AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.record_id = r.id)"
    ).fetchone()["n"]

    return {
        "orphan_chunks": int(orphan_chunks),
        "chunks_without_vectors": len(chunk_ids - vec_ids),
        "vectors_without_chunks": len(vec_ids - chunk_ids),
        "records_without_chunks": int(unindexed),
    }
