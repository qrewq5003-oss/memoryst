from datetime import datetime, timezone
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
    ListMemoriesResponse,
    MemoryMetadata,
    MemoryItem,
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

UI_SEARCH_SCAN_LIMIT = 2000


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(value: str | None) -> int | None:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return None
    return max((_utc_now() - parsed).days, 0)


def _get_freshness_bucket(memory: MemoryItem) -> str:
    updated_days = _days_since(memory.updated_at)
    if updated_days is None or updated_days <= 7:
        return "fresh"
    if updated_days <= 30:
        return "warm"
    return "stale"


def _get_activity_bucket(memory: MemoryItem) -> str:
    accessed_days = _days_since(memory.last_accessed_at)
    if memory.access_count <= 0 or memory.last_accessed_at is None:
        return "never_used"
    if memory.access_count >= 5 or (accessed_days is not None and accessed_days <= 14):
        return "active"
    return "low_use"


def _get_touch_state(memory: MemoryItem) -> str:
    updated_days = _days_since(memory.updated_at)
    accessed_days = _days_since(memory.last_accessed_at)
    if accessed_days is not None and accessed_days <= 14:
        return "recently_accessed"
    if updated_days is not None and updated_days <= 14:
        return "recently_updated"
    if memory.access_count <= 0 and updated_days is not None and updated_days > 30:
        return "stale_unused"
    return "quiet"


def _build_memory_card(memory: MemoryItem) -> dict[str, Any]:
    updated_days = _days_since(memory.updated_at)
    accessed_days = _days_since(memory.last_accessed_at)
    return {
        **memory.model_dump(),
        "freshness": _get_freshness_bucket(memory),
        "activity": _get_activity_bucket(memory),
        "touch_state": _get_touch_state(memory),
        "updated_days": updated_days,
        "accessed_days": accessed_days,
    }


def _matches_memory_search(memory: MemoryItem, search: str) -> bool:
    """Apply a simple text search across memory content and metadata signals."""
    query = " ".join(search.lower().split())
    if not query:
        return True

    haystacks = [
        memory.id,
        memory.content,
        memory.normalized_content,
        memory.type,
        memory.source,
        memory.layer,
        " ".join(memory.metadata.entities),
        " ".join(memory.metadata.keywords),
    ]
    return query in " ".join(haystacks).lower()


