import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode

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
    ConsolidationHistoryEntry,
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


def _normalize_redirect_query(value: Any) -> str:
    return value if isinstance(value, str) else ""


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


def _normalize_scope_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _shorten_display_text(value: str, max_length: int = 36) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3].rstrip()}..."


def _build_friendly_scope_label(value: str) -> str:
    compact = " ".join(value.split()).strip()
    if not compact:
        return "Unnamed chat"

    friendly = re.sub(r"[-_/]+", " ", compact)
    friendly = " ".join(friendly.split())
    if friendly != compact and not any(char.isupper() for char in friendly):
        friendly = friendly.title()

    if len(friendly) > 36:
        return _shorten_display_text(friendly)
    if friendly != compact:
        return friendly
    return _shorten_display_text(compact)


def _normalize_for_similarity(text: str) -> str:
    normalized = text.lower().strip()
    normalized = " ".join(normalized.split())
    return "".join(char if char.isalnum() or char.isspace() else " " for char in normalized)


def _token_overlap_ratio(text1: str, text2: str) -> float:
    tokens1 = set(_normalize_for_similarity(text1).split())
    tokens2 = set(_normalize_for_similarity(text2).split())
    if not tokens1 or not tokens2:
        return 0.0
    return len(tokens1 & tokens2) / min(len(tokens1), len(tokens2))


def _shared_signal_count(left: MemoryItem, right: MemoryItem) -> int:
    left_signals = set(item.lower() for item in left.metadata.entities + left.metadata.keywords)
    right_signals = set(item.lower() for item in right.metadata.entities + right.metadata.keywords)
    return len(left_signals & right_signals)


def _build_consolidation_data(items: list[MemoryItem]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    candidate_map: dict[str, list[dict[str, Any]]] = {item.id: [] for item in items}
    summary_counts = {
        "total_candidates": 0,
        "near_duplicate": 0,
        "stale_low_value_episode": 0,
        "shadowed_by_stable": 0,
    }

    for index, item in enumerate(items):
        if item.pinned or item.metadata.review_status == "reviewed_keep":
            continue

        freshness = _get_freshness_bucket(item)
        activity = _get_activity_bucket(item)

        if item.layer == "episodic" and freshness == "stale" and activity in {"never_used", "low_use"}:
            candidate_map[item.id].append(
                {
                    "type": "stale_low_value_episode",
                    "reason": "Stale episodic memory with low retrieval activity.",
                }
            )

        for other_index in range(index + 1, len(items)):
            other = items[other_index]
            if item.pinned or other.pinned:
                continue

            overlap = _token_overlap_ratio(item.content, other.content)
            exact_duplicate = _normalize_for_similarity(item.content) == _normalize_for_similarity(other.content)
            if exact_duplicate or overlap >= 0.85:
                reason = "Near-duplicate content cluster."
                candidate_map[item.id].append(
                    {
                        "type": "near_duplicate",
                        "reason": reason,
                        "related_id": other.id,
                    }
                )
                candidate_map[other.id].append(
                    {
                        "type": "near_duplicate",
                        "reason": reason,
                        "related_id": item.id,
                    }
                )

        if item.layer == "episodic" and activity != "active":
            for other in items:
                if other.id == item.id or other.layer != "stable":
                    continue
                if _shared_signal_count(item, other) >= 2:
                    candidate_map[item.id].append(
                        {
                            "type": "shadowed_by_stable",
                            "reason": "Similar topic already represented by a stable memory.",
                            "related_id": other.id,
                        }
                    )
                    break

    unique_type_counts = {
        "near_duplicate": 0,
        "stale_low_value_episode": 0,
        "shadowed_by_stable": 0,
    }
    total_candidates = 0
    for candidate_list in candidate_map.values():
        if candidate_list:
            total_candidates += 1
        for candidate_type in unique_type_counts:
            if any(candidate["type"] == candidate_type for candidate in candidate_list):
                unique_type_counts[candidate_type] += 1

    summary_counts["total_candidates"] = total_candidates
    summary_counts.update(unique_type_counts)
    return candidate_map, summary_counts


def _build_consolidation_result(action: str, memory_id: str, related_memory_id: str | None, note: str | None) -> dict[str, Any]:
    labels = {
        "mark_consolidated_archive": "Candidate archived for consolidation review.",
        "mark_reviewed_keep": "Candidate marked as reviewed and kept.",
        "link_to_related_memory": "Candidate linked to related memory.",
    }
    return {
        "memory_id": memory_id,
        "action": action,
        "message": labels.get(action, "Consolidation action applied."),
        "related_memory_id": related_memory_id or None,
        "note": note or None,
    }


def _append_consolidation_history(
    metadata: MemoryMetadata,
    action: str,
    related_memory_id: str | None,
    note: str | None,
) -> list[ConsolidationHistoryEntry]:
    entry = ConsolidationHistoryEntry(
        action=action,
        timestamp=_utc_now().isoformat(),
        related_memory_id=related_memory_id or None,
        note=note or None,
    )
    return [*metadata.consolidation_history, entry]


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
    consolidation: str | None,
    sort: str,
    limit: int,
    offset: int,
    candidate_map: dict[str, list[dict[str, Any]]] | None = None,
) -> ListMemoriesResponse:
    """Apply UI-only filters and sorting, then paginate the filtered list."""
    filtered_items = list(items)
    if search:
        filtered_items = [item for item in filtered_items if _matches_memory_search(item, search)]
    if freshness:
        filtered_items = [item for item in filtered_items if _get_freshness_bucket(item) == freshness]
    if activity:
        filtered_items = [item for item in filtered_items if _get_activity_bucket(item) == activity]
    if consolidation == "candidates_only" and candidate_map is not None:
        filtered_items = [item for item in filtered_items if candidate_map.get(item.id)]
    elif consolidation and consolidation != "candidates_only" and candidate_map is not None:
        filtered_items = [
            item for item in filtered_items
            if any(candidate["type"] == consolidation for candidate in candidate_map.get(item.id, []))
        ]

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


