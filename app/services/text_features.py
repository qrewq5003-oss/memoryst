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

# Narrow Russian local-scene precision channel.
# This layer is intentionally bounded:
# - it activates only for local-scene query families
# - it only helps episodic selection become more concrete
# - it is not a general event-semantics layer
# - it must not replace the main lexical/entity ranking signal
#
# Allowed local-scene intent families for v1:
# - decision / agreement
# - saying / reply / statement
# - meeting outcome
# - recent concrete scene outcome
#
# Maintenance rule:
# - add a pattern only inside an existing intent family and only with eval-backed justification
# - add a new intent family only with a dedicated scenario/test explaining why the current set is insufficient
LOCAL_SCENE_QUERY_PATTERNS = [
    r"\bчто .* реш\w*",
    r"\bчто .* сказа\w*",
    r"\bна что .* договор\w*",
    r"\bчто произош\w*",
    r"\bчто было .* встреч\w*",
    r"\bпосле разговор\w*",
    r"\bвчера\b",
    r"\bутром\b",
]

# Concrete scene outcome markers used by the narrow local-scene helper.
# Keep this list intentionally small and tied to the intent families above.
LOCAL_SCENE_DETAIL_PATTERNS = [
    r"\bреш\w*",
    r"\bсказа\w*",
    r"\bдоговор\w*",
    r"\bперенес\w*",
    r"\bпозва\w*",
    r"\bобсуд\w*",
    r"\bвстреч\w*",
    r"\bразговор\w*",
    r"\bутр\w*",
    r"\bвчера\b",
    r"\bпозже\b",
    r"\bсегодня\b",
]

# Narrow durable relationship formation channel for Russian long-chat arcs.
# This helper exists only to distinguish relationship state carry-over from
# one-off conflict/meeting episodes in the store/extractor path.
#
# Allowed durable relationship state families for v1:
# - trust / distrust shift
# - distance / caution / lingering tension
# - repair / partial reconciliation
# - support / protection / backing each other up
# - working together / renewed cooperation
#
# Maintenance rule:
# - add patterns only when a concrete long-chat store miss is covered by tests/evals
# - do not turn one-off scene actions into stable relationship state by default
DURABLE_RELATIONSHIP_STATE_PATTERNS = {
    "trust": [
        r"\bснова довер\w*",
        r"\bбольше не довер\w*",
        r"\bдовер\w* .*в работ\w*",
        r"\bдовер\w* .*снова\b",
    ],
    "distance": [
        r"\bдерж\w* дистанц\w*",
        r"\bосторож\w*",
        r"\bмежду ними .*напряж\w*",
        r"\bнапряж\w* .*между ними\b",
        r"\bвсё ещё .*напряж\w*",
        r"\bне до конца расслаб\w*",
    ],
    "repair": [
        r"\bчастич\w* помир\w*",
        r"\bпомир\w*",
        r"\bпримир\w*",
        r"\bне в открытой ссоре\b",
        r"\bне возвращать\w*.*ссор\w*",
    ],
    "support": [
        r"\bподдерж\w* .*при всей команд\w*",
        r"\bподдерж\w* .*в работ\w*",
        r"\bне собира\w* .*оставля\w* .*одн\w*",
        r"\bприкрыл\w*",
        r"\bпомога\w* .*с фильм\w*",
    ],
    "cooperation": [
        r"\bснова работа\w* вместе\b",
        r"\bснова сотруднич\w*",
        r"\bсоглас\w* снова работать\b",
        r"\bдерж\w* друг друга в курсе\b",
        r"\bработа\w* спокойн\w*",
        r"\bснова поддерж\w* план\b",
    ],
}

DURABLE_RELATIONSHIP_EPISODIC_BLOCKERS = [
    r"\bначал\w* ссор\w*",
    r"\bначал\w* спор\w*",
    r"\bсорвал\w* на\b",
    r"\bпоссор\w*",
    r"\bспор\w* на встреч\w*",
    r"\bвстрет\w* .*вчера\b",
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


def is_local_scene_query(text: str) -> bool:
    """Gate the narrow local-scene precision layer for eligible Russian queries."""
    if not text:
        return False
    text_lower = text.lower()
    return any(re.search(pattern, text_lower) for pattern in LOCAL_SCENE_QUERY_PATTERNS)


def extract_local_scene_detail_score(text: str) -> float:
    """
    Estimate whether an episodic line contains concrete scene outcome detail.

    This is intentionally lightweight: it rewards explicit action/outcome markers
    and time/context anchors so concrete event lines beat generic query echoes.
    It is not a general event detail parser.
    """
    if not text:
        return 0.0

    text_lower = text.lower()
    marker_count = sum(1 for pattern in LOCAL_SCENE_DETAIL_PATTERNS if re.search(pattern, text_lower))
    keyword_count = len(extract_keywords(text))
    entity_count = len(extract_entities(text))
    question_like = text_lower.strip().endswith("?")

    raw_score = (
        min(marker_count, 4) * 0.18 +
        min(keyword_count, 6) * 0.05 +
        min(entity_count, 3) * 0.08
    )
    if question_like:
        raw_score *= 0.6

    return min(raw_score, 1.0)


def extract_durable_relationship_state_cues(text: str) -> list[str]:
    """
    Extract bounded durable relationship-state cues for store/extractor logic.

    This helper is not a general relationship parser. It only exists to keep
    long-chat relationship carry-over from collapsing into pure episodic memory.
    """
    if not text:
        return []

    text_lower = text.lower()
    cues = []

    for cue, patterns in DURABLE_RELATIONSHIP_STATE_PATTERNS.items():
        if any(re.search(pattern, text_lower) for pattern in patterns):
            cues.append(cue)

    return cues


def is_durable_relationship_statement(text: str) -> bool:
    """Return True for bounded Russian relationship-state carry-over statements."""
    if not text:
        return False

    text_lower = text.lower()
    cues = extract_durable_relationship_state_cues(text)
    if not cues:
        return False

    if any(re.search(pattern, text_lower) for pattern in DURABLE_RELATIONSHIP_EPISODIC_BLOCKERS):
        return False

    return True
