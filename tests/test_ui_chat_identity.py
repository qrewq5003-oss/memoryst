"""
One sidebar entry per chat, and a character label a person can read.

On 2026-10-13 the sidebar listed 75 entries for 62 chats. character_id used to be the
index of the card in SillyTavern's array, and it shifted whenever cards were added or
reordered, so 11 chats had accumulated two or three ids - each one its own entry with
its own counts, the same chat twice and neither half complete. The label under every
entry read "Character: 20" for 40 of the 75: an index shown as if it were a name.

Retrieval has been scoped by chat alone since 2026-09-20. This makes the UI agree.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.repositories.memory_repo import create_memory, list_chat_group_summaries, upsert_tracker
from app.routes.ui import ui_memories_page
from app.schemas import CreateMemoryRequest, ListMemoriesResponse, MemoryItem, MemoryMetadata
from app.ui_helpers.presentation import character_display_name, resolve_selected_group
from tests.conftest_helpers import build_group_summaries

ST_CHAT_ID = "Valeria Mendoza - 2026-07-09@22h06m04s351ms"


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


def _memory(memory_id: str, content: str, *, chat_id: str, character_id: str, updated_at: str) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id=chat_id,
        character_id=character_id,
        type="event",
        content=content,
        normalized_content=content.lower(),
        source="manual",
        layer="episodic",
        importance=0.6,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at=updated_at,
        last_accessed_at=None,
        access_count=0,
        pinned=False,
        archived=False,
        metadata=MemoryMetadata(entities=[], keywords=[]),
    )


class CharacterDisplayNameTests(unittest.TestCase):
    def test_the_name_comes_out_of_a_sillytavern_chat_id(self) -> None:
        # Whatever the character_id holds: the chat id names the character.
        self.assertEqual(character_display_name(ST_CHAT_ID, "20"), "Valeria Mendoza")
        self.assertEqual(character_display_name(ST_CHAT_ID, "Valeria Mendoza1.png"), "Valeria Mendoza")
        self.assertEqual(character_display_name("Tiffany tiff Martinez - 2026-06-24@17h15m01s217ms", "18"), "Tiffany tiff Martinez")

    def test_an_avatar_filename_loses_its_extension(self) -> None:
        self.assertEqual(character_display_name("chat-1", "Mai.png"), "Mai")
        self.assertEqual(character_display_name("chat-1", "Camila1.WEBP"), "Camila1")

    def test_a_card_index_is_not_a_name(self) -> None:
        # "Character: 20" read as a value and meant nothing. Nothing is shown instead.
        self.assertIsNone(character_display_name("chat-1", "20"))
        self.assertIsNone(character_display_name("chat-1", ""))
        self.assertIsNone(character_display_name("chat-1", None))


class GroupSummariesByChatTests(unittest.TestCase):
    """Against a real SQLite database: the UI tests mock this query out."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _make_memory(self, content: str, *, chat_id: str = ST_CHAT_ID, character_id: str, layer: str = "episodic"):
        return create_memory(
            CreateMemoryRequest(
                chat_id=chat_id,
                character_id=character_id,
                type="event",
                content=content,
                source="manual",
                layer=layer,
                importance=0.7,
                metadata=MemoryMetadata(),
            )
        )

    def test_a_chat_that_changed_ids_is_one_group_with_the_counts_added_up(self) -> None:
        self._make_memory("Under the old index.", character_id="20")
        self._make_memory("Under the other old index.", character_id="21", layer="stable")
        self._make_memory("Under the avatar.", character_id="Valeria Mendoza1.png")
        self._make_memory("Another chat entirely.", chat_id="Mai - 2026-10-02@12h40m36s920ms", character_id="Mai.png")

        groups = list_chat_group_summaries()

        self.assertEqual([g["chat_id"] for g in groups], ["Mai - 2026-10-02@12h40m36s920ms", ST_CHAT_ID])
        merged = groups[1]
        self.assertEqual(merged["total_count"], 3)
        self.assertEqual(merged["stable_count"], 1)
        self.assertEqual(merged["episodic_count"], 2)
        # The current identity heads the group; every id it carried is listed.
        self.assertEqual(merged["character_id"], "Valeria Mendoza1.png")
        self.assertEqual(sorted(merged["character_ids"]), ["20", "21", "Valeria Mendoza1.png"])

    def test_a_chat_with_one_id_lists_it_alone(self) -> None:
        self._make_memory("Only one id.", character_id="Mai.png")
        group = list_chat_group_summaries()[0]
        self.assertEqual(group["character_id"], "Mai.png")
        self.assertEqual(group["character_ids"], ["Mai.png"])


