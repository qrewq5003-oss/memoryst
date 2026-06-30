"""Stage 4: detect facts about the same entity that disagree with each other.

Consolidation (rolling summary, tiered arc/chapter/book) folds many source
memories into one piece of text. Left alone, an LLM (or the deterministic
fallback) will happily mash "Алиса не пьёт кофе" and "Алиса теперь пьёт латте
по утрам" into one paragraph without saying which one is current. This module
finds those same-entity, different-content pairs ahead of time, picks the most
recent one as authoritative, and (a) records the supersession on the older
memory's own metadata so it's explicit and queryable, and (b) produces a note
that can be injected into the consolidation prompt so the resulting text
states the update instead of silently merging both versions.
"""

from collections import defaultdict
from dataclasses import dataclass

from app.repositories.memory_repo import get_memory_by_id, update_memory
from app.schemas import MemoryItem, MemoryMetadata, UpdateMemoryRequest
from app.services.text_utils import token_overlap_ratio
from app.ui_helpers.consolidation import append_consolidation_history

# Two memories that share an entity are treated as the SAME fact (paraphrase,
# not an update) when their content token-overlap is at or above this floor.
# Below it, they're different claims about that entity - i.e. a conflict.
PARAPHRASE_TOKEN_OVERLAP_FLOOR = 0.6

SUPERSEDED_REVIEW_STATUS = "superseded"
SUPERSEDED_HISTORY_ACTION = "superseded_by_newer_fact"


@dataclass(frozen=True)
class FactConflict:
    entity: str
    current: MemoryItem
    superseded: list[MemoryItem]


def _cluster_by_content(memories: list[MemoryItem]) -> list[list[MemoryItem]]:
    """Group memories (oldest first) into clusters of paraphrased/duplicate content."""
    clusters: list[list[MemoryItem]] = []
    for memory in sorted(memories, key=lambda m: m.created_at):
        for cluster in clusters:
            if token_overlap_ratio(memory.content, cluster[-1].content) >= PARAPHRASE_TOKEN_OVERLAP_FLOOR:
                cluster.append(memory)
                break
        else:
            clusters.append([memory])
    return clusters


def detect_fact_conflicts(memories: list[MemoryItem]) -> list[FactConflict]:
    """
    Find entities described by two-or-more distinct (non-paraphrase) memories
    and resolve each group to one current fact plus the facts it supersedes.

    Operates only on the in-memory list passed in - does not query the
    database, so it's safe to call on any candidate set before consolidation.
    """
    by_entity: dict[str, list[MemoryItem]] = defaultdict(list)
    for memory in memories:
        # build_rolling_summary_text's deterministic fallback is documented to accept
        # any object with a `.content` attribute, not just full MemoryItem rows - so
        # entities (and conflict detection) are simply skipped for those.
        for entity in getattr(getattr(memory, "metadata", None), "entities", None) or []:
            by_entity[entity.lower()].append(memory)

    conflicts: list[FactConflict] = []
    seen_groups: set[tuple[str, ...]] = set()
    for entity, group in by_entity.items():
        unique_group = list({memory.id: memory for memory in group}.values())
        if len(unique_group) < 2:
            continue

        clusters = _cluster_by_content(unique_group)
        if len(clusters) < 2:
            continue  # every memory about this entity is a paraphrase of the same fact

        current = clusters[-1][-1]
        superseded = [memory for cluster in clusters[:-1] for memory in cluster]

        group_key = tuple(sorted(memory.id for memory in superseded) + [current.id])
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)

        conflicts.append(FactConflict(entity=entity, current=current, superseded=superseded))

    return conflicts


def format_conflict_resolution_notes(conflicts: list[FactConflict]) -> str:
    """Render explicit 'this fact changed' lines to inject into a consolidation prompt."""
    lines = []
    for conflict in conflicts:
        old = "; ".join(memory.content.strip() for memory in conflict.superseded)
        lines.append(
            f'Факт про "{conflict.entity}" обновился: было — {old}; '
            f"стало — {conflict.current.content.strip()}. "
            "Отрази в сводке только актуальную версию и то, что факт изменился, "
            "не пересказывай обе версии как отдельные события."
        )
    return "\n".join(lines)


def apply_conflict_resolutions(conflicts: list[FactConflict]) -> None:
    """
    Persist each conflict's resolution onto the superseded memory's own metadata,
    so the supersession is explicit and queryable rather than only living inside
    whatever text the consolidation step happens to produce.
    """
    conflicts_by_memory_id: dict[str, list[FactConflict]] = defaultdict(list)
    for conflict in conflicts:
        for memory in conflict.superseded:
            conflicts_by_memory_id[memory.id].append(conflict)

    for memory_id, related_conflicts in conflicts_by_memory_id.items():
        existing = get_memory_by_id(memory_id)
        if existing is None:
            continue

        latest_conflict = related_conflicts[-1]
        if (
            existing.metadata.review_status == SUPERSEDED_REVIEW_STATUS
            and existing.metadata.related_memory_id == latest_conflict.current.id
        ):
            continue  # already recorded this exact resolution - avoid duplicate history entries

        note = "; ".join(
            f'факт про "{conflict.entity}" заменён на: {conflict.current.content.strip()}'
            for conflict in related_conflicts
        )

        history = existing.metadata.consolidation_history
        for conflict in related_conflicts:
            history = append_consolidation_history(
                MemoryMetadata(consolidation_history=history),
                action=SUPERSEDED_HISTORY_ACTION,
                related_memory_id=conflict.current.id,
                note=f'факт про "{conflict.entity}" заменён на: {conflict.current.content.strip()}',
            )

        update_memory(
            memory_id,
            UpdateMemoryRequest(
                metadata=existing.metadata.model_copy(
                    update={
                        "review_status": SUPERSEDED_REVIEW_STATUS,
                        "related_memory_id": latest_conflict.current.id,
                        "consolidation_note": note,
                        "consolidation_history": history,
                    }
                )
            ),
        )
