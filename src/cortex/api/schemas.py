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
    expected_updated_at: str | None = Field(
        default=None,
        description="The updated_at you last saw. Send it and the edit is "
        "refused with 409 if the record changed elsewhere; omit it to "
        "overwrite deliberately.",
    )


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


class ModelInfo(BaseModel):
    name: str
    parameter_size: str | None = None
    capabilities: list[str] = []
    can_chat: bool
    can_embed: bool
    can_think: bool


class SettingsOut(BaseModel):
    librarian_model: str
    creative_model: str
    utility_model: str = ""
    embed_model: str
    embed_model_locked: bool = Field(
        default=True,
        description="The embedding model is fixed by the vector index. "
        "Changing it invalidates every vector, so it needs `cortex reindex`.",
    )


class SettingsPatch(BaseModel):
    librarian_model: str | None = None
    creative_model: str | None = None
    utility_model: str | None = None


class ThreadOut(BaseModel):
    id: int
    title: str
    project: str | None
    message_count: int
    has_summary: bool
    fact_count: int
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    sources: list[str] = []
    created_at: str


class ThreadDetail(BaseModel):
    thread: ThreadOut
    messages: list[MessageOut]
    facts: list[str]


class ThreadCreate(BaseModel):
    title: str | None = None
    project: str | None = None


class ThreadPatch(BaseModel):
    title: str | None = None
    project: str | None = Field(
        default=None,
        description="Search scope. Changing it writes a visible marker into "
        "the transcript rather than changing silently.",
    )
    clear_project: bool = Field(
        default=False, description="Set the scope back to all projects."
    )


class AskIn(BaseModel):
    message: str = Field(min_length=1)


class IdeaOut(BaseModel):
    ordinal: int
    title: str
    pitch: str
    detail: str
    banked: bool
    banked_record_id: int | None = None


class GenerationOut(BaseModel):
    id: int
    prompt: str
    project: str | None
    model: str
    mode: str
    output: str
    created_at: str
    ideas: list[IdeaOut]


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=1)
    mode: str = Field(default="options", description="options | freeform")
    count: int = Field(default=4, ge=1, le=10)
    project: str | None = None
    use_context: bool = True


class BankIn(BaseModel):
    ordinals: list[int] = Field(description="Which ideas to file, by ordinal.")
    project: str | None = None
    verbatim: bool = Field(
        default=True,
        description="Store the idea as written. Set false to let the Librarian "
        "retitle and categorise it first.",
    )


class BankOut(BaseModel):
    banked: list[RecordOut]
    skipped: list[dict]
