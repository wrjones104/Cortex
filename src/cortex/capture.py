"""The capture pipeline: raw text in, filed record out.

Verbatim capture is a first-class path, not an afterthought. The prototype
always sent text through the Librarian, which meant banking a creative
generation handed a 27B model an unrequested rewrite of prose you had already
decided you liked.
"""

from __future__ import annotations

import sqlite3

from .embed import Embedder
from .llm import Librarian, LibrarianError, _fallback_title
from .models import CaptureResult
from .retrieve import search
from .store import create_record


def build_context(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    project: str | None,
    *,
    limit: int = 4,
    budget_chars: int = 4000,
) -> str:
    """Pull nearby notes so the Librarian stays consistent with the project.

    Budgeted by characters. The prototype injected five whole records
    unmeasured, which on long notes meant a 12,000-character system prompt.
    """
    try:
        hits = search(conn, embedder, query, project=project, limit=limit)
    except Exception:  # noqa: BLE001 - context is a nicety, never a blocker
        return ""

    parts: list[str] = []
    used = 0
    for hit in hits:
        entry = f"[{hit.record.project_name} - {hit.record.title}]\n{hit.snippet}"
        if used + len(entry) > budget_chars:
            break
        parts.append(entry)
        used += len(entry)

    return "\n\n".join(parts)


def capture(
    conn: sqlite3.Connection,
    embedder: Embedder,
    raw_text: str,
    *,
    librarian: Librarian | None = None,
    project: str | None = None,
    title: str | None = None,
    category: str = "",
    subcategory: str = "",
    verbatim: bool = False,
    use_context: bool = True,
    source: str = "capture",
    idempotency_key: str | None = None,
    allow_duplicate: bool = False,
    chunk_options: dict | None = None,
) -> CaptureResult:
    """File a note. Returns the stored record plus anything worth telling the user.

    Falls back to verbatim storage if the Librarian is unavailable or fails —
    losing the structuring is annoying, losing the note is not acceptable.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Nothing to capture - the note is empty.")

    warnings: list[str] = []
    body = raw_text.strip()
    final_project = project
    final_title = title
    final_category = category
    final_subcategory = subcategory

    if not verbatim and librarian is not None:
        context = build_context(conn, embedder, body, project) if use_context else ""
        try:
            note = librarian.structure(body, project=project, context=context)
            body = note.content
            final_project = project or note.project
            final_title = title or note.title
            final_category = category or note.category
            final_subcategory = subcategory or note.subcategory
        except LibrarianError as exc:
            warnings.append(f"Filed without structuring - {exc}")

    if not final_title:
        final_title = _fallback_title(body)
    if not final_project:
        final_project = "Inbox"

    # DuplicateRecordError propagates: the caller decides whether an identical
    # note is a mistake to report or a re-send to ignore.
    record = create_record(
        conn,
        embedder,
        project=final_project,
        title=final_title,
        body=body,
        category=final_category,
        subcategory=final_subcategory,
        source=source,
        idempotency_key=idempotency_key,
        allow_duplicate=allow_duplicate,
        chunk_options=chunk_options,
    )

    chunk_count = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE record_id = ?", (record.id,)
    ).fetchone()["n"]

    return CaptureResult(
        record=record,
        chunks=int(chunk_count),
        warnings=warnings,
    )
