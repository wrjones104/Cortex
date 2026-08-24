"""Opening a vault, with or without a model server.

Half the useful commands - list, show, export, backup - have no business
failing because Ollama happens to be down, so opening for reading is kept
separate from opening for indexing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .config import Config
from .db import connect
from .embed import OllamaEmbedder
from .llm import OllamaLibrarian
from .migrations import ensure_vector_index, migrate


@dataclass
class Vault:
    conn: sqlite3.Connection
    config: Config
    embedder: OllamaEmbedder
    librarian: OllamaLibrarian

    @property
    def chunk_options(self) -> dict:
        return {
            "target_tokens": self.config.chunk_target_tokens,
            "max_tokens": self.config.chunk_max_tokens,
            "overlap_tokens": self.config.chunk_overlap_tokens,
        }

    def close(self) -> None:
        self.conn.close()


def open_readonly(config: Config) -> sqlite3.Connection:
    """Open and migrate a vault without touching Ollama."""
    conn = connect(config.db_path)
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def open_vault(config: Config) -> Vault:
    """Open a vault ready for indexing. Requires Ollama to be reachable."""
    conn = connect(config.db_path)
    try:
        migrate(conn)
        embedder = OllamaEmbedder(config.ollama_host, config.embed_model)
        ensure_vector_index(conn, embedder.model, embedder.dim)
    except BaseException:
        conn.close()
        raise

    return Vault(
        conn=conn,
        config=config,
        embedder=embedder,
        librarian=OllamaLibrarian(config.ollama_host, config.librarian_model),
    )
