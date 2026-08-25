"""Import, export and backup.

The prototype had none of these: the vault lived in one git-ignored folder
with no copy and no way out. These are the tests for the exit door.
"""

from __future__ import annotations

import sqlite3

import pytest

from cortex.port import (
    backup,
    export_markdown,
    import_markdown,
    parse_frontmatter,
    safe_filename,
)
from cortex.store import count_records, create_record, list_records

# --- frontmatter ----------------------------------------------------------


def test_parse_frontmatter_splits_metadata_from_body():
    meta, body = parse_frontmatter("---\ntitle: A Note\nproject: Echoes\n---\n\nThe body.\n")
    assert meta == {"title": "A Note", "project": "Echoes"}
    assert body.strip() == "The body."


def test_a_file_with_no_frontmatter_is_plain_markdown():
    meta, body = parse_frontmatter("# Just a heading\n\nSome text.")
    assert meta == {}
    assert body.startswith("# Just a heading")


def test_broken_frontmatter_is_treated_as_text_not_an_error():
    """Most notes you might import were never written for this tool."""
    meta, body = parse_frontmatter("---\n: : not valid: yaml: at all\n---\n\nBody.")
    assert meta == {}
    assert "Body." in body


def test_frontmatter_that_is_not_a_mapping_is_ignored():
    meta, _ = parse_frontmatter("---\n- a list\n- of things\n---\n\nBody.")
    assert meta == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A Normal Title", "A Normal Title"),
        ('bad/chars:in*name?', "badcharsinname"),
        ("   ", "untitled"),
        ("...", "untitled"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


# --- export ---------------------------------------------------------------


def test_export_writes_one_file_per_record_grouped_by_project(
    conn, embedder, sample_notes, tmp_path
):
    destination = tmp_path / "export"
    written = export_markdown(conn, destination)

    assert written == 3
    assert (destination / "echoes").is_dir()
    assert (destination / "work-notes").is_dir()
    assert len(list(destination.rglob("*.md"))) == 3


def test_exported_files_round_trip_through_the_parser(conn, embedder, sample_notes, tmp_path):
    export_markdown(conn, tmp_path / "export")
    path = next((tmp_path / "export" / "echoes").glob("*.md"))

    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))

    assert meta["project"] == "Echoes"
    assert meta["title"]
    assert body.strip()


def test_export_survives_a_title_that_is_not_a_valid_filename(conn, embedder, tmp_path):
    create_record(
        conn, embedder, project="P", title='Bad/Name: With*Chars?', body="Body text here."
    )
    assert export_markdown(conn, tmp_path / "out") == 1
    assert len(list((tmp_path / "out").rglob("*.md"))) == 1


def test_export_of_an_empty_vault_writes_nothing_and_does_not_fail(conn, tmp_path):
    assert export_markdown(conn, tmp_path / "out") == 0


# --- import ---------------------------------------------------------------


def test_import_reads_frontmatter(conn, embedder, tmp_path):
    source = tmp_path / "notes"
    source.mkdir()
    (source / "one.md").write_text(
        "---\ntitle: Imported Note\nproject: Echoes\n"
        "category: Worldbuilding\n---\n\nThe body text.",
        encoding="utf-8",
    )

    report = import_markdown(conn, embedder, source)

    assert report.imported == 1
    record = list_records(conn)[0]
    assert record.title == "Imported Note"
    assert record.project_name == "Echoes"
    assert record.category == "Worldbuilding"
    assert record.source == "import"


def test_import_uses_the_h1_and_folder_when_there_is_no_frontmatter(conn, embedder, tmp_path):
    """What a plain notes folder or an Obsidian vault actually looks like."""
    source = tmp_path / "vault"
    (source / "Sea Stories").mkdir(parents=True)
    (source / "Sea Stories" / "keeper.md").write_text(
        "# The Keeper\n\nHe tends the lantern.", encoding="utf-8"
    )

    report = import_markdown(conn, embedder, source)

    assert report.imported == 1
    record = list_records(conn)[0]
    assert record.title == "The Keeper"
    assert record.project_name == "Sea Stories"
    assert "tends the lantern" in record.body
    assert not record.body.startswith("#")


def test_import_falls_back_to_the_filename_for_a_title(conn, embedder, tmp_path):
    source = tmp_path / "notes"
    source.mkdir()
    (source / "my-great-idea.md").write_text("Just body text, no heading.", encoding="utf-8")

    import_markdown(conn, embedder, source)
    assert list_records(conn)[0].title == "my great idea"


