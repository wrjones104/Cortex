"""Librarian output handling.

Local models return near-miss JSON often enough that the repair path is a
normal code path, not an edge case. These tests pin down what we recover from
and what we refuse.
"""

from __future__ import annotations

import pytest

from cortex.llm import LibrarianError, _extract_json, _fallback_title


def test_clean_json_parses():
    assert _extract_json('{"title": "A Note", "category": "Test"}') == {
        "title": "A Note",
        "category": "Test",
    }


def test_json_wrapped_in_a_fenced_code_block_is_recovered():
    raw = 'Here you go:\n```json\n{"title": "A Note"}\n```\nHope that helps!'
    assert _extract_json(raw) == {"title": "A Note"}


def test_an_unlabelled_fence_is_recovered():
    assert _extract_json('```\n{"title": "A Note"}\n```') == {"title": "A Note"}


def test_json_with_prose_around_it_is_recovered():
    raw = 'Sure! {"title": "A Note", "category": "X"} — let me know if you want changes.'
    assert _extract_json(raw) == {"title": "A Note", "category": "X"}


def test_nested_objects_survive_the_greedy_match():
    raw = 'text {"title": "A", "meta": {"nested": true}} more text'
    assert _extract_json(raw) == {"title": "A", "meta": {"nested": True}}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "I'm sorry, I can't do that.",
        "{not json at all}",
        '{"unterminated": ',
    ],
)
def test_unrecoverable_output_raises_rather_than_guessing(raw):
    with pytest.raises(LibrarianError):
        _extract_json(raw)


# --- fallback titles ------------------------------------------------------


def test_fallback_title_uses_the_first_non_empty_line():
    assert _fallback_title("\n\n  The Keeper's Lantern  \nmore text") == "The Keeper's Lantern"


def test_fallback_title_strips_markdown_heading_marks():
    assert _fallback_title("# A Heading\n\nbody") == "A Heading"


def test_fallback_title_is_truncated_not_unbounded():
    title = _fallback_title("x" * 500)
    assert len(title) <= 80
    assert title.endswith("...")


def test_fallback_title_of_empty_text():
    assert _fallback_title("") == "Untitled"
    assert _fallback_title("   \n  ") == "Untitled"


# --- the declared context window ------------------------------------------
#
# /api/show reports the architecture's maximum, not the window Ollama actually
# loaded. Budgeting against the former while the server enforces the latter is
# a silent truncation, so context_length is both clamped and declared.


class _FakeClient:
    """Records what was sent to Ollama."""

    def __init__(self, reply: str = "ok") -> None:
        self.calls: list[dict] = []
        self.reply = reply

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": self.reply}, "prompt_eval_count": 7}


def _chat_with(monkeypatch, probed: int, max_context: int):
    from cortex.llm import OllamaChat

    chatter = OllamaChat("http://x", "fake:1b", max_context=max_context)
    monkeypatch.setattr(type(chatter), "_probe_context", lambda self: probed)
    client = _FakeClient()
    monkeypatch.setattr(type(chatter), "_client", property(lambda self: client))
    return chatter, client


def test_context_is_capped_at_what_we_are_willing_to_load(monkeypatch):
    chatter, _ = _chat_with(monkeypatch, probed=262_144, max_context=32_768)
    assert chatter.context_length == 32_768


def test_a_model_smaller_than_the_cap_keeps_its_own_window(monkeypatch):
    chatter, _ = _chat_with(monkeypatch, probed=8_192, max_context=32_768)
    assert chatter.context_length == 8_192


def test_the_window_we_budget_against_is_the_one_we_send(monkeypatch):
    chatter, client = _chat_with(monkeypatch, probed=262_144, max_context=16_384)
    chatter.complete([{"role": "user", "content": "hello"}])

    assert client.calls[0]["options"]["num_ctx"] == chatter.context_length == 16_384


def test_streaming_declares_the_same_window(monkeypatch):
    chatter, client = _chat_with(monkeypatch, probed=32_768, max_context=8_192)
    client.reply = ""
    monkeypatch.setattr(
        type(client),
        "chat",
        lambda self, **kw: (self.calls.append(kw), iter([{"message": {"content": "hi"}}]))[1],
    )
    list(chatter.stream([{"role": "user", "content": "hello"}]))

    assert client.calls[0]["options"]["num_ctx"] == 8_192


def test_a_nonsense_cap_cannot_produce_an_unusable_window(monkeypatch):
    chatter, _ = _chat_with(monkeypatch, probed=32_768, max_context=0)
    assert chatter.context_length == 512
