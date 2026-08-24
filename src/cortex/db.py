"""SQLite connection handling.

One file holds everything: records, chunks, vectors, the full-text index and
chat threads. That is the whole point of the storage design - a record and its
embedding are written by the same COMMIT, so the two can never drift apart.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import sqlite_vec


class StoreError(RuntimeError):
    """Raised when the vault cannot be opened or written."""


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a vault connection with extensions loaded and sane pragmas.

    isolation_level=None puts transaction control in our hands; every write
    goes through transaction() below rather than relying on implicit commits.
    """
    path = Path(db_path)
    if path.parent and str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row

    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except (AttributeError, sqlite3.OperationalError) as exc:
        conn.close()
        raise StoreError(
            "This Python build cannot load SQLite extensions, which Cortex needs "
            "for vector search. Install Python from python.org, or use the "
            "Docker image."
        ) from exc
    finally:
        with contextlib.suppress(AttributeError):
            conn.enable_load_extension(False)

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Wrap a unit of work so a failure anywhere rolls the whole thing back.

    Nested use is safe: an inner transaction() joins the outer one rather than
    starting a second, so callers can compose store operations freely.
    """
    if conn.in_transaction:
        yield conn
        return

    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        # Guarded: a statement that implicitly committed would make ROLLBACK
        # raise too, masking the original error with a confusing one.
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def serialize_vector(values: Sequence[float]) -> bytes:
    """Pack a float vector into the compact form vec0 columns expect."""
    return sqlite_vec.serialize_float32(list(values))


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
