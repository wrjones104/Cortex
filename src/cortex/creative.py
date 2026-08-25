"""Brainstorming, and taking only the parts you liked.

The prototype held one generation in a single variable and banked the whole
blob as one record. Asking for five alternatives and wanting the third meant
storing all five glued together, or nothing. Regenerating overwrote the batch
you had not finished reading.

Two modes, because brainstorming happens two ways:

- **Options** for when you know you want alternatives. The count is part of
  the request and the model returns a structured array, which is far more
  reliable than cutting prose apart afterwards.
- **Freeform** for when the good idea turns up mid-ramble. Generate as prose,
  then split it into candidates when you see something worth keeping.

Either way each idea is banked separately, with its own title and its own
embedding, which is also what makes it findable later.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .capture import build_context
from .db import transaction
from .embed import Embedder
from .llm import Librarian, LibrarianError, _extract_json, _fallback_title
from .models import Record
from .store import DuplicateRecordError, create_record, get_or_create_project, utcnow

MAX_OPTIONS = 10

IDEAS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "pitch": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["title", "pitch", "detail"],
            },
        }
    },
    "required": ["ideas"],
}

OPTIONS_PROMPT = """\
You are a brainstorming partner. Produce exactly {count} genuinely distinct
alternatives - different in kind, not the same idea reworded.

Each one needs:
- title: three to six words, concrete enough to recognise in a list
- pitch: a single line saying what it is
- detail: a paragraph developing it, with specifics rather than adjectives

Do not rank them, do not add a preamble, and do not repeat the brief back.{context}"""

FREEFORM_PROMPT = """\
You are a brainstorming partner. Think out loud, follow the interesting
threads, and be concrete - specifics are what make an idea usable later.{context}"""

SPLIT_PROMPT = """\
Split this brainstorm into the distinct ideas it contains.

Take the author's own words wherever you can; you are cutting the text apart,
not rewriting it. One entry per idea that could stand on its own. Skip
preamble, transitions and anything that is only commentary.

