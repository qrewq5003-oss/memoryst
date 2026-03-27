from typing import Literal

from pydantic import BaseModel, Field


# Literal types
MemoryType = Literal["profile", "relationship", "event", "summary"]
MemorySource = Literal["auto", "manual"]
MemoryLayer = Literal["episodic", "stable"]


# Input schemas
class MessageInput(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str = Field(..., min_length=1)


class ConsolidationHistoryEntry(BaseModel):
    action: str
    timestamp: str
    related_memory_id: str | None = None
    note: str | None = None


class MemoryMetadata(BaseModel):
    entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    is_summary: bool = False
    summary_kind: str | None = None
    summary_generated_at: str | None = None
    summary_source_memory_ids: list[str] = Field(default_factory=list)
    summarized_memory_count: int | None = None
    consolidation_note: str | None = None
    related_memory_id: str | None = None
    review_status: str | None = None
    consolidation_history: list[ConsolidationHistoryEntry] = Field(default_factory=list)


class CreateMemoryRequest(BaseModel):
    chat_id: str
    character_id: str
    type: MemoryType
    content: str
    source: MemorySource = "manual"
    layer: MemoryLayer
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    pinned: bool = False
    archived: bool = False
    metadata: MemoryMetadata = Field(default_factory=MemoryMetadata)


class UpdateMemoryRequest(BaseModel):
    content: str | None = None
    type: MemoryType | None = None
    source: MemorySource | None = None
    layer: MemoryLayer | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    pinned: bool | None = None
    archived: bool | None = None
    metadata: MemoryMetadata | None = None


class PinMemoryRequest(BaseModel):
    pinned: bool


class ArchiveMemoryRequest(BaseModel):
    archived: bool


# Output schemas
class MemoryItem(BaseModel):
    id: str
    chat_id: str
    character_id: str
    type: MemoryType
    content: str
    normalized_content: str
    source: MemorySource
    layer: MemoryLayer
    importance: float
    created_at: str
    updated_at: str
    last_accessed_at: str | None = None
    access_count: int
    pinned: bool
    archived: bool
    metadata: MemoryMetadata


class ListMemoriesResponse(BaseModel):
    items: list[MemoryItem]
    total: int
    limit: int
    offset: int


class CreateMemoryResponse(BaseModel):
    item: MemoryItem


class UpdateMemoryResponse(BaseModel):
    item: MemoryItem


class PinMemoryResponse(BaseModel):
    id: str
    pinned: bool


class ArchiveMemoryResponse(BaseModel):
    id: str
    archived: bool


class DeleteMemoryResponse(BaseModel):
    id: str
    deleted: bool


class StoreCandidateDebug(BaseModel):
    content: str
    normalized_content: str | None = None
    decision: str
    reason: str
    branch: str
    matched_existing_id: str | None = None


class StoreDebugPayload(BaseModel):
    candidates: list[StoreCandidateDebug] = Field(default_factory=list)


# Store schemas
class StoreMemoryRequest(BaseModel):
    chat_id: str
    character_id: str
    messages: list[MessageInput]
    debug: bool = False


class StoreMemoryResponse(BaseModel):
    stored: int
    updated: int
    skipped: int
    items: list[MemoryItem]
    debug: StoreDebugPayload | None = None


class RetrieveCandidateDebug(BaseModel):
    memory_id: str
    layer: str
    score: float
    keyword_overlap: float
    entity_overlap: float
    recency: float
    passed_threshold: bool
    filtered_by_diversity: bool = False
    selected: bool = False
    selected_from_layer: str | None = None
    rank: int | None = None
    reason: str


class RetrieveDebugPayload(BaseModel):
    query_keywords: list[str] = Field(default_factory=list)
    query_entities: list[str] = Field(default_factory=list)
    recent_keywords: list[str] = Field(default_factory=list)
    recent_entities: list[str] = Field(default_factory=list)
    input_keywords: list[str] = Field(default_factory=list)
    input_entities: list[str] = Field(default_factory=list)
    summary_candidates: int = 0
    stable_candidates: int = 0
    episodic_candidates: int = 0
    selected_summary: int = 0
    selected_stable: int = 0
    selected_episodic: int = 0
    candidates: list[RetrieveCandidateDebug] = Field(default_factory=list)


# Retrieve schemas
class RetrieveMemoryRequest(BaseModel):
    chat_id: str
    character_id: str
    user_input: str
    recent_messages: list[MessageInput] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)
    include_archived: bool = False
    debug: bool = False


class RetrieveMemoryResponse(BaseModel):
    items: list[MemoryItem]
    memory_block: str
    total_candidates: int
    debug: RetrieveDebugPayload | None = None
