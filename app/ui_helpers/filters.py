from datetime import datetime, timezone
from typing import Any

from app.schemas import ListMemoriesResponse, MemoryItem
from app.ui_helpers.classifiers import (
    get_activity_bucket,
    get_freshness_bucket,
    parse_iso_datetime,
    utc_now,
)


def matches_memory_search(memory: MemoryItem, search: str) -> bool:
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


def sort_memories(items: list[MemoryItem], sort: str) -> list[MemoryItem]:
    if sort == "last_accessed_desc":
        return sorted(
            items,
            key=lambda item: (
                parse_iso_datetime(item.last_accessed_at) or datetime.min.replace(tzinfo=timezone.utc),
                parse_iso_datetime(item.updated_at) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
    if sort == "access_count_desc":
        return sorted(
            items,
            key=lambda item: (item.access_count, parse_iso_datetime(item.last_accessed_at) or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
    if sort == "stalest_first":
        return sorted(
            items,
            key=lambda item: (
                parse_iso_datetime(item.updated_at) or utc_now(),
                parse_iso_datetime(item.last_accessed_at) or utc_now(),
            ),
        )
    return sorted(
        items,
        key=lambda item: parse_iso_datetime(item.updated_at) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def filter_and_page_memories(
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
        filtered_items = [item for item in filtered_items if matches_memory_search(item, search)]
    if freshness:
        filtered_items = [item for item in filtered_items if get_freshness_bucket(item) == freshness]
    if activity:
        filtered_items = [item for item in filtered_items if get_activity_bucket(item) == activity]
    if consolidation == "candidates_only" and candidate_map is not None:
        filtered_items = [item for item in filtered_items if candidate_map.get(item.id)]
    elif consolidation and consolidation != "candidates_only" and candidate_map is not None:
        filtered_items = [
            item for item in filtered_items
            if any(candidate["type"] == consolidation for candidate in candidate_map.get(item.id, []))
        ]

    filtered_items = sort_memories(filtered_items, sort)
    paginated_items = filtered_items[offset: offset + limit]
    return ListMemoriesResponse(
        items=paginated_items,
        total=len(filtered_items),
        limit=limit,
        offset=offset,
    )
