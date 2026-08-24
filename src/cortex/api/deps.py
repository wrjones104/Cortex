"""Auth and per-request resources.

Connections are opened per request rather than shared. SQLite objects belong
to the thread that made them, and FastAPI runs sync endpoints on a threadpool,
so a shared connection would fail intermittently under exactly the concurrency
a web client produces. Opening one costs microseconds; WAL mode lets readers
run alongside a writer.

The embedder is the opposite case: it is a module-level singleton so that the
dimension probe — a real call to Ollama — happens once for the process rather
than once per request.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status

from ..config import Config, load_or_create_token
from ..db import StoreError, connect
from ..embed import EmbeddingError, OllamaEmbedder
from ..llm import OllamaLibrarian
from ..migrations import ensure_vector_index, migrate
from ..vault import Vault

_config: Config | None = None
_token: str | None = None
_embedder: OllamaEmbedder | None = None
_librarian: OllamaLibrarian | None = None


def configure(config: Config, token: str | None = None) -> str:
    """Bind the process to one vault. Returns the active API token."""
    global _config, _token, _embedder, _librarian
    _config = config
    _token = token or load_or_create_token(config.data_dir)
    _embedder = OllamaEmbedder(config.ollama_host, config.embed_model)
    _librarian = OllamaLibrarian(config.ollama_host, config.librarian_model)
    return _token


def get_config() -> Config:
    if _config is None:
        raise RuntimeError("API not configured - call configure() before serving.")
    return _config


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Bearer auth on every route except /health.

    Compared with secrets.compare_digest so a wrong token cannot be recovered
    by timing the response.
    """
    if _token is None:
        raise RuntimeError("API not configured - call configure() before serving.")

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Send your Cortex token as 'Authorization: Bearer <token>'. "
            "Run `cortex token` to see it.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(presented, _token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That token is not valid for this vault.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_conn() -> Iterator[sqlite3.Connection]:
    """A migrated connection. Does not need Ollama."""
    config = get_config()
    conn = connect(config.db_path)
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()


def build_vault(config: Config) -> Vault:
    """Open a ready-to-index vault, raising core exceptions rather than HTTP ones.

    Call this from whichever thread will use the connection - SQLite objects
    belong to the thread that created them. The streaming endpoint does its
    work on a worker thread and so opens its own.
    """
    assert _embedder is not None and _librarian is not None

    conn = connect(config.db_path)
    try:
        migrate(conn)
        ensure_vector_index(conn, _embedder.model, _embedder.dim)
    except BaseException:
        conn.close()
        raise
    return Vault(conn=conn, config=config, embedder=_embedder, librarian=_librarian)


def get_vault() -> Iterator[Vault]:
    """A connection with the vector index ready. Needs Ollama to be reachable."""
    config = get_config()

    try:
        vault = build_vault(config)
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The embedding model is unavailable. {exc}",
        ) from exc
    except StoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    try:
        yield vault
    finally:
        vault.close()


Authed = Depends(require_token)
