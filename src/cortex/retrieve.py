"""Hybrid retrieval: vector similarity fused with keyword search.

Neither arm is sufficient alone. Vector search cannot reliably find a proper
noun you half-remember — "Wexler" has no semantic neighbourhood. Keyword
search cannot find a concept you described in different words than you wrote
it in. Reciprocal rank fusion combines the two rankings without needing the
scores to be on comparable scales, which they are not.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .db import serialize_vector
from .embed import Embedder
from .models import Record, SearchHit
from .store import _RECORD_SELECT, slugify

_WORD = re.compile(r"[\w']+", re.UNICODE)

# Deliberately short. These are the words frequent enough that an OR match on
# one of them means nothing; anything more aggressive starts discarding terms
# that carry real meaning in someone's notes.
STOPWORDS = frozenset({
    "all", "and", "any", "are", "been", "but", "can", "come", "day", "did", "for", "from",
    "get", "good", "had", "has", "have", "her", "here", "him", "his", "how", "its", "just",
    "know", "let", "like", "long", "make", "man", "many", "much", "new", "not", "now",
    "old", "one", "our", "out", "over", "put", "say", "see", "she", "some", "such", "take",
    "than", "that", "the", "them", "they", "this", "time", "too", "two", "use", "very",
    "want", "was", "way", "well", "were", "what", "when", "who", "will", "with", "you",
    "your"
})

SNIPPET_CHARS = 260

# kNN has no relevance floor: it returns the k nearest vectors whether or not
# any of them are close, so a query about something you never wrote would
# otherwise hand back your whole vault.
#
# This is a coarse guard, not a precision instrument. Measured cosine
# distances on the same corpus differ sharply between models:
#
#   embeddinggemma    related 0.44-0.54   unrelated 0.77-0.83
#   nomic-embed-text  related 0.47-0.51   unrelated 0.57-0.61
#
# No single number separates both well, so this default suits the configured
# default model and is exposed in Config for anyone who switches. Precision
# comes from fusing with the keyword arm, not from this cut-off.
DEFAULT_MAX_DISTANCE = 0.75


@dataclass
class _Arm:
    """One ranked list of record ids, best first."""

    ranks: dict[int, int]
    snippets: dict[int, str]


def build_fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Raw user input cannot go into MATCH — a stray quote, hyphen or asterisk is
    either a syntax error or a silently different query. Tokenising and
    re-quoting makes any input safe and keeps OR semantics for ranking.

    Stopwords are dropped because OR semantics make them poisonous: a query
    about something you never wrote still matches every note containing "for"
    or "the", so the arm reports a hit for everything. If a query is nothing
    but stopwords they are kept, since a weak arm beats no arm at all.
    """
    tokens = _WORD.findall(query.lower())
    if not tokens:
        return ""

    meaningful = [t for t in tokens if len(t) > 2 and t not in STOPWORDS]
    chosen = meaningful or tokens
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in chosen)


def _vector_arm(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    project_slug: str | None,
    candidates: int,
    max_distance: float,
) -> _Arm:
    vectors = embedder.embed([query])
    if not vectors:
        return _Arm({}, {})

    # vec0 applies k before any join, so over-fetch and filter afterwards
    # rather than losing project-scoped hits to unrelated near neighbours.
    k = candidates * 5 if project_slug else candidates * 2

    rows = conn.execute(
        """
        SELECT c.record_id AS record_id, c.text AS text, v.distance AS distance
        FROM vec_chunks v
        JOIN chunks  c ON c.id = v.rowid
        JOIN records r ON r.id = c.record_id
        JOIN projects p ON p.id = r.project_id
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
        """,
        (serialize_vector(vectors[0]), k),
    ).fetchall()

    ranks: dict[int, int] = {}
    snippets: dict[int, str] = {}
    for row in rows:
        if row["distance"] > max_distance:
            break  # rows are distance-ordered, so everything after is worse too
        record_id = row["record_id"]
        if record_id in ranks:
            continue  # keep only the best-matching chunk per record
        if project_slug is not None:
            owner = conn.execute(
                "SELECT p.slug AS slug FROM records r JOIN projects p ON p.id = r.project_id "
                "WHERE r.id = ?",
                (record_id,),
            ).fetchone()
            if owner is None or owner["slug"] != project_slug:
                continue
        ranks[record_id] = len(ranks) + 1
        snippets[record_id] = row["text"][:SNIPPET_CHARS]
        if len(ranks) >= candidates:
            break

    return _Arm(ranks, snippets)


def _text_arm(
    conn: sqlite3.Connection,
    query: str,
    project_slug: str | None,
    candidates: int,
) -> _Arm:
    match = build_fts_query(query)
    if not match:
        return _Arm({}, {})

    sql = """
        SELECT f.rowid AS record_id,
               snippet(records_fts, 1, '', '', '...', 32) AS snip,
               bm25(records_fts) AS rank
        FROM records_fts f
        JOIN records  r ON r.id = f.rowid
        JOIN projects p ON p.id = r.project_id
        WHERE records_fts MATCH ?
    """
    params: list[object] = [match]
    if project_slug is not None:
        sql += " AND p.slug = ?"
        params.append(project_slug)
    sql += " ORDER BY rank LIMIT ?"
    params.append(candidates)

    ranks: dict[int, int] = {}
    snippets: dict[int, str] = {}
    for position, row in enumerate(conn.execute(sql, params).fetchall(), start=1):
        ranks[row["record_id"]] = position
        snippets[row["record_id"]] = (row["snip"] or "")[:SNIPPET_CHARS]

    return _Arm(ranks, snippets)


def search(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    *,
    project: str | None = None,
    limit: int = 10,
    candidates: int = 50,
    rrf_k: int = 60,
    max_distance: float = DEFAULT_MAX_DISTANCE,
) -> list[SearchHit]:
    """Rank records against a query using both arms, fused.

    Falls back to whichever arm is available: an empty vault, a query with no
    indexable words, or an unreachable embedding model each degrade to the
    other arm rather than failing.
    """
    if not query or not query.strip():
        return []

    project_slug = slugify(project) if project else None

    try:
        vector = _vector_arm(conn, embedder, query, project_slug, candidates, max_distance)
    except Exception:  # noqa: BLE001 - keyword results are better than none
        vector = _Arm({}, {})

    text = _text_arm(conn, query, project_slug, candidates)

    fused: dict[int, float] = {}
    for arm in (vector, text):
        for record_id, rank in arm.ranks.items():
            fused[record_id] = fused.get(record_id, 0.0) + 1.0 / (rrf_k + rank)

    if not fused:
        return []

    ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

    ids = [record_id for record_id, _ in ordered]
    placeholders = ",".join("?" * len(ids))
    rows = {
        row["id"]: Record.from_row(row)
        for row in conn.execute(f"{_RECORD_SELECT} WHERE r.id IN ({placeholders})", ids)
    }

    hits: list[SearchHit] = []
    for record_id, score in ordered:
        record = rows.get(record_id)
        if record is None:
            continue
        snippet = (
            vector.snippets.get(record_id)
            or text.snippets.get(record_id)
            or record.body[:SNIPPET_CHARS]
        )
        hits.append(
            SearchHit(
                record=record,
                score=score,
                snippet=snippet.strip(),
                vector_rank=vector.ranks.get(record_id),
                text_rank=text.ranks.get(record_id),
            )
        )
    return hits
