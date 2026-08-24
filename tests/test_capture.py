from __future__ import annotations

import pytest

from cortex.capture import build_context, capture
from cortex.store import DuplicateRecordError, count_records, get_record


def test_capture_files_a_note_through_the_librarian(conn, embedder, librarian):
    result = capture(conn, embedder, "Wexler tends the lantern.", librarian=librarian)

    assert result.record.id > 0
    assert result.record.category == "Test Category"
    assert result.record.project_name == "Auto Project"
    assert result.chunks == 1
    assert result.warnings == []


def test_explicit_project_overrides_whatever_the_model_picks(conn, embedder, librarian):
    result = capture(conn, embedder, "Some note.", librarian=librarian, project="Echoes")
    assert result.record.project_name == "Echoes"


def test_verbatim_capture_never_touches_the_model(conn, embedder, librarian):
    """Banking prose you already liked must not hand it to a model to rewrite."""
    text = "# Option 3\n\nThe keeper's daughter inherits the lantern."
    result = capture(conn, embedder, text, librarian=librarian, verbatim=True, project="Echoes")

    assert result.record.body == text
    assert result.record.category == ""
    assert librarian.last_context is None


def test_capture_falls_back_to_storing_the_note_when_the_model_fails(conn, embedder):
    """Losing the structuring is annoying. Losing the note is not acceptable."""
    from conftest import FakeLibrarian

    broken = FakeLibrarian(fail=True)
    result = capture(conn, embedder, "An important thought.", librarian=broken, project="Echoes")

    assert result.record.body == "An important thought."
    assert result.record.project_name == "Echoes"
    assert result.warnings
    assert "model unavailable" in result.warnings[0]


def test_capture_with_no_librarian_at_all_still_stores(conn, embedder):
    result = capture(conn, embedder, "Straight to the vault.")
    assert result.record.body == "Straight to the vault."


def test_capture_without_a_project_lands_in_the_inbox(conn, embedder):
    result = capture(conn, embedder, "Unfiled thought.")
    assert result.record.project_name == "Inbox"


def test_empty_capture_is_rejected(conn, embedder, librarian):
    for empty in ("", "   ", "\n\n\t"):
        with pytest.raises(ValueError, match="empty"):
            capture(conn, embedder, empty, librarian=librarian)


def test_capturing_the_same_note_twice_is_refused(conn, embedder, librarian):
    capture(conn, embedder, "Exactly the same words.", librarian=librarian, project="P")
    with pytest.raises(DuplicateRecordError):
        capture(conn, embedder, "Exactly the same words.", librarian=librarian, project="P")
    assert count_records(conn) == 1


def test_a_replayed_phone_capture_stores_once(conn, embedder, librarian):
    first = capture(
        conn, embedder, "Sent from the train.", librarian=librarian,
        project="P", idempotency_key="queue-7",
    )
    second = capture(
        conn, embedder, "Sent from the train.", librarian=librarian,
        project="P", idempotency_key="queue-7",
    )
    assert first.record.id == second.record.id
    assert count_records(conn) == 1


def test_a_very_long_note_captures_as_multiple_chunks(conn, embedder, librarian):
    body = "\n\n".join(f"Paragraph {i}. " + ("Words and more words. " * 25) for i in range(40))
    assert len(body) > 20_000

    result = capture(conn, embedder, body, librarian=librarian, project="P")

    assert result.chunks > 1
    assert get_record(conn, result.record.id).body.startswith("Paragraph 0.")


# --- grounding context ----------------------------------------------------


def test_context_pulls_related_notes(conn, embedder, sample_notes):
    context = build_context(conn, embedder, "the lighthouse keeper Wexler", "Echoes")
    assert "Lighthouse Keeper" in context


def test_context_stays_inside_its_character_budget(conn, embedder):
    from cortex.store import create_record

    for i in range(10):
        create_record(
            conn, embedder, project="P", title=f"Note {i}",
            body=f"Shared harbour lantern vocabulary in entry {i}. " * 60,
        )

    context = build_context(conn, embedder, "harbour lantern", "P", limit=10, budget_chars=600)
    assert len(context) <= 900  # budget plus at most one entry's headroom


def test_context_is_empty_rather_than_fatal_when_retrieval_breaks(conn, embedder):
    from conftest import ExplodingEmbedder

    conn.execute("DROP TABLE vec_chunks")
    assert build_context(conn, ExplodingEmbedder(), "anything", "P") == ""


def test_capture_passes_context_to_the_librarian(conn, embedder, librarian, sample_notes):
    capture(conn, embedder, "More about the keeper and his lantern.",
            librarian=librarian, project="Echoes")
    assert librarian.last_context is not None
    assert "Lighthouse Keeper" in librarian.last_context


def test_context_can_be_switched_off(conn, embedder, librarian, sample_notes):
    capture(conn, embedder, "A note.", librarian=librarian, project="Echoes", use_context=False)
    assert librarian.last_context == ""
