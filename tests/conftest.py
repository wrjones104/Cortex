from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Sequence

import pytest

from cortex.db import connect
from cortex.llm import StructuredNote
from cortex.migrations import ensure_vector_index, migrate

_WORD = re.compile(r"\w+")


class FakeEmbedder:
    """Deterministic bag-of-words embedder.

    Real enough for retrieval tests — documents sharing vocabulary land near
    each other — and it needs no model server, so the whole suite runs offline
    and in milliseconds.
    """

    model = "fake-embed"

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim
        self.calls = 0

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        vectors = []
        for text in texts:
            vector = [0.0] * self._dim
            for word in _WORD.findall(text.lower()):
                digest = hashlib.md5(word.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % self._dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vector))
            if norm:
                vector = [v / norm for v in vector]
            else:
                vector[0] = 1.0
            vectors.append(vector)
        return vectors


class ExplodingEmbedder:
    """Stands in for an unreachable model server."""

    model = "exploding"

    @property
    def dim(self) -> int:
        return 256

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("model server is down")


class FakeLibrarian:
    """Returns predictable structure without touching a model."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.last_context = None
        self.calls = 0

    def structure(self, raw_text, *, project=None, context=""):
        from cortex.llm import LibrarianError

        self.calls += 1
        self.last_context = context
        if self.fail:
            raise LibrarianError("model unavailable")
        first = next((ln.strip() for ln in raw_text.splitlines() if ln.strip()), "Untitled")
        return StructuredNote(
            project=project or "Auto Project",
            category="Test Category",
            subcategory="Test Subcategory",
            title=first[:60],
            content=raw_text.strip(),
        )


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def librarian() -> FakeLibrarian:
    return FakeLibrarian()


@pytest.fixture
def conn(tmp_path, embedder):
    """A migrated, indexed vault on disk in a temp directory."""
    connection = connect(tmp_path / "cortex.db")
    migrate(connection)
    ensure_vector_index(connection, embedder.model, embedder.dim)
    yield connection
    connection.close()


@pytest.fixture
def ollama_available() -> bool:
    import ollama

    host = os.environ.get("CORTEX_OLLAMA_HOST", "http://127.0.0.1:11434")
    try:
        ollama.Client(host=host).list()
        return True
    except Exception:
        return False


@pytest.fixture
def sample_notes(conn, embedder):
    """Three records with deliberately disjoint vocabulary."""
    from cortex.store import create_record

    created = [
        create_record(
            conn,
            embedder,
            project="Echoes",
            title="The Lighthouse Keeper",
            body=(
                "Wexler tends the copper lantern on the northern cliffs. "
                "He has not spoken to anyone in eleven years."
            ),
            category="Worldbuilding",
        ),
        create_record(
            conn,
            embedder,
            project="Echoes",
            title="Harbour Town Politics",
            body=(
                "The harbour council meets weekly. Trade disputes dominate "
                "every session and nothing is ever resolved."
            ),
            category="Worldbuilding",
        ),
        create_record(
            conn,
            embedder,
            project="Work Notes",
            title="Sprint Retrospective",
            body=(
                "Deployment pipeline keeps timing out. We agreed to split the "
                "integration suite across two runners."
            ),
            category="Engineering",
        ),
    ]
    return created


class FakeChatter:
    """A model that answers predictably and records what it was sent.

    The important thing it captures is the exact message list, so tests can
    assert on what actually reached the model rather than on side effects.
    """

    def __init__(self, model: str = "fake-chat", context_length: int = 4096) -> None:
        self.model = model
        self._context = context_length
        self.calls: list[list[dict]] = []
        self.replies: list[str] = []
        self.prompt_tokens = 100

    @property
    def context_length(self) -> int:
        return self._context

    def _next_reply(self, messages: list[dict]) -> str:
        if self.replies:
            return self.replies.pop(0)
        return "An answer."

    def complete(self, messages, *, think: bool = False):
        self.calls.append(messages)
        return self._next_reply(messages), self.prompt_tokens

    def stream(self, messages, *, think: bool = False):
        self.calls.append(messages)
        reply = self._next_reply(messages)
        for word in reply.split(" "):
            yield ("token", word + " ")
        yield ("done", self.prompt_tokens)

    @property
    def last_system(self) -> str:
        return self.calls[-1][0]["content"] if self.calls else ""


@pytest.fixture
def chatter():
    return FakeChatter()
