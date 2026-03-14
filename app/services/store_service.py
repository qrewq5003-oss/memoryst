from app.repositories.memory_repo import (
    create_memory,
    find_memory_by_normalized_content,
    _normalize_content,
)
from app.schemas import CreateMemoryRequest, MemoryItem, StoreMemoryRequest, StoreMemoryResponse
from app.services.extractor import extract_memories


def store_memories(request: StoreMemoryRequest) -> StoreMemoryResponse:
    """
    Store memories from chat messages.

    Process:
    1. Extract memory candidates using rule-based extractor
    2. Check for duplicates using normalized_content
    3. Create new records for non-duplicates
    4. Skip duplicates

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
    updated_count = 0  # TODO: implement update detection
    skipped_count = 0

    for candidate in candidates:
        # Check for duplicate using normalized content
        normalized = _normalize_content(candidate.content)
        existing = find_memory_by_normalized_content(
            chat_id=request.chat_id,
            character_id=request.character_id,
            normalized_content=normalized,
        )

        if existing is not None:
            # Duplicate found, skip
            skipped_count += 1
        else:
            # No duplicate, create new record
            created = create_memory(candidate)
            stored_items.append(created)
            stored_count += 1

    return StoreMemoryResponse(
        stored=stored_count,
        updated=updated_count,
        skipped=skipped_count,
        items=stored_items,
    )