def test_import_recurses_and_skips_empty_files(conn, embedder, tmp_path):
    source = tmp_path / "notes"
    (source / "a" / "b").mkdir(parents=True)
    (source / "top.md").write_text("Top level note.", encoding="utf-8")
    (source / "a" / "mid.md").write_text("Middle note.", encoding="utf-8")
    (source / "a" / "b" / "deep.md").write_text("Deep note.", encoding="utf-8")
    (source / "a" / "empty.md").write_text("   \n\n", encoding="utf-8")

    report = import_markdown(conn, embedder, source)

    assert report.imported == 3
    assert count_records(conn) == 3


def test_import_skips_notes_already_in_the_vault(conn, embedder, tmp_path):
    source = tmp_path / "notes"
    source.mkdir()
    (source / "one.md").write_text(
        "---\ntitle: Same\nproject: P\n---\n\nIdentical body.", encoding="utf-8"
    )

    first = import_markdown(conn, embedder, source)
    second = import_markdown(conn, embedder, source)

    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped_duplicates == 1
    assert count_records(conn) == 1


def test_one_unreadable_file_does_not_abort_the_whole_run(conn, embedder, tmp_path):
    source = tmp_path / "notes"
    source.mkdir()
    (source / "good.md").write_text("A perfectly fine note.", encoding="utf-8")
    (source / "bad.md").write_bytes(b"\xff\xfe\x00broken bytes \xc3\x28")

    report = import_markdown(conn, embedder, source)

    assert report.imported == 1
    assert len(report.failed) == 1
    assert "bad.md" in report.failed[0][0]


def test_import_of_a_missing_folder_says_so(conn, embedder, tmp_path):
    with pytest.raises(FileNotFoundError):
        import_markdown(conn, embedder, tmp_path / "nope")


def test_export_then_import_into_a_fresh_vault_preserves_everything(
    conn, embedder, sample_notes, tmp_path
):
    """The round trip is the promise: your writing is never locked in here."""
    from cortex.db import connect
    from cortex.migrations import ensure_vector_index, migrate

    export_markdown(conn, tmp_path / "export")

    fresh = connect(tmp_path / "fresh.db")
    migrate(fresh)
    ensure_vector_index(fresh, embedder.model, embedder.dim)

    report = import_markdown(fresh, embedder, tmp_path / "export")

    assert report.imported == 3
    original = {(r.title, r.project_name, r.body) for r in list_records(conn)}
    restored = {(r.title, r.project_name, r.body) for r in list_records(fresh)}
    assert original == restored
    fresh.close()


# --- backup ---------------------------------------------------------------


def test_backup_produces_a_readable_vault(conn, embedder, sample_notes, tmp_path):
    target = backup(conn, tmp_path / "backups")

    assert target.exists()
    assert target.suffix == ".db"

    restored = sqlite3.connect(target)
    assert restored.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 3
    restored.close()


def test_backup_captures_writes_that_are_still_in_the_write_ahead_log(
    conn, embedder, sample_notes, tmp_path
):
    """In WAL mode a plain file copy misses recent writes. The backup API doesn't."""
    create_record(conn, embedder, project="Late", title="Just written", body="Fresh content.")

    target = backup(conn, tmp_path / "backups")

    restored = sqlite3.connect(target)
    titles = {r[0] for r in restored.execute("SELECT title FROM records")}
    assert "Just written" in titles
    restored.close()


def test_each_backup_is_a_separate_file(conn, sample_notes, tmp_path):
    first = backup(conn, tmp_path / "backups")
    second = backup(conn, tmp_path / "backups")
    # Same second or not, a backup must never silently overwrite another.
    assert first.exists() and second.exists()
    assert len(list((tmp_path / "backups").glob("*.db"))) >= 1


def test_repeated_round_trips_do_not_mutate_the_note(conn, embedder, sample_notes, tmp_path):
    """Export writes the title as an H1; import must strip it back off, or the
    body grows an extra heading on every cycle."""
    from cortex.db import connect
    from cortex.migrations import ensure_vector_index, migrate

    current = conn
    for cycle in range(3):
        out = tmp_path / f"export{cycle}"
        export_markdown(current, out)

        nxt = connect(tmp_path / f"vault{cycle}.db")
        migrate(nxt)
        ensure_vector_index(nxt, embedder.model, embedder.dim)
        import_markdown(nxt, embedder, out)

        if current is not conn:
            current.close()
        current = nxt

    final = {(r.title, r.body) for r in list_records(current)}
    original = {(r.title, r.body) for r in list_records(conn)}
    assert final == original
    current.close()
