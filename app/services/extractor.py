from app.schemas import CreateMemoryRequest, MessageInput, MemoryMetadata, MemoryType
from app.services import text_features


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

# Episodic markers - indicate specific events/episodes
# These override relationship type for layer determination
EPISODIC_MARKERS_RU = [
    "поссорились",
    "обсуждали",
    "решили",
    "пошли",
    "встретились",
    "поехали",
    "сказал",
    "сказала",
    "сделали",
    "аргументировал",
    "спорили",
    "ругались",
    "признался",
    "призналась",
    "рассказал",
    "рассказала",
]

EPISODIC_MARKERS_EN = [
    "argued",
    "discussed",
    "decided",
    "went",
    "met",
    "fought",
    "quarreled",
    "confessed",
    "told",
    "said",
    "did",
    "happened",
    " in ",
    " at ",
]

# Stable markers - indicate enduring states/patterns
STABLE_MARKERS_RU = [
    "доверяю",
    "доверяет",
    "люблю",
    "любит",
    "предпочитаю",
    "предпочитает",
    "забочусь",
    "заботится",
    "всегда",
    "постоянно",
    "обычно",
    "часто",
]

STABLE_MARKERS_EN = [
    "trusts",
    "trust",
    "loves",
    "love",
    "prefers",
    "prefer",
    "cares about",
    "care about",
    "always",
    "constantly",
    "usually",
    "often",
]


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


def _get_layer(memory_type: MemoryType, text: str) -> str:
    """
    Get memory layer based on type AND content analysis.
    
    Key principle:
    - Specific events, scenes, actions → episodic (even if relationship-related)
    - Enduring states, preferences, patterns → stable
    
    Examples:
    - "Elena and I argued in Rome" → relationship/episodic (specific event)
    - "Elena trusts the user" → relationship/stable (enduring state)
    - "I want to shoot the film in Rome" → event/episodic (specific plan)
    """
    text_lower = text.lower()
    
    # If event type, always episodic
    if memory_type == "event":
        return "episodic"
    
    # Check for episodic markers first - these override relationship type
    for marker in EPISODIC_MARKERS_RU + EPISODIC_MARKERS_EN:
        if marker in text_lower:
            return "episodic"
    
    # Check for stable markers
    for marker in STABLE_MARKERS_RU + STABLE_MARKERS_EN:
        if marker in text_lower:
            return "stable"
    
    # Default for profile/relationship without clear markers:
    # - relationship with conflict/argument words → episodic (it's a specific event)
    # - profile → stable (preferences are usually enduring)
    if memory_type == "relationship":
        # Check for conflict/argument indicators → episodic
        conflict_words = ["conflict", "argue", "fight", "quarrel", "конфликт", "спор", "ссора", "руг"]
        for word in conflict_words:
            if word in text_lower:
                return "episodic"
        # Default relationship without conflict → stable
        return "stable"
    
    # Default for profile → stable
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
        entities = text_features.extract_entities(text)
        keywords = text_features.extract_keywords(text)

        request = CreateMemoryRequest(
            chat_id=chat_id,
            character_id=character_id,
            type=memory_type,
            content=content,
            source="auto",
            layer=_get_layer(memory_type, text),
            importance=_get_importance(memory_type),
            pinned=False,
            archived=False,
            metadata=MemoryMetadata(entities=entities, keywords=keywords),
        )
        candidates.append(request)

    # Limit to 3 items
    return candidates[:3]
