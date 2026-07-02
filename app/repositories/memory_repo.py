import json
import uuid

from app.db import get_connection
from app.schemas import (
    ArchiveMemoryRequest,
    CreateMemoryRequest,
    ListMemoriesResponse,
    MemoryItem,
    MemoryMetadata,
    PinMemoryRequest,
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
) -> ListMemoriesResponse:
    """List memories with optional filters."""
    where_clauses = []
    params = []

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
            WHERE chat_id = ? AND character_id = ?{archived_sql}
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
                COUNT(*) AS total_count,
                SUM(CASE WHEN type = 'summary' OR json_extract(metadata_json, '$.is_summary') = 1 THEN 1 ELSE 0 END) AS summary_count,
                SUM(CASE WHEN layer = 'stable' AND type != 'summary' AND json_extract(metadata_json, '$.is_summary') != 1 THEN 1 ELSE 0 END) AS stable_count,
                SUM(CASE WHEN layer = 'episodic' AND type != 'summary' AND json_extract(metadata_json, '$.is_summary') != 1 THEN 1 ELSE 0 END) AS episodic_count,
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
    """
    where_clauses: list[str] = []
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

    This is a minimal helper for store_service deduplication.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM memories
            WHERE chat_id = ? AND character_id = ? AND normalized_content = ?
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
