"""Brainstorming, and banking only the parts you wanted.

The prototype banked a whole generation as one record: asking for five
alternatives and wanting the third meant storing all five glued together, or
nothing. These tests pin down the parts that fix that.
"""

from __future__ import annotations

import json

import pytest

from cortex.creative import (
    MAX_OPTIONS,
    GenerationNotFoundError,
    bank,
    delete_generation,
    generate,
    get_generation,
    list_generations,
    split,
)
from cortex.store import count_records, get_record, list_records


def ideas_json(*titles: str) -> str:
    return json.dumps(
        {
            "ideas": [
                {"title": t, "pitch": f"{t} in one line.", "detail": f"A paragraph about {t}."}
                for t in titles
            ]
        }
    )


def drain(events):
    out = {"status": [], "token": "", "done": None}
    for kind, payload in events:
        if kind == "token":
            out["token"] += payload
        elif kind == "status":
            out["status"].append(payload)
        elif kind == "done":
            out["done"] = payload
    return out


# --- generating -----------------------------------------------------------


def test_options_mode_produces_separate_ideas(conn, embedder, chatter):
    chatter.replies = [ideas_json("The Tidesong Gearwork", "The Salt-Crystal Resonance")]

    result = drain(generate(conn, embedder, chatter, "how does the bell work?", count=2))

    generation = get_generation(conn, result["done"]["generation_id"])
    assert [i.title for i in generation.ideas] == [
        "The Tidesong Gearwork",
        "The Salt-Crystal Resonance",
    ]
    assert generation.mode == "options"
    assert all(not i.banked for i in generation.ideas)


def test_options_mode_asks_for_the_requested_count(conn, embedder, chatter):
    chatter.replies = [ideas_json("A", "B", "C", "D", "E")]
    drain(generate(conn, embedder, chatter, "five ways", count=5))
    assert "exactly 5" in chatter.calls[-1][0]["content"]


def test_the_count_is_clamped(conn, embedder, chatter):
    chatter.replies = [ideas_json("A")]
    drain(generate(conn, embedder, chatter, "one way", count=999))
    assert f"exactly {MAX_OPTIONS}" in chatter.calls[-1][0]["content"]


def test_options_mode_requests_a_schema(conn, embedder, chatter):
    """Asking for the shape up front is far more reliable than cutting prose
    apart afterwards."""
    chatter.replies = [ideas_json("A")]
    drain(generate(conn, embedder, chatter, "a prompt", count=1))
    assert chatter.formats[-1] is not None


def test_freeform_mode_streams_prose_and_stores_it(conn, embedder, chatter):
    chatter.replies = ["Some rambling thoughts about bells and tides."]

    result = drain(generate(conn, embedder, chatter, "ramble at me", mode="freeform"))

    generation = get_generation(conn, result["done"]["generation_id"])
    assert generation.mode == "freeform"
    assert "rambling thoughts" in generation.output
    assert generation.ideas == []
    assert chatter.formats[-1] is None


def test_unusable_structured_output_is_kept_as_prose(conn, embedder, chatter):
    """A minute of generation must not be thrown away because the JSON was
    malformed - it can still be split."""
    chatter.replies = ["I'm afraid I can't produce that as a list."]

    result = drain(generate(conn, embedder, chatter, "options please", count=3))

    assert result["done"]["mode"] == "freeform"
    generation = get_generation(conn, result["done"]["generation_id"])
    assert "can't produce that" in generation.output


def test_an_empty_prompt_is_refused(conn, embedder, chatter):
    with pytest.raises(ValueError, match="empty"):
        drain(generate(conn, embedder, chatter, "   "))


def test_an_unknown_mode_is_refused(conn, embedder, chatter):
    with pytest.raises(ValueError, match="Unknown mode"):
        drain(generate(conn, embedder, chatter, "a prompt", mode="interpretive-dance"))


def test_generation_is_grounded_in_existing_notes(conn, embedder, chatter, sample_notes):
    chatter.replies = [ideas_json("A")]
    drain(generate(conn, embedder, chatter, "more about the lighthouse keeper", project="Echoes"))

    system = chatter.calls[-1][0]["content"]
    assert "Lighthouse Keeper" in system
    assert "Do not contradict" in system


def test_grounding_can_be_switched_off(conn, embedder, chatter, sample_notes):
    chatter.replies = [ideas_json("A")]
    drain(
        generate(
            conn, embedder, chatter, "anything", project="Echoes", use_context=False
        )
    )
    assert "Do not contradict" not in chatter.calls[-1][0]["content"]


# --- history --------------------------------------------------------------


