import re
from datetime import datetime, timezone

from app.repositories.memory_repo import increment_access_count, list_retrieval_candidates
from app.schemas import (
    MemoryItem,
    RetrieveCandidateDebug,
    RetrieveDebugPayload,
    RetrieveMemoryRequest,
    RetrieveMemoryResponse,
)
from app.services.formatter import format_memory_block
from app.services import text_features

KEYWORD_WEIGHT = 0.50
ENTITY_WEIGHT = 0.25
IMPORTANCE_WEIGHT = 0.12
RECENCY_WEIGHT = 0.03
PINNED_BONUS = 0.05
BOTH_MATCH_BONUS = 0.10
MIN_RETRIEVAL_SCORE = 0.15
NEAR_DUPLICATE_TOKEN_OVERLAP = 0.80


def _compute_score_details(
    memory: MemoryItem,
    input_keywords: list[str],
    input_entities: list[str],
) -> dict[str, float]:
    """
    Compute relevance score components for a memory item.

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
        return {
            "keyword_overlap": keyword_overlap,
            "entity_overlap": entity_overlap,
            "recency": 0.0,
            "score": 0.0,
        }

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
    return {
        "keyword_overlap": keyword_overlap,
        "entity_overlap": entity_overlap,
        "recency": recency,
        "score": min(score, 1.0),
    }


def _compute_score(
    memory: MemoryItem,
    input_keywords: list[str],
    input_entities: list[str],
) -> float:
    return _compute_score_details(memory, input_keywords, input_entities)["score"]


def _normalize_for_similarity(text: str) -> str:
    """Normalize text for retrieval-side near-duplicate checks."""
    normalized = text.lower().strip()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _token_overlap_ratio(text1: str, text2: str) -> float:
    """Compute overlap ratio using the smaller token set as the denominator."""
    tokens1 = set(_normalize_for_similarity(text1).split())
    tokens2 = set(_normalize_for_similarity(text2).split())
    if not tokens1 or not tokens2:
        return 0.0
    return len(tokens1 & tokens2) / min(len(tokens1), len(tokens2))


def _is_too_similar_to_selected(candidate: MemoryItem, selected: list[MemoryItem]) -> bool:
    """Skip near-duplicate memories so top slots stay diverse."""
    candidate_normalized = _normalize_for_similarity(candidate.content)
    if not candidate_normalized:
        return True

    for existing in selected:
        existing_normalized = _normalize_for_similarity(existing.content)
        if candidate_normalized == existing_normalized:
            return True

        overlap_ratio = _token_overlap_ratio(candidate.content, existing.content)
        if overlap_ratio >= NEAR_DUPLICATE_TOKEN_OVERLAP:
            return True

    return False


def retrieve_memories(request: RetrieveMemoryRequest) -> RetrieveMemoryResponse:
    """
    Retrieve relevant memories for the current context.

    Algorithm:
    1. Get candidates via list_memories
    2. Compute score for each
    3. Filter out weak items below minimum retrieval threshold
    4. Sort by score DESC
    5. Take top-k
    6. Update usage metrics for top-k items
    7. Format memory block
    """
    # Extract keywords and entities from user_input
    query_keywords = text_features.extract_keywords(request.user_input)
    query_entities = text_features.extract_entities(request.user_input)
    input_keywords = list(query_keywords)
    input_entities = list(query_entities)
    recent_keywords: list[str] = []
    recent_entities: list[str] = []

    # Also consider recent_messages
    for msg in request.recent_messages:
        msg_keywords = text_features.extract_keywords(msg.text)
        msg_entities = text_features.extract_entities(msg.text)
        recent_keywords.extend(msg_keywords)
        recent_entities.extend(msg_entities)
        input_keywords.extend(msg_keywords)
        input_entities.extend(msg_entities)

    # Get candidates without UI pagination bias
    all_candidates = list_retrieval_candidates(
        chat_id=request.chat_id,
        character_id=request.character_id,
        include_archived=request.include_archived,
    )
    total_candidates = len(all_candidates)

    # Score each candidate
    scored = []
    debug_candidates: list[RetrieveCandidateDebug] = []
    debug_by_id: dict[str, RetrieveCandidateDebug] = {}
    for memory in all_candidates:
        details = _compute_score_details(memory, input_keywords, input_entities)
        score = details["score"]
        passed_threshold = score >= MIN_RETRIEVAL_SCORE
        if passed_threshold:
            scored.append((score, memory))
        if request.debug:
            debug_entry = RetrieveCandidateDebug(
                memory_id=memory.id,
                score=score,
                keyword_overlap=details["keyword_overlap"],
                entity_overlap=details["entity_overlap"],
                recency=details["recency"],
                passed_threshold=passed_threshold,
                reason="threshold_passed" if passed_threshold else "below_threshold",
            )
            debug_candidates.append(debug_entry)
            debug_by_id[memory.id] = debug_entry

    # Sort by score DESC
    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top-k with lightweight anti-redundancy filtering.
    top_items: list[MemoryItem] = []
    for _, item in scored:
        if _is_too_similar_to_selected(item, top_items):
            if request.debug:
                debug_by_id[item.id].filtered_by_diversity = True
                debug_by_id[item.id].reason = "filtered_near_duplicate"
            continue
        if len(top_items) >= request.limit:
            if request.debug:
                debug_by_id[item.id].reason = "not_in_top_limit"
            continue
        top_items.append(item)
        if request.debug:
            debug_by_id[item.id].selected = True
            debug_by_id[item.id].rank = len(top_items)
            debug_by_id[item.id].reason = "selected_top"

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
        debug=(
            RetrieveDebugPayload(
                query_keywords=query_keywords,
                query_entities=query_entities,
                recent_keywords=recent_keywords,
                recent_entities=recent_entities,
                input_keywords=input_keywords,
                input_entities=input_entities,
                candidates=debug_candidates,
            )
            if request.debug
            else None
        ),
    )
