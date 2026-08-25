"""One shared Ollama client per host.

Model routing is a runtime setting, so a Librarian is built per request.
Giving each its own httpx client would cost ~170 ms of SSL setup every time;
they are interchangeable, so they are cached by host instead.
"""

from __future__ import annotations

from functools import lru_cache

import ollama


@lru_cache(maxsize=8)
def client_for(host: str) -> ollama.Client:
    return ollama.Client(host=host)
