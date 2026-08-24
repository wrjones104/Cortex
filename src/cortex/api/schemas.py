"""Request and response shapes.

Kept separate from the core dataclasses on purpose: the wire format is a
contract with the web and phone clients, and it should be free to stay stable
while the internals move.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import Record, SearchHit


class ProjectOut(BaseModel):
    id: int
    name: str
    slug: str
    record_count: int


class RecordOut(BaseModel):
    id: int
    project: str
    title: str
    body: str
    category: str
    subcategory: str
    source: str
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: Record) -> RecordOut:
        return cls(
            id=record.id,
            project=record.project_name,
            title=record.title,
            body=record.body,
            category=record.category,
            subcategory=record.subcategory,
            source=record.source,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class RecordList(BaseModel):
    records: list[RecordOut]
    total: int
    limit: int
    offset: int


class CaptureIn(BaseModel):
    text: str = Field(min_length=1, description="The raw note.")
    project: str | None = None
    title: str | None = None
    category: str = ""
    subcategory: str = ""
    verbatim: bool = Field(
        default=False, description="Store exactly as written, with no model rewrite."
    )
    allow_duplicate: bool = False
    idempotency_key: str | None = Field(
        default=None,
        description="Client-generated id. Re-sending the same key returns the "
        "original record instead of creating a second one.",
    )


class CaptureOut(BaseModel):
    record: RecordOut
    chunks: int
    warnings: list[str] = []


class RecordPatch(BaseModel):
    project: str | None = None
    title: str | None = None
    body: str | None = None
    category: str | None = None
    subcategory: str | None = None


class SearchHitOut(BaseModel):
    record: RecordOut
    score: float
    snippet: str
    matched_by: str

    @classmethod
    def of(cls, hit: SearchHit) -> SearchHitOut:
        return cls(
            record=RecordOut.of(hit.record),
            score=hit.score,
            snippet=hit.snippet,
            matched_by=hit.matched_by,
        )


class SearchOut(BaseModel):
    query: str
    hits: list[SearchHitOut]


class SyncIn(BaseModel):
    captures: list[CaptureIn] = Field(
        description="A batch of queued captures. Each should carry an "
        "idempotency_key so a retried batch cannot duplicate anything."
    )


class SyncResultItem(BaseModel):
    idempotency_key: str | None
    status: str = Field(
        description=(
            "stored - written now. "
            "already_stored - this idempotency key was seen before; the "
            "original record is returned and nothing new was written. "
            "duplicate - an identical note is already in the project under a "
            "different key. "
            "failed - see detail."
        )
    )
    record: RecordOut | None = None
    detail: str | None = None


class SyncOut(BaseModel):
    results: list[SyncResultItem]
    stored: int
    already_stored: int
    duplicates: int
    failed: int


class ModelStatus(BaseModel):
    role: str
    name: str
    installed: bool


class StatusOut(BaseModel):
    version: str
    records: int
    projects: int
    vault_path: str
    integrity: dict[str, int]
    ollama_reachable: bool
    ollama_detail: str | None = None
    models: list[ModelStatus] = []
