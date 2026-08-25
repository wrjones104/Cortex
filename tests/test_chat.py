"""Conversations.

The prototype sent two messages every time - a system prompt and the newest
question - so nothing here was possible. These tests pin down the four pieces
that make a long thread survive on a local model.
"""

from __future__ import annotations

import pytest

from cortex.chat import (
    MIN_VERBATIM_TURNS,
    ThreadNotFoundError,
    add_facts,
    add_message,
    answer,
    build_window,
    compact,
    condense,
    create_thread,
    delete_thread,
    get_thread,
    list_facts,
    list_messages,
    list_threads,
    rename_thread,
    set_thread_scope,
)
from cortex.tokens import calibrate, chars_per_token, estimate, input_budget


def drain(events):
    """Collect a stream of answer() events into something assertable."""
    out = {"status": [], "token": "", "sources": [], "done": None, "thinking": ""}
    for kind, payload in events:
        if kind == "token":
            out["token"] += payload
        elif kind == "thinking":
            out["thinking"] += payload
        elif kind == "status":
            out["status"].append(payload)
        elif kind == "sources":
            out["sources"] = payload
        elif kind == "done":
            out["done"] = payload
    return out


# --- threads --------------------------------------------------------------


def test_create_and_list_threads(conn):
    first = create_thread(conn, title="About the keeper", project="Echoes")
    second = create_thread(conn, title="Work stuff")

    assert first.project == "Echoes"
    assert second.project is None
    assert [t.id for t in list_threads(conn)] == [second.id, first.id]


def test_threads_are_ordered_by_recent_activity(conn):
    first = create_thread(conn, title="One")
    create_thread(conn, title="Two")

    add_message(conn, first.id, "user", "poke")

    assert list_threads(conn)[0].id == first.id


def test_rename_and_delete(conn):
    thread = create_thread(conn)
    assert rename_thread(conn, thread.id, "Renamed").title == "Renamed"

    delete_thread(conn, thread.id)
    with pytest.raises(ThreadNotFoundError):
        get_thread(conn, thread.id)


def test_deleting_a_thread_takes_its_messages_and_facts(conn):
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "hello")
    add_facts(conn, thread.id, ["the keeper is called Wexler"])

    delete_thread(conn, thread.id)

    assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM thread_facts").fetchone()["n"] == 0


def test_message_count_ignores_markers(conn):
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "hi")
    add_message(conn, thread.id, "assistant", "hello")
    set_thread_scope(conn, thread.id, "Echoes")

    assert get_thread(conn, thread.id).message_count == 2


# --- scope changes are visible -------------------------------------------


def test_changing_scope_writes_a_marker_into_the_transcript(conn):
    """The prototype changed retrieval scope silently, so a week later there
    was no way to tell which answers had been scoped to what."""
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "first question")

    set_thread_scope(conn, thread.id, "Echoes")

    markers = [m for m in list_messages(conn, thread.id) if m.role == "marker"]
    assert len(markers) == 1
    assert "Echoes" in markers[0].content
    assert get_thread(conn, thread.id).project == "Echoes"


def test_scope_change_to_all_projects_is_also_marked(conn):
    thread = create_thread(conn, project="Echoes")
    set_thread_scope(conn, thread.id, None)

    markers = [m for m in list_messages(conn, thread.id) if m.role == "marker"]
    assert "all projects" in markers[0].content


def test_setting_the_same_scope_writes_nothing(conn):
    thread = create_thread(conn, project="Echoes")
    set_thread_scope(conn, thread.id, "  echoes ")
    assert [m for m in list_messages(conn, thread.id) if m.role == "marker"] == []


def test_markers_are_never_sent_to_the_model(conn, chatter):
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "a question")
    set_thread_scope(conn, thread.id, "Echoes")

    window = build_window(conn, get_thread(conn, thread.id), "next", [],
                          context_length=4096, ratio=4.0)

    assert all("Now searching" not in m["content"] for m in window.messages)
    assert all(m["role"] in ("system", "user", "assistant") for m in window.messages)


