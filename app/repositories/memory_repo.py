import json
import re
import uuid
from datetime import datetime, timezone

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


def _normalize_content(content: str) -> str:
    """Normalize content for search: lowercase, strip punctuation, collapse whitespace."""
    text = content.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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


def _get_utc_now() -> str:
    """Get current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


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
    now = _get_utc_now()
    memory = MemoryItem(
        id=str(uuid.uuid4()),
        chat_id=request.chat_id,
        character_id=request.character_id,
        type=request.type,
        content=request.content,
        normalized_content=_normalize_content(request.content),
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


def update_memory(memory_id: str, payload: UpdateMemoryRequest) -> MemoryItem | None:
    """Update a memory record. Only updates provided fields."""
    existing = get_memory_by_id(memory_id)
    if existing is None:
        return None

    updates = {}
    update_params = []

    if payload.content is not None:
        updates["content"] = "?"
        update_params.append(payload.content)
        updates["normalized_content"] = "?"
        update_params.append(_normalize_content(payload.content))

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
    update_params.append(_get_utc_now())
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
            (int(pinned), _get_utc_now(), memory_id),
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
            (int(archived), _get_utc_now(), memory_id),
        )
        conn.commit()
        return cursor.rowcount > 0
