from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_api_key
from app.repositories.memory_repo import (
    create_memory,
    delete_memory,
    get_memory_by_id,
    list_memories,
    set_archived,
    set_pinned,
    update_memory,
)
from app.schemas import (
    ArchiveMemoryRequest,
    ArchiveMemoryResponse,
    CreateMemoryRequest,
    CreateMemoryResponse,
    DeleteMemoryResponse,
    ListMemoriesResponse,
    MemoryItem,
    MessageInput,
    PinMemoryRequest,
    PinMemoryResponse,
    RetrieveMemoryRequest,
    RetrieveMemoryResponse,
    StoreMemoryRequest,
    StoreMemoryResponse,
    UpdateMemoryRequest,
    UpdateMemoryResponse,
)
from app.services.retrieve_service import retrieve_memories
from app.services.store_service import store_memories
from app.services import vector_store

router = APIRouter(
    prefix="/memory",
    tags=["memory"],
    dependencies=[Depends(require_api_key)],
)


@router.post("/create", response_model=CreateMemoryResponse)
def create_memory_endpoint(request: CreateMemoryRequest) -> CreateMemoryResponse:
    """Create a new memory record."""
    memory = create_memory(request)
    return CreateMemoryResponse(item=memory)


@router.get("/list", response_model=ListMemoriesResponse)
def list_memories_endpoint(
    chat_id: str | None = Query(default=None),
    character_id: str | None = Query(default=None),
    memory_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    layer: str | None = Query(default=None),
    archived: bool | None = Query(default=None),
    pinned: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ListMemoriesResponse:
    """List memories with optional filters."""
    return list_memories(
        chat_id=chat_id,
        character_id=character_id,
        memory_type=memory_type,
        source=source,
        layer=layer,
        archived=archived,
        pinned=pinned,
        limit=limit,
        offset=offset,
    )


@router.post("/store", response_model=StoreMemoryResponse, response_model_exclude_none=True)
def store_memory_endpoint(request: StoreMemoryRequest) -> StoreMemoryResponse:
    """
    Store memories from chat messages.

    Extracts memory candidates from messages and stores them.
    Duplicates are skipped.
    """
    return store_memories(request)


@router.post("/retrieve", response_model=RetrieveMemoryResponse, response_model_exclude_none=True)
def retrieve_memory_endpoint(request: RetrieveMemoryRequest) -> RetrieveMemoryResponse:
    """
    Retrieve relevant memories for the current context.

    Scores memories by keyword/entity overlap, importance, and recency.
    Returns top-k results with formatted memory block.
    """
    return retrieve_memories(request)


@router.get("/{id}", response_model=MemoryItem)
def get_memory_endpoint(id: str) -> MemoryItem:
    """Get a memory record by ID."""
    memory = get_memory_by_id(id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.patch("/{id}", response_model=UpdateMemoryResponse)
def update_memory_endpoint(id: str, request: UpdateMemoryRequest) -> UpdateMemoryResponse:
    """Update a memory record."""
    memory = update_memory(id, request)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return UpdateMemoryResponse(item=memory)


@router.post("/{id}/pin", response_model=PinMemoryResponse)
def pin_memory_endpoint(id: str, request: PinMemoryRequest) -> PinMemoryResponse:
    """Set the pinned status of a memory."""
    result = set_pinned(id, request.pinned)
    if not result:
        raise HTTPException(status_code=404, detail="Memory not found")
    return PinMemoryResponse(id=id, pinned=request.pinned)


@router.post("/{id}/archive", response_model=ArchiveMemoryResponse)
def archive_memory_endpoint(id: str, request: ArchiveMemoryRequest) -> ArchiveMemoryResponse:
    """Set the archived status of a memory."""
    result = set_archived(id, request.archived)
    if not result:
        raise HTTPException(status_code=404, detail="Memory not found")
    return ArchiveMemoryResponse(id=id, archived=request.archived)


@router.delete("/{id}", response_model=DeleteMemoryResponse)
def delete_memory_endpoint(id: str) -> DeleteMemoryResponse:
    """Delete a memory record."""
    result = delete_memory(id)
    if not result:
        raise HTTPException(status_code=404, detail="Memory not found")
    return DeleteMemoryResponse(id=id, deleted=True)


class AddKeyRequest(BaseModel):
    key: str


class RemoveKeyRequest(BaseModel):
    key: str


class KeyStatus(BaseModel):
    masked: str
    active: bool


@router.get("/keys", response_model=list[KeyStatus])
def list_keys_endpoint() -> list[KeyStatus]:
    """List all Google API keys (masked)."""
    return [KeyStatus(**k) for k in vector_store.list_keys()]


@router.post("/keys")
def add_key_endpoint(request: AddKeyRequest) -> dict:
    """Add a new Google API key to the pool."""
    vector_store.add_key(request.key)
    return {"status": "added", "total_keys": vector_store.get_key_count()}


@router.delete("/keys")
def remove_key_endpoint(request: RemoveKeyRequest) -> dict:
    """Remove a Google API key from the pool."""
    removed = vector_store.remove_key(request.key)
    if not removed:
        raise HTTPException(status_code=400, detail="Key not found or cannot remove last key")
    return {"status": "removed", "total_keys": vector_store.get_key_count()}


class SummarizeRequest(BaseModel):
    chat_id: str
    character_id: str
    window_size: int = 8
    min_new: int = 3


class SummarizeResponse(BaseModel):
    action: str
    summary_memory_id: str | None = None
    summary_text: str = ""
    summarized_count: int = 0
    new_input_count: int = 0


@router.post("/summarize", response_model=SummarizeResponse)
def summarize_endpoint(request: SummarizeRequest) -> SummarizeResponse:
    """Generate or update a rolling summary for a chat/character."""
    from app.services.summary_service import generate_rolling_summary

    result = generate_rolling_summary(
        chat_id=request.chat_id,
        character_id=request.character_id,
        window_size=request.window_size,
        min_new_memories_for_refresh=request.min_new,
    )
    return SummarizeResponse(
        action=result.action,
        summary_memory_id=result.summary_memory_id,
        summary_text=result.summary_text,
        summarized_count=result.summarized_count,
        new_input_count=result.new_input_count,
    )


class BackfillRequest(BaseModel):
    chat_id: str
    character_id: str
    messages: list[MessageInput]


class BackfillResponse(BaseModel):
    processed: int
    stored: int
    skipped: int
    duplicates: int


@router.post("/backfill", response_model=BackfillResponse)
def backfill_endpoint(request: BackfillRequest) -> BackfillResponse:
    """Backfill memories from existing chat history."""
    from app.services.extractor import extract_memories
    from app.repositories.memory_repo import (
        create_memory,
        find_memory_by_normalized_content,
    )
    from app.services.text_utils import normalize_content
    from app.services.store_service import passes_memory_quality_gate
    from app.services import vector_store

    candidates = extract_memories(
        chat_id=request.chat_id,
        character_id=request.character_id,
        messages=request.messages,
    )

    stored = 0
    skipped = 0
    duplicates = 0

    for candidate in candidates:
        if not passes_memory_quality_gate(candidate):
            skipped += 1
            continue

        normalized = normalize_content(candidate.content)
        existing = find_memory_by_normalized_content(
            chat_id=request.chat_id,
            character_id=request.character_id,
            normalized_content=normalized,
        )

        if existing is not None:
            duplicates += 1
            continue

        created = create_memory(candidate)
        stored += 1

        vector_store.add_memory(
            created.id,
            created.content,
            {"chat_id": created.chat_id, "character_id": created.character_id},
        )

    return BackfillResponse(
        processed=len(request.messages),
        stored=stored,
        skipped=skipped,
        duplicates=duplicates,
    )
