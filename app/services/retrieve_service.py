import re
from datetime import datetime, timezone
from functools import cmp_to_key

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
CLOSE_SCORE_LAYER_TIE_EPSILON = 0.05
RELATIONSHIP_CUE_WEIGHT = 0.14
RELATIONSHIP_SUPPORT_BONUS_BY_LAYER = {
    "summary": 0.03,
    "stable": 0.03,
    "episodic": 0.015,
}
EPISODIC_SPECIFICITY_BONUS = 0.06
EPISODIC_LOW_VALUE_PENALTY = 0.12
# Narrow support-only policy for Russian relationship/general-state phrasing:
# - gated by `relationship_query_like`
# - uses only the bounded cue groups from text_features
# - cannot replace the main lexical/entity ranking signal
# - must stay regression/eval-backed rather than becoming a general retrieval crutch
#
# Narrow support-only policy for Russian local-scene episodic precision:
# - gated by `local_scene_query_like`
# - only affects episodic candidates
# - helps concrete scene outcomes beat low-value query echoes
# - must not become a generic event heuristic or override a clearly stronger raw match
LAYER_SELECTION_CAPS = {
    "summary": 1,
    "stable": 2,
    "episodic": 2,
}
LAYER_TIE_PRIORITY = {
    "summary": 2,
    "stable": 1,
    "episodic": 0,
}


