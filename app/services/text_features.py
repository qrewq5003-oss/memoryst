import re

import pymorphy3

# Initialize pymorphy3 morphological analyzer lazily for Russian normalization
_morph = None

# Russian pronouns and service words to exclude from entities
RUSSIAN_PRONOUNS = {
    "я", "мы", "ты", "вы", "он", "она", "оно", "они",
    "меня", "нас", "тебя", "вас", "его", "её", "их",
    "мне", "нам", "тебе", "вам", "ему", "ей", "им",
    "мной", "мною", "нами", "тобой", "тобою", "вами",
    "нём", "ней", "них", "нём", "ней",
    "этой", "этом", "этому", "эту", "этим", "этой",
    "этот", "эта", "это", "эти",
    "какой", "какая", "какое", "какие",
    "кто", "что", "кого", "чего", "кому", "чему",
    "кем", "чем", "ком", "чём",
    "весь", "вся", "всё", "все",
    "сам", "сама", "само", "сами",
    "мой", "моя", "моё", "мои",
    "твой", "твоя", "твоё", "твои",
    "наш", "наша", "наше", "наши",
    "ваш", "ваша", "ваше", "ваши",
    "свой", "своя", "своё", "свои",
}

KEYWORD_STOPWORDS = {
    "что", "как", "так", "вот", "уже", "еще", "ещё", "был", "была", "было", "были",
    "the", "and", "but", "for", "with", "from", "this", "that", "have", "has", "had",
    "was", "were", "been", "being", "are", "is", "about", "just", "into",
    "она", "оно", "они", "него", "неё", "них",
    "всегда", "часто", "обычно", "постоянно", "никогда",
}

# Narrow Russian relationship/general-state robustness channel.
# This is intentionally not a general semantic layer and should stay small:
# - it only helps relationship/general-state query families
# - it only supports retrieval after the main lexical/entity signals are computed
# - it must grow through eval-backed regressions, not ad hoc regex accumulation
#
# Allowed cue groups for v1:
# - conflict
# - repair
# - trust
# - distance
# - together
# - attitude
#
# Maintenance rule:
# - adding a pattern inside an existing group requires an eval-backed justification
# - adding a new group requires a dedicated long-chat/eval scenario explaining why
RELATIONSHIP_STATE_CUE_PATTERNS = {
    "conflict": [
        r"\bзл\w*",
        r"\bссор\w*",
        r"\bконфликт\w*",
        r"\bруг\w*",
        r"\bспор\w*",
        r"\bсорвал\w*",
    ],
    "repair": [
        r"\bпомир\w*",
        r"\bпримир\w*",
        r"\bналад\w*",
    ],
    "trust": [
        r"\bдовер\w*",
        r"\bполага\w*",
    ],
    "distance": [
        r"\bнапряж\w*",
        r"\bдистанц\w*",
        r"\bосторож\w*",
        r"\bне до конца\b",
        r"\bне расслаб\w*",
    ],
    "together": [
        r"\bсотруднич\w*",
        r"\bработа\w* вместе\b",
        r"\bснова работа\w* вместе\b",
        r"\bпомога\w*",
        r"\bвместе\b",
    ],
    "attitude": [
        r"\bотнос\w*",
        r"\bотношен\w*",
        r"\bмежду ними\b",
    ],
}

# Broad Russian phrasings that should gate the narrow cue channel for
# relationship/general-state questions. Keep this list intentionally small.
GENERAL_STATE_QUERY_PATTERNS = [
    r"\bчто у (?:них|неё|нее|него) сейчас\b",
    r"\bчто у них вообще\b",
    r"\bкак .* относ\w*",
    r"\bчто между ними\b",
    r"\bони уже .* или нет\b",
    r"\bони снова .*вместе\b",
]


def _get_morph():
    """Lazy initialization of pymorphy3 morph."""
    global _morph
    if _morph is None:
        _morph = pymorphy3.MorphAnalyzer()
    return _morph


def _is_russian_word(word: str) -> bool:
    """Check if word contains Cyrillic characters."""
    return bool(re.search(r"[а-яё]", word.lower()))


def _normalize_russian_word(word: str) -> str:
    """
    Normalize Russian word to its normal form using pymorphy3.

    Falls back to the original token if normalization looks suspicious.
    """
    if not _is_russian_word(word):
        return word

    try:
        morph = _get_morph()
        parsed = morph.parse(word)[0]
        normalized = parsed.normal_form
        if not normalized or len(normalized) < 2 or any(char.isdigit() for char in normalized):
            return word
        return normalized
    except Exception:
        return word


def extract_entities(text: str) -> list[str]:
    """Extract deduplicated entities with Russian normalization when applicable."""
    words = re.findall(r"(?<![.!?]\s)\b([A-ZА-Я][a-zа-яё]+)\b", text)
    seen = set()
    entities = []

    for word in words:
        lower = word.lower()
        if lower in RUSSIAN_PRONOUNS:
            continue

        normalized = word
        if _is_russian_word(word):
            normalized_ru = _normalize_russian_word(word)
            if (
                normalized_ru
                and len(normalized_ru) >= 2
                and not any(char.isdigit() for char in normalized_ru)
            ):
                normalized = normalized_ru.capitalize() if word[0].isupper() else normalized_ru

        normalized_lower = normalized.lower()
        if len(normalized) > 1 and normalized_lower not in seen:
            seen.add(normalized_lower)
            entities.append(normalized)

    return entities[:10]


def extract_keywords(text: str) -> list[str]:
    """Extract deduplicated keywords with the same normalization rules used in storage."""
    normalized_text = re.sub(r"[^\w\s]", " ", text.lower())
    words = normalized_text.split()
    keywords = []
    seen = set()

    for word in words:
        if len(word) < 3 or word in KEYWORD_STOPWORDS:
            continue

        normalized = _normalize_russian_word(word) if _is_russian_word(word) else word
        if len(normalized) < 2 or normalized in seen:
            continue

        seen.add(normalized)
        keywords.append(normalized)

    return keywords[:8]


def extract_relationship_state_cues(text: str) -> list[str]:
    """
    Extract narrow, explicit Russian relationship/general-state cues.

    This helper is a bounded robustness channel for relationship phrasing
    variation. It is not a general-purpose semantic tagger.
    """
    if not text:
        return []

    text_lower = text.lower()
    cues = []

    for cue, patterns in RELATIONSHIP_STATE_CUE_PATTERNS.items():
        if any(re.search(pattern, text_lower) for pattern in patterns):
            cues.append(cue)

    if any(re.search(pattern, text_lower) for pattern in GENERAL_STATE_QUERY_PATTERNS):
        cues.append("status")

    return cues


def is_relationship_state_query(text: str) -> bool:
    """Gate the narrow cue layer for Russian relationship/general-state queries."""
    return bool(extract_relationship_state_cues(text))
