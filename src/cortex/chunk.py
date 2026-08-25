"""Splitting note bodies into embeddable chunks.

The prototype embedded whole records in one call, which meant any note over
roughly 13,000 characters was rejected outright by the embedding model and
lost. Chunking removes that ceiling and improves retrieval at the same time:
a long note stops being represented by a single averaged-out vector.

Token counts here are estimates, not a real tokenizer. That is deliberate —
pulling in a tokenizer to split text is a heavy dependency for a number we
only use to stay comfortably under a limit. The bounds are chosen so that even
if the estimate is off by a factor of two, a chunk still fits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Rough average across English prose, Markdown and code.
CHARS_PER_TOKEN = 4

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    tokens: int


def estimate_tokens(text: str) -> int:
    """Approximate token count. Never returns 0 for non-empty text."""
    if not text:
        return 0
    return max(1, -(-len(text) // CHARS_PER_TOKEN))


def _split_oversized(block: str, max_tokens: int) -> list[str]:
    """Break a block that is too large on its own into pieces that fit.

    Tries sentences first, then words, then raw characters — so even a wall of
    text with no punctuation at all still produces valid chunks.
    """
    if estimate_tokens(block) <= max_tokens:
        return [block]

    limit = max_tokens * CHARS_PER_TOKEN
    pieces: list[str] = []

    for sentence in _SENTENCE_BREAK.split(block):
        if not sentence.strip():
            continue
        if estimate_tokens(sentence) <= max_tokens:
            pieces.append(sentence)
            continue

        current = ""
        for word in sentence.split():
            if len(word) > limit:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(word[i : i + limit] for i in range(0, len(word), limit))
                continue
            candidate = f"{current} {word}" if current else word
            if len(candidate) > limit:
                pieces.append(current)
                current = word
            else:
                current = candidate
        if current:
            pieces.append(current)

    return pieces or [block[:limit]]


def chunk_text(
    text: str,
    *,
    target_tokens: int = 400,
    max_tokens: int = 512,
    overlap_tokens: int = 60,
) -> list[Chunk]:
    """Split text into overlapping chunks that respect paragraph boundaries.

    Guarantees every returned chunk is at or under max_tokens by estimate, so
    the embedder can never reject one.
    """
    if not text or not text.strip():
        return []
    if max_tokens < target_tokens:
        raise ValueError("max_tokens must be >= target_tokens")

    blocks: list[str] = []
    for raw in _PARAGRAPH_BREAK.split(text.strip()):
        block = raw.strip()
        if block:
            blocks.extend(_split_oversized(block, max_tokens))

    if not blocks:
        return []

    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0

    def flush() -> list[str]:
        """Emit the pending block group and return the tail to carry forward."""
        nonlocal current, current_tokens
        if not current:
            return []
        body = "\n\n".join(current)
        chunks.append(Chunk(ordinal=len(chunks), text=body, tokens=estimate_tokens(body)))

        carry: list[str] = []
        carried = 0
        for block in reversed(current):
            block_tokens = estimate_tokens(block)
            if carried + block_tokens > overlap_tokens:
                break
            carry.insert(0, block)
            carried += block_tokens
        # Never carry the whole chunk forward, or packing cannot make progress.
        if len(carry) == len(current):
            carry = carry[1:]
        return carry

    for block in blocks:
        block_tokens = estimate_tokens(block)

        if current and current_tokens + block_tokens > target_tokens:
            carry = flush()
            current = list(carry)
            current_tokens = sum(estimate_tokens(b) for b in current)

            # An overlap tail plus this block must still fit inside max_tokens.
            while current and current_tokens + block_tokens > max_tokens:
                current.pop(0)
                current_tokens = sum(estimate_tokens(b) for b in current)

        current.append(block)
        current_tokens += block_tokens

    if current:
        body = "\n\n".join(current)
        chunks.append(Chunk(ordinal=len(chunks), text=body, tokens=estimate_tokens(body)))

    return chunks
