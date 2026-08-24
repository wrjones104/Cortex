"""Plain data shapes passed between layers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    slug: str
    created_at: str
    prompt_override: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Project:
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            created_at=row["created_at"],
            prompt_override=row["prompt_override"],
        )


@dataclass(frozen=True)
class Record:
    id: int
    project_id: int
    project_name: str
    title: str
    body: str
    category: str
    subcategory: str
    source: str
    content_hash: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Record:
        keys = row.keys()
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            project_name=row["project_name"] if "project_name" in keys else "",
            title=row["title"],
            body=row["body"],
            category=row["category"],
            subcategory=row["subcategory"],
            source=row["source"],
            content_hash=row["content_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class SearchHit:
    """One record matched by search, with the evidence for why it ranked."""

    record: Record
    score: float
    snippet: str
    vector_rank: int | None = None
    text_rank: int | None = None

    @property
    def matched_by(self) -> str:
        if self.vector_rank is not None and self.text_rank is not None:
            return "both"
        if self.vector_rank is not None:
            return "meaning"
        return "keyword"


@dataclass
class CaptureResult:
    record: Record
    chunks: int
    duplicate_of: int | None = None
    warnings: list[str] = field(default_factory=list)