def _sort_memories(items: list[MemoryItem], sort: str) -> list[MemoryItem]:
    if sort == "last_accessed_desc":
        return sorted(
            items,
            key=lambda item: (
                _parse_iso_datetime(item.last_accessed_at) or datetime.min.replace(tzinfo=timezone.utc),
                _parse_iso_datetime(item.updated_at) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
    if sort == "access_count_desc":
        return sorted(
            items,
            key=lambda item: (item.access_count, _parse_iso_datetime(item.last_accessed_at) or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
    if sort == "stalest_first":
        return sorted(
            items,
            key=lambda item: (
                _parse_iso_datetime(item.updated_at) or _utc_now(),
                _parse_iso_datetime(item.last_accessed_at) or _utc_now(),
            ),
        )
    return sorted(
        items,
        key=lambda item: _parse_iso_datetime(item.updated_at) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _filter_and_page_memories(
    items: list[MemoryItem],
    search: str | None,
    freshness: str | None,
    activity: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> ListMemoriesResponse:
    """Apply UI-only filters and sorting, then paginate the filtered list."""
    filtered_items = list(items)
    if search:
        filtered_items = [item for item in filtered_items if _matches_memory_search(item, search)]
    if freshness:
        filtered_items = [item for item in filtered_items if _get_freshness_bucket(item) == freshness]
    if activity:
        filtered_items = [item for item in filtered_items if _get_activity_bucket(item) == activity]

    filtered_items = _sort_memories(filtered_items, sort)
    paginated_items = filtered_items[offset: offset + limit]
    return ListMemoriesResponse(
        items=paginated_items,
        total=len(filtered_items),
        limit=limit,
        offset=offset,
    )


def _sorted_breakdown(items: dict[str, int]) -> list[dict[str, Any]]:
    """Convert a counter dict into a stable list for template rendering."""
    return [
        {"label": label, "count": count}
        for label, count in sorted(items.items())
    ]


def _build_store_summary(store_result: StoreMemoryResponse | None) -> dict[str, Any] | None:
    """Build compact aggregate summary for a store run."""
    if store_result is None:
        return None

    summary = {
        "stored": store_result.stored,
        "updated": store_result.updated,
        "skipped": store_result.skipped,
        "debug_breakdown": None,
    }

    if store_result.debug is None:
        return summary

    decision_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    branch_counts: dict[str, int] = {}

    for candidate in store_result.debug.candidates:
        decision_counts[candidate.decision] = decision_counts.get(candidate.decision, 0) + 1
        reason_counts[candidate.reason] = reason_counts.get(candidate.reason, 0) + 1
        branch_counts[candidate.branch] = branch_counts.get(candidate.branch, 0) + 1

    summary["debug_breakdown"] = {
        "decisions": _sorted_breakdown(decision_counts),
        "reasons": _sorted_breakdown(reason_counts),
        "branches": _sorted_breakdown(branch_counts),
    }
    return summary


def _build_retrieve_summary(retrieve_result: RetrieveMemoryResponse | None) -> dict[str, Any] | None:
    """Build compact aggregate summary for a retrieval run."""
    if retrieve_result is None:
        return None

    summary = {
        "total_candidates": retrieve_result.total_candidates,
        "selected_count": len(retrieve_result.items),
        "top_score": None,
        "avg_selected_score": None,
        "debug_breakdown": None,
    }

    if retrieve_result.debug is None:
        return summary

    reason_counts: dict[str, int] = {}
    below_threshold = 0
    filtered_by_diversity = 0
    selected_top = 0
    selected_scores: list[float] = []

    for candidate in retrieve_result.debug.candidates:
        reason_counts[candidate.reason] = reason_counts.get(candidate.reason, 0) + 1
        if not candidate.passed_threshold:
            below_threshold += 1
        if candidate.filtered_by_diversity:
            filtered_by_diversity += 1
        if candidate.selected:
            selected_top += 1
            selected_scores.append(candidate.score)

    if selected_scores:
        summary["top_score"] = max(selected_scores)
        summary["avg_selected_score"] = sum(selected_scores) / len(selected_scores)

    summary["debug_breakdown"] = {
        "below_threshold": below_threshold,
        "filtered_by_diversity": filtered_by_diversity,
        "selected_top": selected_top,
        "reasons": _sorted_breakdown(reason_counts),
    }
    return summary


def _render_memories_page(
    request: Request,
    *,
    chat_id: str | None = None,
    character_id: str | None = None,
    type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    search: str | None = None,
    freshness: str | None = None,
    activity: str | None = None,
    sort: str = "updated_desc",
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
    search = search or None
    freshness = freshness or None
    activity = activity or None

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

    base_memories = list_memories(
        chat_id=chat_id,
        character_id=character_id,
        memory_type=type,
        source=source,
        layer=layer,
        archived=archived_bool,
        pinned=pinned_bool,
        limit=UI_SEARCH_SCAN_LIMIT,
        offset=0,
    )
    memories = _filter_and_page_memories(
        base_memories.items,
        search=search,
        freshness=freshness,
        activity=activity,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    memory_cards = [_build_memory_card(item) for item in memories.items]

    filters = {
        "chat_id": chat_id,
        "character_id": character_id,
        "type": type,
        "source": source,
        "layer": layer,
        "search": search,
        "freshness": freshness,
        "activity": activity,
        "sort": sort,
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
            "search": search,
            "freshness": freshness,
            "activity": activity,
            "sort": sort,
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
            "memory_cards": memory_cards,
            "filters": filters,
            "store_result": store_result.model_dump() if store_result else None,
            "retrieve_result": retrieve_result.model_dump() if retrieve_result else None,
            "store_summary": _build_store_summary(store_result),
            "retrieve_summary": _build_retrieve_summary(retrieve_result),
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
    search: str | None = None,
    freshness: str | None = None,
    activity: str | None = None,
    sort: str = "updated_desc",
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
        search=search,
        freshness=freshness,
        activity=activity,
        sort=sort,
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
