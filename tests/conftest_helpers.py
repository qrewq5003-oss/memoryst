from collections import defaultdict

from app.schemas import ListMemoriesResponse, MemoryItem


def build_group_summaries(memories: ListMemoriesResponse) -> list[dict[str, object]]:
    """Derive chat group summaries from a ListMemoriesResponse for test mocking."""
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for item in memories.items:
        key = (item.chat_id, item.character_id)
        if key not in groups:
            groups[key] = {
                "chat_id": item.chat_id,
                "character_id": item.character_id,
                "total_count": 0,
                "summary_count": 0,
                "stable_count": 0,
                "episodic_count": 0,
                "last_updated": item.updated_at,
            }
        g = groups[key]
        g["total_count"] = int(g["total_count"]) + 1
        if item.type == "summary" or item.metadata.is_summary:
            g["summary_count"] = int(g["summary_count"]) + 1
        elif item.layer == "stable":
            g["stable_count"] = int(g["stable_count"]) + 1
        else:
            g["episodic_count"] = int(g["episodic_count"]) + 1
        if item.updated_at > str(g["last_updated"]):
            g["last_updated"] = item.updated_at
    return sorted(groups.values(), key=lambda g: str(g["last_updated"]), reverse=True)
