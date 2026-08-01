import json
import uuid
from typing import get_args

from app.db import get_connection
from app.schemas import (
    ArchiveMemoryRequest,
    CreateMemoryRequest,
    ListMemoriesResponse,
    MemoryItem,
    MemoryMetadata,
    PinMemoryRequest,
    TrackerType,
    UpdateMemoryRequest,
)
from app.services.text_utils import get_utc_now, normalize_content as _normalize_content


def _row_to_memory_item(row: dict) -> MemoryItem:
    """Convert database row to MemoryItem."""
    return MemoryItem(
        id=row["id"],
        chat_id=row["chat_id"],
        character_id=row["character_id"],
        type=row["type"],
        content=row["content"],
        normalized_content=row["normalized_content"],
        source=row["source"],
        layer=row["layer"],
        importance=row["importance"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_accessed_at=row["last_accessed_at"],
        access_count=row["access_count"],
        pinned=bool(row["pinned"]),
        archived=bool(row["archived"]),
        metadata=MemoryMetadata.model_validate_json(row["metadata_json"]),
    )


def insert_memory(memory: MemoryItem) -> MemoryItem:
    """Insert a new memory record into the database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO memories (
                id, chat_id, character_id, type, content, normalized_content,
                source, layer, importance, created_at, updated_at,
                last_accessed_at, access_count, pinned, archived, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.id,
                memory.chat_id,
                memory.character_id,
                memory.type,
                memory.content,
                memory.normalized_content,
                memory.source,
                memory.layer,
                memory.importance,
                memory.created_at,
                memory.updated_at,
                memory.last_accessed_at,
                memory.access_count,
                int(memory.pinned),
                int(memory.archived),
                memory.metadata.model_dump_json(),
            ),
        )
        conn.commit()
    return memory


def create_memory(request: CreateMemoryRequest) -> MemoryItem:
    """Create a new memory from a request."""
    now = get_utc_now()
    # Normalize content: strip leading/trailing whitespace
    content = request.content.strip()
    memory = MemoryItem(
        id=str(uuid.uuid4()),
        chat_id=request.chat_id,
        character_id=request.character_id,
        type=request.type,
        content=content,
        normalized_content=_normalize_content(content),
        source=request.source,
        layer=request.layer,
        importance=request.importance,
        created_at=now,
        updated_at=now,
        last_accessed_at=None,
        access_count=0,
        pinned=request.pinned,
        archived=request.archived,
        metadata=request.metadata,
    )
    return insert_memory(memory)


def get_memory_by_id(memory_id: str) -> MemoryItem | None:
    """Get a memory record by ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM memories WHERE id = ?",
            (memory_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_memory_item(dict(row))


def list_memories(
    chat_id: str | None = None,
    character_id: str | None = None,
    memory_type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    archived: bool | None = None,
    pinned: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    include_trackers: bool = False,
) -> ListMemoriesResponse:
    """
    List memories with optional filters.

    Trackers are excluded by default: they live in `memories` for storage reasons but
    are not memories in the retrieval/consolidation sense, and a caller that treats one
    as an ordinary row (store_service's soft-match dedup, most of all) would overwrite a
    whole tracker document with a single extracted fact. Read them via list_trackers().
    """
    where_clauses = []
    params = []

    if not include_trackers:
        where_clauses.append("type != 'tracker'")

    if chat_id is not None:
        where_clauses.append("chat_id = ?")
        params.append(chat_id)

    if character_id is not None:
        where_clauses.append("character_id = ?")
        params.append(character_id)

    if memory_type is not None:
        where_clauses.append("type = ?")
        params.append(memory_type)

    if source is not None:
        where_clauses.append("source = ?")
        params.append(source)

    if layer is not None:
        where_clauses.append("layer = ?")
        params.append(layer)

    if archived is not None:
        where_clauses.append("archived = ?")
        params.append(int(archived))

    if pinned is not None:
        where_clauses.append("pinned = ?")
        params.append(int(pinned))

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    with get_connection() as conn:
        cursor = conn.cursor()

        # Count total
        cursor.execute(
            f"SELECT COUNT(*) FROM memories {where_sql}",
            params,
        )
        total = cursor.fetchone()[0]

        # Fetch items
        cursor.execute(
            f"""
            SELECT * FROM memories {where_sql}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        rows = cursor.fetchall()
        items = [_row_to_memory_item(dict(row)) for row in rows]

    return ListMemoriesResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def list_retrieval_candidates(
    chat_id: str,
    character_id: str,
    include_archived: bool = False,
) -> list[MemoryItem]:
    """
    List all retrieval candidates for a chat/character pair without UI pagination bias.

    Retrieval scoring should operate on the full candidate set rather than the
    recency-ordered paginated listing used by the UI/API.

    Trackers are not candidates: they reach the prompt through their own injection path
    in the extension, so scoring them here would both double-inject them and let a whole
    tracker document crowd out real memories in the budget.
    """
    params: list[object] = [chat_id, character_id]
    archived_sql = ""
    if not include_archived:
        archived_sql = " AND archived = 0"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT * FROM memories
            WHERE chat_id = ? AND character_id = ? AND type != 'tracker'{archived_sql}
            """,
            params,
        )
        rows = cursor.fetchall()
        return [_row_to_memory_item(dict(row)) for row in rows]


def list_chat_group_summaries(
    memory_type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    archived: bool | None = None,
    pinned: bool | None = None,
) -> list[dict[str, object]]:
    """
    Lightweight query for the chat sidebar: returns one row per (chat_id, character_id)
    with counts and last_updated, without loading full memory rows.

    Trackers are excluded from every count, but not from the grouping. The distinction
    matters in both directions:

      - counting them was the bug. Trackers carry layer='stable', so each one inflated
        total_count and stable_count in the sidebar. It survived because the UI tests
        mock this function out and rebuild the aggregation in Python, where no tracker
        exists to be miscounted.
      - filtering them out of the FROM clause instead looks equivalent and is not: a
        chat whose only rows are trackers would stop appearing in the sidebar
        entirely, and its trackers would become unreachable in the UI, since the
        trackers section only renders for a selected chat. An existing test caught
        exactly that.

    So a tracker-only chat still lists, with zero counts, and tracker_count says why.
    last_updated deliberately spans trackers too - a tracker rewrite is activity in
    that chat even though it isn't a memory.
    """
    where_clauses: list[str] = []
    params: list[object] = []

    if memory_type is not None:
        where_clauses.append("type = ?")
        params.append(memory_type)
    if source is not None:
        where_clauses.append("source = ?")
        params.append(source)
    if layer is not None:
        where_clauses.append("layer = ?")
        params.append(layer)
    if archived is not None:
        where_clauses.append("archived = ?")
        params.append(int(archived))
    if pinned is not None:
        where_clauses.append("pinned = ?")
        params.append(int(pinned))

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT
                chat_id,
                character_id,
                SUM(CASE WHEN type != 'tracker' THEN 1 ELSE 0 END) AS total_count,
                SUM(CASE WHEN type = 'tracker' THEN 1 ELSE 0 END) AS tracker_count,
                -- COALESCE, because json_extract returns NULL for a row whose metadata
                -- has no is_summary key, and in SQL `NULL != 1` is NULL rather than
                -- true: such a row would be counted in total_count but silently fall
                -- out of both stable_count and episodic_count. Every row written
                -- through pydantic carries the key today, so this is a guard against
                -- the counts quietly disagreeing with the total, not a live bug.
                SUM(CASE WHEN type != 'tracker' AND (type = 'summary' OR COALESCE(json_extract(metadata_json, '$.is_summary'), 0) = 1) THEN 1 ELSE 0 END) AS summary_count,
                SUM(CASE WHEN type NOT IN ('tracker', 'summary') AND layer = 'stable' AND COALESCE(json_extract(metadata_json, '$.is_summary'), 0) != 1 THEN 1 ELSE 0 END) AS stable_count,
                SUM(CASE WHEN type NOT IN ('tracker', 'summary') AND layer = 'episodic' AND COALESCE(json_extract(metadata_json, '$.is_summary'), 0) != 1 THEN 1 ELSE 0 END) AS episodic_count,
                MAX(updated_at) AS last_updated
            FROM memories
            {where_sql}
            GROUP BY chat_id, character_id
            ORDER BY last_updated DESC
            """,
            params,
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


SORT_ORDERS = {
    "updated_desc": "updated_at DESC",
    "last_accessed_desc": "COALESCE(last_accessed_at, '') DESC, updated_at DESC",
    "access_count_desc": "access_count DESC, COALESCE(last_accessed_at, '') DESC",
    "stalest_first": "updated_at ASC, last_accessed_at ASC",
}

FRESHNESS_SQL = {
    "fresh": "julianday('now') - julianday(updated_at) <= 7",
    "warm": "julianday('now') - julianday(updated_at) > 7 AND julianday('now') - julianday(updated_at) <= 30",
    "stale": "julianday('now') - julianday(updated_at) > 30",
}

ACTIVITY_SQL = {
    "never_used": "access_count <= 0 OR last_accessed_at IS NULL",
    "active": "access_count >= 5 OR (last_accessed_at IS NOT NULL AND julianday('now') - julianday(last_accessed_at) <= 14)",
    "low_use": "access_count > 0 AND access_count < 5 AND (last_accessed_at IS NULL OR julianday('now') - julianday(last_accessed_at) > 14)",
}


def list_ui_filtered_memories(
    *,
    chat_id: str | None = None,
    character_id: str | None = None,
    memory_type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    archived: bool | None = None,
    pinned: bool | None = None,
    hide_consolidated: bool = False,
    search: str | None = None,
    freshness: str | None = None,
    activity: str | None = None,
    sort: str = "updated_desc",
    limit: int = 50,
    offset: int = 0,
) -> ListMemoriesResponse:
    """
    List memories with UI-level filters applied in SQL.
    Handles search (LIKE), freshness/activity (date math), sorting, and pagination.

    Trackers are excluded unconditionally. This backs both the memory cards and the
    consolidation pool: a tracker is neither an ordinary memory card nor a valid
    consolidation source (consolidating one would fold a live document into a summary
    and then mark it superseded). The UI renders trackers from their own section.
    """
    where_clauses: list[str] = ["type != 'tracker'"]
    params: list[object] = []

    if chat_id is not None:
        where_clauses.append("chat_id = ?")
        params.append(chat_id)
    if character_id is not None:
        where_clauses.append("character_id = ?")
        params.append(character_id)
    if memory_type is not None:
        where_clauses.append("type = ?")
        params.append(memory_type)
    if source is not None:
        where_clauses.append("source = ?")
        params.append(source)
    if layer is not None:
        where_clauses.append("layer = ?")
        params.append(layer)
    if archived is not None:
        where_clauses.append("archived = ?")
        params.append(int(archived))
    if pinned is not None:
        where_clauses.append("pinned = ?")
        params.append(int(pinned))
    if hide_consolidated:
        # review_status lives in metadata_json, not a dedicated column - unlike
        # archived, these records must stay normal retrieval candidates (see
        # list_retrieval_candidates), so this filter is UI-only. Covers both
        # terminal "folded away" statuses: CONSOLIDATED_REVIEW_STATUS
        # (summary_service.py) and SUPERSEDED_REVIEW_STATUS (conflict_resolver.py)
        # - literals duplicated here rather than imported to avoid a
        # repository -> service layering inversion.
        where_clauses.append(
            "json_extract(metadata_json, '$.review_status') IS NOT 'consolidated'"
            " AND json_extract(metadata_json, '$.review_status') IS NOT 'superseded'"
        )

    if search:
        query = " ".join(search.lower().split())[:200]
        if query:
            like_pattern = f"%{query}%"
            where_clauses.append(
                "(LOWER(content) LIKE ? OR LOWER(normalized_content) LIKE ? OR LOWER(type) LIKE ? OR LOWER(source) LIKE ? OR LOWER(layer) LIKE ? OR LOWER(metadata_json) LIKE ?)"
            )
            params.extend([like_pattern] * 6)

    if freshness and freshness in FRESHNESS_SQL:
        where_clauses.append(f"({FRESHNESS_SQL[freshness]})")

    if activity and activity in ACTIVITY_SQL:
        where_clauses.append(f"({ACTIVITY_SQL[activity]})")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    order_sql = SORT_ORDERS.get(sort, SORT_ORDERS["updated_desc"])

    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            f"SELECT COUNT(*) FROM memories {where_sql}",
            params,
        )
        total = cursor.fetchone()[0]

        cursor.execute(
            f"""
            SELECT * FROM memories {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        rows = cursor.fetchall()
        items = [_row_to_memory_item(dict(row)) for row in rows]

    return ListMemoriesResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def update_memory(memory_id: str, payload: UpdateMemoryRequest) -> MemoryItem | None:
    """Update a memory record. Only updates provided fields."""
    existing = get_memory_by_id(memory_id)
    if existing is None:
        return None

    updates = {}
    update_params = []

    if payload.content is not None:
        # Normalize content: strip leading/trailing whitespace
        content = payload.content.strip()
        updates["content"] = "?"
        update_params.append(content)
        updates["normalized_content"] = "?"
        update_params.append(_normalize_content(content))

    if payload.type is not None:
        updates["type"] = "?"
        update_params.append(payload.type)

    if payload.source is not None:
        updates["source"] = "?"
        update_params.append(payload.source)

    if payload.layer is not None:
        updates["layer"] = "?"
        update_params.append(payload.layer)

    if payload.importance is not None:
        updates["importance"] = "?"
        update_params.append(payload.importance)

    if payload.pinned is not None:
        updates["pinned"] = "?"
        update_params.append(int(payload.pinned))

    if payload.archived is not None:
        updates["archived"] = "?"
        update_params.append(int(payload.archived))

    if payload.metadata is not None:
        updates["metadata_json"] = "?"
        update_params.append(payload.metadata.model_dump_json())

    if not updates:
        return existing

    updates["updated_at"] = "?"
    update_params.append(get_utc_now())
    update_params.append(memory_id)

    set_sql = ", ".join(f"{col} = {val}" for col, val in updates.items())

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE memories SET {set_sql} WHERE id = ?",
            update_params,
        )
        conn.commit()

    return get_memory_by_id(memory_id)


def delete_memory(memory_id: str) -> bool:
    """Delete a memory record by ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM memories WHERE id = ?",
            (memory_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def set_pinned(memory_id: str, pinned: bool) -> bool:
    """Set the pinned status of a memory."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE memories
            SET pinned = ?, updated_at = ?
            WHERE id = ?
            """,
            (int(pinned), get_utc_now(), memory_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def set_archived(memory_id: str, archived: bool) -> bool:
    """Set the archived status of a memory."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE memories
            SET archived = ?, updated_at = ?
            WHERE id = ?
            """,
            (int(archived), get_utc_now(), memory_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def set_review_status(memory_ids: list[str], review_status: str) -> int:
    """
    Bulk-set metadata.review_status for the given memory ids.

    Deliberately skips updated_at: bumping it would make a just-consolidated
    source memory look freshly touched to retrieval's recency scoring, when it
    should keep aging normally like any other record.
    """
    if not memory_ids:
        return 0

    with get_connection() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in memory_ids)
        cursor.execute(
            f"""
            UPDATE memories
            SET metadata_json = json_set(metadata_json, '$.review_status', ?)
            WHERE id IN ({placeholders})
            """,
            [review_status, *memory_ids],
        )
        conn.commit()
        return cursor.rowcount


def find_memory_by_normalized_content(
    chat_id: str,
    character_id: str,
    normalized_content: str,
) -> MemoryItem | None:
    """
    Find existing memory by normalized content for deduplication.

    This is a minimal helper for store_service deduplication. Trackers are excluded as a
    backstop: an extracted fact must never dedupe onto a tracker document and rewrite it.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM memories
            WHERE chat_id = ? AND character_id = ? AND normalized_content = ?
              AND type != 'tracker'
            """,
            (chat_id, character_id, normalized_content),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_memory_item(dict(row))


def increment_access_count(memory_id: str) -> bool:
    """
    Increment access_count and update last_accessed_at for a memory.

    Called after retrieve to track usage metrics.
    """
    now = get_utc_now()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE memories
            SET access_count = access_count + 1,
                last_accessed_at = ?
            WHERE id = ?
            """,
            (now, memory_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_tracker(
    chat_id: str,
    character_id: str,
    tracker_type: str,
) -> MemoryItem | None:
    """Get the single tracker document of a given type for a chat/character pair."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM memories
            WHERE chat_id = ? AND character_id = ? AND type = 'tracker'
              AND json_extract(metadata_json, '$.tracker_type') = ?
            """,
            (chat_id, character_id, tracker_type),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_memory_item(dict(row))


def list_trackers(chat_id: str, character_id: str) -> list[MemoryItem]:
    """List all tracker documents for a chat/character pair, oldest-updated first."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM memories
            WHERE chat_id = ? AND character_id = ? AND type = 'tracker'
            ORDER BY json_extract(metadata_json, '$.tracker_type')
            """,
            (chat_id, character_id),
        )
        rows = cursor.fetchall()
        return [_row_to_memory_item(dict(row)) for row in rows]


def upsert_tracker(
    *,
    chat_id: str,
    character_id: str,
    tracker_type: str,
    content: str,
    metadata: MemoryMetadata,
    importance: float = 0.5,
) -> tuple[MemoryItem, bool]:
    """
    Create or rewrite the tracker of this type in place. Returns (item, created).

    Deliberately does not route the update through update_memory(): UpdateMemoryRequest
    caps content at 5000 chars, which a timeline document outgrows, and a tracker has no
    business carrying the partial-update semantics of an edited memory. It also never
    touches the vector store - a tracker must not surface as a semantic match.

    metadata.tracker_type is forced to match the tracker_type argument, so the row can't
    disagree with the unique index that keys on it.

    Raises ValueError on an unknown tracker_type. The forcing above goes through
    model_copy(), which does not validate, so an unknown value used to be written happily
    and only blow up on the way back out - MemoryMetadata rejects it, and _row_to_memory_item
    raises for that row. One bad write therefore poisoned every subsequent read of the
    whole chat, including list_memories, with a pydantic error that named the field but
    not the row or the writer. Failing at the write keeps the blast radius at one call.
    """
    allowed = get_args(TrackerType)
    if tracker_type not in allowed:
        raise ValueError(
            f"unknown tracker_type {tracker_type!r}; expected one of {', '.join(allowed)}"
        )

    metadata = metadata.model_copy(update={"tracker_type": tracker_type})
    now = get_utc_now()
    existing = get_tracker(chat_id, character_id, tracker_type)

    if existing is None:
        item = MemoryItem(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            character_id=character_id,
            type="tracker",
            content=content,
            normalized_content=_normalize_content(content),
            source="auto",
            layer="stable",
            importance=importance,
            created_at=now,
            updated_at=now,
            last_accessed_at=None,
            access_count=0,
            pinned=False,
            archived=False,
            metadata=metadata,
        )
        return insert_memory(item), True

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE memories
            SET content = ?, normalized_content = ?, metadata_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                content,
                _normalize_content(content),
                metadata.model_dump_json(),
                now,
                existing.id,
            ),
        )
        conn.commit()

    updated = get_memory_by_id(existing.id)
    assert updated is not None  # just updated it, inside the same process
    return updated, False
