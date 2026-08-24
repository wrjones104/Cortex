"""Hybrid search.

The point of fusing two arms is that each covers the other's blind spot:
keyword search finds the proper noun that has no semantic neighbourhood,
vector search finds the concept described in different words. These tests
check both arms work, that fusion prefers agreement, and that a failure in
either arm degrades to the other instead of to nothing.
"""

from __future__ import annotations

from cortex.retrieve import build_fts_query, search
from cortex.store import create_record

# --- FTS query construction ------------------------------------------------


def test_build_fts_query_quotes_every_token():
    assert build_fts_query("copper lantern") == '"copper" OR "lantern"'


def test_build_fts_query_neutralises_syntax_that_would_break_match():
    """Raw user input in MATCH is either a syntax error or a different query."""
    for raw in ['a "quoted" phrase', "NOT AND OR", "well-known", "wild*card", "paren(s)", "^caret"]:
        built = build_fts_query(raw)
        assert '"' in built or built == ""


def test_build_fts_query_is_empty_for_input_with_no_words():
    assert build_fts_query("   ") == ""
    assert build_fts_query("!!! ??? ***") == ""


# --- retrieval -------------------------------------------------------------


def test_keyword_arm_finds_a_proper_noun(conn, embedder, sample_notes):
    """'Wexler' has no semantic neighbourhood — only the keyword arm can find it."""
    hits = search(conn, embedder, "Wexler")
    assert hits
    assert hits[0].record.title == "The Lighthouse Keeper"
    assert hits[0].text_rank is not None


def test_vector_arm_still_works_when_the_keyword_arm_finds_nothing(conn, embedder, sample_notes):
    hits = search(conn, embedder, "lantern cliffs copper")
    assert hits
    assert any(h.vector_rank is not None for h in hits)


def test_search_returns_nothing_for_an_empty_query(conn, embedder, sample_notes):
    assert search(conn, embedder, "") == []
    assert search(conn, embedder, "   ") == []


def test_search_on_an_empty_vault_is_empty_not_an_error(conn, embedder):
    assert search(conn, embedder, "anything at all") == []


def test_project_filter_excludes_other_projects(conn, embedder, sample_notes):
    hits = search(conn, embedder, "deployment pipeline runners", project="Echoes")
    assert all(h.record.project_name == "Echoes" for h in hits)

    unscoped = search(conn, embedder, "deployment pipeline runners")
    assert any(h.record.project_name == "Work Notes" for h in unscoped)


def test_project_filter_accepts_any_spelling_of_the_name(conn, embedder, sample_notes):
    for spelling in ("Echoes", "echoes", "  ECHOES  "):
        hits = search(conn, embedder, "harbour council", project=spelling)
        assert hits
        assert all(h.record.project_name == "Echoes" for h in hits)


def test_a_record_matched_by_both_arms_outranks_one_matched_by_either(conn, embedder):
    create_record(
        conn, embedder, project="P", title="Both",
        body="The copper lantern burns above the harbour every single night.",
    )
    create_record(
        conn, embedder, project="P", title="Keyword only",
        body="Lantern. Nothing else in this note resembles the query at all.",
    )

    hits = search(conn, embedder, "copper lantern harbour")
    assert hits[0].record.title == "Both"
    assert hits[0].matched_by == "both"


def test_limit_is_respected(conn, embedder):
    for i in range(15):
        create_record(
            conn, embedder, project="P", title=f"Note {i}",
            body=f"Shared vocabulary about lanterns and harbours, entry {i}.",
        )
    assert len(search(conn, embedder, "lanterns harbours", limit=5)) == 5


def test_one_record_appears_once_however_many_chunks_matched(conn, embedder):
    """A long note produces many chunks; it must not flood the results."""
    body = "\n\n".join(f"The copper lantern section {i} burns brightly. " * 10 for i in range(20))
    record = create_record(conn, embedder, project="P", title="Long", body=body)
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] > 3

    hits = search(conn, embedder, "copper lantern burns")
    assert [h.record.id for h in hits].count(record.id) == 1


def test_search_degrades_to_keywords_when_the_embedder_is_down(conn, embedder, sample_notes):
    """An unreachable model server should cost you ranking, not results."""
    from conftest import ExplodingEmbedder

    hits = search(conn, ExplodingEmbedder(), "Wexler lantern")

    assert hits
    assert all(h.vector_rank is None for h in hits)
    assert all(h.text_rank is not None for h in hits)


def test_hits_carry_a_snippet(conn, embedder, sample_notes):
    hits = search(conn, embedder, "harbour council trade")
    assert hits
    assert hits[0].snippet.strip()


def test_deleted_records_stop_appearing(conn, embedder, sample_notes):
    from cortex.store import delete_record

    target = next(r for r in sample_notes if r.title == "The Lighthouse Keeper")
    assert any(h.record.id == target.id for h in search(conn, embedder, "Wexler"))

    delete_record(conn, target.id)

    assert not any(h.record.id == target.id for h in search(conn, embedder, "Wexler"))


def test_updated_body_is_searchable_by_its_new_text(conn, embedder):
    from cortex.store import update_record

    record = create_record(conn, embedder, project="P", title="T", body="Original copper lantern.")
    update_record(conn, embedder, record.id, body="Replaced with brass bellows entirely.")

    assert not search(conn, embedder, "copper lantern")
    assert any(h.record.id == record.id for h in search(conn, embedder, "brass bellows"))


def test_an_unrelated_query_does_not_return_the_whole_vault(conn, embedder, sample_notes):
    """kNN always returns its k nearest, however far away they are. Without a
    distance floor, searching for something you never wrote hands back
    everything you did."""
    hits = search(conn, embedder, "quarterly amortisation schedules for leasehold property")
    assert hits == []


def test_the_distance_floor_is_adjustable(conn, embedder, sample_notes):
    loose = search(conn, embedder, "quarterly amortisation schedules", max_distance=2.0)
    assert loose


def test_stopwords_are_dropped_from_the_keyword_arm():
    """OR semantics make stopwords poisonous: a match on 'for' is not a match."""
    assert build_fts_query("schedules for the property") == '"schedules" OR "property"'
    assert "for" not in build_fts_query("looking for lanterns")


def test_a_query_of_nothing_but_stopwords_still_searches():
    """A weak arm beats no arm — the vector side can still rank these."""
    built = build_fts_query("what is it all for")
    assert built
    assert '"what"' in built


def test_an_unrelated_query_does_not_match_on_a_stopword(conn, embedder, sample_notes):
    """The end-to-end version: no arm should claim a hit here."""
    assert search(conn, embedder, "quarterly amortisation schedules for the property") == []
