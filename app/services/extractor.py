import re

from app.schemas import CreateMemoryRequest, MemoryMetadata, MemoryType, MessageInput
from app.services import text_features

PREFERENCE_MARKERS_RU = [
    "мне нравится",
    "люблю",
    "обожаю",
    "предпочитаю",
    "предпочитает",
    "любимый",
    "любимая",
    "ненавижу",
    "не люблю",
    "интересуюсь",
    "интересуется",
]

PREFERENCE_MARKERS_EN = [
    "i like",
    "i love",
    "my favorite",
    "interested in",
]

PROFILE_MARKERS_RU = [
    "владеет",
    "говорит на",
]

PROFILE_MARKERS_EN = [
    "is from",
    "works as",
    "works at",
    "studies at",
    "lives in",
    "born in",
]

RELATIONSHIP_MARKERS_RU = [
    "доверяю",
    "доверяет",
    "доверял",
    "доверяла",
    "забочусь",
    "заботится",
    "друг",
    "подруга",
    "брат",
    "сестра",
    "муж",
    "жена",
    "коллега",
    "встречается",
    "женат",
    "замужем",
    "отношени",
    "напряжени",
    "дистанци",
    "осторож",
    "поддерж",
    "сотруднич",
    "помир",
]

RELATIONSHIP_MARKERS_EN = [
    "trust",
    "trusts",
    "care about",
    "cares about",
    "friend",
    "brother",
    "sister",
    "husband",
    "wife",
    "colleague",
    "dating",
    "married",
    "relationship",
]

TEMPORAL_MARKERS_RU = [
    "вчера",
    "сегодня",
    "завтра",
    "утром",
    "вечером",
    "ночью",
    "позже",
]

TEMPORAL_MARKERS_EN = [
    "yesterday",
    "today",
    "tomorrow",
    "this morning",
    "this evening",
    "tonight",
    "later",
]

EVENT_ACTION_MARKERS_RU = [
    "обсуждали",
    "решили",
    "пошли",
    "встретил",
    "встретила",
    "встретились",
    "сказал",
    "сказала",
    "поехал",
    "поехала",
    "поехали",
    "сделал",
    "сделала",
    "сделали",
    "запланировал",
    "запланировала",
    "договорились",
    "поссорились",
    "спорили",
    "ругались",
    "сорвался",
    "сорвалась",
    "началась",
]

EVENT_ACTION_MARKERS_EN = [
    "discussed",
    "decided",
    "went",
    "met",
    "said",
    "told",
    "did",
    "planned",
    "argued",
    "fought",
    "visited",
    "called",
    "booked",
]

PREFERENCE_PATTERNS_EN = [
    r"\b(?:i|you|we|they|he|she)\s+(?:like|likes|love|loves|prefer|prefers|enjoy|enjoys|hate|hates)\b",
]

PREFERENCE_PATTERNS_EN_CASED = [
    r"\b[A-Z][a-z'-]+\s+(?:likes|loves|prefers|enjoys|hates)\b",
]

PROFILE_PATTERNS_EN = [
    r"\bis (?:a|an) [a-z][a-z\s-]{0,30}\b",
    r"\bis from [a-z][a-z\s-]{1,30}\b",
    r"\blives in [a-z][a-z\s-]{1,30}\b",
    r"\bworks as [a-z][a-z\s-]{1,30}\b",
    r"\bhas [a-z\s-]{0,20}(eyes|hair|accent)\b",
    r"\b(?:i|you|we|they|he|she|[a-z][a-z'-]+)\s+owns [a-z][a-z\s-]{1,30}\b",
    r"\b(?:i|you|we|they|he|she|[a-z][a-z'-]+)\s+speaks [a-z][a-z\s-]{1,30}\b",
]

PROFILE_PATTERNS_RU = [
    r"\bработает [а-яё-]+(?:ом|ем)\b",
    r"\bживет в [а-яё][а-яё\s-]{1,30}\b|\bживёт в [а-яё][а-яё\s-]{1,30}\b",
    r"\bродом из [а-яё][а-яё\s-]{1,30}\b",
    r"\bучится(?: в [а-яё][а-яё\s-]{1,30})?\b",
    r"\b(?:[а-яё][а-яё-]+)\s+(?:врач|доктор|учитель|студент|программист)\b",
    r"\b(?:зеленые|зелёные|карие|голубые) глаза\b",
    r"\bвладеет\b",
]


def _contains_any(text_lower: str, markers: list[str]) -> bool:
    return any(marker in text_lower for marker in markers)


def _matches_any_pattern(text_lower: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text_lower) for pattern in patterns)


def _looks_like_preference(text: str, text_lower: str) -> bool:
    if _contains_any(text_lower, PREFERENCE_MARKERS_RU + PREFERENCE_MARKERS_EN):
        return True
    if _matches_any_pattern(text_lower, PREFERENCE_PATTERNS_EN):
        return True
    return _matches_any_pattern(text, PREFERENCE_PATTERNS_EN_CASED)


