from collections import defaultdict
from typing import Any

from app.schemas import ConsolidationHistoryEntry, MemoryItem, MemoryMetadata
from app.services.text_utils import normalize_for_similarity
from app.ui_helpers.classifiers import get_activity_bucket, get_freshness_bucket, utc_now


def shared_signal_count(left: MemoryItem, right: MemoryItem) -> int:
    left_signals = set(item.lower() for item in left.metadata.entities + left.metadata.keywords)
    right_signals = set(item.lower() for item in right.metadata.entities + right.metadata.keywords)
    return len(left_signals & right_signals)


def _build_token_index(items: list[MemoryItem]) -> dict[str, list[int]]:
    """Build inverted index: token → list of item indices."""
    index: dict[str, list[int]] = defaultdict(list)
    for i, item in enumerate(items):
        normalized = normalize_for_similarity(item.content)
        for token in set(normalized.split()):
            index[token].append(i)
    return index


def _build_signal_index(items: list[MemoryItem]) -> dict[str, list[int]]:
    """Build inverted index: signal (entity/keyword) → list of stable item indices."""
    index: dict[str, list[int]] = defaultdict(list)
    for i, item in enumerate(items):
        if item.layer != "stable":
            continue
        for signal in item.metadata.entities + item.metadata.keywords:
            index[signal.lower()].append(i)
    return index


def _token_overlap_from_tokens(tokens_i: set[str], tokens_j: set[str]) -> float:
    """Compute overlap ratio from pre-computed token sets."""
    if not tokens_i or not tokens_j:
        return 0.0
    return len(tokens_i & tokens_j) / min(len(tokens_i), len(tokens_j))


def _find_near_duplicate_candidates(
    items: list[MemoryItem],
    token_index: dict[str, list[int]],
) -> set[tuple[int, int]]:
    """Find pairs of near-duplicate items using the token index."""
    pairs: set[tuple[int, int]] = set()
    tokens_cache: dict[int, set[str]] = {}

    for i, item in enumerate(items):
        if item.pinned or item.metadata.review_status == "reviewed_keep":
            continue

        if i not in tokens_cache:
            tokens_cache[i] = set(normalize_for_similarity(item.content).split())
        tokens_i = tokens_cache[i]
        if not tokens_i:
            continue

        candidate_indices: set[int] = set()
        for token in tokens_i:
            for j in token_index.get(token, []):
                if j > i:
                    candidate_indices.add(j)

        for j in candidate_indices:
            other = items[j]
            if other.pinned:
                continue

            if j not in tokens_cache:
                tokens_cache[j] = set(normalize_for_similarity(other.content).split())
            tokens_j = tokens_cache[j]

            if tokens_i == tokens_j:
                pairs.add((i, j))
            elif _token_overlap_from_tokens(tokens_i, tokens_j) >= 0.85:
                pairs.add((i, j))

    return pairs


def build_consolidation_data(items: list[MemoryItem]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    candidate_map: dict[str, list[dict[str, Any]]] = {item.id: [] for item in items}
    summary_counts = {
        "total_candidates": 0,
        "near_duplicate": 0,
        "stale_low_value_episode": 0,
        "shadowed_by_stable": 0,
    }

    token_index = _build_token_index(items)
    signal_index = _build_signal_index(items)

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

    for i, j in _find_near_duplicate_candidates(items, token_index):
        reason = "Near-duplicate content cluster."
        candidate_map[items[i].id].append(
            {
                "type": "near_duplicate",
                "reason": reason,
                "related_id": items[j].id,
            }
        )
        candidate_map[items[j].id].append(
            {
                "type": "near_duplicate",
                "reason": reason,
                "related_id": items[i].id,
            }
        )

    for index, item in enumerate(items):
        if item.pinned or item.metadata.review_status == "reviewed_keep":
            continue
        if item.layer != "episodic" or get_activity_bucket(item) == "active":
            continue

        signals = set(s.lower() for s in item.metadata.entities + item.metadata.keywords)
        candidate_stable_indices: set[int] = set()
        for signal in signals:
            for j in signal_index.get(signal, []):
                candidate_stable_indices.add(j)

        for j in candidate_stable_indices:
            other = items[j]
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