def test_generating_again_does_not_destroy_the_previous_batch(conn, embedder, chatter):
    """The prototype held one generation in a single variable, so a second
    attempt overwrote a batch you had not finished mining."""
    chatter.replies = [ideas_json("First A", "First B")]
    first = drain(generate(conn, embedder, chatter, "attempt one", count=2))["done"]

    chatter.replies = [ideas_json("Second A", "Second B")]
    second = drain(generate(conn, embedder, chatter, "attempt two", count=2))["done"]

    assert first["generation_id"] != second["generation_id"]
    assert [i.title for i in get_generation(conn, first["generation_id"]).ideas] == [
        "First A",
        "First B",
    ]
    assert len(list_generations(conn)) == 2


def test_generations_are_newest_first(conn, embedder, chatter):
    for prompt in ("one", "two", "three"):
        chatter.replies = [ideas_json("X")]
        drain(generate(conn, embedder, chatter, prompt, count=1))

    assert [g.prompt for g in list_generations(conn)] == ["three", "two", "one"]


def test_deleting_a_generation_takes_its_ideas(conn, embedder, chatter):
    chatter.replies = [ideas_json("A", "B")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=2))["done"][
        "generation_id"
    ]

    delete_generation(conn, generation_id)

    assert conn.execute("SELECT COUNT(*) AS n FROM generation_ideas").fetchone()["n"] == 0
    with pytest.raises(GenerationNotFoundError):
        get_generation(conn, generation_id)


def test_a_missing_generation_raises(conn):
    with pytest.raises(GenerationNotFoundError):
        get_generation(conn, 999)


# --- splitting ------------------------------------------------------------


def test_split_cuts_a_freeform_generation_into_candidates(conn, embedder, chatter, librarian):
    chatter.replies = ["A long ramble covering two separate notions."]
    generation_id = drain(
        generate(conn, embedder, chatter, "ramble", mode="freeform")
    )["done"]["generation_id"]

    chatter.replies = [ideas_json("The First Notion", "The Second Notion")]
    ideas = split(conn, librarian, chatter, generation_id)

    assert [i.title for i in ideas] == ["The First Notion", "The Second Notion"]


def test_splitting_twice_replaces_the_previous_attempt(conn, embedder, chatter, librarian):
    chatter.replies = ["A ramble."]
    generation_id = drain(
        generate(conn, embedder, chatter, "ramble", mode="freeform")
    )["done"]["generation_id"]

    chatter.replies = [ideas_json("Bad A", "Bad B")]
    split(conn, librarian, chatter, generation_id)

    chatter.replies = [ideas_json("Better A")]
    ideas = split(conn, librarian, chatter, generation_id)

    assert [i.title for i in ideas] == ["Better A"]


def test_resplitting_keeps_ideas_you_already_banked(conn, embedder, chatter, librarian):
    chatter.replies = ["A ramble."]
    generation_id = drain(
        generate(conn, embedder, chatter, "ramble", mode="freeform")
    )["done"]["generation_id"]

    chatter.replies = [ideas_json("Keeper", "Discard")]
    split(conn, librarian, chatter, generation_id)
    bank(conn, embedder, generation_id, [0], project="Echoes")

    chatter.replies = [ideas_json("Fresh One")]
    ideas = split(conn, librarian, chatter, generation_id)

    titles = [i.title for i in ideas]
    assert "Keeper" in titles, "a banked idea must not vanish on re-split"
    assert "Discard" not in titles
    assert "Fresh One" in titles


def test_splitting_a_generation_with_no_prose_is_a_no_op(conn, embedder, chatter, librarian):
    chatter.replies = [ideas_json("A")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=1))["done"][
        "generation_id"
    ]
    assert [i.title for i in split(conn, librarian, chatter, generation_id)] == ["A"]


# --- banking --------------------------------------------------------------


def test_banking_one_idea_of_several(conn, embedder, chatter):
    """The whole point of the milestone."""
    chatter.replies = [ideas_json("Option One", "Option Two", "Option Three")]
    generation_id = drain(generate(conn, embedder, chatter, "three ways", count=3))["done"][
        "generation_id"
    ]

    result = bank(conn, embedder, generation_id, [1], project="Echoes")

    assert len(result.banked) == 1
    assert result.banked[0].title == "Option Two"
    assert count_records(conn) == 1


def test_each_banked_idea_becomes_its_own_record(conn, embedder, chatter):
    chatter.replies = [ideas_json("Alpha", "Beta", "Gamma")]
    generation_id = drain(generate(conn, embedder, chatter, "three", count=3))["done"][
        "generation_id"
    ]

    result = bank(conn, embedder, generation_id, [0, 2], project="Echoes")

    assert len(result.banked) == 2
    titles = {r.title for r in list_records(conn)}
    assert titles == {"Alpha", "Gamma"}
    # Separate records means separate embeddings, which is what makes them
    # findable individually later.
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] >= 2


