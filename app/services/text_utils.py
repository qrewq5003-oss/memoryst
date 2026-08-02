import re
from datetime import datetime, timezone


def get_utc_now() -> str:
    """Get current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def normalize_for_similarity(text: str) -> str:
    """Normalize text for similarity checks: lowercase, strip punctuation, collapse whitespace.

    The trailing strip matters: punctuation becomes a space, so without it "Хорошо."
    normalizes to "хорошо " and compares unequal to "хорошо". Callers compare these
    strings directly (dedup keys, near-duplicate checks), not just token sets.
    """
    normalized = text.lower().strip()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def token_overlap_ratio(text1: str, text2: str) -> float:
    """Compute overlap ratio using the smaller token set as the denominator."""
    tokens1 = set(normalize_for_similarity(text1).split())
    tokens2 = set(normalize_for_similarity(text2).split())
    if not tokens1 or not tokens2:
        return 0.0
    return len(tokens1 & tokens2) / min(len(tokens1), len(tokens2))


def normalize_content(content: str) -> str:
    """Normalize content for dedup: lowercase, strip punctuation, collapse whitespace."""
    text = content.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_ooc_text(text: str) -> bool:
    """Check whether text is an out-of-character (OOC) marker message, case-insensitively."""
    lowered = text.strip().lower()
    return lowered.startswith("ooc:") or lowered.startswith("ooc(") or lowered.startswith("(ooc")


def truncate_content(text: str, max_length: int = 500) -> str:
    """Truncate content to reasonable length, breaking at sentence or word boundary."""
    compact = re.sub(r"\s+", " ", text.strip())
    if len(compact) <= max_length:
        return compact
    truncated = compact[:max_length]
    last_period = truncated.rfind(".")
    if last_period > max_length // 2:
        return truncated[: last_period + 1]
    last_space = truncated.rfind(" ")
    if last_space >= max_length // 2:
        truncated = truncated[:last_space]
    return f"{truncated}..."


# Many character cards open every reply with a status line:
#   [ 🕰️ Time 7:45 PM | 🗓️ Saturday, March 15, 2025 | 📍 Milan - Kitchen | 🌙 Clear, 54°F ]
#   [ Время 14:44 | Среда, 12 апреля, 1721 | Хамам | Свет сквозь купол мягкий ]
#
# It is scaffolding, not content, and it cost twice. The rule-based extractor stored the
# line together with the reply, so 287 memories carried 80-496 characters of clock and
# weather - and 18 of them consist of nothing else, the header having filled the 500-char
# content limit on its own. It also fed the scorer: `Time` reached 248 entity occurrences
# and place names leaked in from the 📍 field, matching every memory of that location
# regardless of what was said there.
_TRANSCRIPT_HEADER_MARKERS = re.compile(r"🕰️|🗓️|📍|\bTime\b|\bВремя\b")


def _looks_like_transcript_header(fragment: str) -> bool:
    """A bracketed opening fragment is a header when it reads as a list of fields.

    Two shapes occur in the corpus and both have to be caught:
      - marked, "[ 🕰️ Time 7:45 PM | 🗓️ Saturday ... ]" - one pipe plus a time, date or
        place marker;
      - bare, "[ Милан | 15 января 1477 | 11:05 | Возраст: 7 | Кастелло Сфорцеско ]" -
        no marker word at all, recognised by having three or more fields.

    The structure is what identifies it. Prose does open with a bracket occasionally, but
    not with pipe-separated fields, so requiring either a marker or three fields keeps
    ordinary text untouched.
    """
    pipes = fragment.count("|")
    if pipes >= 2:
        return True
    return pipes >= 1 and bool(_TRANSCRIPT_HEADER_MARKERS.search(fragment))


def strip_transcript_header(text: str) -> str:
    """Remove a leading roleplay status header, returning what the speaker actually said.

    Returns "" when the header is the whole text - that happens when the header itself
    was long enough to be truncated, leaving a memory that records only a clock reading.
    Callers should treat an empty result as "nothing worth storing".

    Anything that does not look like a header is returned untouched, including text that
    merely starts with a bracket.
    """
    if not text:
        return text

    stripped = text.lstrip()
    if not stripped.startswith("["):
        return text

    end = stripped.find("]")
    if end == -1:
        # No closing bracket: either the header was truncated mid-way, or this is not a
        # header at all. Judge on the opening fragment.
        return "" if _looks_like_transcript_header(stripped[:300]) else text

    if not _looks_like_transcript_header(stripped[: end + 1]):
        return text
    return stripped[end + 1:].lstrip()


# Model scaffolding that leaks into a reply and then into memory. Three shapes occur,
# and they need different treatment - which is why stripping tags alone does not work.
#
#   <!-- GFX_START --> <div style=...> > STATUS: ... </div> <!-- GFX_END -->
#       A rendered widget. The markup AND its contents are presentation, so the whole
#       block goes.
#   <reasoning> ... </reasoning>, <aside>, <anticipation_mode>
#       The model thinking out loud. Contents are meta, so the whole block goes.
#   <output> **СЕЛИМ** — Мне не жалко масла ... </output>
#       A wrapper around the actual reply. Only the tags go; the contents stay.
#
# Ten stored memories were nothing but these, several with the real prose sitting after
# the block - so dropping the row wholesale would have thrown away the fact along with
# the scaffolding.
_GFX_BLOCK_RE = re.compile(r"<!--\s*GFX_START\s*-->.*?<!--\s*GFX_END\s*-->", re.S | re.I)
_GFX_OPEN_RE = re.compile(r"<!--\s*GFX_START\s*-->.*", re.S | re.I)
_META_TAGS = (
    "reasoning", "thinking", "think", "anticipation_mode", "aside", "summary",
    "analysis", "plan", "scratchpad", "internal",
)
_META_BLOCK_RE = re.compile(
    r"<(" + "|".join(_META_TAGS) + r")\b[^>]*>.*?</\1>", re.S | re.I
)
# Truncation cuts a block before its closing tag often enough to matter: two of the ten
# stored rows were an unclosed <reasoning> and <aside>, and matching only balanced blocks
# left their contents behind as if they were prose.
_META_OPEN_RE = re.compile(r"<(?:" + "|".join(_META_TAGS) + r")\b[^>]*>.*", re.S | re.I)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_ANY_TAG_RE = re.compile(r"</?[a-zA-Z][^>]{0,300}>")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_scene_scaffolding(text: str) -> str:
    """Remove rendered widgets and the model's own thinking, keeping the reply.

    Returns "" when nothing but scaffolding was there. Runs before
    strip_transcript_header, because the header usually sits between the block and the
    prose and only becomes leading once the block is gone.
    """
    if not text or "<" not in text:
        return text

    cleaned = _GFX_BLOCK_RE.sub(" ", text)
    # A widget truncated before its GFX_END marker takes the rest of the text with it:
    # everything after the opening marker is the widget.
    cleaned = _GFX_OPEN_RE.sub(" ", cleaned)
    cleaned = _META_BLOCK_RE.sub(" ", cleaned)
    cleaned = _META_OPEN_RE.sub(" ", cleaned)
    cleaned = _HTML_COMMENT_RE.sub(" ", cleaned)
    # Whatever tags remain are wrappers around real content (<output>) or strays left by
    # a truncated block (</think>), so drop the tags and keep the text.
    cleaned = _ANY_TAG_RE.sub(" ", cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def clean_memory_text(text: str) -> str:
    """Strip everything that is transcript furniture rather than something said.

    Returns "" when the whole text was furniture; callers treat that as "nothing worth
    storing".
    """
    return strip_transcript_header(strip_scene_scaffolding(text))
