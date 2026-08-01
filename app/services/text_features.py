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
    "что", "как", "так", "вот", "уже", "еще", "ещё", "был", "была", "было", "были", "про",
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
# This is not a general relationship semantics parser. It should stay bounded
# and only help the store path preserve load-bearing relationship state after
# a longer arc. If a phrase reads primarily like a scene outcome, flare-up, or
# one-off meeting detail, it should stay episodic unless there is explicit
# carry-over wording.
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
# - add new patterns inside existing families first; new families require a
#   dedicated scenario/test explaining why the current set is insufficient
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

# Safety blockers against scene overcapture. These should keep conflict bursts,
# one-off meeting/action lines, and other local outcomes from becoming stable
# relationship memories just because a relationship term also appears nearby.
DURABLE_RELATIONSHIP_EPISODIC_BLOCKERS = [
    r"\bначал\w* ссор\w*",
    r"\bначал\w* спор\w*",
    r"\bсорвал\w* на\b",
    r"\bпоссор\w*",
    r"\bспор\w* на встреч\w*",
    r"\bвстрет\w* .*вчера\b",
]

# Narrow question-form anti-artifact filter for store/extractor logic.
# This exists only to stop raw user prompts from leaking into stored memories.
# It is intentionally not:
# - a semantic classifier
# - a retrieval feature
# - a generic prompt understanding layer
#
# Allowed guarded families for v1:
# - relationship question prompts
# - local-scene question prompts
#
# User-role-only rule:
# - this helper family is only meaningful when the caller already knows the
#   source message is `role="user"`
# - assistant/narration/system-like content must not be filtered through these
#   guards by default
#
# Maintenance rule:
# - expand only for a concrete live/runtime artifact
# - add a regression test first
# - keep the written scope narrow and explicit
QUESTION_PREFIX_PATTERNS = [
    r"^\s*(?:что|как|когда|где|почему|зачем|кто|кого|кому|чего|чему)\b",
    r"^\s*(?:был|была|было|были|есть|ли)\b",
    r"^\s*они\b",
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


PROPER_NOUN_TAGS = ("Name", "Surn", "Patr", "Geox", "Orgn")

# A capitalised word, plus whether anything other than whitespace precedes it inside its
# sentence. Sentence-initial position carries no information about proper-nounhood, so the
# two cases have to be told apart before deciding.
_CAPITALIZED_RE = re.compile(r"\b([A-ZА-Я][a-zа-яё]+)\b")
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?…]\s*|\n\s*)$")


def _is_proper_noun_ru(word: str) -> bool:
    """Ask pymorphy3 whether any reading of this word is a name, place or organisation.

    Every parse is considered, not just the best one: for a given name the proper-noun
    reading is often not the top-scoring parse (Тиффани scores 0.10, Анри 0.04), while a
    plain common noun like `девушка` or `пользователь` has no such reading at all.
    """
    try:
        for parse in _get_morph().parse(word):
            if any(tag in parse.tag for tag in PROPER_NOUN_TAGS):
                return True
    except Exception:
        # Same reasoning as _normalize_russian_word: a morphology failure must not take
        # retrieval down. Falling back to "not a proper noun" only drops entities, which
        # scores worse - it cannot invent a match.
        return False
    return False


def _collect_entity_candidates(text: str) -> list[str]:
    """Capitalised words that are actually evidence of a proper noun.

    Capitalisation alone is not that evidence. The previous rule tried to skip
    sentence-initial words with a `(?<![.!?]\\s)` lookbehind and got both directions
    wrong: the first word of a text has no preceding period, so third-person phrasing
    made "Девушка" and "Пользователь" entities and every imperative query contributed a
    phantom one ("Расскажи про чай" -> ["Рассказать"]) that no memory could ever match -
    and since entity_overlap divides by the number of *query* entities, that phantom
    halved the score of a genuine name standing next to it. Meanwhile a real name in the
    second sentence *did* have a preceding period and was discarded, so the same two
    facts produced different entities depending on sentence order.

    Three rules replace it:
      - capitalised mid-sentence -> a proper noun in either alphabet, accept;
      - sentence-initial and Russian -> accept only on morphological evidence;
      - sentence-initial and Latin -> accept, because no morphology is available to do
        better and the two errors are not symmetric. A phantom entity only dilutes
        entity_overlap; dropping a real name removes the signal outright. A query like
        "Elena project" leads with the name, so rejecting sentence-initial Latin would
        leave it with no entity at all - two existing tests caught exactly that. The
        residual cost is an English imperative contributing "Tell"; a stoplist would
        cover it, but that treats the symptom and belongs with the other deferred work.
    """
    candidates: list[str] = []
    for match in _CAPITALIZED_RE.finditer(text):
        word = match.group(1)
        sentence_initial = bool(_SENTENCE_START_RE.search(text[: match.start()]))

        if not sentence_initial or not _is_russian_word(word):
            candidates.append(word)
        elif _is_proper_noun_ru(word):
            candidates.append(word)

    return candidates


