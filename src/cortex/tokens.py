"""Counting tokens well enough to fill a context window.

Chunking only needs to stay *under* a limit, so a rough estimate with plenty
of headroom is fine there. Budgeting a conversation is the opposite problem:
guess low and you waste context you paid for, guess high and the model
silently drops the oldest turns — the exact amnesia the summary exists to
prevent.

Rather than ship a tokenizer for a number, this calibrates. Every Ollama chat
response reports `prompt_eval_count`, the real token count of the prompt that
was just sent. Comparing that to what we predicted gives a characters-per-
token ratio per model, which is stored in the vault and converges after a
handful of turns.
"""

from __future__ import annotations

import sqlite3

from .db import get_meta, set_meta

# Where a model starts before it has ever answered. English prose sits near
# 4.0; code and heavy punctuation run lower.
DEFAULT_CHARS_PER_TOKEN = 4.0

# Ratios outside this are a sign something else went wrong (a truncated
# response, a tool-call prompt we did not build) and should not move the
# calibration.
MIN_RATIO = 1.5
MAX_RATIO = 8.0

# How fast the stored ratio follows new observations. Low enough that one odd
# turn cannot swing the budget, high enough to settle within a few messages.
SMOOTHING = 0.3

# Conversations are budgeted with this much of the window left for the answer.
OUTPUT_RESERVE = 0.30

# Used when a model will not report its context length.
FALLBACK_CONTEXT = 8192


def _key(model: str) -> str:
    return f"chars_per_token:{model}"


def chars_per_token(conn: sqlite3.Connection, model: str) -> float:
    raw = get_meta(conn, _key(model))
    if raw is None:
        return DEFAULT_CHARS_PER_TOKEN
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_CHARS_PER_TOKEN
    return value if MIN_RATIO <= value <= MAX_RATIO else DEFAULT_CHARS_PER_TOKEN


def estimate(text: str, ratio: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Predicted token count for a piece of text."""
    if not text:
        return 0
    return max(1, round(len(text) / ratio))


def estimate_messages(messages: list[dict], ratio: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Predicted tokens for a chat payload.

    The per-message constant covers the role tags and separators every chat
    template adds around the content; without it a thread of many short turns
    is badly underestimated.
    """
    return sum(estimate(m.get("content", ""), ratio) + 4 for m in messages)


def calibrate(
    conn: sqlite3.Connection, model: str, sent_chars: int, actual_tokens: int
) -> float | None:
    """Fold one real measurement into the stored ratio. Returns the new value.

    `actual_tokens` is prompt_eval_count from the response that was just
    produced by sending `sent_chars` characters.
    """
    if actual_tokens <= 0 or sent_chars <= 0:
        return None

    observed = sent_chars / actual_tokens
    if not (MIN_RATIO <= observed <= MAX_RATIO):
        return None

    current = chars_per_token(conn, model)
    updated = round(current * (1 - SMOOTHING) + observed * SMOOTHING, 3)
    set_meta(conn, _key(model), str(updated))
    return updated


def input_budget(context_length: int) -> int:
    """How many tokens of prompt to allow, leaving room for the answer."""
    usable = max(context_length, 512)
    return max(256, int(usable * (1 - OUTPUT_RESERVE)))
