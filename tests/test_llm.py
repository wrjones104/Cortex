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
