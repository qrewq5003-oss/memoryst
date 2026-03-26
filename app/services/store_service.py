from app.repositories.memory_repo import (
    create_memory,
    find_memory_by_normalized_content,
    list_memories,
    update_memory,
    _normalize_content,
)
from app.schemas import (
    CreateMemoryRequest,
    MemoryItem,
    StoreMemoryRequest,
    StoreMemoryResponse,
    UpdateMemoryRequest,
    MemoryMetadata,
)
from app.services.extractor import extract_memories
from app.services.deduper import (
    can_auto_update,
    check_soft_match,
    merge_candidate_with_existing,
)
from datetime import datetime, timezone


def _get_utc_now() -> str:
    """Get current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def store_memories(request: StoreMemoryRequest) -> StoreMemoryResponse:
    """
    Store memories from chat messages.

    Process:
    1. Extract memory candidates using rule-based extractor
    2. Check for exact match by normalized_content
    3. Check for soft match by entity/keyword overlap
    4. Update existing auto-records on match
    5. Create new records for non-matches
    6. Skip duplicates (manual/pinned/archived)

    Auto-update rules:
    - Only update records with source = "auto"
    - Never update manual records automatically
    - Never update pinned records automatically
    - Never update archived records automatically

    Returns count of stored, updated, skipped items.
    """
    # Extract candidates
    candidates = extract_memories(
        chat_id=request.chat_id,
        character_id=request.character_id,
        messages=request.messages,
    )

    stored_items: list[MemoryItem] = []
    stored_count = 0
    updated_count = 0
    skipped_count = 0

    for candidate in candidates:
        # Check for exact duplicate using normalized content
        normalized = _normalize_content(candidate.content)
        existing = find_memory_by_normalized_content(
            chat_id=request.chat_id,
            character_id=request.character_id,
            normalized_content=normalized,
        )

        if existing is not None:
            # Exact match found (by normalized_content)
            if not can_auto_update(existing):
                skipped_count += 1
                continue

            # Merge and update - pass is_exact=True for importance boost
            merged, _ = merge_candidate_with_existing(candidate, existing, is_exact=True)

            # Build update request - updated_at will be updated by update_memory
            update_payload = UpdateMemoryRequest(
                importance=merged.importance,
                metadata=merged.metadata,
            )

            # Update content only if merged version has better content
            if merged.content != existing.content:
                update_payload.content = merged.content

            updated = update_memory(existing.id, update_payload)
            if updated:
                stored_items.append(updated)
                updated_count += 1
            else:
                skipped_count += 1
        else:
            # No exact match - check for soft match
            # Get all memories for this chat/character to check soft matches
            all_memories = list_memories(
                chat_id=request.chat_id,
                character_id=request.character_id,
                limit=200,
            ).items
            
            soft_match_found = False
            for existing_memory in all_memories:
                if check_soft_match(candidate, existing_memory):
                    # Soft match found - update existing (is_exact=False for lower importance boost)
                    merged, _ = merge_candidate_with_existing(candidate, existing_memory, is_exact=False)

                    update_payload = UpdateMemoryRequest(
                        importance=merged.importance,
                        metadata=merged.metadata,
                    )

                    if merged.content != existing_memory.content:
                        update_payload.content = merged.content

                    updated = update_memory(existing_memory.id, update_payload)
                    if updated:
                        stored_items.append(updated)
                        updated_count += 1
                    else:
                        skipped_count += 1
                    soft_match_found = True
                    break
            
            if not soft_match_found:
                # No match found - create new record
                created = create_memory(candidate)
                stored_items.append(created)
                stored_count += 1

    return StoreMemoryResponse(
        stored=stored_count,
        updated=updated_count,
        skipped=skipped_count,
        items=stored_items,
    )