class SidebarMergesSplitChatsTests(unittest.TestCase):
    def _split_chat(self) -> ListMemoriesResponse:
        return ListMemoriesResponse(
            items=[
                _memory("m-old", "Валерия работает в кафе.", chat_id=ST_CHAT_ID, character_id="20", updated_at="2026-07-09T00:00:00+00:00"),
                _memory("m-new", "Валерия переехала.", chat_id=ST_CHAT_ID, character_id="Valeria Mendoza1.png", updated_at="2026-09-21T00:00:00+00:00"),
                _memory("m-idx", "Чат только со старым индексом.", chat_id="chat-index-only", character_id="18", updated_at="2026-06-01T00:00:00+00:00"),
            ],
            total=3,
            limit=50,
            offset=0,
        )

    def test_one_entry_per_chat_and_the_list_is_scoped_by_chat_alone(self) -> None:
        memories = self._split_chat()
        with (
            patch("app.routes.ui.list_chat_group_summaries", return_value=build_group_summaries(memories)),
            patch("app.routes.ui.list_ui_filtered_memories", return_value=memories) as listed,
        ):
            response = ui_memories_page(_request(), selected_chat_id=ST_CHAT_ID, selected_character_id="20")

        body = response.body.decode()
        self.assertEqual(body.count('class="chat-group-link chat-group-link-active"'), 1)
        self.assertEqual(body.count('class="chat-group-title">Valeria'), 1, "the chat appears once in the list")
        # Resolved by chat although the link carried an old index.
        self.assertIn("Current scope", body)
        self.assertIn("Character: Valeria Mendoza", body)
        self.assertNotIn("Character: 20", body)
        # Both memories, whichever id they were written under.
        self.assertIn("Валерия работает в кафе.", body)
        self.assertIn("Валерия переехала.", body)
        for call in listed.call_args_list:
            self.assertEqual(call.kwargs.get("chat_id"), ST_CHAT_ID)
            self.assertIsNone(call.kwargs.get("character_id"), "the list must not be narrowed to one id")
        # The footer says which ids the chat has carried, current one first.
        self.assertIn("Character ID: <code>Valeria Mendoza1.png, 20</code>", body)

    def test_an_index_only_chat_shows_no_character_line_at_all(self) -> None:
        memories = self._split_chat()
        with (
            patch("app.routes.ui.list_chat_group_summaries", return_value=build_group_summaries(memories)),
            patch("app.routes.ui.list_ui_filtered_memories", return_value=memories),
        ):
            response = ui_memories_page(_request(), selected_chat_id="chat-index-only")

        body = response.body.decode()
        self.assertNotIn("Character: 18", body)
        self.assertNotIn("Character: </span>", body)
        # The id itself stays where ids belong, in the footer for the selected chat.
        self.assertIn("Character ID: <code>18</code>", body)

    def test_delete_chat_covers_the_whole_chat_not_the_current_id(self) -> None:
        memories = self._split_chat()
        with (
            patch("app.routes.ui.list_chat_group_summaries", return_value=build_group_summaries(memories)),
            patch("app.routes.ui.list_ui_filtered_memories", return_value=memories),
        ):
            response = ui_memories_page(_request(), selected_chat_id=ST_CHAT_ID)

        body = response.body.decode()
        self.assertIn('action="/ui/delete-chat"', body)
        self.assertIn('name="character_id" value=""', body)

    def test_the_resolver_ignores_a_stale_character_id(self) -> None:
        groups = build_group_summaries(self._split_chat())
        chosen = resolve_selected_group(groups, requested_chat_id=ST_CHAT_ID, requested_character_id="no-such-id", view="chat")
        self.assertEqual(chosen["chat_id"], ST_CHAT_ID)


class TrackersUnderAnOldIdTests(unittest.TestCase):
    """The Tools page reads trackers through every id the chat has carried."""

    def setUp(self) -> None:
        self.original_api_key = config.API_KEY
        self.original_db_path = config.DATABASE_PATH
        self.addCleanup(self._restore_config)
        config.API_KEY = ""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        init_schema()
        self.client = TestClient(app)

    def _restore_config(self) -> None:
        config.API_KEY = self.original_api_key
        config.DATABASE_PATH = self.original_db_path

    def test_a_tracker_written_under_the_old_index_still_renders(self) -> None:
        create_memory(
            CreateMemoryRequest(
                chat_id=ST_CHAT_ID, character_id="Valeria Mendoza1.png", type="event",
                content="newest row, so the avatar heads the group", source="auto",
                layer="episodic", metadata=MemoryMetadata(),
            )
        )
        upsert_tracker(
            chat_id=ST_CHAT_ID,
            character_id="20",
            tracker_type="timeline",
            content="- Thursday, February 13, 2025 - Milan: arrived.",
            metadata=MemoryMetadata(),
        )

        response = self.client.get("/ui/tools", params={"selected_chat_id": ST_CHAT_ID})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Milan: arrived.", response.text)


if __name__ == "__main__":
    unittest.main()
