from app.schemas import MemoryItem
from app.services.text_utils import normalize_for_similarity as _normalize_for_dedup, truncate_content

MAX_FORMATTED_MEMORIES = 4
MAX_FORMATTED_CONTENT_LENGTH = 120


def _format_labels(item: MemoryItem) -> str:
    """Build compact formatting labels for a memory item."""
    labels = []
    if item.pinned:
        labels.append("[PINNED]")
    if item.type == "summary" or item.metadata.is_summary:
        labels.append("[SUMMARY]")
    else:
        labels.append("[STABLE]" if item.layer == "stable" else "[EPISODIC]")
    return " ".join(labels)


def format_memory_block(items: list[MemoryItem]) -> str:
    """
    Format memory items into a memory block for prompt.

    Returns empty string if items is empty.
    Maximum 4 memory entries.
    Near-duplicate content is removed after normalization.
    Empty lines are skipped.
    Content is truncated to keep the block concise.
    """
    if not items:
        return ""

    # Remove duplicate-like content after normalization, preserve order.
    seen_contents = set()
    unique_items: list[MemoryItem] = []
    for item in items:
        stripped_content = item.content.strip()
        if not stripped_content:
            continue

        normalized_content = _normalize_for_dedup(stripped_content)
        if not normalized_content or normalized_content in seen_contents:
            continue

        seen_contents.add(normalized_content)
        unique_items.append(item)

    unique_items = unique_items[:MAX_FORMATTED_MEMORIES]

    lines = ["[Relevant Memory]"]
    for item in unique_items:
        lines.append(f"- {_format_labels(item)} {truncate_content(item.content, MAX_FORMATTED_CONTENT_LENGTH)}")

    return "\n".join(lines)
