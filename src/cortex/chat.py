"""Conversations with the vault.

The prototype sent the model exactly two messages every time — a system prompt
and the newest question — so follow-ups had nothing to refer back to, and the
retrieval step embedded "tell me more about that" and searched the vault for
the word "that". Nothing was stored, so a refresh lost the lot.

Four pieces make a long thread work on a local model, in the order they pay off:

1. Query condensation. Rewrite the follow-up into a standalone question using
   the last few turns, and retrieve with *that*. The single biggest quality
   win here, and the cheapest.
2. A budgeted window. Ask the model how big its context actually is, keep back
   room for the answer, and fill the rest newest-first.
3. A rolling summary. When the window overflows, fold the oldest turns into
   running prose so they still count for something.
4. A facts ledger. Names, decisions and corrections extracted from turns as
   they are summarised away, and never summarised themselves. This is what
   stops turn 30 forgetting the name you gave it on turn 4.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field

from .db import transaction
from .embed import Embedder
from .llm import Chatter, LibrarianError
from .models import SearchHit
from .retrieve import search
from .store import get_or_create_project, slugify, utcnow
from .tokens import calibrate, chars_per_token, estimate, estimate_messages, input_budget

# How the input budget is divided. Retrieved notes are capped so a long thread
# cannot be squeezed out by five verbose records, and vice versa.
RETRIEVAL_SHARE = 0.35
SUMMARY_SHARE = 0.15

# Turns kept verbatim regardless of budget, so the immediate exchange is never
# summarised out from under a follow-up.
MIN_VERBATIM_TURNS = 4

PERSONA = """\
You are the librarian of this person's private knowledge vault.

Answer only from the notes below and the conversation so far. These are the
person's own notes, so a confident wrong answer is worse than no answer.

If the notes do not cover something, say exactly that - "your notes do not say"
- and stop. Never fill a gap with a plausible detail. Do not infer events that
are not written down.

Quote their own wording where it helps. Be concise; this is a conversation,
not a report."""

CONDENSE_PROMPT = """\
Rewrite the user's latest message as a standalone search query.

Resolve every pronoun and reference using the conversation. Keep the proper
nouns - they matter more than anything else for finding the right notes.
Output only the rewritten query, with no preamble and no quotes.

If the message is already standalone, output it unchanged."""

SUMMARY_PROMPT = """\
You are maintaining a running summary of a conversation.

Rewrite the summary so it also covers the new exchanges. Keep it under 200
words. Preserve decisions, open questions and anything the user corrected.
Drop pleasantries. Write plain prose, no headings, no bullets."""

FACTS_PROMPT = """\
Extract durable facts from this part of a conversation.

A durable fact is one that should still be true in an hour: a name, a
decision, a constraint, a preference, or a correction the user made. Skip
anything transient, and skip anything that is only a question.

Output a JSON array of short strings. Output [] if there is nothing worth
keeping."""


@dataclass
class Thread:
    id: int
    title: str
    project: str | None
    model: str | None
    summary: str
    summarised_upto: int
    created_at: str
    updated_at: str
    message_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Thread:
        keys = row.keys()
        return cls(
            id=row["id"],
            title=row["title"],
            project=row["project"] if "project" in keys else None,
            model=row["model"],
            summary=row["summary"],
            summarised_upto=row["summarised_upto"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=row["message_count"] if "message_count" in keys else 0,
        )


@dataclass
class Message:
    id: int
    thread_id: int
    role: str
    content: str
    sources: list[str]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Message:
        raw = row["sources_json"]
        return cls(
            id=row["id"],
            thread_id=row["thread_id"],
            role=row["role"],
            content=row["content"],
            sources=json.loads(raw) if raw else [],
            created_at=row["created_at"],
        )


@dataclass
class Window:
    """What will actually be sent, and why."""

    messages: list[dict]
    hits: list[SearchHit] = field(default_factory=list)
    standalone_query: str = ""
    compacted: bool = False
    prompt_tokens_estimated: int = 0
    overflowed: bool = False
    """True when history had to be dropped to fit - the signal to compact."""


class ThreadNotFoundError(LookupError):
    pass


_THREAD_SELECT = """
SELECT t.*, p.name AS project,
       (SELECT COUNT(*) FROM messages m
         WHERE m.thread_id = t.id AND m.role != 'marker') AS message_count,
       (SELECT MAX(m.id) FROM messages m WHERE m.thread_id = t.id) AS last_message_id