def _build_scope_query(
    *,
    view: str,
    selected_chat_id: str | None,
    selected_character_id: str | None,
) -> str:
    return _build_query_string(
        {
            "view": view if view == "all" else None,
            "selected_chat_id": selected_chat_id if view != "all" else None,
            "selected_character_id": selected_character_id if view != "all" else None,
        }
    )


def _redirect_query_to_render_args(redirect_query: str) -> dict[str, Any]:
    redirect_query = _normalize_redirect_query(redirect_query)
    if not redirect_query:
        return {}

    params = dict(parse_qsl(redirect_query, keep_blank_values=False))
    render_args: dict[str, Any] = {}
    string_keys = {
        "selected_chat_id",
        "selected_character_id",
        "view",
        "type",
        "source",
        "layer",
        "search",
        "freshness",
        "activity",
        "consolidation",
        "sort",
        "archived",
        "pinned",
    }
    int_keys = {"limit", "offset"}

    for key in string_keys:
        if key in params:
            render_args[key] = params[key]
    for key in int_keys:
        if key in params:
            try:
                render_args[key] = int(params[key])
            except ValueError:
                continue

    return render_args


def _build_chat_groups(items: list[MemoryItem]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (item.chat_id, item.character_id)
        group = grouped.get(key)
        if group is None:
            group = {
                "chat_id": item.chat_id,
                "character_id": item.character_id,
                "total_count": 0,
                "summary_count": 0,
                "stable_count": 0,
                "episodic_count": 0,
                "last_updated": item.updated_at,
            }
            grouped[key] = group

        group["total_count"] += 1
        if item.type == "summary" or item.metadata.is_summary:
            group["summary_count"] += 1
        elif item.layer == "stable":
            group["stable_count"] += 1
        else:
            group["episodic_count"] += 1

        if item.updated_at > group["last_updated"]:
            group["last_updated"] = item.updated_at

    groups = list(grouped.values())
    groups.sort(
        key=lambda group: (
            group["last_updated"],
            group["chat_id"],
            group["character_id"],
        ),
        reverse=True,
    )
    for group in groups:
        group["last_updated_days"] = _days_since(group["last_updated"])
        group["display_label"] = _build_friendly_scope_label(group["chat_id"])
        group["display_character_label"] = _build_friendly_scope_label(group["character_id"])
        group["has_friendly_label"] = group["display_label"] != group["chat_id"]
    return groups


def _resolve_selected_group(
    chat_groups: list[dict[str, Any]],
    *,
    requested_chat_id: str | None,
    requested_character_id: str | None,
    view: str,
) -> dict[str, Any] | None:
    if view == "all":
        return None

    if requested_chat_id and requested_character_id:
        for group in chat_groups:
            if (
                group["chat_id"] == requested_chat_id
                and group["character_id"] == requested_character_id
            ):
                return group

    return chat_groups[0] if chat_groups else None


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
    selected_chat_id: str | None = None,
    selected_character_id: str | None = None,
    view: str | None = None,
    chat_id: str | None = None,
    character_id: str | None = None,
    type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    search: str | None = None,
    freshness: str | None = None,
    activity: str | None = None,
    consolidation: str | None = None,
    sort: str = "updated_desc",
    archived: str | None = None,
    pinned: str | None = None,
    limit: int = 50,
    offset: int = 0,
    store_result: StoreMemoryResponse | None = None,
    retrieve_result: RetrieveMemoryResponse | None = None,
    consolidation_result: dict[str, Any] | None = None,
    store_form: dict[str, Any] | None = None,
    retrieve_form: dict[str, Any] | None = None,
) -> Any:
    """Render the memories page with optional store/retrieve diagnostics sections."""
    legacy_chat_id = _normalize_scope_value(chat_id)
    legacy_character_id = _normalize_scope_value(character_id)
    requested_chat_id = _normalize_scope_value(selected_chat_id) or legacy_chat_id
    requested_character_id = _normalize_scope_value(selected_character_id) or legacy_character_id
    view_mode = "all" if view == "all" else "chat"
    type = type or None
    source = source or None
    layer = layer or None
    search = search or None
    freshness = freshness or None
    activity = activity or None
    consolidation = consolidation or None

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
        memory_type=type,
        source=source,
        layer=layer,
        archived=archived_bool,
        pinned=pinned_bool,
        limit=UI_SEARCH_SCAN_LIMIT,
        offset=0,
    )
    chat_groups = _build_chat_groups(base_memories.items)
    selected_group = _resolve_selected_group(
        chat_groups,
        requested_chat_id=requested_chat_id,
        requested_character_id=requested_character_id,
        view=view_mode,
    )
    active_chat_id = selected_group["chat_id"] if selected_group else None
    active_character_id = selected_group["character_id"] if selected_group else None

    if view_mode == "all":
        scoped_items = list(base_memories.items)
    elif active_chat_id and active_character_id:
        scoped_items = [
            item
            for item in base_memories.items
            if item.chat_id == active_chat_id and item.character_id == active_character_id
        ]
    else:
        scoped_items = []

    candidate_map, consolidation_summary = _build_consolidation_data(scoped_items)
    memories = _filter_and_page_memories(
        scoped_items,
        search=search,
        freshness=freshness,
        activity=activity,
        consolidation=consolidation,
        sort=sort,
        limit=limit,
        offset=offset,
        candidate_map=candidate_map,
    )
    memory_cards = [
        {
            **_build_memory_card(item),
            "consolidation_candidates": candidate_map.get(item.id, []),
            "is_consolidation_candidate": bool(candidate_map.get(item.id)),
        }
        for item in memories.items
    ]

    redirect_query = _build_query_string(
        {
            "view": view_mode if view_mode == "all" else None,
            "selected_chat_id": active_chat_id if view_mode != "all" else None,
            "selected_character_id": active_character_id if view_mode != "all" else None,
            "type": type,
            "source": source,
            "layer": layer,
            "search": search,
            "freshness": freshness,
            "activity": activity,
            "consolidation": consolidation,
            "sort": sort,
            "archived": archived,
            "pinned": pinned,
            "limit": limit,
            "offset": offset,
        }
    )
    clear_filters_query = _build_scope_query(
        view=view_mode,
        selected_chat_id=active_chat_id,
        selected_character_id=active_character_id,
    )
    clear_filters_url = "/ui"
    if clear_filters_query:
        clear_filters_url = f"/ui?{clear_filters_query}"

    all_chats_query = _build_query_string(
        {
            "view": "all",
            "type": type,
            "source": source,
            "layer": layer,
            "search": search,
            "freshness": freshness,
            "activity": activity,
            "consolidation": consolidation,
            "sort": sort,
            "archived": archived,
            "pinned": pinned,
            "limit": limit,
        }
    )
    all_chats_url = f"/ui?{all_chats_query}" if all_chats_query else "/ui"

    for group in chat_groups:
        group_query = _build_query_string(
            {
                "selected_chat_id": group["chat_id"],
                "selected_character_id": group["character_id"],
                "type": type,
                "source": source,
                "layer": layer,
                "search": search,
                "freshness": freshness,
                "activity": activity,
                "consolidation": consolidation,
                "sort": sort,
                "archived": archived,
                "pinned": pinned,
                "limit": limit,
            }
        )
        group["url"] = f"/ui?{group_query}" if group_query else "/ui"
        group["is_selected"] = (
            view_mode != "all"
            and active_chat_id == group["chat_id"]
            and active_character_id == group["character_id"]
        )

    scope_title = "All Chats"
    scope_subtitle = "Global view across the current filtered dataset"
    scope_meta: list[dict[str, str]] = []
    if view_mode != "all":
        scope_title = selected_group["display_label"] if selected_group else (active_chat_id or "Select a chat")
        scope_subtitle = (
            f"Character: {selected_group['display_character_label']}"
            if selected_group
            else (f"Character: {active_character_id}" if active_character_id else None)
        )
        if active_chat_id:
            scope_meta.append({"label": "Chat ID", "value": active_chat_id})
        if active_character_id:
            scope_meta.append({"label": "Character ID", "value": active_character_id})

    filters = {
        "chat_id": active_chat_id,
        "character_id": active_character_id,
        "selected_chat_id": active_chat_id,
        "selected_character_id": active_character_id,
        "view": view_mode,
        "type": type,
        "source": source,
        "layer": layer,
        "search": search,
        "freshness": freshness,
        "activity": activity,
        "consolidation": consolidation,
        "sort": sort,
        "archived": archived,
        "pinned": pinned,
        "limit": limit,
        "offset": offset,
        "query_string": _build_query_string(
            {
                "view": view_mode if view_mode == "all" else None,
                "selected_chat_id": active_chat_id if view_mode != "all" else None,
                "selected_character_id": active_character_id if view_mode != "all" else None,
                "type": type,
                "source": source,
                "layer": layer,
                "search": search,
                "freshness": freshness,
                "activity": activity,
                "consolidation": consolidation,
                "sort": sort,
                "archived": archived,
                "pinned": pinned,
                "limit": limit,
            }
        ),
        "redirect_query": redirect_query,
        "clear_filters_url": clear_filters_url,
    }

    return templates.TemplateResponse(
        request,
        "memories.html",
        {
            "memories": memories.model_dump(),
            "memory_cards": memory_cards,
            "chat_groups": chat_groups,
            "scope_title": scope_title,
            "scope_subtitle": scope_subtitle,
            "scope_meta": scope_meta,
            "scope_is_all_chats": view_mode == "all",
            "all_chats_url": all_chats_url,
            "all_chats_selected": view_mode == "all",
            "has_chat_groups": bool(chat_groups),
            "consolidation_summary": consolidation_summary,
            "filters": filters,
            "store_result": store_result.model_dump() if store_result else None,
            "retrieve_result": retrieve_result.model_dump() if retrieve_result else None,
            "consolidation_result": consolidation_result,
            "store_summary": _build_store_summary(store_result),
            "retrieve_summary": _build_retrieve_summary(retrieve_result),
            "store_form": store_form or {
                "chat_id": active_chat_id or "",
                "character_id": active_character_id or "",
                "messages": "",
                "debug": False,
            },
            "retrieve_form": retrieve_form or {
                "chat_id": active_chat_id or "",
                "character_id": active_character_id or "",
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
    selected_chat_id: str | None = None,
    selected_character_id: str | None = None,
    view: str | None = None,
    chat_id: str | None = None,
    character_id: str | None = None,
    type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    search: str | None = None,
    freshness: str | None = None,
    activity: str | None = None,
    consolidation: str | None = None,
    sort: str = "updated_desc",
    archived: str | None = None,
    pinned: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """Render memories page with filters."""
    return _render_memories_page(
        request,
        selected_chat_id=selected_chat_id,
        selected_character_id=selected_character_id,
        view=view,
        chat_id=chat_id,
        character_id=character_id,
        type=type,
        source=source,
        layer=layer,
        search=search,
        freshness=freshness,
        activity=activity,
        consolidation=consolidation,
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
        selected_chat_id=chat_id,
        selected_character_id=character_id,
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
        selected_chat_id=chat_id,
        selected_character_id=character_id,
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
    redirect_query: str = Form(""),
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
    redirect_query = _normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


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
    redirect_query: str = Form(""),
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
    redirect_query = _normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/{memory_id}/pin")
def ui_toggle_pin(memory_id: str, redirect_query: str = Form("")) -> RedirectResponse:
    """Toggle pinned status and redirect back to UI."""
    memory = get_memory_by_id(memory_id)
    if memory:
        set_pinned(memory_id, not memory.pinned)
    redirect_query = _normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/{memory_id}/archive")
def ui_toggle_archive(memory_id: str, redirect_query: str = Form("")) -> RedirectResponse:
    """Toggle archived status and redirect back to UI."""
    memory = get_memory_by_id(memory_id)
    if memory:
        set_archived(memory_id, not memory.archived)
    redirect_query = _normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/{memory_id}/delete")
def ui_delete_memory(memory_id: str, redirect_query: str = Form("")) -> RedirectResponse:
    """Delete a memory and redirect back to UI."""
    delete_memory(memory_id)
    redirect_query = _normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/{memory_id}/consolidate")
def ui_consolidate_memory(
    request: Request,
    memory_id: str,
    action: str = Form(...),
    related_memory_id: str = Form(""),
    note: str = Form(""),
    redirect_query: str = Form(""),
) -> Any:
    """Apply manual consolidation triage workflow from the admin UI."""
    memory = get_memory_by_id(memory_id)
    if memory is None:
        return _render_memories_page(
            request,
            consolidation_result={
                "memory_id": memory_id,
                "action": action,
                "message": "Memory not found.",
                "related_memory_id": None,
                "note": None,
            },
        )

    related_memory_id = related_memory_id.strip()
    note = note.strip()

    updated_metadata = memory.metadata.model_copy(
        update={
            "review_status": {
                "mark_consolidated_archive": "consolidated_archive",
                "mark_reviewed_keep": "reviewed_keep",
                "link_to_related_memory": "linked_to_related",
            }.get(action, memory.metadata.review_status),
            "related_memory_id": related_memory_id or memory.metadata.related_memory_id,
            "consolidation_note": note or memory.metadata.consolidation_note,
            "consolidation_history": _append_consolidation_history(
                memory.metadata,
                action,
                related_memory_id,
                note,
            ),
        }
    )

    update_payload = UpdateMemoryRequest(metadata=updated_metadata)
    if action == "mark_consolidated_archive":
        update_payload.archived = True
    update_memory(memory_id, update_payload)

    updated_memory = get_memory_by_id(memory_id)
    selected_chat_id = updated_memory.chat_id if updated_memory else memory.chat_id
    selected_character_id = updated_memory.character_id if updated_memory else memory.character_id

    return _render_memories_page(
        request,
        **_redirect_query_to_render_args(redirect_query),
        consolidation_result=_build_consolidation_result(action, memory_id, related_memory_id, note),
    )
