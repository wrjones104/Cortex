"""Runtime configuration.

Everything is env-overridable so the same code runs from a dev checkout, a
pipx install, or a container without editing source.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sys
from dataclasses import dataclass, field, replace
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

    # Which file under data_dir is the vault. One process serves several
    # accounts, and each one gets its own; see accounts.py for why isolation
    # is by file rather than by a user_id column. The default is the vault
    # that existed before accounts did, which the owner adopts.
    vault_file: str = "cortex.db"

    embed_model: str = "embeddinggemma"
    librarian_model: str = "qwen2.5:14b"
    creative_model: str = "gemma4:12b"
    # Small and fast: condensation and summaries run often and are not
    # the answer. Empty means "use the librarian".
    utility_model: str = ""

    # Chunking. Kept well under the 2048-token ceiling shared by every
    # embedding model we support, so a chunk can never be rejected or
    # silently truncated by the embedder.
    chunk_target_tokens: int = 400
    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 60

    # Reciprocal rank fusion constant. 60 is the value from the original
    # RRF paper and behaves well without tuning.
    rrf_k: int = 60

    # Vector hits further away than this are dropped. None means "use the
    # default for whichever embedding model is configured" — good values
    # differ sharply between models; see MAX_DISTANCE_BY_MODEL in retrieve.py.
    max_distance: float | None = None

    @property
    def effective_max_distance(self) -> float:
        if self.max_distance is not None:
            return self.max_distance
        from .retrieve import max_distance_for

        return max_distance_for(self.embed_model)

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.vault_file

    def for_vault(self, vault_file: str) -> Config:
        """The same configuration pointed at a different vault.

        Model routing, chunking and the Ollama host are properties of the
        server; which file is open is a property of who is asking.
        """
        return replace(self, vault_file=vault_file)

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @classmethod
    def from_env(cls) -> Config:
        def _float(name: str, fallback: float | None) -> float | None:
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
            utility_model=os.environ.get("CORTEX_UTILITY_MODEL", cls.utility_model),
            chunk_target_tokens=_int("CORTEX_CHUNK_TARGET", cls.chunk_target_tokens),
            chunk_max_tokens=_int("CORTEX_CHUNK_MAX", cls.chunk_max_tokens),
            chunk_overlap_tokens=_int("CORTEX_CHUNK_OVERLAP", cls.chunk_overlap_tokens),
            max_distance=_float("CORTEX_MAX_DISTANCE", cls.max_distance),
        )


def load_or_create_token(data_dir: Path) -> str:
    """Return the API bearer token, generating one on first run.

    Kept in a file next to the vault rather than an env var so the token
    survives a reboot and can be read back by `cortex token`. On POSIX the
    file is chmod 600; Windows inherits the user's profile ACL, which for a
    single-user local app is the same protection the vault itself has.
    """
    override = os.environ.get("CORTEX_API_TOKEN")
    if override and override.strip():
        return override.strip()

    path = data_dir / "api_token"
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    with contextlib.suppress(OSError, NotImplementedError):
        path.chmod(0o600)
    return token