# --- condensation ---------------------------------------------------------


def test_condense_rewrites_a_follow_up(conn, chatter):
    """The single biggest quality win: 'tell me more about that' must not be
    embedded verbatim and searched for the word 'that'."""
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "who is Wexler?")
    add_message(conn, thread.id, "assistant", "Wexler is the lighthouse keeper.")

    chatter.replies = ["Who is Wexler the lighthouse keeper?"]
    result = condense(chatter, list_messages(conn, thread.id), "tell me more about that")

    assert result == "Who is Wexler the lighthouse keeper?"
    assert "Wexler is the lighthouse keeper" in chatter.calls[0][1]["content"]


def test_condense_with_no_history_returns_the_question(conn, chatter):
    assert condense(chatter, [], "who is Wexler?") == "who is Wexler?"
    assert chatter.calls == []


def test_a_runaway_rewrite_is_discarded(conn, chatter):
    """A model that answers the question instead of rewriting it would poison
    retrieval, so an implausible rewrite is dropped."""
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "who is Wexler?")

    chatter.replies = ["Well, " + "let me explain at length. " * 60]
    assert condense(chatter, list_messages(conn, thread.id), "more?") == "more?"


def test_condense_survives_the_model_being_down(conn):
    from conftest import FakeChatter
    from cortex.llm import LibrarianError

    class Broken(FakeChatter):
        def complete(self, messages, *, think=False):
            raise LibrarianError("model is down")

    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "who is Wexler?")

    assert condense(Broken(), list_messages(conn, thread.id), "more?") == "more?"


# --- the window -----------------------------------------------------------


def test_the_window_actually_contains_the_conversation(conn, chatter):
    """The prototype's whole bug in one assertion."""
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "who is Wexler?")
    add_message(conn, thread.id, "assistant", "The lighthouse keeper.")

    window = build_window(conn, get_thread(conn, thread.id), "and his daughter?", [],
                          context_length=4096, ratio=4.0)

    roles = [m["role"] for m in window.messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert window.messages[-1]["content"] == "and his daughter?"
    assert "who is Wexler?" in window.messages[1]["content"]


def test_facts_are_carried_in_the_system_prompt(conn):
    thread = create_thread(conn)
    add_facts(conn, thread.id, ["the keeper is called Wexler", "the story is set in 1811"])

    window = build_window(conn, get_thread(conn, thread.id), "q", [],
                          context_length=4096, ratio=4.0)

    system = window.messages[0]["content"]
    assert "Wexler" in system
    assert "1811" in system


def test_a_tight_budget_keeps_the_newest_turns(conn):
    thread = create_thread(conn)
    for i in range(40):
        add_message(conn, thread.id, "user", f"question number {i} " * 20)
        add_message(conn, thread.id, "assistant", f"answer number {i} " * 20)

    window = build_window(conn, get_thread(conn, thread.id), "latest", [],
                          context_length=2048, ratio=4.0)

    kept = [m["content"] for m in window.messages if m["role"] in ("user", "assistant")]
    assert len(kept) < 80  # not everything
    assert "question number 39" in " ".join(kept)
    assert "question number 0 " not in " ".join(kept)


def test_the_last_few_turns_survive_even_an_impossible_budget(conn):
    """A follow-up with no immediate history is worse than overflowing."""
    thread = create_thread(conn)
    for _ in range(10):
        add_message(conn, thread.id, "user", "x" * 4000)
        add_message(conn, thread.id, "assistant", "y" * 4000)

    window = build_window(conn, get_thread(conn, thread.id), "latest", [],
                          context_length=512, ratio=4.0)

    turns = [m for m in window.messages if m["role"] in ("user", "assistant")]
    assert len(turns) >= MIN_VERBATIM_TURNS


def test_retrieved_notes_cannot_crowd_out_the_conversation(conn, embedder, sample_notes):
    from cortex.retrieve import search

    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "an earlier question")
    hits = search(conn, embedder, "lantern harbour keeper", limit=5)

    window = build_window(conn, get_thread(conn, thread.id), "q", hits,
                          context_length=1024, ratio=4.0)

    from cortex.tokens import estimate
    system = window.messages[0]["content"]
    notes_part = system.split("Notes from the vault:")[-1] if "Notes" in system else ""
    assert estimate(notes_part, 4.0) <= input_budget(1024) * 0.4


