from typing import Any

from app.schemas import ConsolidationHistoryEntry, MemoryItem, MemoryMetadata
from app.services.text_utils import normalize_for_similarity, token_overlap_ratio
from app.ui_helpers.classifiers import get_activity_bucket, get_freshness_bucket, utc_now


def shared_signal_count(left: MemoryItem, right: MemoryItem) -> int:
    left_signals = set(item.lower() for item in left.metadata.entities + left.metadata.keywords)
    right_signals = set(item.lower() for item in right.metadata.entities + right.metadata.keywords)
    return len(left_signals & right_signals)


def build_consolidation_data(items: list[MemoryItem]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    candidate_map: dict[str, list[dict[str, Any]]] = {item.id: [] for item in items}
    summary_counts = {
        "total_candidates": 0,
        "near_duplicate": 0,
        "stale_low_value_episode": 0,
        "shadowed_by_stable": 0,
    }

    for index, item in enumerate(items):
        if item.pinned or item.metadata.review_status == "reviewed_keep":
            continue

        freshness = get_freshness_bucket(item)
        activity = get_activity_bucket(item)

        if item.layer == "episodic" and freshness == "stale" and activity in {"never_used", "low_use"}:
            candidate_map[item.id].append(
                {
                    "type": "stale_low_value_episode",
                    "reason": "Stale episodic memory with low retrieval activity.",
                }
            )

        for other_index in range(index + 1, len(items)):
            other = items[other_index]
            if item.pinned or other.pinned:
                continue

            overlap = token_overlap_ratio(item.content, other.content)
            exact_duplicate = normalize_for_similarity(item.content) == normalize_for_similarity(other.content)
            if exact_duplicate or overlap >= 0.85:
                reason = "Near-duplicate content cluster."
                candidate_map[item.id].append(
                    {
                        "type": "near_duplicate",
                        "reason": reason,
                        "related_id": other.id,
                    }
                )
                candidate_map[other.id].append(
                    {
                        "type": "near_duplicate",
                        "reason": reason,
                        "related_id": item.id,
                    }
                )

        if item.layer == "episodic" and activity != "active":
            for other in items:
                if other.id == item.id or other.layer != "stable":
                    continue
                if shared_signal_count(item, other) >= 2:
                    candidate_map[item.id].append(
                        {
                            "type": "shadowed_by_stable",
                            "reason": "Similar topic already represented by a stable memory.",
                            "related_id": other.id,
                        }
                    )
                    break

    unique_type_counts = {
        "near_duplicate": 0,
        "stale_low_value_episode": 0,
        "shadowed_by_stable": 0,
    }
    total_candidates = 0
    for candidate_list in candidate_map.values():
        if candidate_list:
            total_candidates += 1
        for candidate_type in unique_type_counts:
            if any(candidate["type"] == candidate_type for candidate in candidate_list):
                unique_type_counts[candidate_type] += 1

    summary_counts["total_candidates"] = total_candidates
    summary_counts.update(unique_type_counts)
    return candidate_map, summary_counts


def build_consolidation_result(action: str, memory_id: str, related_memory_id: str | None, note: str | None) -> dict[str, Any]:
    labels = {
        "mark_consolidated_archive": "Candidate archived for consolidation review.",
        "mark_reviewed_keep": "Candidate marked as reviewed and kept.",
        "link_to_related_memory": "Candidate linked to related memory.",
    }
    return {
        "memory_id": memory_id,
        "action": action,
        "message": labels.get(action, "Consolidation action applied."),
        "related_memory_id": related_memory_id or None,
        "note": note or None,
    }


def append_consolidation_history(
    metadata: MemoryMetadata,
    action: str,
    related_memory_id: str | None,
    note: str | None,
) -> list[ConsolidationHistoryEntry]:
    entry = ConsolidationHistoryEntry(
        action=action,
        timestamp=utc_now().isoformat(),
        related_memory_id=related_memory_id or None,
        note=note or None,
    )
    return [*metadata.consolidation_history, entry]
