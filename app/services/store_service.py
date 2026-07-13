import sys

from app.repositories.memory_repo import (
    create_memory,
    find_memory_by_normalized_content,
    list_memories,
    update_memory,
)
from app.schemas import (
    CreateMemoryRequest,
    MemoryItem,
    MemoryMetadata,
    StoreCandidateDebug,
    StoreDebugPayload,
    StoreMemoryRequest,
    StoreMemoryResponse,
    UpdateMemoryRequest,
)
from app.services import chat_buffer_service
from app.services import tracker_service
from app.services.scene_extractor import extract_scene_memories
from app.services.deduper import (
    can_auto_update,
    check_soft_match,
    merge_candidate_with_existing,
)
from app.services.text_utils import (
    get_utc_now,
    normalize_content as _normalize_content,
    normalize_for_similarity as _normalize_quality_text,
)
from app.services import vector_store

MIN_MEMORY_CONTENT_LENGTH = 12
MIN_MEMORY_WORD_COUNT = 3
LOW_VALUE_PATTERNS = {
    "ok",
    "okay",
    "yes",
    "yeah",
    "yep",
    "no",
    "nope",
    "i understand",
    "understood",
    "got it",
    "we talked",
    "мы говорили",
    "понял",
    "поняла",
    "понятно",
    "хорошо",
    "ладно",
    "да",
    "нет",
}


def _evaluate_memory_quality_gate(candidate: CreateMemoryRequest) -> tuple[bool, str]:
    """Explain whether an extracted candidate is strong enough to store."""
    if candidate.source != "auto":
        return True, "non_auto_bypass"

    content = candidate.content.strip()
    if not content:
        return False, "empty_content"

    normalized = _normalize_quality_text(content)
    if not normalized:
        return False, "empty_after_normalization"
    if normalized in LOW_VALUE_PATTERNS:
        return False, "low_value_pattern"

    words = normalized.split()
    if len(content) < MIN_MEMORY_CONTENT_LENGTH:
        return False, "content_too_short"
    if len(words) < MIN_MEMORY_WORD_COUNT:
        return False, "too_few_words"

    if len(candidate.metadata.keywords) >= 2:
        return True, "has_keywords"
    if len(candidate.metadata.entities) >= 1:
        return True, "has_entities"
    if len(words) >= 5:
        return True, "rich_content"

    return False, "insufficient_retrieval_signal"


def passes_memory_quality_gate(candidate: CreateMemoryRequest) -> bool:
    """Return True when an auto-extracted candidate is informative enough to store."""
    if candidate.source != "auto":
        return True

    return _evaluate_memory_quality_gate(candidate)[0]