def test_the_summary_is_included_and_capped(conn):
    thread = create_thread(conn)
    conn.execute("UPDATE threads SET summary = ? WHERE id = ?", ("S" * 40000, thread.id))

    window = build_window(conn, get_thread(conn, thread.id), "q", [],
                          context_length=2048, ratio=4.0)

    system = window.messages[0]["content"]
    assert "Earlier in this conversation" in system
    assert len(system) < 40000


# --- compaction and the facts ledger --------------------------------------


def test_compaction_summarises_the_oldest_turns_and_keeps_the_newest(conn, chatter):
    thread = create_thread(conn)
    for i in range(12):
        add_message(conn, thread.id, "user", f"question {i}")
        add_message(conn, thread.id, "assistant", f"answer {i}")

    chatter.replies = ['["the keeper is called Wexler"]', "They discussed the keeper at length."]
    assert compact(conn, chatter, thread.id) is True

    updated = get_thread(conn, thread.id)
    assert updated.summary == "They discussed the keeper at length."
    assert updated.summarised_upto > 0
    assert list_facts(conn, thread.id) == ["the keeper is called Wexler"]


def test_compaction_leaves_the_recent_turns_verbatim(conn, chatter):
    thread = create_thread(conn)
    for i in range(12):
        add_message(conn, thread.id, "user", f"question {i}")
        add_message(conn, thread.id, "assistant", f"answer {i}")

    chatter.replies = ["[]", "A summary."]
    compact(conn, chatter, thread.id, keep_turns=4)

    window = build_window(conn, get_thread(conn, thread.id), "next", [],
                          context_length=8192, ratio=4.0)
    verbatim = " ".join(m["content"] for m in window.messages if m["role"] != "system")
    assert "answer 11" in verbatim
    assert "question 0" not in verbatim


def test_nothing_to_compact_is_not_an_error(conn, chatter):
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "just one")
    assert compact(conn, chatter, thread.id) is False


def test_facts_survive_a_failed_summary(conn):
    """Losing the summary is a setback; losing the facts is amnesia."""
    from conftest import FakeChatter
    from cortex.llm import LibrarianError

    class HalfBroken(FakeChatter):
        def complete(self, messages, *, think=False):
            if "durable facts" in messages[0]["content"]:
                return '["Wexler is the keeper"]', 10
            raise LibrarianError("summariser is down")

    thread = create_thread(conn)
    for i in range(12):
        add_message(conn, thread.id, "user", f"question {i}")
        add_message(conn, thread.id, "assistant", f"answer {i}")

    assert compact(conn, HalfBroken(), thread.id) is True
    assert list_facts(conn, thread.id) == ["Wexler is the keeper"]
    # The watermark must not advance, or those turns vanish unsummarised.
    assert get_thread(conn, thread.id).summarised_upto == 0


def test_the_ledger_is_append_only_and_deduplicated(conn):
    thread = create_thread(conn)
    assert add_facts(conn, thread.id, ["a", "b"]) == 2
    assert add_facts(conn, thread.id, ["b", "c"]) == 1
    assert list_facts(conn, thread.id) == ["a", "b", "c"]


