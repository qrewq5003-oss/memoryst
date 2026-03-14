from fastapi import APIRouter, HTTPException, Query

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

router = APIRouter(prefix="/memory", tags=["memory"])


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


@router.post("/store", response_model=StoreMemoryResponse)
def store_memory_endpoint(request: StoreMemoryRequest) -> StoreMemoryResponse:
    """
    Store memories from chat messages.

    Extracts memory candidates from messages and stores them.
    Duplicates are skipped.
    """
    return store_memories(request)


@router.post("/retrieve", response_model=RetrieveMemoryResponse)
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