Each entry needs a title of three to six words, a one-line pitch, and the
relevant text as detail."""


@dataclass
class Idea:
    ordinal: int
    title: str
    pitch: str
    detail: str
    banked_record_id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Idea:
        return cls(
            ordinal=row["ordinal"],
            title=row["title"],
            pitch=row["pitch"],
            detail=row["detail"],
            banked_record_id=row["banked_record_id"],
        )

    @property
    def banked(self) -> bool:
        return self.banked_record_id is not None


@dataclass
class Generation:
    id: int
    prompt: str
    project: str | None
    model: str
    mode: str
    output: str
    created_at: str
    ideas: list[Idea]


class GenerationNotFoundError(LookupError):
    pass


_GENERATION_SELECT = """
SELECT g.*, p.name AS project
FROM generations g
LEFT JOIN projects p ON p.id = g.project_id
"""


# --- storage --------------------------------------------------------------


def get_generation(conn: sqlite3.Connection, generation_id: int) -> Generation:
    row = conn.execute(f"{_GENERATION_SELECT} WHERE g.id = ?", (generation_id,)).fetchone()
    if row is None:
        raise GenerationNotFoundError(f"No generation with id {generation_id}")

    ideas = [
        Idea.from_row(r)
        for r in conn.execute(
            "SELECT * FROM generation_ideas WHERE generation_id = ? ORDER BY ordinal",
            (generation_id,),
        )
    ]
    return Generation(
        id=row["id"],
        prompt=row["prompt"],
        project=row["project"],
        model=row["model"],
        mode=row["mode"],
        output=row["output"],
        created_at=row["created_at"],
        ideas=ideas,
    )


def list_generations(conn: sqlite3.Connection, limit: int = 20) -> list[Generation]:
    rows = conn.execute(
        f"{_GENERATION_SELECT} ORDER BY g.created_at DESC, g.id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [get_generation(conn, row["id"]) for row in rows]


def delete_generation(conn: sqlite3.Connection, generation_id: int) -> None:
    get_generation(conn, generation_id)
    with transaction(conn):
        conn.execute("DELETE FROM generations WHERE id = ?", (generation_id,))


def _store_generation(
    conn: sqlite3.Connection,
    *,
    prompt: str,
    project: str | None,
    model: str,
    mode: str,
    output: str,
    ideas: list[Idea],
) -> int:
    project_id = get_or_create_project(conn, project).id if project else None
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO generations (prompt, project_id, model, mode, output, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (prompt, project_id, model, mode, output, utcnow()),
        )
        generation_id = cursor.lastrowid
        _insert_ideas(conn, generation_id, ideas)
    return generation_id


def _insert_ideas(conn: sqlite3.Connection, generation_id: int, ideas: list[Idea]) -> None:
    for idea in ideas:
        conn.execute(
            "INSERT INTO generation_ideas (generation_id, ordinal, title, pitch, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (generation_id, idea.ordinal, idea.title, idea.pitch, idea.detail),
        )


def _parse_ideas(raw: str) -> list[Idea]:
    """Read an ideas array out of a model reply, tolerating near-misses."""
    try:
        data = _extract_json(raw)
    except LibrarianError:
        return []

    entries = data.get("ideas") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []

    ideas: list[Idea] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        detail = str(entry.get("detail") or "").strip()
        pitch = str(entry.get("pitch") or "").strip()
        body = detail or pitch
        if not body:
            continue
        title = str(entry.get("title") or "").strip() or _fallback_title(body)
        ideas.append(Idea(ordinal=len(ideas), title=title, pitch=pitch, detail=body))
    return ideas


# --- generating -----------------------------------------------------------


def generate(
    conn: sqlite3.Connection,
    embedder: Embedder,
    chatter,
    prompt: str,
    *,
    mode: str = "options",
    count: int = 4,
    project: str | None = None,
    use_context: bool = True,
) -> Iterator[tuple[str, object]]:
    """Brainstorm, yielding events as the model works.

    Events: ('status', str), ('token', str), ('done', {'generation_id': int}).

    Tokens are streamed in both modes. In options mode they are JSON rather
    than prose, so a client should show progress rather than the text - but a
    27B model takes a minute to produce five developed ideas, and showing
    nothing at all for that long is worse.
    """
    if not prompt or not prompt.strip():
        raise ValueError("Nothing to brainstorm - the prompt is empty.")
    if mode not in ("options", "freeform"):
        raise ValueError(f"Unknown mode: {mode}")

    prompt = prompt.strip()
    count = max(1, min(int(count), MAX_OPTIONS))

    context = ""
    if use_context:
        yield ("status", "Reading what you already have")
        context = build_context(conn, embedder, prompt, project)

    grounding = (
        f"\n\nStay consistent with these existing notes. Do not contradict them:\n\n{context}"
        if context
        else ""
    )

    system = (
        OPTIONS_PROMPT.format(count=count, context=grounding)
        if mode == "options"
        else FREEFORM_PROMPT.format(context=grounding)
    )

    yield ("status", "Generating" if mode == "freeform" else f"Working up {count} alternatives")

    raw = ""
    stream = chatter.stream(
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        think=False,
        **({"format": IDEAS_SCHEMA} if mode == "options" else {}),
    )
    for kind, payload in stream:
        if kind == "token":
            raw += payload
            yield ("token", payload)
        elif kind == "thinking":
            yield ("thinking", payload)

    ideas = _parse_ideas(raw) if mode == "options" else []

    if mode == "options" and not ideas:
        # The structured request came back unusable. Keep the text rather than
        # throwing away a minute of generation - it can still be split.
        yield ("status", "Could not read that as separate options; keeping it as prose")
        mode = "freeform"

    generation_id = _store_generation(
        conn,
        prompt=prompt,
        project=project,
        model=getattr(chatter, "model", "unknown"),
        mode=mode,
        output=raw if mode == "freeform" else "",
        ideas=ideas,
    )

    yield ("done", {"generation_id": generation_id, "ideas": len(ideas), "mode": mode})


def split(
    conn: sqlite3.Connection,
    librarian: Librarian,
    chatter,
    generation_id: int,
) -> list[Idea]:
    """Cut a freeform generation into bankable candidates.

    Replaces any previous split of the same generation, so re-splitting after
    an unhelpful first attempt is safe. Ideas already banked keep their link.
    """
    generation = get_generation(conn, generation_id)
    if not generation.output.strip():
        return generation.ideas

    try:
        raw, _ = chatter.complete(
            [
                {"role": "system", "content": SPLIT_PROMPT},
                {"role": "user", "content": generation.output},
            ],
            think=False,
            format=IDEAS_SCHEMA,
        )
    except LibrarianError as exc:
        raise LibrarianError(f"Could not split that generation: {exc}") from exc

    ideas = _parse_ideas(raw)
    if not ideas:
        return generation.ideas

    already = {i.title: i.banked_record_id for i in generation.ideas if i.banked}

    with transaction(conn):
        conn.execute(
            "DELETE FROM generation_ideas WHERE generation_id = ? AND banked_record_id IS NULL",
            (generation_id,),
        )
        kept = conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1) AS m FROM generation_ideas WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()["m"]

        fresh = [i for i in ideas if i.title not in already]
        for offset, idea in enumerate(fresh, start=1):
            idea.ordinal = kept + offset
        _insert_ideas(conn, generation_id, fresh)

    return get_generation(conn, generation_id).ideas


# --- banking --------------------------------------------------------------


@dataclass
class BankResult:
    banked: list[Record]
    skipped: list[tuple[int, str]]
    """(ordinal, reason) for ideas that were not filed."""


def bank(
    conn: sqlite3.Connection,
    embedder: Embedder,
    generation_id: int,
    ordinals: list[int],
    *,
    librarian: Librarian | None = None,
    project: str | None = None,
    verbatim: bool = True,
    chunk_options: dict | None = None,
) -> BankResult:
    """File the chosen ideas, one record each.

    verbatim defaults to True on purpose. The prototype always sent banked
    text back through the Librarian, which meant a 27B model got an
    unrequested rewrite of prose you had already decided you liked.
    """
    generation = get_generation(conn, generation_id)
    by_ordinal = {idea.ordinal: idea for idea in generation.ideas}
    target = project or generation.project or "Inbox"

    banked: list[Record] = []
    skipped: list[tuple[int, str]] = []

    for ordinal in ordinals:
        idea = by_ordinal.get(ordinal)
        if idea is None:
            skipped.append((ordinal, "No such idea in this generation."))
            continue
        if idea.banked:
            skipped.append((ordinal, "Already filed."))
            continue

        title = idea.title
        body = idea.detail
        category = ""
        subcategory = ""

        if not verbatim and librarian is not None:
            try:
                note = librarian.structure(body, project=target, context="")
                title = note.title or title
                body = note.content or body
                category = note.category
                subcategory = note.subcategory
            except LibrarianError as exc:
                skipped.append((ordinal, f"Could not structure it - {exc}"))
                continue

        try:
            record = create_record(
                conn,
                embedder,
                project=target,
                title=title,
                body=body,
                category=category,
                subcategory=subcategory,
                source="creative",
                chunk_options=chunk_options,
            )
        except DuplicateRecordError as exc:
            skipped.append((ordinal, str(exc)))
            continue

        with transaction(conn):
            conn.execute(
                "UPDATE generation_ideas SET banked_record_id = ? "
                "WHERE generation_id = ? AND ordinal = ?",
                (record.id, generation_id, ordinal),
            )
        banked.append(record)

    return BankResult(banked=banked, skipped=skipped)