def _looks_like_profile(text_lower: str) -> bool:
    if _contains_any(text_lower, PROFILE_MARKERS_RU + PROFILE_MARKERS_EN):
        return True
    return _matches_any_pattern(text_lower, PROFILE_PATTERNS_RU + PROFILE_PATTERNS_EN)


def _looks_like_relationship(text_lower: str) -> bool:
    return _contains_any(text_lower, RELATIONSHIP_MARKERS_RU + RELATIONSHIP_MARKERS_EN)


def _has_temporal_context(text_lower: str) -> bool:
    return _contains_any(text_lower, TEMPORAL_MARKERS_RU + TEMPORAL_MARKERS_EN)


def _looks_like_event(text_lower: str) -> bool:
    if _contains_any(text_lower, EVENT_ACTION_MARKERS_RU + EVENT_ACTION_MARKERS_EN):
        return True

    # Specific plans are episodic; generic wants/likes are not.
    plan_markers = [
        "хочу ",
        "хочет ",
        "буду ",
        "собираюсь ",
        "want to ",
        "wants to ",
        "will ",
        "going to ",
    ]
    return _has_temporal_context(text_lower) or _contains_any(text_lower, plan_markers)


def _detect_type(text: str, *, allow_durable_relationship: bool = True) -> MemoryType | None:
    """Detect memory type based on lightweight semantic markers."""
    text_lower = text.lower()

    # Bounded carry-over gate for Russian long-chat relationship state.
    # This should only promote durable state shifts to `relationship`, not
    # absorb ordinary conflict/meeting scenes that belong in episodic memory.
    if allow_durable_relationship and text_features.is_durable_relationship_statement(text):
        return "relationship"
    if _looks_like_event(text_lower):
        return "event"
    if _looks_like_relationship(text_lower):
        return "relationship"
    if _looks_like_preference(text, text_lower) or _looks_like_profile(text_lower):
        return "profile"
    return None


def _get_importance(memory_type: MemoryType) -> float:
    """Get default importance based on memory type."""
    if memory_type == "profile":
        return 0.7
    if memory_type == "relationship":
        return 0.8
    return 0.6


def _get_layer(
    memory_type: MemoryType,
    text: str,
    *,
    allow_durable_relationship: bool = True,
) -> str:
    """
    Get memory layer based on type and whether the text describes a durable fact or an episode.
    """
    text_lower = text.lower()

    if memory_type == "event":
        return "episodic"

    # Durable relationship gate: keep stable relation memories for longer-arc
    # state carry-over, while letting scene-like relationship mentions stay
    # episodic through the normal checks below.
    if (
        memory_type == "relationship"
        and allow_durable_relationship
        and text_features.is_durable_relationship_statement(text)
    ):
        return "stable"

    if _looks_like_event(text_lower):
        return "episodic"

    if _looks_like_preference(text, text_lower) or _looks_like_profile(text_lower):
        return "stable"

    if memory_type == "relationship":
        conflict_words = ["conflict", "argue", "fight", "quarrel", "конфликт", "спор", "ссора", "руг"]
        if _contains_any(text_lower, conflict_words) or _has_temporal_context(text_lower):
            return "episodic"
        return "stable"

    return "stable"


def _is_meaningful(text: str) -> bool:
    """Check if text is meaningful enough to store."""
    if len(text.strip()) < 10:
        return False
    if text.strip() in ["...", "???", "!!!", "???"]:
        return False
    return True


def _truncate_content(text: str, max_length: int = 500) -> str:
    """Truncate content to reasonable length."""
    if len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_period = truncated.rfind(".")
    if last_period > max_length // 2:
        return truncated[: last_period + 1]
    return truncated + "..."


def extract_memories(
    chat_id: str,
    character_id: str,
    messages: list[MessageInput],
) -> list[CreateMemoryRequest]:
    """
    Extract memory candidates from messages using rule-based heuristics.

    Returns at most 3 memory items.
    """
    candidates = []

    for msg in messages:
        text = msg.text

        if not _is_meaningful(text):
            continue

        # Durable relationship formation is carry-over oriented. Question-form
        # user prompts are not valid relationship memories by default, so skip
        # them before they can fall through to the generic relationship branch.
        if (
            msg.role == "user"
            and text_features.is_question_like_text(text)
            and (
                text_features.is_question_form_relationship_prompt(text)
                or _looks_like_relationship(text.lower())
            )
        ):
            continue

        allow_durable_relationship = True

        memory_type = _detect_type(
            text,
            allow_durable_relationship=allow_durable_relationship,
        )
        if memory_type is None:
            continue

        content = _truncate_content(text)
        entities = text_features.extract_entities(text)
        keywords = text_features.extract_keywords(text)

        candidates.append(
            CreateMemoryRequest(
                chat_id=chat_id,
                character_id=character_id,
                type=memory_type,
                content=content,
                source="auto",
                layer=_get_layer(
                    memory_type,
                    text,
                    allow_durable_relationship=allow_durable_relationship,
                ),
                importance=_get_importance(memory_type),
                pinned=False,
                archived=False,
                metadata=MemoryMetadata(entities=entities, keywords=keywords),
            )
        )

    return candidates[:3]
