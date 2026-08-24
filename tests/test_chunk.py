"""Chunking is the thing standing between a long note and data loss.

The prototype embedded whole records, so anything over roughly 13,000
characters was rejected by the embedder and never saved. The invariant these
tests defend is simple: no chunk may ever exceed max_tokens, whatever the
input looks like.
"""

from __future__ import annotations

import pytest

from cortex.chunk import CHARS_PER_TOKEN, chunk_text, estimate_tokens


def test_empty_input_produces_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \t ") == []


def test_short_note_is_a_single_chunk():
    chunks = chunk_text("A short thought about lighthouses.")
    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert "lighthouses" in chunks[0].text


def test_ordinals_are_sequential():
    body = "\n\n".join(f"Paragraph number {i}. " * 40 for i in range(12))
    chunks = chunk_text(body, target_tokens=100, max_tokens=120, overlap_tokens=10)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_no_chunk_exceeds_max_tokens():
    body = "\n\n".join(f"Paragraph {i}: " + ("filler words here " * 60) for i in range(20))
    chunks = chunk_text(body, target_tokens=200, max_tokens=256, overlap_tokens=30)
    assert chunks
    assert all(c.tokens <= 256 for c in chunks)


def test_single_giant_paragraph_is_split():
    """One unbroken block must still be cut down, not passed through whole."""
    body = "This sentence has punctuation. " * 500
    chunks = chunk_text(body, target_tokens=200, max_tokens=256, overlap_tokens=20)
    assert len(chunks) > 1
    assert all(c.tokens <= 256 for c in chunks)


def test_text_with_no_punctuation_at_all_is_still_split():
    """The worst case: no paragraph breaks, no sentences, no short words."""
    body = "word " * 5000
    chunks = chunk_text(body, target_tokens=200, max_tokens=256, overlap_tokens=20)
    assert len(chunks) > 1
    assert all(c.tokens <= 256 for c in chunks)


def test_single_token_longer_than_the_limit_is_hard_split():
    """A base64 blob or a pasted hash has no boundaries to split on."""
    body = "x" * 20_000
    chunks = chunk_text(body, target_tokens=100, max_tokens=128, overlap_tokens=10)
    assert len(chunks) > 1
    assert all(c.tokens <= 128 for c in chunks)
    assert "".join(c.text for c in chunks).count("x") >= 20_000


def test_the_note_size_that_broke_the_prototype_now_chunks_cleanly():
    """~20,000 characters — well past the old ~13,000 character hard failure."""
    body = "\n\n".join(
        f"Section {i}. " + ("The keeper walked the cliff path at dusk. " * 12)
        for i in range(40)
    )
    assert len(body) > 20_000

    chunks = chunk_text(body, target_tokens=400, max_tokens=512, overlap_tokens=60)
    assert len(chunks) > 1
    assert all(c.tokens <= 512 for c in chunks)
    # 512 estimated tokens is ~2048 characters, which is at most ~1024 real
    # tokens — comfortably inside every embedding model's 2048-token window.
    assert all(len(c.text) <= 512 * CHARS_PER_TOKEN for c in chunks)


def test_chunks_overlap_so_context_is_not_severed_at_the_seam():
    paragraphs = [f"Distinct paragraph {i} with unique marker word{i}." for i in range(30)]
    chunks = chunk_text("\n\n".join(paragraphs), target_tokens=40, max_tokens=60, overlap_tokens=20)
    assert len(chunks) > 2

    overlaps = 0
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        tail = set(earlier.text.split())
        head = set(later.text.split())
        if tail & head:
            overlaps += 1
    assert overlaps > 0


def test_all_content_is_preserved_across_chunks():
    paragraphs = [f"Unique marker {i} appears exactly once." for i in range(25)]
    chunks = chunk_text("\n\n".join(paragraphs), target_tokens=30, max_tokens=50, overlap_tokens=10)
    combined = " ".join(c.text for c in chunks)
    for i in range(25):
        assert f"Unique marker {i} " in combined + " "


def test_max_below_target_is_rejected():
    with pytest.raises(ValueError):
        chunk_text("some text", target_tokens=500, max_tokens=100)


def test_estimate_tokens_never_zero_for_real_text():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("hello world") >= 1
