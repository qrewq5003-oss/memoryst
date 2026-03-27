from dataclasses import dataclass
from datetime import datetime, timezone

from app.repositories.memory_repo import create_memory, list_memories, update_memory
from app.schemas import CreateMemoryRequest, MemoryItem, MemoryMetadata, UpdateMemoryRequest
from app.services import text_features

ROLLING_SUMMARY_KIND = "rolling_v1"
DEFAULT_SUMMARY_WINDOW = 8
MIN_SUMMARY_INPUTS = 3
SUMMARY_MAX_SEGMENTS = 3

RELATIONSHIP_HINTS = (
    "довер",
    "ссор",
    "спор",
    "руг",
    "помир",
    "отношен",
    "обещ",
)
GOAL_HINTS = (
    "хочет",
    "хочу",
    "план",
    "собира",
    "цель",
    "решил",
    "решила",
    "договор",
    "поед",
    "сдела",
)
STATE_HINTS = (
    "боит",
    "тревож",
    "устал",
    "злит",
    "важно",
    "переж",
)


@dataclass(frozen=True)
class RollingSummaryResult:
    action: str
    chat_id: str
    character_id: str
    summary_memory_id: str | None
    summary_text: str
    source_memory_ids: list[str]
    summarized_count: int


def _get_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_rolling_summary(memory: MemoryItem) -> bool:
    return (
        memory.type == "summary"
        or (memory.metadata.is_summary and memory.metadata.summary_kind == ROLLING_SUMMARY_KIND)
    )


def _truncate_sentence(text: str, max_length: int = 120) -> str:
    compact = " ".join(text.split()).strip()
    if len(compact) <= max_length:
        return compact.rstrip(".")
    truncated = compact[:max_length].rstrip()
    last_space = truncated.rfind(" ")
    if last_space >= max_length // 2:
        truncated = truncated[:last_space]
    return truncated.rstrip(".")


def _pick_unique_segments(memories: list[MemoryItem], hints: tuple[str, ...], max_count: int = 1) -> list[str]:
    segments: list[str] = []
    seen = set()
    for memory in memories:
        text = memory.content.strip()
        lower = text.lower()
        if hints and not any(hint in lower for hint in hints):
            continue
        normalized = " ".join(lower.split())
        if normalized in seen:
            continue
        seen.add(normalized)
        segments.append(_truncate_sentence(text))
        if len(segments) >= max_count:
            break
    return segments


def build_rolling_summary_text(memories: list[MemoryItem]) -> str:
    """
    Build a deterministic compact summary from recent episodic memories.

    Input memories should already be scoped to one chat/character and ordered oldest -> newest.
    """
    if not memories:
        return ""

    recent_events = _pick_unique_segments(list(reversed(memories)), hints=(), max_count=SUMMARY_MAX_SEGMENTS)
    relationship_updates = _pick_unique_segments(list(reversed(memories)), RELATIONSHIP_HINTS, max_count=1)
    ongoing_goals = _pick_unique_segments(list(reversed(memories)), GOAL_HINTS + STATE_HINTS, max_count=2)

    lines = [f"Краткая сводка последних эпизодов ({len(memories)}):"]
    if recent_events:
        lines.append("Недавние события: " + "; ".join(recent_events[:SUMMARY_MAX_SEGMENTS]) + ".")
    if relationship_updates:
        lines.append("Изменения в отношениях: " + "; ".join(relationship_updates) + ".")
    if ongoing_goals:
        lines.append("Текущие цели и состояние: " + "; ".join(ongoing_goals) + ".")

    return " ".join(lines)


def _build_summary_metadata(memories: list[MemoryItem], summary_text: str) -> MemoryMetadata:
    entity_candidates = text_features.extract_entities(summary_text)
    keyword_candidates = text_features.extract_keywords(summary_text)

    for memory in memories:
        for entity in memory.metadata.entities:
            if entity not in entity_candidates:
                entity_candidates.append(entity)
        for keyword in memory.metadata.keywords:
            if keyword not in keyword_candidates:
                keyword_candidates.append(keyword)

    return MemoryMetadata(
        entities=entity_candidates[:10],
        keywords=keyword_candidates[:12],
        is_summary=True,
        summary_kind=ROLLING_SUMMARY_KIND,
        summary_generated_at=_get_utc_now(),
        summary_source_memory_ids=[memory.id for memory in memories],
        summarized_memory_count=len(memories),
    )


def generate_rolling_summary(
    chat_id: str,
    character_id: str,
    window_size: int = DEFAULT_SUMMARY_WINDOW,
) -> RollingSummaryResult:
    """
    Create or update one rolling summary for the recent episodic memories of a chat/character.
    """
    episodic_memories = list_memories(
        chat_id=chat_id,
        character_id=character_id,
        layer="episodic",
        archived=False,
        limit=max(window_size, 1),
        offset=0,
    ).items
    episodic_memories = [memory for memory in episodic_memories if not memory.metadata.is_summary]

    if len(episodic_memories) < MIN_SUMMARY_INPUTS:
        return RollingSummaryResult(
            action="skipped",
            chat_id=chat_id,
            character_id=character_id,
            summary_memory_id=None,
            summary_text="",
            source_memory_ids=[memory.id for memory in episodic_memories],
            summarized_count=len(episodic_memories),
        )

    selected_memories = list(reversed(episodic_memories[:window_size]))
    summary_text = build_rolling_summary_text(selected_memories)
    summary_metadata = _build_summary_metadata(selected_memories, summary_text)

    existing_summaries = [
        memory
        for memory in list_memories(
            chat_id=chat_id,
            character_id=character_id,
            archived=False,
            limit=50,
            offset=0,
        ).items
        if _is_rolling_summary(memory)
    ]
    existing_summary = existing_summaries[0] if existing_summaries else None

    if existing_summary is None:
        created = create_memory(
            CreateMemoryRequest(
                chat_id=chat_id,
                character_id=character_id,
                type="summary",
                content=summary_text,
                source="auto",
                layer="stable",
                importance=0.9,
                pinned=False,
                archived=False,
                metadata=summary_metadata,
            )
        )
        return RollingSummaryResult(
            action="created",
            chat_id=chat_id,
            character_id=character_id,
            summary_memory_id=created.id,
            summary_text=created.content,
            source_memory_ids=summary_metadata.summary_source_memory_ids,
            summarized_count=len(selected_memories),
        )

    updated = update_memory(
        existing_summary.id,
        UpdateMemoryRequest(
            type="summary",
            content=summary_text,
            importance=0.9,
            metadata=summary_metadata,
        ),
    )
    return RollingSummaryResult(
        action="updated" if updated is not None else "skipped",
        chat_id=chat_id,
        character_id=character_id,
        summary_memory_id=existing_summary.id if updated is None else updated.id,
        summary_text=summary_text,
        source_memory_ids=summary_metadata.summary_source_memory_ids,
        summarized_count=len(selected_memories),
    )
