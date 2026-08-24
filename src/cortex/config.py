"""Runtime configuration.

Everything is env-overridable so the same code runs from a dev checkout, a
pipx install, or a container without editing source.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def default_data_dir() -> Path:
    """Platform-appropriate location for the vault.

    Honours CORTEX_DATA_DIR above everything else so a dev checkout or a test
    can point somewhere disposable.
    """
    override = os.environ.get("CORTEX_DATA_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "cortex"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "cortex"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "cortex"


@dataclass(frozen=True)
class Config:
    data_dir: Path = field(default_factory=default_data_dir)
    ollama_host: str = "http://127.0.0.1:11434"

    embed_model: str = "embeddinggemma"
    librarian_model: str = "qwen2.5:14b"
    creative_model: str = "gemma4:12b"

    # Chunking. Kept well under the 2048-token ceiling shared by every
    # embedding model we support, so a chunk can never be rejected or
    # silently truncated by the embedder.
    chunk_target_tokens: int = 400
    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 60

    # Reciprocal rank fusion constant. 60 is the value from the original
    # RRF paper and behaves well without tuning.
    rrf_k: int = 60

    # Vector hits further away than this are dropped. Model-dependent — see
    # the note on DEFAULT_MAX_DISTANCE in retrieve.py before changing it.
    max_distance: float = 0.75

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cortex.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @classmethod
    def from_env(cls) -> Config:
        def _float(name: str, fallback: float) -> float:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                return fallback
            try:
                return float(raw)
            except ValueError:
                return fallback

        def _int(name: str, fallback: int) -> int:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                return fallback
            try:
                return int(raw)
            except ValueError:
                return fallback

        return cls(
            data_dir=default_data_dir(),
            ollama_host=os.environ.get("CORTEX_OLLAMA_HOST", cls.ollama_host),
            embed_model=os.environ.get("CORTEX_EMBED_MODEL", cls.embed_model),
            librarian_model=os.environ.get("CORTEX_LIBRARIAN_MODEL", cls.librarian_model),
            creative_model=os.environ.get("CORTEX_CREATIVE_MODEL", cls.creative_model),
            chunk_target_tokens=_int("CORTEX_CHUNK_TARGET", cls.chunk_target_tokens),
            chunk_max_tokens=_int("CORTEX_CHUNK_MAX", cls.chunk_max_tokens),
            chunk_overlap_tokens=_int("CORTEX_CHUNK_OVERLAP", cls.chunk_overlap_tokens),
            max_distance=_float("CORTEX_MAX_DISTANCE", cls.max_distance),
        )
