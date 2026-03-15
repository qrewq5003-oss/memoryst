import re

import pymorphy3

from app.schemas import CreateMemoryRequest, MessageInput, MemoryMetadata, MemoryType

# Initialize pymorphy3 morphological analyzer (lazy loading for performance)
_morph = None


def _get_morph():
    """Lazy initialization of pymorphy3 morph."""
    global _morph
    if _morph is None:
        _morph = pymorphy3.MorphAnalyzer()
    return _morph


# Russian pronouns and service words to exclude from entities
RUSSIAN_PRONOUNS = {
    'я', 'мы', 'ты', 'вы', 'он', 'она', 'оно', 'они',
    'меня', 'нас', 'тебя', 'вас', 'его', 'её', 'их',
    'мне', 'нам', 'тебе', 'вам', 'ему', 'ей', 'им',
    'мной', 'мною', 'нами', 'тобой', 'тобою', 'вами',
    'нём', 'ней', 'них', 'нём', 'ней',
    'этой', 'этом', 'этому', 'эту', 'этим', 'этой',
    'этот', 'эта', 'это', 'эти',
    'какой', 'какая', 'какое', 'какие',
    'кто', 'что', 'кого', 'чего', 'кому', 'чему',
    'кем', 'чем', 'ком', 'чём',
    'весь', 'вся', 'всё', 'все',
    'сам', 'сама', 'само', 'сами',
    'мой', 'моя', 'моё', 'мои',
    'твой', 'твоя', 'твоё', 'твои',
    'наш', 'наша', 'наше', 'наши',
    'ваш', 'ваша', 'ваше', 'ваши',
    'свой', 'своя', 'своё', 'свои',
}


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


def _is_russian_word(word: str) -> bool:
    """Check if word contains Cyrillic characters."""
    return bool(re.search(r'[а-яё]', word.lower()))


def _normalize_russian_word(word: str) -> str:
    """
    Normalize Russian word to its normal form using pymorphy3.
    
    Protection against bad normalization:
    - Returns original if normalization produces strange results
    - Checks: non-empty, length >= 2, no digits
    
    For non-Russian words, returns the word as-is.
    """
    if not _is_russian_word(word):
        return word
    
    try:
        morph = _get_morph()
        parsed = morph.parse(word)[0]
        normalized = parsed.normal_form
        
        # Protection: keep original if normalized form looks bad
        if not normalized or len(normalized) < 2 or any(c.isdigit() for c in normalized):
            return word
        
        return normalized
    except Exception:
        # Fallback to original word on any error
        return word


def _extract_entities(text: str) -> list[str]:
    """Extract entities using simple heuristic: capitalized words not at start of sentence."""
    # Find words that start with capital letter (not at the beginning of the text)
    words = re.findall(r'(?<![.!?]\s)\b([A-ZА-Я][a-zа-яё]+)\b', text)
    # Remove duplicates while preserving order
    seen = set()
    entities = []
    for word in words:
        lower = word.lower()
        # Skip pronouns and service words
        if lower in RUSSIAN_PRONOUNS:
            continue
        # Normalize Russian words to normal form, preserving capitalization style
        if _is_russian_word(word):
            normalized = _normalize_russian_word(word)
            # Only capitalize if normalized form looks reasonable
            # Protection: keep original if normalization seems wrong
            if normalized and len(normalized) >= 2 and not any(c.isdigit() for c in normalized):
                # Preserve capitalization: if original was capitalized, capitalize normalized
                if word[0].isupper():
                    normalized = normalized[0].upper() + normalized[1:] if len(normalized) > 1 else normalized.upper()
                if normalized.lower() not in seen:
                    seen.add(normalized.lower())
                    entities.append(normalized)
            else:
                # Fallback: keep original word
                if lower not in seen:
                    seen.add(lower)
                    entities.append(word)
        else:
            # Non-Russian words: keep as-is
            if lower not in seen and len(word) > 1:
                seen.add(lower)
                entities.append(word)
    return entities[:10]  # Limit to 10 entities


def _extract_keywords(text: str) -> list[str]:
    """Extract keywords: lowercase, strip punctuation, remove short words, dedupe, normalize Russian."""
    # Lowercase
    text = text.lower()
    # Remove punctuation
    text = re.sub(r'[^\w\s]', ' ', text)
    # Split and filter
    words = text.split()
    # Remove short words (< 3 chars) and stopwords
    stopwords = {
        'что', 'как', 'так', 'вот', 'уже', 'еще', 'ещё', 'был', 'была', 'было', 'были',
        'the', 'and', 'but', 'for', 'with', 'from', 'this', 'that', 'have', 'has', 'had',
        'was', 'were', 'been', 'being', 'are', 'is', 'was',
        # Russian pronouns and common words
        'она', 'оно', 'они', 'него', 'неё', 'них',
        # Adverbs that are too common
        'всегда', 'часто', 'обычно', 'постоянно', 'никогда',
    }
    keywords = []
    seen = set()
    for word in words:
        if len(word) >= 3 and word not in stopwords and word not in seen:
            # Normalize Russian words to normal form
            if _is_russian_word(word):
                normalized = _normalize_russian_word(word)
                if normalized not in seen and len(normalized) >= 2:
                    keywords.append(normalized)
                    seen.add(normalized)
            else:
                # Non-Russian words: keep as-is
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
        entities = _extract_entities(text)
        keywords = _extract_keywords(text)

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