# Words that name a *role*, not a person, plus artefacts of the chat transcript itself.
#
# An entity like `пользователь` sits in 231 stored memories and `user` in 377, so it does
# not distinguish anything: matching on it lifts every one of those rows equally, which
# promotes the irrelevant alongside the relevant. On the query side it is worse than
# useless, because entity_overlap divides by the number of query entities - a query
# resolving to ["пользователь", "Валерия"] scores a genuine Валерия match 1/2 instead of
# 1/1, the same arithmetic that the phantom "Напомнить" used to cause.
#
# `time` is not a role word: it comes from the "[ 🕰️ 2:03 PM ]" headers some characters
# prefix their replies with, and reached 248 occurrences that way.
ENTITY_STOPWORDS = {
    # roles, Russian
    "пользователь", "юзер", "девушка", "девушки", "парень", "мужчина", "женщина",
    "собеседник", "собеседница", "персонаж", "партнер", "партнёр", "герой", "героиня",
    "человек", "рассказчик", "автор",
    # roles, English
    "user", "character", "assistant", "narrator", "partner", "girl", "boy", "man",
    "woman", "person", "someone", "narrator",
    # transcript artefacts
    "time", "date", "system", "ooc",
}


def filter_entities(entities: list[str]) -> list[str]:
    """Drop role words and duplicates, preserving order and original spelling.

    Applied to every source of entities - the rule-based extractor, the facts the LLM
    returns, and the query - because a stoplist enforced on only one side removes half
    the effect: the noisy word simply stops matching from the other direction.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for entity in entities:
        cleaned = (entity or "").strip()
        lowered = cleaned.lower()
        if not cleaned or lowered in ENTITY_STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        kept.append(cleaned)
    return kept


# Deliberately lossy transliteration, tuned for matching names rather than for producing
# readable Latin. The endings are what matter: `валерия` has to reach the same key as
# `Valeria`, so `ия` becomes `ia` rather than the more correct `iya`.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "u",
    "я": "a",
}
_DOUBLED_RE = re.compile(r"(.)\1+")
_ENTITY_SPLIT_RE = re.compile(r"[\s\-–—_,./]+")


def entity_match_keys(entity: str) -> set[str]:
    """Canonical keys under which this entity should be considered the same as another.

    Used only for comparison - the stored value keeps its original spelling, so a wrong
    merge changes a score and never the data.

    Three things are collapsed, each measured on the live corpus:
      - alphabet. `валерия` (445 rows) and `Valeria` (237) are one person whose variants
        never matched, and 1527 of 11793 entity occurrences sit in groups like it. A
        Russian query therefore could not see a third of that character's memories.
      - doubled letters, so SillyTavern's `Allina` reaches the same key as `Алина`.
      - multi-word names, split into tokens, so the full name extraction now writes
        ("Алина Волкова") still matches a query that says just "Алина". Without this the
        naming fix would have made matching *worse* than before it.

    Tokens shorter than three characters are dropped: they carry no identity and would
    collide freely.
    """
    keys: set[str] = set()
    for token in _ENTITY_SPLIT_RE.split((entity or "").lower()):
        if not token:
            continue
        latin = "".join(_TRANSLIT.get(char, char) for char in token)
        # `Wanted` and `Вантед` are the same persona; w/v is the one Latin-side
        # substitution needed to bring transliterated pairs together.
        latin = latin.replace("w", "v")
        latin = _DOUBLED_RE.sub(r"\1", latin)
        if len(latin) >= 3:
            keys.add(latin)
    return keys


def entity_overlap_ratio(memory_entities: list[str], input_entities: list[str]) -> float:
    """Share of the query's entities that the memory also carries.

    Counted per entity rather than per key, so a two-word name stays one entity and does
    not quietly double the denominator the way a naive key-set intersection would.
    """
    if not input_entities:
        return 0.0

    memory_keys: set[str] = set()
    for entity in memory_entities:
        memory_keys |= entity_match_keys(entity)

    matched = sum(1 for entity in input_entities if entity_match_keys(entity) & memory_keys)
    return matched / len(input_entities)


def extract_entities(text: str) -> list[str]:
    """Extract deduplicated entities with Russian normalization when applicable."""
    words = _collect_entity_candidates(text)
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

    return filter_entities(entities)[:10]


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


def is_question_like_text(text: str) -> bool:
    """Detect compact interrogative phrasing for the narrow anti-artifact filter only."""
    if not text:
        return False

    text_lower = text.strip().lower()
    if not text_lower:
        return False
    if text_lower.endswith("?"):
        return True
    return any(re.search(pattern, text_lower) for pattern in QUESTION_PREFIX_PATTERNS)


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
    """
    Return True for bounded Russian relationship-state carry-over statements.

    This is a formation gate only. It should help the extractor emit
    `relationship/stable` for durable arc state, not become a generic semantic
    shortcut for anything mentioning two characters.
    """
    if not text:
        return False

    text_lower = text.lower()
    cues = extract_durable_relationship_state_cues(text)
    if not cues:
        return False

    if any(re.search(pattern, text_lower) for pattern in DURABLE_RELATIONSHIP_EPISODIC_BLOCKERS):
        return False

    return True


def is_question_form_relationship_prompt(text: str) -> bool:
    """
    Guard against storing user-side relationship questions as stable carry-over memories.

    This remains intentionally narrow: only interrogative phrasing with
    relationship-state semantics should be blocked.
    """
    if not is_question_like_text(text):
        return False

    return bool(
        extract_durable_relationship_state_cues(text)
        or extract_relationship_state_cues(text)
    )


def is_question_form_local_scene_prompt(text: str) -> bool:
    """
    Guard against storing user-side local-scene questions as episodic memories.

    This remains intentionally narrow: only interrogative phrasing with
    local-scene/event semantics should be blocked.
    """
    if not is_question_like_text(text):
        return False

    return is_local_scene_query(text)