def test_banking_defaults_to_verbatim(conn, embedder, chatter, librarian):
    """The prototype always re-ran the Librarian, handing a 27B model an
    unrequested rewrite of prose you had already decided you liked."""
    chatter.replies = [ideas_json("Untouched")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=1))["done"][
        "generation_id"
    ]

    calls_before = librarian.calls
    result = bank(conn, embedder, generation_id, [0], librarian=librarian, project="P")

    assert librarian.calls == calls_before
    assert result.banked[0].body == "A paragraph about Untouched."
    assert result.banked[0].title == "Untouched"


def test_banking_can_ask_for_a_clean_up(conn, embedder, chatter, librarian):
    chatter.replies = [ideas_json("Rough")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=1))["done"][
        "generation_id"
    ]

    result = bank(
        conn, embedder, generation_id, [0], librarian=librarian, project="P", verbatim=False
    )

    assert result.banked[0].category == "Test Category"


def test_a_banked_idea_is_marked_and_cannot_be_filed_twice(conn, embedder, chatter):
    chatter.replies = [ideas_json("Only One")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=1))["done"][
        "generation_id"
    ]

    bank(conn, embedder, generation_id, [0], project="P")
    assert get_generation(conn, generation_id).ideas[0].banked is True

    again = bank(conn, embedder, generation_id, [0], project="P")
    assert again.banked == []
    assert again.skipped == [(0, "Already filed.")]
    assert count_records(conn) == 1


def test_banking_an_ordinal_that_does_not_exist(conn, embedder, chatter):
    chatter.replies = [ideas_json("A")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=1))["done"][
        "generation_id"
    ]

    result = bank(conn, embedder, generation_id, [7], project="P")
    assert result.banked == []
    assert "No such idea" in result.skipped[0][1]


def test_one_bad_idea_does_not_stop_the_others(conn, embedder, chatter):
    chatter.replies = [ideas_json("Good One", "Good Two")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=2))["done"][
        "generation_id"
    ]

    result = bank(conn, embedder, generation_id, [0, 99, 1], project="P")

    assert len(result.banked) == 2
    assert len(result.skipped) == 1


def test_banking_falls_back_to_the_generation_project(conn, embedder, chatter):
    chatter.replies = [ideas_json("A")]
    generation_id = drain(
        generate(conn, embedder, chatter, "p", count=1, project="Echoes")
    )["done"]["generation_id"]

    result = bank(conn, embedder, generation_id, [0])
    assert result.banked[0].project_name == "Echoes"


def test_banking_with_no_project_anywhere_lands_in_the_inbox(conn, embedder, chatter):
    chatter.replies = [ideas_json("A")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=1))["done"][
        "generation_id"
    ]
    assert bank(conn, embedder, generation_id, [0]).banked[0].project_name == "Inbox"


def test_a_duplicate_idea_is_reported_not_crashed(conn, embedder, chatter):
    chatter.replies = [ideas_json("Same", "Same")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=2))["done"][
        "generation_id"
    ]

    result = bank(conn, embedder, generation_id, [0, 1], project="P")

    assert len(result.banked) == 1
    assert len(result.skipped) == 1
    assert "already in this project" in result.skipped[0][1]


def test_banked_records_are_searchable_individually(conn, embedder, chatter):
    from cortex.retrieve import search

    chatter.replies = [
        json.dumps(
            {
                "ideas": [
                    {
                        "title": "Tidesong Gearwork",
                        "pitch": "Powered by the tide.",
                        "detail": "A gearwork driven by the rising tide turns the bell.",
                    },
                    {
                        "title": "Salt Crystal Resonance",
                        "pitch": "Powered by salinity.",
                        "detail": "Crystals in the housing resonate as salinity changes.",
                    },
                ]
            }
        )
    ]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=2))["done"][
        "generation_id"
    ]
    bank(conn, embedder, generation_id, [0, 1], project="Echoes")

    hits = search(conn, embedder, "crystals resonate salinity")
    assert hits
    assert hits[0].record.title == "Salt Crystal Resonance"


def test_banking_marks_the_source(conn, embedder, chatter):
    chatter.replies = [ideas_json("A")]
    generation_id = drain(generate(conn, embedder, chatter, "p", count=1))["done"][
        "generation_id"
    ]
    record = bank(conn, embedder, generation_id, [0], project="P").banked[0]
    assert get_record(conn, record.id).source == "creative"
