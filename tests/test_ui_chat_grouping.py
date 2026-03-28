import unittest
from unittest.mock import patch

from fastapi import Request

from app.routes.ui import ui_memories_page
from app.schemas import ListMemoriesResponse, MemoryItem, MemoryMetadata


def _request(path: str = "/ui") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        }
    )


def _memory(
    memory_id: str,
    content: str,
    *,
    chat_id: str,
    character_id: str,
    layer: str = "episodic",
    memory_type: str = "event",
    updated_at: str = "2026-03-20T00:00:00+00:00",
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id=chat_id,
        character_id=character_id,
        type=memory_type,
        content=content,
        normalized_content=content.lower(),
        source="manual",
        layer=layer,
        importance=0.6,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at=updated_at,
        last_accessed_at=None,
        access_count=0,
        pinned=False,
        archived=False,
        metadata=MemoryMetadata(entities=[], keywords=[]),
    )


class UiChatGroupingTests(unittest.TestCase):
    def test_all_chats_view_renders_memories_from_multiple_chat_groups(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory("memory-1", "Alice planned the Rome museum trip.", chat_id="chat-1", character_id="char-a"),
                _memory("memory-2", "Elena changed the train booking.", chat_id="chat-2", character_id="char-b"),
            ],
            total=2,
            limit=50,
            offset=0,
        )

        with patch("app.routes.ui.list_memories", return_value=memories):
            response = ui_memories_page(_request(), view="all")

        body = response.body.decode()
        self.assertIn("All Chats", body)
        self.assertIn("Alice planned the Rome museum trip.", body)
        self.assertIn("Elena changed the train booking.", body)

    def test_sidebar_renders_friendlier_chat_label_and_keeps_raw_chat_id_visible(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory(
                    "memory-1",
                    "Alice planned the Rome museum trip.",
                    chat_id="summer_trip/chat-room_alpha",
                    character_id="char-a",
                ),
            ],
            total=1,
            limit=50,
            offset=0,
        )

        with patch("app.routes.ui.list_memories", return_value=memories):
            response = ui_memories_page(_request())

        body = response.body.decode()
        self.assertIn("Summer Trip Chat Room Alpha", body)
        self.assertIn("Raw chat ID:", body)
        self.assertIn("summer_trip/chat-room_alpha", body)

    def test_selected_scope_is_marked_in_sidebar_and_scope_header(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory("memory-1", "Alice planned the Rome museum trip.", chat_id="chat-primary", character_id="char-a"),
                _memory("memory-2", "Elena changed the train booking.", chat_id="chat-secondary", character_id="char-b"),
            ],
            total=2,
            limit=50,
            offset=0,
        )

        with patch("app.routes.ui.list_memories", return_value=memories):
            response = ui_memories_page(_request(), selected_chat_id="chat-secondary", selected_character_id="char-b")

        body = response.body.decode()
        self.assertIn("Current scope", body)
        self.assertIn("chat-group-link-active", body)
        self.assertIn("Character ID: <code>char-b</code>", body)

    def test_sidebar_stats_render_summary_stable_and_episodic_counts_for_selected_chat(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory("summary-1", "Short summary", chat_id="chat-1", character_id="char-a", layer="stable", memory_type="summary"),
                _memory("stable-1", "Stable profile fact", chat_id="chat-1", character_id="char-a", layer="stable", memory_type="profile"),
                _memory("episodic-1", "Recent episodic event", chat_id="chat-1", character_id="char-a", layer="episodic"),
            ],
            total=3,
            limit=50,
            offset=0,
        )

        with patch("app.routes.ui.list_memories", return_value=memories):
            response = ui_memories_page(_request())

        body = response.body.decode()
        self.assertIn("1 summary", body)
        self.assertIn("1 stable", body)
        self.assertIn("1 episodic", body)
        self.assertIn("3 total", body)


if __name__ == "__main__":
    unittest.main()
