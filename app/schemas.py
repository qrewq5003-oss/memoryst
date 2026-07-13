from typing import Literal

from pydantic import BaseModel, Field


# Literal types
MemoryType = Literal["profile", "relationship", "event", "summary", "tracker"]
TrackerType = Literal["timeline", "relationship", "npc_whoswho", "character_pov_notes"]
MemorySource = Literal["auto", "manual"]
MemoryLayer = Literal["episodic", "stable"]


# Input schemas
class MessageInput(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str = Field(..., min_length=1, max_length=10000)


class ChatMessageItem(BaseModel):
    """A single raw chat message, either still in the hot buffer or cooled into chat_messages."""

    id: str
    chat_id: str
    character_id: str
    role: Literal["user", "assistant"]
    text: str
    created_at: str
    sequence_index: int


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
    source_message_ids: list[str] = Field(default_factory=list)
    consolidation_note: str | None = None
    related_memory_id: str | None = None
    review_status: str | None = None
    consolidation_history: list[ConsolidationHistoryEntry] = Field(default_factory=list)

    # Trackers (type='tracker'). A tracker is rewritten in place on every update, so
    # tracker_last_sequence_index is a per-tracker watermark: the sequence_index of the
    # newest chat message already folded into the document.
    tracker_type: TrackerType | None = None
    tracker_generated_at: str | None = None
    tracker_last_sequence_index: int | None = None
    tracker_entries: list[dict] | None = None


class CreateMemoryRequest(BaseModel):
    chat_id: str = Field(..., min_length=1, max_length=200)
    character_id: str = Field(..., min_length=1, max_length=200)
    type: MemoryType
    content: str = Field(..., min_length=1, max_length=5000)
    source: MemorySource = "manual"
    layer: MemoryLayer
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    pinned: bool = False
    archived: bool = False
    metadata: MemoryMetadata = Field(default_factory=MemoryMetadata)


class UpdateMemoryRequest(BaseModel):
    content: str | None = Field(default=None, max_length=5000)
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
    chat_id: str = Field(..., min_length=1, max_length=200)
    character_id: str = Field(..., min_length=1, max_length=200)
    messages: list[MessageInput]
    debug: bool = False
    # Overrides the active provider's default model for scene extraction only
    # (see sillytavern-extension's "Scene Extraction Model" setting). None keeps
    # prior behavior: the active provider's configured default model is used.
    model: str | None = None


class StoreMemoryResponse(BaseModel):
    stored: int
    updated: int
    skipped: int
    items: list[MemoryItem]
    debug: StoreDebugPayload | None = None
    # "llm": scene_extractor's LLM call ran and parsed (even if it legitimately
    # found nothing). "regex_fallback": the LLM was skipped/disabled or the call
    # failed and the cruder rule-based extractor ran instead. None: no messages
    # to extract from at all. See scene_extractor.extract_scene_memories.
    extraction_method: Literal["llm", "regex_fallback"] | None = None


class RetrieveCandidateDebug(BaseModel):
    memory_id: str
    layer: str
    score: float
    keyword_overlap: float
    entity_overlap: float
    relationship_cue_overlap: float = 0.0
    relationship_support_bonus: float = 0.0
    episodic_detail_score: float = 0.0
    episodic_specificity_bonus: float = 0.0
    episodic_low_value_penalty: float = 0.0
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
    relationship_query_like: bool = False
    local_scene_query_like: bool = False
    query_relationship_cues: list[str] = Field(default_factory=list)
    recent_relationship_cues: list[str] = Field(default_factory=list)
    input_relationship_cues: list[str] = Field(default_factory=list)
    summary_candidates: int = 0
    stable_candidates: int = 0
    episodic_candidates: int = 0
    selected_summary: int = 0
    selected_stable: int = 0
    selected_episodic: int = 0
    candidates: list[RetrieveCandidateDebug] = Field(default_factory=list)


# Retrieve schemas
class RetrieveMemoryRequest(BaseModel):
    chat_id: str = Field(..., min_length=1, max_length=200)
    character_id: str = Field(..., min_length=1, max_length=200)
    user_input: str = Field(..., min_length=1, max_length=2000)
    recent_messages: list[MessageInput] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)
    include_archived: bool = False
    debug: bool = False
    # Manual raw-history trigger (Stage 5): caller already has a consolidated
    # memory's metadata.source_message_ids (e.g. from a previous retrieve
    # response) and explicitly wants the original raw messages behind it.
    manual_source_message_ids: list[str] = Field(default_factory=list, max_length=50)


class RawFallbackResult(BaseModel):
    """Raw chat_messages surfaced as a fallback/supplement to consolidated memory.

    Kept separate from `items` so callers never confuse raw, unconsolidated
    history with vetted memory.
    """

    trigger: Literal["automatic", "manual"]
    query: str | None = None
    messages: list[ChatMessageItem] = Field(default_factory=list)


class RetrieveMemoryResponse(BaseModel):
    items: list[MemoryItem]
    memory_block: str
    total_candidates: int
    debug: RetrieveDebugPayload | None = None
    raw_fallback: list[RawFallbackResult] = Field(default_factory=list)
