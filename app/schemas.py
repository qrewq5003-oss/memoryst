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
    # Display names of the two participants, used only to tell scene extraction who it is
    # writing about. Optional so an older extension keeps working: without them the model
    # falls back to roles, which is what produced "Девушка"/"Пользователь" as stored facts
    # and entities - phrasings a query never matches.
    character_name: str | None = Field(default=None, max_length=100)
    user_name: str | None = Field(default=None, max_length=100)
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
    # Per-tracker "messages since last update" counters, so the extension can drive its
    # reminder toast off the response it already gets every turn instead of polling.
    # Additive: a client that doesn't know about trackers ignores it.
    trackers: list["TrackerCounter"] = Field(default_factory=list)


# Tracker schemas
class TrackerCounter(BaseModel):
    tracker_type: TrackerType
    messages_since_update: int


class TrackerItem(BaseModel):
    tracker_type: TrackerType
    memory_id: str
    content: str
    entries: list[dict] = Field(default_factory=list)
    updated_at: str
    last_sequence_index: int | None = None
    messages_since_update: int


class ListTrackersResponse(BaseModel):
    items: list[TrackerItem]


class TrackerUpdateRequest(BaseModel):
    chat_id: str = Field(..., min_length=1, max_length=200)
    character_id: str = Field(..., min_length=1, max_length=200)
    tracker_type: TrackerType
    # Same model-override mechanism as /memory/consolidate. No third way to pick a model.
    model: str | None = None
    # Reset the watermark and rebuild the document from the start of the chat.
    full_rebuild: bool = False
    # Who the two participants are. The backend only knows a numeric SillyTavern
    # character_id, and a roleplay assistant narrates itself in the third person, so
    # without these the NPC tracker cannot reliably tell the main character apart from
    # a genuine NPC. Optional: the prompt falls back to role-based wording.
    character_name: str | None = Field(default=None, max_length=200)
    user_name: str | None = Field(default=None, max_length=200)


class TrackerUpdateResponse(BaseModel):
    # "skipped_llm_failed" is deliberately distinct from "skipped_llm_unavailable":
    # collapsing a broken LLM call into "not configured" is precisely what made the
    # scene-extraction failure invisible for so long (see CLAUDE.md).
    action: Literal[
        "created",
        "updated",
        "skipped_no_new_messages",
        "skipped_llm_unavailable",
        "skipped_llm_failed",
    ]
    tracker_type: TrackerType
    content: str = ""
    entries_count: int = 0
    messages_consumed: int = 0
    extraction_method: Literal["llm"] | None = None
    # Human-readable cause, set only for the two skipped_llm_* actions. The UI shows this
    # verbatim next to the tracker instead of a silent/generic failure - see CLAUDE.md's
    # note on the scene-extraction investigation where a swallowed reason hid a real bug.
    error_detail: str | None = None


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