FROM threads t
LEFT JOIN projects p ON p.id = t.project_id
"""


# --- threads --------------------------------------------------------------


def create_thread(
    conn: sqlite3.Connection,
    *,
    title: str = "New conversation",
    project: str | None = None,
    model: str | None = None,
) -> Thread:
    project_id = get_or_create_project(conn, project).id if project else None
    now = utcnow()
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO threads (title, project_id, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (title.strip() or "New conversation", project_id, model, now, now),
        )
    return get_thread(conn, cursor.lastrowid)


def get_thread(conn: sqlite3.Connection, thread_id: int) -> Thread:
    row = conn.execute(f"{_THREAD_SELECT} WHERE t.id = ?", (thread_id,)).fetchone()
    if row is None:
        raise ThreadNotFoundError(f"No conversation with id {thread_id}")
    return Thread.from_row(row)


def list_threads(conn: sqlite3.Connection, limit: int = 50) -> list[Thread]:
    """Most recently active first.

    Tie-broken on the newest message id rather than on the thread id, because
    two threads touched within the same clock tick would otherwise fall back to
    creation order - and replying to an old conversation would leave it sitting
    below a newer empty one. Message ids are monotonic, so they settle it
    without depending on timestamp resolution at all.
    """
    rows = conn.execute(
        f"{_THREAD_SELECT} ORDER BY t.updated_at DESC, last_message_id DESC, t.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [Thread.from_row(r) for r in rows]


def delete_thread(conn: sqlite3.Connection, thread_id: int) -> None:
    get_thread(conn, thread_id)
    with transaction(conn):
        conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))


def rename_thread(conn: sqlite3.Connection, thread_id: int, title: str) -> Thread:
    get_thread(conn, thread_id)
    with transaction(conn):
        conn.execute(
            "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
            (title.strip() or "New conversation", utcnow(), thread_id),
        )
    return get_thread(conn, thread_id)


def set_thread_scope(conn: sqlite3.Connection, thread_id: int, project: str | None) -> Thread:
    """Change which project the thread searches, and say so in the transcript.

    The prototype changed retrieval scope silently, so scrolling back a week
    later gave no way to tell which answers had been scoped to what.
    """
    thread = get_thread(conn, thread_id)
    current = thread.project

    if (slugify(current) if current else None) == (slugify(project) if project else None):
        return thread

    project_id = get_or_create_project(conn, project).id if project else None
    label = project or "all projects"

    with transaction(conn):
        conn.execute(
            "UPDATE threads SET project_id = ?, updated_at = ? WHERE id = ?",
            (project_id, utcnow(), thread_id),
        )
        conn.execute(
            "INSERT INTO messages (thread_id, role, content, tokens, created_at) "
            "VALUES (?, 'marker', ?, 0, ?)",
            (thread_id, f"Now searching {label}.", utcnow()),
        )
    return get_thread(conn, thread_id)


# --- messages -------------------------------------------------------------


def add_message(
    conn: sqlite3.Connection,
    thread_id: int,
    role: str,
    content: str,
    *,
    sources: list[str] | None = None,
    tokens: int = 0,
) -> Message:
    now = utcnow()
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO messages (thread_id, role, content, sources_json, tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, role, content, json.dumps(sources) if sources else None, tokens, now),
        )
        conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, thread_id))
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return Message.from_row(row)


def list_messages(conn: sqlite3.Connection, thread_id: int) -> list[Message]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE thread_id = ? ORDER BY id", (thread_id,)
    ).fetchall()
    return [Message.from_row(r) for r in rows]


def list_facts(conn: sqlite3.Connection, thread_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT fact FROM thread_facts WHERE thread_id = ? ORDER BY id", (thread_id,)
    ).fetchall()
    return [r["fact"] for r in rows]


def add_facts(conn: sqlite3.Connection, thread_id: int, facts: list[str]) -> int:
    """Append to the ledger. Duplicates are ignored, nothing is ever removed."""
    added = 0
    now = utcnow()
    with transaction(conn):
        for fact in facts:
            clean = fact.strip()
            if not clean:
                continue
            cursor = conn.execute(
                "INSERT OR IGNORE INTO thread_facts (thread_id, fact, created_at) "
                "VALUES (?, ?, ?)",
                (thread_id, clean, now),
            )
            added += cursor.rowcount
    return added


# --- the four pieces ------------------------------------------------------


def condense(chatter: Chatter, history: list[Message], question: str) -> str:
    """Turn a follow-up into something worth embedding.

    "Tell me more about that" retrieves nothing useful; "tell me more about
    Wexler's daughter" retrieves the right note. Falls back to the raw
    question if the model is unavailable or answers oddly.
    """
    recent = [m for m in history if m.role in ("user", "assistant")][-4:]
    if not recent:
        return question

    transcript = "\n".join(f"{m.role}: {m.content}" for m in recent)
    try:
        rewritten, _ = chatter.complete(
            [
                {"role": "system", "content": CONDENSE_PROMPT},
                {"role": "user", "content": f"Conversation:\n{transcript}\n\nLatest: {question}"},
            ],
            think=False,
        )
    except LibrarianError:
        return question

    rewritten = rewritten.strip().strip('"').strip()
    # A rewrite that lost the question or ran away with itself is worse than
    # the original, so keep the original in those cases.
    if not rewritten or len(rewritten) > max(400, len(question) * 6):
        return question
    return rewritten


def _extract_facts(chatter: Chatter, transcript: str) -> list[str]:
    try:
        raw, _ = chatter.complete(
            [
                {"role": "system", "content": FACTS_PROMPT},
                {"role": "user", "content": transcript},
            ],
            think=False,
        )
    except LibrarianError:
        return []

    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()][:12]


def compact(
    conn: sqlite3.Connection,
    chatter: Chatter,
    thread_id: int,
    *,
    keep_turns: int = MIN_VERBATIM_TURNS,
) -> bool:
    """Fold everything older than the last `keep_turns` into summary + facts.

    Facts are extracted from exactly the turns being summarised, which is the
    moment their detail is about to be lost. Returns whether anything moved.
    """
    thread = get_thread(conn, thread_id)
    history = [
        m
        for m in list_messages(conn, thread_id)
        if m.role in ("user", "assistant") and m.id > thread.summarised_upto
    ]

    older = history[:-keep_turns] if keep_turns else history
    if not older:
        return False

    transcript = "\n\n".join(f"{m.role}: {m.content}" for m in older)

    facts = _extract_facts(chatter, transcript)
    if facts:
        add_facts(conn, thread_id, facts)

    existing = f"Summary so far:\n{thread.summary}\n\n" if thread.summary else ""
    try:
        summary, _ = chatter.complete(
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": f"{existing}New exchanges:\n{transcript}"},
            ],
            think=False,
        )
    except LibrarianError:
        # Facts were still captured, so the turns are not a total loss - but
        # do not advance the watermark, or they vanish unsummarised.
        return bool(facts)

    if not summary.strip():
        return bool(facts)

    with transaction(conn):
        conn.execute(
            "UPDATE threads SET summary = ?, summarised_upto = ?, updated_at = ? WHERE id = ?",
            (summary.strip(), older[-1].id, utcnow(), thread_id),
        )
    return True


def build_window(
    conn: sqlite3.Connection,
    thread: Thread,
    question: str,
    hits: list[SearchHit],
    *,
    context_length: int,
    ratio: float,
) -> Window:
    """Assemble the prompt, newest-first, inside the model's real window."""
    budget = input_budget(context_length)

    facts = list_facts(conn, thread.id)
    system = PERSONA

    if facts:
        system += "\n\nEstablished in this conversation:\n" + "\n".join(f"- {f}" for f in facts)

    if thread.summary:
        allowance = int(budget * SUMMARY_SHARE)
        text = thread.summary
        if estimate(text, ratio) > allowance:
            text = text[: int(allowance * ratio)]
        system += f"\n\nEarlier in this conversation:\n{text}"

    # Retrieved notes, capped so a verbose record cannot crowd out the thread.
    retrieval_allowance = int(budget * RETRIEVAL_SHARE)
    used = 0
    blocks: list[str] = []
    kept: list[SearchHit] = []
    for hit in hits:
        block = f"[{hit.record.project_name} - {hit.record.title}]\n{hit.snippet}"
        cost = estimate(block, ratio)
        if used + cost > retrieval_allowance:
            break
        blocks.append(block)
        kept.append(hit)
        used += cost

    if blocks:
        system += "\n\nNotes from the vault:\n\n" + "\n\n".join(blocks)

    messages: list[dict] = [{"role": "system", "content": system}]
    tail = [{"role": "user", "content": question}]
    spent = estimate_messages(messages + tail, ratio)

    # Fill what remains with recent turns, newest first, so the most relevant
    # history survives a tight budget.
    history = [
        m
        for m in list_messages(conn, thread.id)
        if m.role in ("user", "assistant") and m.id > thread.summarised_upto
    ]
    chosen: list[dict] = []
    overflowed = False
    for message in reversed(history):
        entry = {"role": message.role, "content": message.content}
        cost = estimate(message.content, ratio) + 4
        if spent + cost > budget and len(chosen) >= MIN_VERBATIM_TURNS:
            # Everything older than this is being left out.
            overflowed = True
            break
        chosen.insert(0, entry)
        spent += cost

    messages.extend(chosen)
    messages.extend(tail)

    return Window(
        messages=messages,
        hits=kept,
        prompt_tokens_estimated=estimate_messages(messages, ratio),
        # Either turns were dropped, or the floor of verbatim turns pushed us
        # over anyway. Both mean the thread no longer fits as it is.
        overflowed=overflowed or spent > budget,
    )