def test_malformed_fact_extraction_is_ignored(conn):
    from conftest import FakeChatter

    class Rambling(FakeChatter):
        def complete(self, messages, *, think=False):
            if "durable facts" in messages[0]["content"]:
                return "I'm not sure I can do that.", 10
            return "A summary.", 10

    thread = create_thread(conn)
    for i in range(12):
        add_message(conn, thread.id, "user", f"q{i}")
        add_message(conn, thread.id, "assistant", f"a{i}")

    compact(conn, Rambling(), thread.id)
    assert list_facts(conn, thread.id) == []


def test_facts_extracted_before_compaction_outlive_the_turns(conn, chatter):
    """The whole point of the ledger: turn 30 still knows what turn 4 said."""
    thread = create_thread(conn)
    add_message(conn, thread.id, "user", "the keeper is called Wexler")
    add_message(conn, thread.id, "assistant", "Noted.")
    for i in range(12):
        add_message(conn, thread.id, "user", f"unrelated {i}")
        add_message(conn, thread.id, "assistant", f"fine {i}")

    chatter.replies = ['["the keeper is called Wexler"]', "Early exchange about a keeper."]
    compact(conn, chatter, thread.id)

    window = build_window(conn, get_thread(conn, thread.id), "what was the name?", [],
                          context_length=1024, ratio=4.0)

    # The original turn is gone from the verbatim window...
    verbatim = " ".join(m["content"] for m in window.messages if m["role"] != "system")
    assert "the keeper is called Wexler" not in verbatim
    # ...but the name is still in front of the model.
    assert "Wexler" in window.messages[0]["content"]


# --- answering ------------------------------------------------------------


def test_answer_stores_both_sides_of_the_exchange(conn, embedder, chatter, sample_notes):
    thread = create_thread(conn, project="Echoes")
    chatter.replies = ["Wexler tends the lantern."]

    result = drain(answer(conn, embedder, chatter, thread.id, "who is Wexler?"))

    assert result["token"].strip() == "Wexler tends the lantern."
    stored = [m for m in list_messages(conn, thread.id) if m.role != "marker"]
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[0].content == "who is Wexler?"


def test_a_follow_up_sees_the_earlier_turns(conn, embedder, chatter, sample_notes):
    thread = create_thread(conn, project="Echoes")

    chatter.replies = ["He is the keeper."]
    drain(answer(conn, embedder, chatter, thread.id, "who is Wexler?"))

    chatter.replies = ["Standalone: Wexler's daughter", "She inherits the lantern."]
    drain(answer(conn, embedder, chatter, thread.id, "and his daughter?"))

    sent = chatter.calls[-1]
    transcript = " ".join(m["content"] for m in sent)
    assert "who is Wexler?" in transcript
    assert "He is the keeper." in transcript


def test_answer_reports_its_sources(conn, embedder, chatter, sample_notes):
    thread = create_thread(conn, project="Echoes")
    result = drain(answer(conn, embedder, chatter, thread.id, "Wexler lantern cliffs"))

    assert result["sources"]
    assert result["done"]["sources"] == result["sources"]
    stored = [m for m in list_messages(conn, thread.id) if m.role == "assistant"][0]
    assert stored.sources == result["sources"]


def test_a_follow_up_about_the_same_note_still_receives_it(conn, embedder, chatter, sample_notes):
    """Notes are retrieved fresh every turn.

    An earlier answer stays in the window, but the note bodies behind it do
    not - the system prompt is rebuilt each turn. Excluding already-quoted
    notes therefore starves a follow-up of the exact note it is asking about,
    and the model invents an answer to fill the gap.
    """
    thread = create_thread(conn, project="Echoes")
    first = drain(answer(conn, embedder, chatter, thread.id, "Wexler lantern cliffs"))
    assert first["sources"]

    chatter.replies = ["Wexler and the cliffs again", "Still Wexler."]
    second = drain(answer(conn, embedder, chatter, thread.id, "tell me more about him"))

    assert second["sources"], "a follow-up must not be left with no notes at all"
    assert set(second["sources"]) & set(first["sources"])