def store_memories(request: StoreMemoryRequest) -> StoreMemoryResponse:
    """
    Store memories from chat messages.

    Process:
    1. Cool messages into the raw-history buffer to get stable message ids (Stage 2)
    2. Extract memory candidates from the whole scene via LLM, regex pre-filtered (Stage 3)
    3. Check for exact match by normalized_content
    4. Check for soft match by entity/keyword overlap
    5. Update existing auto-records on match
    6. Create new records for non-matches
    7. Skip duplicates (manual/pinned/archived)

    Auto-update rules:
    - Only update records with source = "auto"
    - Never update manual records automatically
    - Never update pinned records automatically
    - Never update archived records automatically

    Returns count of stored, updated, skipped items.
    """
    # Assign stable ids to incoming messages (OOC/system filtered here, see
    # chat_buffer_service) before extraction, so extracted facts can carry
    # source_message_ids back to chat_messages.
    buffered_messages = chat_buffer_service.add_messages(
        request.chat_id, request.character_id, request.messages
    )

    # TEMP DEBUG (see CLAUDE.md investigation): confirms what the caller actually
    # sent, independent of whatever extract_scene_facts logs on failure - lets us
    # tell "extension didn't send model" apart from "model was sent but still failed".
    print(
        f"[store_service] chat={request.chat_id} char={request.character_id}: "
        f"request.model={request.model!r}",
        file=sys.stderr,
        flush=True,
    )

    # Extract candidates from the whole scene at once
    candidates, extraction_method = extract_scene_memories(
        chat_id=request.chat_id,
        character_id=request.character_id,
        messages=buffered_messages,
        model=request.model,
    )

    stored_items: list[MemoryItem] = []
    stored_count = 0
    updated_count = 0
    skipped_count = 0
    debug_candidates: list[StoreCandidateDebug] = []

    # Load existing memories once for soft-match checks (not per candidate)
    existing_memories = list_memories(
        chat_id=request.chat_id,
        character_id=request.character_id,
        limit=200,
    ).items

    for candidate in candidates:
        normalized = _normalize_content(candidate.content)
        quality_ok, quality_reason = _evaluate_memory_quality_gate(candidate)
        if not quality_ok:
            skipped_count += 1
            if request.debug:
                debug_candidates.append(
                    StoreCandidateDebug(
                        content=candidate.content,
                        normalized_content=normalized,
                        decision="skipped_low_value",
                        reason=quality_reason,
                        branch="quality_gate",
                    )
                )
            continue

        # Check for exact duplicate using normalized content
        existing = find_memory_by_normalized_content(
            chat_id=request.chat_id,
            character_id=request.character_id,
            normalized_content=normalized,
        )

        if existing is not None:
            # Exact match found (by normalized_content)
            if not can_auto_update(existing):
                skipped_count += 1
                if request.debug:
                    debug_candidates.append(
                        StoreCandidateDebug(
                            content=candidate.content,
                            normalized_content=normalized,
                            decision="skipped_exact_protected",
                            reason="exact_match_not_auto_updatable",
                            branch="exact",
                            matched_existing_id=existing.id,
                        )
                    )
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
                if request.debug:
                    debug_candidates.append(
                        StoreCandidateDebug(
                            content=candidate.content,
                            normalized_content=normalized,
                            decision="updated",
                            reason="exact_match_auto_updated",
                            branch="exact",
                            matched_existing_id=existing.id,
                        )
                    )
            else:
                skipped_count += 1
                if request.debug:
                    debug_candidates.append(
                        StoreCandidateDebug(
                            content=candidate.content,
                            normalized_content=normalized,
                            decision="skipped_other",
                            reason="exact_match_update_failed",
                            branch="exact",
                            matched_existing_id=existing.id,
                        )
                    )
        else:
            # No exact match - check for soft match
            soft_match_found = False
            for existing_memory in existing_memories:
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
                        if request.debug:
                            debug_candidates.append(
                                StoreCandidateDebug(
                                    content=candidate.content,
                                    normalized_content=normalized,
                                    decision="updated",
                                    reason="soft_match_auto_updated",
                                    branch="soft",
                                    matched_existing_id=existing_memory.id,
                                )
                            )
                    else:
                        skipped_count += 1
                        if request.debug:
                            debug_candidates.append(
                                StoreCandidateDebug(
                                    content=candidate.content,
                                    normalized_content=normalized,
                                    decision="skipped_other",
                                    reason="soft_match_update_failed",
                                    branch="soft",
                                    matched_existing_id=existing_memory.id,
                                )
                            )
                    soft_match_found = True
                    break
            
            if not soft_match_found:
                # No match found - create new record
                created = create_memory(candidate)
                stored_items.append(created)
                stored_count += 1
                vector_store.add_memory(
                    created.id,
                    created.content,
                    {"chat_id": created.chat_id, "character_id": created.character_id},
                )
                if request.debug:
                    debug_candidates.append(
                        StoreCandidateDebug(
                            content=candidate.content,
                            normalized_content=normalized,
                            decision="stored",
                            reason="new_memory_created",
                            branch="new",
                        )
                    )

    print(
        f"[store_service] chat={request.chat_id} char={request.character_id}: "
        f"extraction_method={extraction_method} {len(candidates)} candidates -> "
        f"stored={stored_count} updated={updated_count} skipped={skipped_count}",
        file=sys.stderr,
        flush=True,
    )

    return StoreMemoryResponse(
        stored=stored_count,
        updated=updated_count,
        skipped=skipped_count,
        items=stored_items,
        debug=StoreDebugPayload(candidates=debug_candidates) if request.debug else None,
        extraction_method=extraction_method,
        # Piggybacked, not fetched: the extension already calls /memory/store every turn,
        # so its reminder toast costs no extra request. Empty until a tracker exists.
        trackers=tracker_service.list_tracker_counters(request.chat_id, request.character_id),
    )
