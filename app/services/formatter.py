from app.schemas import MemoryItem


def format_memory_block(items: list[MemoryItem]) -> str:
    """
    Format memory items into a memory block for prompt.

    Returns empty string if items is empty.
    Maximum 6 lines.
    No duplicate content.
    """
    if not items:
        return ""

    # Remove duplicates by content, preserve order
    seen_contents = set()
    unique_items = []
    for item in items:
        if item.content not in seen_contents:
            seen_contents.add(item.content)
            unique_items.append(item)

    # Limit to 6 items
    unique_items = unique_items[:6]

    # Build memory block
    lines = ["[Relevant Memory]"]
    for item in unique_items:
        lines.append(f"- {item.content}")

    return "\n".join(lines)
