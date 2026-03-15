from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

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
    CreateMemoryRequest,
    MemoryMetadata,
    UpdateMemoryRequest,
)

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["ui"])


def _parse_list(value: str) -> list[str]:
    """Parse comma-separated string into list."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _build_query_string(params: dict[str, Any]) -> str:
    """Build query string from params, excluding empty values."""
    return urlencode({k: v for k, v in params.items() if v not in (None, "")})


@router.get("/ui")
def ui_memories_page(
    request: Request,
    chat_id: str | None = None,
    character_id: str | None = None,
    type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    archived: str | None = None,
    pinned: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """Render memories page with filters."""
    # Convert string booleans from query params
    if archived == "true":
        archived_bool = True
    elif archived == "false":
        archived_bool = False
    else:
        archived_bool = None

    if pinned == "true":
        pinned_bool = True
    elif pinned == "false":
        pinned_bool = False
    else:
        pinned_bool = None

    memories = list_memories(
        chat_id=chat_id,
        character_id=character_id,
        memory_type=type,
        source=source,
        layer=layer,
        archived=archived_bool,
        pinned=pinned_bool,
        limit=limit,
        offset=offset,
    )

    filters = {
        "chat_id": chat_id,
        "character_id": character_id,
        "type": type,
        "source": source,
        "layer": layer,
        "archived": archived,
        "pinned": pinned,
        "limit": limit,
        "offset": offset,
        "query_string": _build_query_string({
            "chat_id": chat_id,
            "character_id": character_id,
            "type": type,
            "source": source,
            "layer": layer,
            "archived": archived,
            "pinned": pinned,
            "limit": limit,
        }),
    }

    # Convert Pydantic models to dict for Jinja2 template
    # model_dump() recursively converts nested models (MemoryItem, MemoryMetadata)
    return templates.TemplateResponse(
        "memories.html",
        {
            "request": request,
            "memories": memories.model_dump(),
            "filters": filters,
        },
    )


@router.post("/ui/create")
def ui_create_memory(
    chat_id: str = Form(...),
    character_id: str = Form(...),
    type: str = Form(...),
    content: str = Form(...),
    source: str = Form("manual"),
    layer: str = Form(...),
    importance: float = Form(0.5),
    pinned: bool = Form(False),
    archived: bool = Form(False),
    entities: str = Form(""),
    keywords: str = Form(""),
) -> RedirectResponse:
    """Create a new memory and redirect back to UI."""
    request = CreateMemoryRequest(
        chat_id=chat_id,
        character_id=character_id,
        type=type,  # type: ignore
        content=content,
        source=source,  # type: ignore
        layer=layer,  # type: ignore
        importance=min(max(importance, 0.0), 1.0),
        pinned=pinned,
        archived=archived,
        metadata=MemoryMetadata(
            entities=_parse_list(entities),
            keywords=_parse_list(keywords),
        ),
    )
    create_memory(request)
    return RedirectResponse(url="/ui", status_code=303)


@router.post("/ui/{memory_id}/update")
def ui_update_memory(
    memory_id: str,
    content: str = Form(...),
    type: str = Form(...),
    source: str = Form(...),
    layer: str = Form(...),
    importance: float = Form(0.5),
    pinned: bool = Form(False),
    archived: bool = Form(False),
    entities: str = Form(""),
    keywords: str = Form(""),
) -> RedirectResponse:
    """Update a memory and redirect back to UI."""
    request = UpdateMemoryRequest(
        content=content,
        type=type,  # type: ignore
        source=source,  # type: ignore
        layer=layer,  # type: ignore
        importance=min(max(importance, 0.0), 1.0),
        pinned=pinned,
        archived=archived,
        metadata=MemoryMetadata(
            entities=_parse_list(entities),
            keywords=_parse_list(keywords),
        ),
    )
    update_memory(memory_id, request)
    return RedirectResponse(url="/ui", status_code=303)


@router.post("/ui/{memory_id}/pin")
def ui_toggle_pin(memory_id: str) -> RedirectResponse:
    """Toggle pinned status and redirect back to UI."""
    memory = get_memory_by_id(memory_id)
    if memory:
        set_pinned(memory_id, not memory.pinned)
    return RedirectResponse(url="/ui", status_code=303)


@router.post("/ui/{memory_id}/archive")
def ui_toggle_archive(memory_id: str) -> RedirectResponse:
    """Toggle archived status and redirect back to UI."""
    memory = get_memory_by_id(memory_id)
    if memory:
        set_archived(memory_id, not memory.archived)
    return RedirectResponse(url="/ui", status_code=303)


@router.post("/ui/{memory_id}/delete")
def ui_delete_memory(memory_id: str) -> RedirectResponse:
    """Delete a memory and redirect back to UI."""
    delete_memory(memory_id)
    return RedirectResponse(url="/ui", status_code=303)
