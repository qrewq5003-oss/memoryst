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
    MessageInput,
    RetrieveMemoryRequest,
    RetrieveMemoryResponse,
    StoreMemoryRequest,
    StoreMemoryResponse,
    UpdateMemoryRequest,
)
from app.services.retrieve_service import retrieve_memories
from app.services.store_service import store_memories

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


def _parse_messages(value: str) -> list[MessageInput]:
    """Parse textarea input into user messages, one non-empty line per message."""
    messages = []
    for line in value.splitlines():
        text = line.strip()
        if not text:
            continue
        messages.append(MessageInput(role="user", text=text))
    return messages


def _render_memories_page(
    request: Request,
    *,
    chat_id: str | None = None,
    character_id: str | None = None,
    type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    archived: str | None = None,
    pinned: str | None = None,
    limit: int = 50,
    offset: int = 0,
    store_result: StoreMemoryResponse | None = None,
    retrieve_result: RetrieveMemoryResponse | None = None,
    store_form: dict[str, Any] | None = None,
    retrieve_form: dict[str, Any] | None = None,
) -> Any:
    """Render the memories page with optional store/retrieve diagnostics sections."""
    chat_id = chat_id or None
    character_id = character_id or None
    type = type or None
    source = source or None
    layer = layer or None

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

    return templates.TemplateResponse(
        request,
        "memories.html",
        {
            "memories": memories.model_dump(),
            "filters": filters,
            "store_result": store_result.model_dump() if store_result else None,
            "retrieve_result": retrieve_result.model_dump() if retrieve_result else None,
            "store_form": store_form or {
                "chat_id": "",
                "character_id": "",
                "messages": "",
                "debug": False,
            },
            "retrieve_form": retrieve_form or {
                "chat_id": "",
                "character_id": "",
                "user_input": "",
                "recent_messages": "",
                "limit": 5,
                "include_archived": False,
                "debug": False,
            },
        },
    )


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
    return _render_memories_page(
        request,
        chat_id=chat_id,
        character_id=character_id,
        type=type,
        source=source,
        layer=layer,
        archived=archived,
        pinned=pinned,
        limit=limit,
        offset=offset,
    )


@router.post("/ui/store")
def ui_store_memories(
    request: Request,
    chat_id: str = Form(...),
    character_id: str = Form(...),
    messages: str = Form(...),
    debug: bool = Form(False),
) -> Any:
    """Run store pipeline from the admin UI and render results inline."""
    store_request = StoreMemoryRequest(
        chat_id=chat_id,
        character_id=character_id,
        messages=_parse_messages(messages),
        debug=debug,
    )
    result = store_memories(store_request)
    return _render_memories_page(
        request,
        chat_id=chat_id,
        character_id=character_id,
        store_result=result,
        store_form={
            "chat_id": chat_id,
            "character_id": character_id,
            "messages": messages,
            "debug": debug,
        },
        retrieve_form={
            "chat_id": chat_id,
            "character_id": character_id,
            "user_input": "",
            "recent_messages": "",
            "limit": 5,
            "include_archived": False,
            "debug": False,
        },
    )


@router.post("/ui/retrieve")
def ui_retrieve_memories(
    request: Request,
    chat_id: str = Form(...),
    character_id: str = Form(...),
    user_input: str = Form(...),
    recent_messages: str = Form(""),
    limit: int = Form(5),
    include_archived: bool = Form(False),
    debug: bool = Form(False),
) -> Any:
    """Run retrieval pipeline from the admin UI and render results inline."""
    retrieve_request = RetrieveMemoryRequest(
        chat_id=chat_id,
        character_id=character_id,
        user_input=user_input,
        recent_messages=_parse_messages(recent_messages),
        limit=limit,
        include_archived=include_archived,
        debug=debug,
    )
    result = retrieve_memories(retrieve_request)
    return _render_memories_page(
        request,
        chat_id=chat_id,
        character_id=character_id,
        retrieve_result=result,
        store_form={
            "chat_id": chat_id,
            "character_id": character_id,
            "messages": "",
            "debug": False,
        },
        retrieve_form={
            "chat_id": chat_id,
            "character_id": character_id,
            "user_input": user_input,
            "recent_messages": recent_messages,
            "limit": limit,
            "include_archived": include_archived,
            "debug": debug,
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
