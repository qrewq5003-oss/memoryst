import re

from app.schemas import CreateMemoryRequest, MessageInput, MemoryMetadata, MemoryType


# Russian markers
PROFILE_MARKERS_RU = [
    "мне нравится",
    "я люблю",
    "я предпочитаю",
    "мой любимый",
    "я обожаю",
]

RELATIONSHIP_MARKERS_RU = [
    "доверяю",
    "обещаю",
    "пообещал",
    "пообещала",
    "конфликт",
    "поссорились",
    "забочусь",
    "боюсь потерять",
    "мне важно",
    "я переживаю за",
]

EVENT_MARKERS_RU = [
    "хочу",
    "буду",
    "вчера",
    "сегодня",
    "обсуждали",
    "решили",
    "пошли",
    "встретились",
    "сказал",
    "сказала",
    "поехали",
    "сделали",
]

# English markers
PROFILE_MARKERS_EN = [
    "i like",
    "i love",
    "i prefer",
    "my favorite",
]

RELATIONSHIP_MARKERS_EN = [
    "trust",
    "promise",
    "conflict",
    "argue",
    "care about",
    "afraid of losing",
]

EVENT_MARKERS_EN = [
    "want to",
    "will",
    "yesterday",
    "today",
    "discussed",
    "decided",
    "went",
    "met",
]


def _extract_entities(text: str) -> list[str]:
    """Extract entities using simple heuristic: capitalized words not at start of sentence."""
    # Find words that start with capital letter (not at the beginning of the text)
    words = re.findall(r'(?<![.!?]\s)\b([A-ZА-Я][a-zа-яё]+)\b', text)
    # Remove duplicates while preserving order
    seen = set()
    entities = []
    for word in words:
        lower = word.lower()
        if lower not in seen and len(word) > 1:
            seen.add(lower)
            entities.append(word)
    return entities[:10]  # Limit to 10 entities


def _extract_keywords(text: str) -> list[str]:
    """Extract keywords: lowercase, strip punctuation, remove short words, dedupe."""
    # Lowercase
    text = text.lower()
    # Remove punctuation
    text = re.sub(r'[^\w\s]', ' ', text)
    # Split and filter
    words = text.split()
    # Remove short words (< 3 chars) and stopwords
    stopwords = {'что', 'как', 'так', 'вот', 'уже', 'еще', 'ещё', 'был', 'была', 'было', 'были', 'the', 'and', 'but', 'for', 'with', 'from', 'this', 'that', 'have', 'has', 'had', 'was', 'were', 'been', 'being', 'are', 'is', 'was'}
    keywords = []
    seen = set()
    for word in words:
        if len(word) >= 3 and word not in stopwords and word not in seen:
            keywords.append(word)
            seen.add(word)
    return keywords[:8]  # Limit to 8 keywords


def _detect_type(text: str) -> MemoryType | None:
    """Detect memory type based on markers."""
    text_lower = text.lower()

    # Check profile markers
    for marker in PROFILE_MARKERS_RU + PROFILE_MARKERS_EN:
        if marker in text_lower:
            return "profile"

    # Check relationship markers
    for marker in RELATIONSHIP_MARKERS_RU + RELATIONSHIP_MARKERS_EN:
        if marker in text_lower:
            return "relationship"

    # Check event markers
    for marker in EVENT_MARKERS_RU + EVENT_MARKERS_EN:
        if marker in text_lower:
            return "event"

    return None


def _get_importance(memory_type: MemoryType) -> float:
    """Get default importance based on memory type."""
    if memory_type == "profile":
        return 0.7
    elif memory_type == "relationship":
        return 0.8
    else:  # event
        return 0.6


def _get_layer(memory_type: MemoryType) -> str:
    """Get memory layer based on type."""
    if memory_type == "event":
        return "episodic"
    else:  # profile, relationship
        return "stable"


def _is_meaningful(text: str) -> bool:
    """Check if text is meaningful enough to store."""
    if len(text.strip()) < 10:
        return False
    # Filter out obvious noise
    if text.strip() in ["...", "???", "!!!", "???"]:
        return False
    return True


def _truncate_content(text: str, max_length: int = 500) -> str:
    """Truncate content to reasonable length."""
    if len(text) <= max_length:
        return text
    # Try to cut at sentence boundary
    truncated = text[:max_length]
    last_period = truncated.rfind(".")
    if last_period > max_length // 2:
        return truncated[:last_period + 1]
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

        memory_type = _detect_type(text)
        if memory_type is None:
            continue

        content = _truncate_content(text)
        entities = _extract_entities(text)
        keywords = _extract_keywords(text)

        request = CreateMemoryRequest(
            chat_id=chat_id,
            character_id=character_id,
            type=memory_type,
            content=content,
            source="auto",
            layer=_get_layer(memory_type),
            importance=_get_importance(memory_type),
            pinned=False,
            archived=False,
            metadata=MemoryMetadata(entities=entities, keywords=keywords),
        )
        candidates.append(request)

    # Limit to 3 items
    return candidates[:3]