def test_answering_works_when_retrieval_fails(conn, chatter):
    from conftest import ExplodingEmbedder

    thread = create_thread(conn)
    chatter.replies = ["I have nothing on that."]
    result = drain(answer(conn, ExplodingEmbedder(), chatter, thread.id, "anything?"))

    assert result["token"].strip() == "I have nothing on that."
    assert result["sources"] == []


def test_an_empty_question_is_refused(conn, embedder, chatter):
    thread = create_thread(conn)
    with pytest.raises(ValueError, match="empty"):
        drain(answer(conn, embedder, chatter, thread.id, "   "))


def test_the_first_question_names_the_thread(conn, embedder, chatter, sample_notes):
    thread = create_thread(conn)
    assert thread.title == "New conversation"

    chatter.replies = ["An answer.", "The Lighthouse Keeper"]
    drain(answer(conn, embedder, chatter, thread.id, "who is Wexler?"))

    assert get_thread(conn, thread.id).title == "The Lighthouse Keeper"


def test_a_named_thread_is_not_renamed(conn, embedder, chatter, sample_notes):
    thread = create_thread(conn, title="My own name")
    drain(answer(conn, embedder, chatter, thread.id, "a question"))
    assert get_thread(conn, thread.id).title == "My own name"


def test_a_long_thread_compacts_itself_mid_answer(conn, embedder, sample_notes):
    from conftest import FakeChatter

    small = FakeChatter(context_length=1024)
    thread = create_thread(conn)
    for i in range(30):
        add_message(conn, thread.id, "user", f"question {i} " * 40)
        add_message(conn, thread.id, "assistant", f"answer {i} " * 40)

    result = drain(answer(conn, embedder, small, thread.id, "so what now?"))

    assert result["done"]["compacted"] is True
    assert "Summarising earlier turns" in result["status"]
    assert get_thread(conn, thread.id).summary


# --- token calibration ----------------------------------------------------


def test_the_estimator_calibrates_towards_reality(conn):
    """Chunking only needs to stay under a limit; budgeting a window needs the
    estimate to be right in both directions."""
    assert chars_per_token(conn, "m") == 4.0

    # A model that really uses 3 chars per token.
    for _ in range(20):
        calibrate(conn, "m", sent_chars=3000, actual_tokens=1000)

    assert 3.0 <= chars_per_token(conn, "m") <= 3.1


def test_calibration_is_per_model(conn):
    calibrate(conn, "a", 3000, 1000)
    assert chars_per_token(conn, "a") < 4.0
    assert chars_per_token(conn, "b") == 4.0


def test_absurd_measurements_are_ignored(conn):
    """A truncated response or a prompt we did not build must not move it."""
    for tokens in (0, 1, 100000):
        calibrate(conn, "m", 3000, tokens)
    assert chars_per_token(conn, "m") == 4.0


def test_input_budget_leaves_room_for_the_answer():
    assert input_budget(10000) == 7000
    assert input_budget(0) >= 256


def test_estimate_counts_something_for_any_real_text():
    assert estimate("") == 0
    assert estimate("hello world") >= 1


def test_a_scoped_thread_carries_the_project_description(conn):
    from cortex.store import get_or_create_project, update_project

    get_or_create_project(conn, "Echoes")
    update_project(conn, "Echoes", description="A coastal town that forgets its own history.")

    thread = create_thread(conn, project="Echoes")
    window = build_window(conn, get_thread(conn, thread.id), "a question", [],
                          context_length=4096, ratio=4.0)

    assert "forgets its own history" in window.messages[0]["content"]


def test_an_unscoped_thread_carries_no_project_description(conn):
    from cortex.store import get_or_create_project, update_project

    get_or_create_project(conn, "Echoes")
    update_project(conn, "Echoes", description="A coastal town.")

    thread = create_thread(conn)
    window = build_window(conn, get_thread(conn, thread.id), "q", [],
                          context_length=4096, ratio=4.0)

    assert "A coastal town" not in window.messages[0]["content"]
