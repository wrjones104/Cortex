"""Import, export and backup.

A knowledge vault you cannot get your writing out of is a trap. Markdown with
YAML frontmatter is the format every other note tool reads, so export is a
real exit and not a gesture. It doubles as the backup you can still read in
ten years when nothing runs this code any more.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .db import connect
from .embed import Embedder
from .store import DuplicateRecordError, create_record, list_records, slugify

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class ImportReport:
    imported: int = 0
    skipped_duplicates: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.imported + self.skipped_duplicates + len(self.failed)


def safe_filename(text: str, max_length: int = 60) -> str:
    cleaned = _UNSAFE_FILENAME.sub("", text).strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:max_length].strip() or "untitled").rstrip(".")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a Markdown file into its frontmatter mapping and its body.

    A file with no frontmatter, or with frontmatter that is not a mapping, is
    treated as plain Markdown rather than rejected - most notes you might
    import were never written for this tool.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, text

    if not isinstance(meta, dict):
        return {}, text
    return meta, match.group(2)


def export_markdown(conn: sqlite3.Connection, destination: Path | str) -> int:
    """Write every record as a Markdown file, grouped into project folders."""
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)

    written = 0
    offset = 0
    while True:
        batch = list_records(conn, limit=200, offset=offset)
        if not batch:
            break
        offset += len(batch)

        for record in batch:
            folder = root / slugify(record.project_name)
            folder.mkdir(parents=True, exist_ok=True)

            meta = {
                "id": record.id,
                "title": record.title,
                "project": record.project_name,
                "category": record.category,
                "subcategory": record.subcategory,
                "source": record.source,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }
            front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
            payload = f"---\n{front}\n---\n\n# {record.title}\n\n{record.body}\n"

            path = folder / f"{record.id:05d}-{safe_filename(record.title)}.md"
            path.write_text(payload, encoding="utf-8")
            written += 1

    return written


def import_markdown(
    conn: sqlite3.Connection,
    embedder: Embedder,
    source: Path | str,
    *,
    default_project: str = "Imported",
    chunk_options: dict | None = None,
    progress=None,
) -> ImportReport:
    """Ingest a folder of Markdown files, recursively.

    Frontmatter is used where present. Where it isn't, the H1 or the filename
    becomes the title and the containing folder becomes the project - which is
    what an Obsidian vault or a plain notes folder tends to look like.
    """
    root = Path(source)
    report = ImportReport()

    if not root.exists():
        raise FileNotFoundError(f"No such folder: {root}")

    files = sorted(root.rglob("*.md")) if root.is_dir() else [root]

    for path in files:
        try:
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            body = body.strip()
            if not body:
                continue

            title = str(meta.get("title") or "").strip()
            heading = re.match(r"\A#\s+(.+)", body)

            if not title:
                if heading:
                    title = heading.group(1).strip()
                    body = body[heading.end() :].strip()
                else:
                    title = path.stem.replace("-", " ").replace("_", " ").strip()
            elif heading and heading.group(1).strip() == title:
                # Our own export writes the title as an H1 so the file reads
                # well in any Markdown viewer. Strip it back off on the way in,
                # or a note grows an extra heading every round trip.
                body = body[heading.end() :].strip()

            project = str(meta.get("project") or "").strip()
            if not project:
                if root.is_dir() and path.parent != root:
                    project = path.parent.name.replace("-", " ").replace("_", " ").title()
                else:
                    project = default_project

            create_record(
                conn,
                embedder,
                project=project,
                title=title,
                body=body,
                category=str(meta.get("category") or "").strip(),
                subcategory=str(meta.get("subcategory") or "").strip(),
                source="import",
                chunk_options=chunk_options,
            )
            report.imported += 1

        except DuplicateRecordError:
            report.skipped_duplicates += 1
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            report.failed.append((str(path), f"{type(exc).__name__}: {exc}"))

        if progress is not None:
            progress(report, path)

    return report


def backup(conn: sqlite3.Connection, backup_dir: Path | str) -> Path:
    """Take a consistent copy of the vault using SQLite's online backup API.

    Copying the file by hand is not equivalent: in WAL mode the database is
    spread across the main file and the write-ahead log, so a plain copy taken
    mid-write is a corrupt vault.
    """
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"cortex-{stamp}.db"

    destination = connect(target)
    try:
        conn.backup(destination)
    finally:
        destination.close()

    return target