def _compute_score_details(
    memory: MemoryItem,
    input_keywords: list[str],
    input_entities: list[str],
    *,
    user_input_text: str,
    local_scene_query_like: bool = False,
    relationship_query_like: bool = False,
    input_relationship_cues: list[str] | None = None,
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

    # Auxiliary cue overlap only exists for the explicitly gated Russian
    # relationship/general-state query family. It supports wording variation,
    # but should never turn retrieval into generic semantic matching.
    input_relationship_cue_set = set(input_relationship_cues or [])
    memory_relationship_cues = set(text_features.extract_relationship_state_cues(memory.content))
    if relationship_query_like and input_relationship_cue_set:
        relationship_cue_overlap = (
            len(memory_relationship_cues & input_relationship_cue_set) / len(input_relationship_cue_set)
        )
    else:
        relationship_cue_overlap = 0.0

    if keyword_overlap == 0.0 and entity_overlap == 0.0 and relationship_cue_overlap == 0.0:
        return {
            "keyword_overlap": keyword_overlap,
            "entity_overlap": entity_overlap,
            "relationship_cue_overlap": relationship_cue_overlap,
            "relationship_support_bonus": 0.0,
            "episodic_detail_score": 0.0,
            "episodic_specificity_bonus": 0.0,
            "episodic_low_value_penalty": 0.0,
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
        entity_overlap * ENTITY_WEIGHT +
        relationship_cue_overlap * RELATIONSHIP_CUE_WEIGHT
    )

    both_match_bonus = BOTH_MATCH_BONUS if keyword_overlap > 0.0 and entity_overlap > 0.0 else 0.0
    # Keep this as a narrow support bonus. It should help eligible summary/stable
    # relationship memories survive thresholding, not override a clearly stronger
    # raw lexical match from another candidate.
    relationship_support_bonus = 0.0
    if relationship_query_like and relationship_cue_overlap > 0.0:
        relationship_support_bonus = RELATIONSHIP_SUPPORT_BONUS_BY_LAYER[_get_retrieval_layer(memory)]

    episodic_detail_score = 0.0
    episodic_specificity_bonus = 0.0
    episodic_low_value_penalty = 0.0
    if memory.layer == "episodic":
        episodic_detail_score = text_features.extract_local_scene_detail_score(memory.content)
        # Keep this as a bounded support bonus for concrete scene outcomes only.
        if local_scene_query_like and episodic_detail_score >= 0.45:
            episodic_specificity_bonus = EPISODIC_SPECIFICITY_BONUS

        # Penalize question-like or query-echo episodic lines that add little scene value.
        # This is an anti-noise guardrail, not a universal episodic penalty.
        normalized_query = _normalize_for_similarity(user_input_text)
        normalized_memory = _normalize_for_similarity(memory.content)
        question_like_memory = memory.content.strip().endswith("?")
        query_echo_like = (
            normalized_query != ""
            and normalized_memory != ""
            and (
                normalized_memory == normalized_query
                or normalized_memory in normalized_query
                or _token_overlap_ratio(memory.content, user_input_text) >= 0.85
            )
        )
        if question_like_memory and query_echo_like and episodic_detail_score < 0.45:
            episodic_low_value_penalty = EPISODIC_LOW_VALUE_PENALTY

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

    score = (
        relevance_score
        + both_match_bonus
        + relationship_support_bonus
        + episodic_specificity_bonus
        + support_score
        - episodic_low_value_penalty
    )

    # Cap at 1.0
    return {
        "keyword_overlap": keyword_overlap,
        "entity_overlap": entity_overlap,
        "relationship_cue_overlap": relationship_cue_overlap,
        "relationship_support_bonus": relationship_support_bonus,
        "episodic_detail_score": episodic_detail_score,
        "episodic_specificity_bonus": episodic_specificity_bonus,
        "episodic_low_value_penalty": episodic_low_value_penalty,
        "recency": recency,
        "score": min(max(score, 0.0), 1.0),
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


def _get_retrieval_layer(memory: MemoryItem) -> str:
    if memory.type == "summary" or memory.metadata.is_summary:
        return "summary"
    if memory.layer == "stable":
        return "stable"
    return "episodic"


def _compare_scored_entries(left: dict[str, object], right: dict[str, object]) -> int:
    left_score = float(left["score"])
    right_score = float(right["score"])
    score_diff = left_score - right_score
    if abs(score_diff) > CLOSE_SCORE_LAYER_TIE_EPSILON:
        return -1 if left_score > right_score else 1

    left_layer = str(left["layer"])
    right_layer = str(right["layer"])
    if left_layer != right_layer:
        left_priority = LAYER_TIE_PRIORITY[left_layer]
        right_priority = LAYER_TIE_PRIORITY[right_layer]
        if left_priority != right_priority:
            return -1 if left_priority > right_priority else 1

    left_memory = left["memory"]
    right_memory = right["memory"]
    if left_memory.updated_at != right_memory.updated_at:  # type: ignore[union-attr]
        return -1 if left_memory.updated_at > right_memory.updated_at else 1  # type: ignore[union-attr]
    if left_memory.id != right_memory.id:  # type: ignore[union-attr]
        return -1 if left_memory.id > right_memory.id else 1  # type: ignore[union-attr]
    return 0


def _sort_scored_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(entries, key=cmp_to_key(_compare_scored_entries))


def _try_select_entry(
    entry: dict[str, object],
    *,
    top_items: list[MemoryItem],
    selected_ids: set[str],
    layer_selected_counts: dict[str, int],
    debug_by_id: dict[str, RetrieveCandidateDebug],
    request_debug: bool,
) -> bool:
    memory = entry["memory"]
    memory_id = memory.id
    layer = entry["layer"]

    if memory_id in selected_ids:
        return False

    if _is_too_similar_to_selected(memory, top_items):
        if request_debug:
            debug_by_id[memory_id].filtered_by_diversity = True
            debug_by_id[memory_id].reason = "filtered_near_duplicate"
        return False

    top_items.append(memory)
    selected_ids.add(memory_id)
    layer_selected_counts[layer] += 1
    if request_debug:
        debug_by_id[memory_id].selected = True
        debug_by_id[memory_id].selected_from_layer = layer
        debug_by_id[memory_id].rank = len(top_items)
        debug_by_id[memory_id].reason = "selected_top"
    return True


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
    query_relationship_cues = text_features.extract_relationship_state_cues(request.user_input)
    local_scene_query_like = text_features.is_local_scene_query(request.user_input)
    relationship_query_like = text_features.is_relationship_state_query(request.user_input)
    input_keywords = list(query_keywords)
    input_entities = list(query_entities)
    recent_keywords: list[str] = []
    recent_entities: list[str] = []
    recent_relationship_cues: list[str] = []

    # Also consider recent_messages
    for msg in request.recent_messages:
        msg_keywords = text_features.extract_keywords(msg.text)
        msg_entities = text_features.extract_entities(msg.text)
        msg_relationship_cues = text_features.extract_relationship_state_cues(msg.text)
        recent_keywords.extend(msg_keywords)
        recent_entities.extend(msg_entities)
        recent_relationship_cues.extend(msg_relationship_cues)
        input_keywords.extend(msg_keywords)
        input_entities.extend(msg_entities)
        if relationship_query_like:
            query_relationship_cues.extend(msg_relationship_cues)

    input_relationship_cues = list(dict.fromkeys(query_relationship_cues))

    # Get candidates without UI pagination bias
    all_candidates = list_retrieval_candidates(
        chat_id=request.chat_id,
        character_id=request.character_id,
        include_archived=request.include_archived,
    )
    total_candidates = len(all_candidates)

    # Score each candidate and partition them into explicit retrieval layers.
    scored_entries: list[dict[str, object]] = []
    debug_candidates: list[RetrieveCandidateDebug] = []
    debug_by_id: dict[str, RetrieveCandidateDebug] = {}
    layer_candidate_counts = {
        "summary": 0,
        "stable": 0,
        "episodic": 0,
    }
    for memory in all_candidates:
        layer = _get_retrieval_layer(memory)
        layer_candidate_counts[layer] += 1
        details = _compute_score_details(
            memory,
            input_keywords,
            input_entities,
            user_input_text=request.user_input,
            local_scene_query_like=local_scene_query_like,
            relationship_query_like=relationship_query_like,
            input_relationship_cues=input_relationship_cues,
        )
        score = details["score"]
        passed_threshold = score >= MIN_RETRIEVAL_SCORE
        if passed_threshold:
            scored_entries.append(
                {
                    "memory": memory,
                    "score": score,
                    "layer": layer,
                }
            )
        if request.debug:
            debug_entry = RetrieveCandidateDebug(
                memory_id=memory.id,
                layer=layer,
                score=score,
                keyword_overlap=details["keyword_overlap"],
                entity_overlap=details["entity_overlap"],
                relationship_cue_overlap=details["relationship_cue_overlap"],
                relationship_support_bonus=details["relationship_support_bonus"],
                episodic_detail_score=details["episodic_detail_score"],
                episodic_specificity_bonus=details["episodic_specificity_bonus"],
                episodic_low_value_penalty=details["episodic_low_value_penalty"],
                recency=details["recency"],
                passed_threshold=passed_threshold,
                reason="threshold_passed" if passed_threshold else "below_threshold",
            )
            debug_candidates.append(debug_entry)
            debug_by_id[memory.id] = debug_entry

    score_by_id = {entry["memory"].id: entry["score"] for entry in scored_entries}
    scored_by_layer = {
        layer: _sort_scored_entries([entry for entry in scored_entries if entry["layer"] == layer])
        for layer in ("summary", "stable", "episodic")
    }
    globally_sorted = _sort_scored_entries(scored_entries)

    # Layered retrieval policy:
    # 1. If limit allows, seed one item from each layer so summary/stable/episodic can all contribute.
    # 2. Fill remaining slots with the best raw-score items under per-layer caps.
    # 3. If slots still remain, do a final fill without caps so recall is not artificially cut off.
    #
    # Composition is driven by the explicit layer policy above, not by hidden score boosts.
    # Raw score remains the main ranking signal inside and across the selected layers.
    # Only near-ties can prefer a more durable layer as an explicit tiebreak.
    top_items: list[MemoryItem] = []
    selected_ids: set[str] = set()
    layer_selected_counts = {
        "summary": 0,
        "stable": 0,
        "episodic": 0,
    }

    if request.limit >= 3:
        for layer in ("summary", "stable", "episodic"):
            for entry in scored_by_layer[layer]:
                if _try_select_entry(
                    entry,
                    top_items=top_items,
                    selected_ids=selected_ids,
                    layer_selected_counts=layer_selected_counts,
                    debug_by_id=debug_by_id,
                    request_debug=request.debug,
                ):
                    break
            if len(top_items) >= request.limit:
                break

    for entry in globally_sorted:
        if len(top_items) >= request.limit:
            break
        layer = entry["layer"]
        if layer_selected_counts[layer] >= LAYER_SELECTION_CAPS[layer]:
            continue
        _try_select_entry(
            entry,
            top_items=top_items,
            selected_ids=selected_ids,
            layer_selected_counts=layer_selected_counts,
            debug_by_id=debug_by_id,
            request_debug=request.debug,
        )

    for entry in globally_sorted:
        if len(top_items) >= request.limit:
            break
        _try_select_entry(
            entry,
            top_items=top_items,
            selected_ids=selected_ids,
            layer_selected_counts=layer_selected_counts,
            debug_by_id=debug_by_id,
            request_debug=request.debug,
        )

    top_items.sort(
        key=lambda item: (
            score_by_id.get(item.id, 0.0),
            item.updated_at,
            item.id,
        ),
        reverse=True,
    )

    if request.debug:
        for rank, item in enumerate(top_items, start=1):
            debug_by_id[item.id].rank = rank
        for entry in globally_sorted:
            memory_id = entry["memory"].id
            if memory_id not in selected_ids and debug_by_id[memory_id].reason == "threshold_passed":
                debug_by_id[memory_id].reason = "not_in_top_limit"

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
                relationship_query_like=relationship_query_like,
                local_scene_query_like=local_scene_query_like,
                query_relationship_cues=text_features.extract_relationship_state_cues(request.user_input),
                recent_relationship_cues=recent_relationship_cues,
                input_relationship_cues=input_relationship_cues,
                summary_candidates=layer_candidate_counts["summary"],
                stable_candidates=layer_candidate_counts["stable"],
                episodic_candidates=layer_candidate_counts["episodic"],
                selected_summary=layer_selected_counts["summary"],
                selected_stable=layer_selected_counts["stable"],
                selected_episodic=layer_selected_counts["episodic"],
                candidates=debug_candidates,
            )
            if request.debug
            else None
        ),
    )
