"""Streaming really streams.

This needs a real uvicorn server. Starlette's TestClient buffers a streaming
response, so a test written against it cannot tell incremental delivery from
collecting every event and flushing them at the end - which is exactly the bug
this file exists to catch. No model server is involved; the librarian is a
fake that blocks until the test releases it.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from cortex.api import deps
from cortex.api.app import create_app
from cortex.config import Config
from cortex.llm import StructuredNote

TOKEN = "streaming-test-token"


class BlockingLibrarian:
    """Stands in for a local model that takes many seconds to answer."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def structure(self, raw_text, *, project=None, context=""):
        self.entered.set()
        if not self.release.wait(timeout=15):
            raise AssertionError("test never released the librarian")
        return StructuredNote(
            project=project or "P",
            category="C",
            subcategory="S",
            title="Blocked Note",
            content=raw_text,
        )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(tmp_path, embedder, monkeypatch):
    librarian = BlockingLibrarian()

    deps.configure(Config(data_dir=tmp_path, embed_model="fake-embed"), TOKEN)
    monkeypatch.setattr(deps, "_embedder", embedder)
    monkeypatch.setattr(deps, "_librarian", librarian)

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "server did not start"

    try:
        yield f"http://127.0.0.1:{port}", librarian
    finally:
        librarian.release.set()
        server.should_exit = True
        thread.join(timeout=10)


def test_progress_arrives_while_the_model_is_still_working(live_server):
    base, librarian = live_server
    headers = {"Authorization": f"Bearer {TOKEN}"}

    with httpx.stream(
        "POST",
        f"{base}/api/records/stream",
        json={"text": "A slow note.", "project": "P"},
        headers=headers,
        timeout=30,
    ) as response:
        assert response.status_code == 200
        lines = response.iter_lines()

        # Read events until the capture reaches the stage that blocks. The
        # response cannot possibly complete until we release the librarian, so
        # receiving anything at all here is the proof: these events were
        # flushed as they happened, not collected and sent at the end.
        seen: list[str] = []
        for line in lines:
            if line.startswith("data: "):
                seen.append(json.loads(line[6:])["stage"])
                if seen[-1] == "structuring":
                    break

        assert seen[0] == "context"
        assert seen[-1] == "structuring"
        assert not librarian.release.is_set(), "the capture cannot have finished yet"
        assert librarian.entered.wait(timeout=5), "the librarian should be running"

        librarian.release.set()
        rest = "\n".join(lines)

    assert "event: record" in rest
    assert '"title": "Blocked Note"' in rest


def test_the_stream_survives_a_client_that_disconnects_early(live_server):
    """Closing the connection mid-stream must not take the server down."""
    base, librarian = live_server
    headers = {"Authorization": f"Bearer {TOKEN}"}

    with httpx.stream(
        "POST",
        f"{base}/api/records/stream",
        json={"text": "Abandoned note.", "project": "P"},
        headers=headers,
        timeout=30,
    ) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                break  # walk away mid-stream

    librarian.release.set()

    # The server is still healthy and serving.
    health = httpx.get(f"{base}/health", timeout=10)
    assert health.status_code == 200
