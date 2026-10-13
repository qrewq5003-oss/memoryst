from collections import defaultdict

from app.schemas import ListMemoriesResponse, MemoryItem


def build_group_summaries(memories: ListMemoriesResponse) -> list[dict[str, object]]:
    """Derive chat group summaries from a ListMemoriesResponse for test mocking.

    One group per chat, as list_chat_group_summaries returns since 2026-10-13: the
    character_id is the one on the most recently updated row, character_ids every one
    seen."""
    groups: dict[str, dict[str, object]] = {}
    for item in memories.items:
        key = item.chat_id
        if key not in groups:
            groups[key] = {
                "chat_id": item.chat_id,
                "character_id": item.character_id,
                "character_ids": [],
                "total_count": 0,
                "summary_count": 0,
                "stable_count": 0,
                "episodic_count": 0,
                "last_updated": item.updated_at,
            }
        g = groups[key]
        if item.character_id not in g["character_ids"]:
            g["character_ids"].append(item.character_id)
        g["total_count"] = int(g["total_count"]) + 1
        if item.type == "summary" or item.metadata.is_summary:
            g["summary_count"] = int(g["summary_count"]) + 1
        elif item.layer == "stable":
            g["stable_count"] = int(g["stable_count"]) + 1
        else:
            g["episodic_count"] = int(g["episodic_count"]) + 1
        if item.updated_at > str(g["last_updated"]):
            g["last_updated"] = item.updated_at
            g["character_id"] = item.character_id
    return sorted(groups.values(), key=lambda g: str(g["last_updated"]), reverse=True)


def render_with_details(body: str, memories: list) -> str:
    """List-page HTML plus every card's Inspect Details fragment.

    Inspect Details is fetched when a card is expanded rather than shipped with
    the list - a collapsed <details> still sends everything inside it, and across
    the list that was 202KB of a 366KB page. The content is still reachable, so
    tests that assert "this data reaches the UI" stay meaningful; they just have
    to look where it now lives.
    """
    from unittest.mock import patch

    from app.routes.ui import ui_memory_details_fragment

    parts = [body]
    for memory in memories:
        with patch("app.routes.ui.get_memory_by_id", return_value=memory):
            fragment = ui_memory_details_fragment(_details_request(), memory.id)
        parts.append(fragment.body.decode())
    return "\n".join(parts)


def _details_request():
    from fastapi import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/ui/memory/x/fragment",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        }
    )
