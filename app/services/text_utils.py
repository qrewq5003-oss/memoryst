import re
from datetime import datetime, timezone


def get_utc_now() -> str:
    """Get current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def normalize_for_similarity(text: str) -> str:
    """Normalize text for similarity checks: lowercase, strip punctuation, collapse whitespace."""
    normalized = text.lower().strip()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


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
