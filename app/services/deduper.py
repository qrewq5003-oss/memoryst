from app.schemas import CreateMemoryRequest, MemoryItem, MemoryMetadata


def _merge_lists(list1: list[str], list2: list[str]) -> list[str]:
    """Merge two lists without duplicates, preserving order."""
    result = list1.copy()
    for item in list2:
        if item not in result:
            result.append(item)
    return result


def _is_better_content(new_content: str, old_content: str) -> bool:
    """
    Check if new content is better than old content.
    
    Simple heuristic: longer content is better (more detailed).
    """
    # Only consider it better if significantly longer (10+ chars more)
    return len(new_content) > len(old_content) + 10


def check_exact_match(
    candidate: CreateMemoryRequest,
    existing: MemoryItem,
    candidate_normalized: str | None = None,
) -> bool:
    """
    Check if candidate exactly matches existing memory.

    Exact match criteria:
    - Same chat_id
    - Same character_id
    - Same type
    - Same normalized_content

    If candidate_normalized is provided, use it. Otherwise compare existing.normalized_content.
    """
    if (
        candidate.chat_id != existing.chat_id
        or candidate.character_id != existing.character_id
        or candidate.type != existing.type
    ):
        return False

    # Compare normalized content
    if candidate_normalized is not None:
        return candidate_normalized == existing.normalized_content
    
    # Fallback: compare existing normalized_content directly
    # (candidate doesn't have normalized_content, so this is for soft-match branch)
    return False


def check_soft_match(
    candidate: CreateMemoryRequest,
    existing: MemoryItem,
) -> bool:
    """
    Check if candidate soft-matches existing memory.
    
    Soft match criteria:
    - Same chat_id
    - Same character_id
    - Same type
    - existing.source = "auto"
    - existing.pinned = false
    - existing.archived = false
    - entity_overlap >= 1
    - keyword_overlap >= 2
    
    # Эвристика может давать ложные совпадения при большом объёме памяти
    """
    # Check basic filters
    if existing.source != "auto":
        return False
    if existing.pinned:
        return False
    if existing.archived:
        return False
    
    if (
        candidate.chat_id != existing.chat_id
        or candidate.character_id != existing.character_id
        or candidate.type != existing.type
    ):
        return False
    
    # Calculate entity overlap
    candidate_entities = set(e.lower() for e in candidate.metadata.entities)
    existing_entities = set(e.lower() for e in existing.metadata.entities)
    entity_overlap = len(candidate_entities & existing_entities)
    
    if entity_overlap < 1:
        return False
    
    # Calculate keyword overlap
    candidate_keywords = set(candidate.metadata.keywords)
    existing_keywords = set(existing.metadata.keywords)
    keyword_overlap = len(candidate_keywords & existing_keywords)
    
    if keyword_overlap < 2:
        return False
    
    return True


def merge_candidate_with_existing(
    candidate: CreateMemoryRequest,
    existing: MemoryItem,
    is_exact: bool = False,
) -> tuple[CreateMemoryRequest, bool]:
    """
    Merge candidate with existing memory.

    Args:
    - candidate: new memory candidate
    - existing: existing memory to update
    - is_exact: True if this is an exact match (for importance boost)

    Returns:
    - Merged CreateMemoryRequest
    - Boolean indicating if content was updated

    Merge rules:
    - Combine entities without duplicates
    - Combine keywords without duplicates
    - Update importance: min(old + 0.05, 1.0) for exact, min(old + 0.03, 1.0) for soft
    - Update content only if new content is clearly better
    """
    importance_boost = 0.05 if is_exact else 0.03
    
    # Merge metadata
    merged_entities = _merge_lists(
        list(existing.metadata.entities),
        list(candidate.metadata.entities),
    )
    merged_keywords = _merge_lists(
        list(existing.metadata.keywords),
        list(candidate.metadata.keywords),
    )
    
    # Decide whether to update content
    content_updated = False
    final_content = existing.content
    
    if existing.source == "auto" and _is_better_content(candidate.content, existing.content):
        final_content = candidate.content
        content_updated = True
    
    return CreateMemoryRequest(
        chat_id=existing.chat_id,
        character_id=existing.character_id,
        type=existing.type,
        content=final_content,
        source="auto",
        layer=existing.layer,
        importance=min(existing.importance + importance_boost, 1.0),
        pinned=existing.pinned,
        archived=existing.archived,
        metadata=MemoryMetadata(
            entities=merged_entities,
            keywords=merged_keywords,
        ),
    ), content_updated