def answer(
    conn: sqlite3.Connection,
    embedder: Embedder,
    chatter: Chatter,
    thread_id: int,
    question: str,
    *,
    utility: Chatter | None = None,
    max_distance: float = 0.65,
    rrf_k: int = 60,
) -> Iterator[tuple[str, object]]:
    """Answer a question in a thread, yielding events as they happen.

    Events: ('status', str), ('thinking', str), ('token', str),
    ('sources', list[str]), ('done', dict).
    """
    if not question or not question.strip():
        raise ValueError("Nothing to ask - the message is empty.")

    thread = get_thread(conn, thread_id)
    utility = utility or chatter
    ratio = chars_per_token(conn, chatter.model)
    question = question.strip()

    history = list_messages(conn, thread_id)

    # 1. Condense, so retrieval sees a standalone question.
    standalone = question
    if any(m.role in ("user", "assistant") for m in history):
        yield ("status", "Working out what you mean")
        standalone = condense(utility, history, question)

    # 2. Retrieve, skipping notes already in this conversation.
    yield ("status", "Searching your notes")
    hits: list[SearchHit] = []
    try:
        # Retrieved fresh every turn, including notes quoted before. An earlier
        # answer is in the window but the note bodies behind it are not - the
        # system prompt is rebuilt each turn - so skipping them starves a
        # follow-up of the exact note it is asking about, and the model fills
        # the gap by inventing one. The retrieval budget is what protects
        # against bloat, not exclusion.
        hits = search(
            conn,
            embedder,
            standalone,
            project=thread.project,
            limit=5,
            rrf_k=rrf_k,
            max_distance=max_distance,
        )
    except Exception:  # noqa: BLE001 - answering without notes beats not answering
        hits = []

    # 3. Compact if the thread no longer fits, before building the window.
    compacted = False
    provisional = build_window(
        conn, thread, question, hits, context_length=chatter.context_length, ratio=ratio
    )
    # Check whether history had to be dropped, not whether the assembled
    # window exceeds the budget - build_window already truncates to fit, so
    # its output can never be over budget and would never trigger this.
    if provisional.overflowed:
        yield ("status", "Summarising earlier turns")
        compacted = compact(conn, utility, thread_id)
        thread = get_thread(conn, thread_id)

    window = build_window(
        conn, thread, question, hits, context_length=chatter.context_length, ratio=ratio
    )
    window.standalone_query = standalone
    window.compacted = compacted

    sources = [h.record.title for h in window.hits]
    add_message(conn, thread_id, "user", question)

    yield ("sources", sources)
    yield ("status", "Answering")

    reply = ""
    prompt_tokens = 0
    for kind, payload in chatter.stream(window.messages, think=False):
        if kind == "token":
            reply += payload
            yield ("token", payload)
        elif kind == "thinking":
            yield ("thinking", payload)
        elif kind == "done":
            prompt_tokens = int(payload or 0)

    add_message(conn, thread_id, "assistant", reply, sources=sources)

    # 4. Calibrate the estimator against what the model actually counted.
    if prompt_tokens:
        sent_chars = sum(len(m["content"]) for m in window.messages)
        calibrate(conn, chatter.model, sent_chars, prompt_tokens)

    if thread.title == "New conversation":
        _autotitle(conn, utility, thread_id, question)

    yield (
        "done",
        {
            "sources": sources,
            "standalone_query": standalone,
            "compacted": compacted,
            "prompt_tokens": prompt_tokens,
            "estimated_tokens": window.prompt_tokens_estimated,
        },
    )


def _autotitle(conn: sqlite3.Connection, chatter: Chatter, thread_id: int, question: str) -> None:
    """Name a thread from its opening question, so the list is scannable."""
    try:
        title, _ = chatter.complete(
            [
                {
                    "role": "system",
                    "content": "Give this conversation a title of at most six words. "
                    "Output only the title, with no quotes and no punctuation at the end.",
                },
                {"role": "user", "content": question},
            ],
            think=False,
        )
    except LibrarianError:
        title = ""

    clean = title.strip().strip('"').splitlines()[0] if title.strip() else ""
    if not clean:
        clean = question[:60]
    rename_thread(conn, thread_id, clean[:80])
