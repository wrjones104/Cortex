"""Embedding backends.

Everything downstream depends on the Embedder protocol rather than on Ollama,
so the storage and retrieval layers are testable without a model server
running. The real implementation is a thin wrapper over Ollama's batch embed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import ollama

from .ollama_client import client_for


class EmbeddingError(RuntimeError):
    """Raised when text could not be embedded."""


@runtime_checkable
class Embedder(Protocol):
    model: str

    @property
    def dim(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Embeds through a local Ollama server."""

    def __init__(self, host: str, model: str, batch_size: int = 32) -> None:
        self.model = model
        self.host = host
        self.batch_size = batch_size
        self._dim: int | None = None

    @property
    def _client(self) -> ollama.Client:
        """Fetched on first use, and shared with every other caller on this host.

        Constructing an ollama.Client builds an httpx.Client, which loads a
        default SSL context - around 170 ms. Plenty of things hold an embedder
        without ever calling a model, so that should not be paid up front.
        """
        return client_for(self.host)

    @property
    def dim(self) -> int:
        """Vector width, probed once from the model itself.

        Probing beats hardcoding: it means switching embedding models is a
        config change plus a reindex, not a code change.
        """
        if self._dim is None:
            probe = self.embed(["dimension probe"])
            if not probe or not probe[0]:
                raise EmbeddingError(f"{self.model} returned an empty embedding.")
            self._dim = len(probe[0])
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            try:
                # truncate=False on purpose: silently dropping the tail of a
                # chunk is exactly the failure mode chunking exists to prevent,
                # so an oversized input should be a loud error instead.
                response = self._client.embed(model=self.model, input=batch, truncate=False)
            except Exception as exc:  # noqa: BLE001 - surfaced with context below
                raise EmbeddingError(
                    f"Could not embed with {self.model}: {type(exc).__name__}: {exc}"
                ) from exc

            vectors = response["embeddings"]
            if len(vectors) != len(batch):
                raise EmbeddingError(
                    f"{self.model} returned {len(vectors)} embeddings for {len(batch)} inputs."
                )
            out.extend(list(v) for v in vectors)

        return out
