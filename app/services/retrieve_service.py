from datetime import datetime, timezone

from app.repositories.memory_repo import increment_access_count, list_retrieval_candidates
from app.schemas import MemoryItem, RetrieveMemoryRequest, RetrieveMemoryResponse
from app.services.formatter import format_memory_block
from app.services import text_features

KEYWORD_WEIGHT = 0.50
ENTITY_WEIGHT = 0.25
IMPORTANCE_WEIGHT = 0.12
RECENCY_WEIGHT = 0.03
PINNED_BONUS = 0.05
BOTH_MATCH_BONUS = 0.10


def _compute_score(
    memory: MemoryItem,
    input_keywords: list[str],
    input_entities: list[str],
) -> float:
    """
    Compute relevance score for a memory item.

    Formula:
    - keyword_overlap: intersection / max(1, len(input_keywords))
    - entity_overlap: intersection / max(1, len(input_entities))
    - recency: 1 / (1 + days_since_updated)
    - relevance_score is driven primarily by keyword/entity overlap
    - support_score = importance + recency + pinned bonus, scaled down for weak matches
    - bonus for memories matching both keywords and entities
    - cap at 1.0

    Recency is calculated from updated_at, not created_at.
    It is intentionally weak so freshness only helps between otherwise similar candidates.
    """
    # Keyword overlap
    memory_keywords = set(memory.metadata.keywords)
    input_kw_set = set(input_keywords)
    if len(input_kw_set) > 0:
        keyword_overlap = len(memory_keywords & input_kw_set) / len(input_kw_set)
    else:
        keyword_overlap = 0.0

    # Entity overlap
    memory_entities = set(e.lower() for e in memory.metadata.entities)
    input_ent_set = set(e.lower() for e in input_entities)
    if len(input_ent_set) > 0:
        entity_overlap = len(memory_entities & input_ent_set) / len(input_ent_set)
    else:
        entity_overlap = 0.0

    if keyword_overlap == 0.0 and entity_overlap == 0.0:
        return 0.0

    # Recency: 1 / (1 + days_since_updated)
    try:
        updated = datetime.fromisoformat(memory.updated_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_since = (now - updated).days
        recency = 1.0 / (1.0 + days_since)
    except (ValueError, TypeError):
        recency = 0.5  # Default if parsing fails

    relevance_score = (
        keyword_overlap * KEYWORD_WEIGHT +
        entity_overlap * ENTITY_WEIGHT
    )

    both_match_bonus = BOTH_MATCH_BONUS if keyword_overlap > 0.0 and entity_overlap > 0.0 else 0.0

    # Weak matches should not climb mainly on importance or freshness.
    combined_overlap = (keyword_overlap * 0.65) + (entity_overlap * 0.35)
    if combined_overlap >= 0.60:
        support_multiplier = 1.0
    elif combined_overlap >= 0.30:
        support_multiplier = 0.6
    else:
        support_multiplier = 0.25

    support_score = (
        memory.importance * IMPORTANCE_WEIGHT +
        recency * RECENCY_WEIGHT +
        (PINNED_BONUS if memory.pinned else 0.0)
    ) * support_multiplier

    score = relevance_score + both_match_bonus + support_score

    # Cap at 1.0
    return min(score, 1.0)


def retrieve_memories(request: RetrieveMemoryRequest) -> RetrieveMemoryResponse:
    """
    Retrieve relevant memories for the current context.

    Algorithm:
    1. Get candidates via list_memories
    2. Compute score for each
    3. Filter out zero-score items
    4. Sort by score DESC
    5. Take top-k
    6. Update usage metrics for top-k items
    7. Format memory block
    """
    # Extract keywords and entities from user_input
    input_keywords = text_features.extract_keywords(request.user_input)
    input_entities = text_features.extract_entities(request.user_input)

    # Also consider recent_messages
    for msg in request.recent_messages:
        input_keywords.extend(text_features.extract_keywords(msg.text))
        input_entities.extend(text_features.extract_entities(msg.text))

    # Get candidates without UI pagination bias
    all_candidates = list_retrieval_candidates(
        chat_id=request.chat_id,
        character_id=request.character_id,
        include_archived=request.include_archived,
    )
    total_candidates = len(all_candidates)

    # Score each candidate
    scored = []
    for memory in all_candidates:
        score = _compute_score(memory, input_keywords, input_entities)
        if score > 0:
            scored.append((score, memory))

    # Sort by score DESC
    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top-k
    top_items = [item for _, item in scored[: request.limit]]

    # Update usage metrics for top-k items
    # This tracks which memories are being used for retrieval
    for item in top_items:
        increment_access_count(item.id)

    # Format memory block
    memory_block = format_memory_block(top_items)

    return RetrieveMemoryResponse(
        items=top_items,
        memory_block=memory_block,
        total_candidates=total_candidates,
    )
