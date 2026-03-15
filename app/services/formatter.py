from app.schemas import MemoryItem


def format_memory_block(items: list[MemoryItem]) -> str:
    """
    Format memory items into a memory block for prompt.

    Returns empty string if items is empty.
    Maximum 6 lines.
    No duplicate content (after stripping).
    Empty lines are skipped.
    """
    if not items:
        return ""

    # Remove duplicates by stripped content, preserve order
    # Also skip empty content after strip
    seen_contents = set()
    unique_items = []
    for item in items:
        # Strip content for deduplication and output
        stripped_content = item.content.strip()
        # Skip empty content
        if not stripped_content:
            continue
        if stripped_content not in seen_contents:
            seen_contents.add(stripped_content)
            unique_items.append(stripped_content)

    # Limit to 6 items
    unique_items = unique_items[:6]

    # Build memory block
    lines = ["[Relevant Memory]"]
    for content in unique_items:
        lines.append(f"- {content}")

    return "\n".join(lines)
