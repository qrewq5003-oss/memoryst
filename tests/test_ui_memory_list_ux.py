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
    chat_id: str = "chat-1",
    character_id: str = "char-1",
    updated_at: str = "2026-03-20T00:00:00+00:00",
    source: str = "manual",
    layer: str = "episodic",
    memory_type: str = "event",
    pinned: bool = False,
    archived: bool = False,
    entities: list[str] | None = None,
    keywords: list[str] | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id=chat_id,
        character_id=character_id,
        type=memory_type,
        content=content,
        normalized_content=content.lower(),
        source=source,
        layer=layer,
        importance=0.7,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at=updated_at,
        last_accessed_at=None,
        access_count=3,
        pinned=pinned,
        archived=archived,
        metadata=MemoryMetadata(
            entities=entities or [],
            keywords=keywords or [],
        ),
    )


class UiMemoryListUxTests(unittest.TestCase):
    def test_default_view_selects_one_chat_scope_instead_of_rendering_all_chats_mixed(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory(
                    "memory-1",
                    "Alice planned the Rome museum trip.",
                    chat_id="chat-new",
                    updated_at="2026-03-22T00:00:00+00:00",
                ),
                _memory(
                    "memory-2",
                    "Bob fixed the Paris calendar.",
                    chat_id="chat-old",
                    updated_at="2026-03-10T00:00:00+00:00",
                ),
            ],
            total=2,
            limit=50,
            offset=0,
        )

        with patch("app.routes.ui.list_memories", return_value=memories):
            response = ui_memories_page(_request())

        body = response.body.decode()
        self.assertIn("chat-new", body)
        self.assertIn("Alice planned the Rome museum trip.", body)
        self.assertNotIn("Bob fixed the Paris calendar.", body)
        self.assertIn("All Chats", body)

    def test_text_search_filters_rendered_memories(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory(
                    "memory-1",
                    "Alice planned the Rome museum trip.",
                    entities=["Alice"],
                    keywords=["rome", "museum"],
                ),
                _memory(
                    "memory-2",
                    "Bob fixed the Paris calendar.",
                    entities=["Bob"],
                    keywords=["paris", "calendar"],
                ),
            ],
            total=2,
            limit=50,
            offset=0,
        )

        with patch("app.routes.ui.list_memories", return_value=memories):
            response = ui_memories_page(_request(), search="rome")

        body = response.body.decode()
        self.assertIn("Alice planned the Rome museum trip.", body)
        self.assertNotIn("Bob fixed the Paris calendar.", body)
        self.assertIn('name="search" value="rome"', body)

    def test_selected_filter_values_are_preserved_in_form(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory(
                    "memory-1",
                    "Elena documented the theater booking.",
                    chat_id="chat-1",
                    character_id="char-1",
                    layer="stable",
                    memory_type="profile",
                )
            ],
            total=1,
            limit=50,
            offset=0,
        )

        with patch(
            "app.routes.ui.list_memories",
            return_value=memories,
        ):
            response = ui_memories_page(
                _request(),
                selected_chat_id="chat-1",
                selected_character_id="char-1",
                type="profile",
                source="manual",
                layer="stable",
                search="elena",
                pinned="true",
                archived="false",
                limit=25,
            )

        body = response.body.decode()
        self.assertIn('name="selected_chat_id" value="chat-1"', body)
        self.assertIn('name="selected_character_id" value="char-1"', body)
        self.assertIn('name="search" value="elena"', body)
        self.assertIn('<option value="profile" selected>', body)
        self.assertIn('<option value="manual" selected>', body)
        self.assertIn('<option value="stable" selected>', body)
        self.assertIn('<option value="true" selected>', body)
        self.assertIn('<option value="false" selected>', body)
        self.assertIn('name="limit" value="25"', body)

    def test_clear_filters_link_and_empty_search_render_cleanly(self) -> None:
        with patch(
            "app.routes.ui.list_memories",
            return_value=ListMemoriesResponse(items=[], total=0, limit=50, offset=0),
        ):
            response = ui_memories_page(_request())

        body = response.body.decode()
        self.assertIn('href="/ui"', body)
        self.assertIn('name="search" value=""', body)

    def test_memory_cards_render_badges_and_metadata_details(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory(
                    "memory-1",
                    "Elena saved the Rome hotel address.",
                    source="auto",
                    layer="stable",
                    memory_type="profile",
                    pinned=True,
                    archived=True,
                    entities=["Elena", "Rome"],
                    keywords=["hotel", "address"],
                )
            ],
            total=1,
            limit=50,
            offset=0,
        )

        with patch("app.routes.ui.list_memories", return_value=memories):
            response = ui_memories_page(_request())

        body = response.body.decode()
        self.assertIn("Inspect Details", body)
        self.assertIn("Pinned", body)
        self.assertIn("Archived", body)
        self.assertIn("profile", body)
        self.assertIn("auto", body)
        self.assertIn("stable", body)
        self.assertIn("Entities:", body)
        self.assertIn("Keywords:", body)
        self.assertIn("Elena", body)
        self.assertIn("address", body)
        self.assertIn("Normalized:", body)


if __name__ == "__main__":
    unittest.main()
