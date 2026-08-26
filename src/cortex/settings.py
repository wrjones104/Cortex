"""Settings that can change without restarting.

Model routing lives in the vault rather than the environment, so switching the
Librarian is a click rather than an edit-and-restart. Config supplies the
defaults; anything stored in `meta` wins.

The embedding model is deliberately not in here. Changing it invalidates every
vector in the index, so it is a `cortex reindex` rather than a setting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .config import Config
from .db import get_meta, set_meta, transaction

# Settings holding the name of a model, as opposed to the profile switch.
# Only these get validated against what Ollama has installed.
MODEL_FIELDS = ("librarian_model", "creative_model", "utility_model", "single_model")

PROFILES = ("split", "single")

# Only these may be overridden at runtime, so a bad key cannot quietly become
# a permanent row in meta.
OVERRIDABLE = (*MODEL_FIELDS, "model_profile")


@dataclass(frozen=True)
class Settings:
    librarian_model: str
    creative_model: str
    embed_model: str
    """Fixed by the vector index. Change it with `cortex reindex`."""
    utility_model: str = ""
    """Small fast model for query condensation and summaries. Falls back to
    the Librarian when empty - these are frequent little calls where latency
    matters more than depth."""
    model_profile: str = "split"
    """'split' for a specialist per role, 'single' for one model doing all of
    them. The per-role choices are kept either way, so switching back restores
    the arrangement rather than making you rebuild it."""
    single_model: str = ""
    """The model every role uses under the 'single' profile."""

    @property
    def single(self) -> bool:
        return self.model_profile == "single" and bool(self.single_model.strip())

    # The three properties below are what callers must build clients from.
    # The plain fields are the stored *preference* and stay untouched by the
    # profile, so that the Settings screen can still show what split mode
    # would use while single mode is active.

    @property
    def effective_librarian(self) -> str:
        """Files notes, and answers in chat."""
        return self.single_model if self.single else self.librarian_model

    @property
    def effective_creative(self) -> str:
        """Brainstorms."""
        return self.single_model if self.single else self.creative_model

    @property
    def effective_utility(self) -> str:
        """Condenses queries, summarises, extracts facts, titles threads."""
        if self.single:
            return self.single_model
        return self.utility_model or self.librarian_model


def get_settings(conn: sqlite3.Connection, config: Config) -> Settings:
    return Settings(
        librarian_model=get_meta(conn, "librarian_model") or config.librarian_model,
        creative_model=get_meta(conn, "creative_model") or config.creative_model,
        embed_model=get_meta(conn, "embed_model") or config.embed_model,
        utility_model=get_meta(conn, "utility_model") or config.utility_model,
        model_profile=get_meta(conn, "model_profile") or config.model_profile,
        single_model=get_meta(conn, "single_model") or config.single_model,
    )


def set_settings(conn: sqlite3.Connection, **values: str | None) -> None:
    """Persist model routing. Unknown keys are rejected rather than ignored."""
    unknown = set(values) - set(OVERRIDABLE)
    if unknown:
        raise ValueError(f"Not a changeable setting: {', '.join(sorted(unknown))}")

    profile = values.get("model_profile")
    if profile is not None and profile.strip() and profile.strip() not in PROFILES:
        raise ValueError(
            f"Not a model profile: {profile.strip()}. Choose one of {', '.join(PROFILES)}."
        )

    with transaction(conn):
        for key, value in values.items():
            if value is not None and value.strip():
                set_meta(conn, key, value.strip())


def normalise_model(name: str) -> str:
    """Ollama treats a bare name as the :latest tag. Compare them the same way."""
    name = name.strip()
    return name if ":" in name else f"{name}:latest"


def installed_models(host: str, timeout: float = 10.0) -> list[dict]:
    """Every model Ollama has, with what it can actually do.

    Capabilities are what stop you selecting an embedding model as your chat
    model - a mistake the prototype allowed, and which then failed with a 400
    the next time you generated anything.

    This reads /api/tags over HTTP rather than through ollama.Client.list().
    The endpoint returns a `capabilities` array per model, but the client's
    typed Model has no such field, so the typed path drops it silently and
    every model comes back looking incapable of everything.
    """
    import httpx

    response = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=timeout)
    response.raise_for_status()

    models = []
    for entry in response.json().get("models", []):
        capabilities = list(entry.get("capabilities") or [])
        models.append(
            {
                "name": entry.get("model") or entry.get("name"),
                "parameter_size": (entry.get("details") or {}).get("parameter_size"),
                "capabilities": capabilities,
                "can_chat": "completion" in capabilities,
                "can_embed": "embedding" in capabilities,
                "can_think": "thinking" in capabilities,
            }
        )
    return sorted(models, key=lambda m: m["name"] or "")
