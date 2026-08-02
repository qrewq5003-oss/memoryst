"""
Stage C: /ui/tools renders a Trackers section with a card per tracker type, each with an
"Обновить" button wired to POST /memory/tracker/update. These are HTTP-level render
checks only (the button's fetch/DOM-update behavior lives in _scripts.html and is out
of reach of a Python test) - see test_tracker_api.py for the error_detail contract the
JS reads on failure.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.repositories.memory_repo import create_memory, upsert_tracker
from app.schemas import CreateMemoryRequest, MemoryMetadata

CHAT_ID = "chat-ui-trackers"
CHARACTER_ID = "20"


class UiTrackersRenderTests(unittest.TestCase):
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

    def _seed_chat_group(self) -> None:
        # The sidebar/scope resolver only recognizes a (chat_id, character_id) pair once
        # some row exists for it - passing selected_chat_id/character_id alone is not
        # enough, matching how the rest of the UI scopes itself.
        create_memory(
            CreateMemoryRequest(
                chat_id=CHAT_ID,
                character_id=CHARACTER_ID,
                type="event",
                content="seed memory so the chat group resolves",
                source="auto",
                layer="episodic",
                metadata=MemoryMetadata(),
            )
        )

    def _get(self, chat_id: str | None = CHAT_ID, character_id: str | None = CHARACTER_ID):
        params = {}
        if chat_id:
            params["selected_chat_id"] = chat_id
        if character_id:
            params["selected_character_id"] = character_id
        return self.client.get("/ui/tools", params=params)

    def test_no_chat_selected_hides_the_tracker_cards(self) -> None:
        response = self._get(chat_id=None, character_id=None)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Trackers", response.text)
        self.assertIn("Select a single chat", response.text)
        # The bare substring also matches the JS selector in _scripts.html (always
        # included); the actual button element is what must be absent here.
        self.assertNotIn('class="tracker-update-btn"', response.text)

    def test_selected_chat_renders_all_four_tracker_cards_with_update_buttons(self) -> None:
        self._seed_chat_group()

        response = self._get()

        self.assertEqual(response.status_code, 200)
        for label in ("Timeline", "Relationship", "NPC Who's Who", "Character POV Notes"):
            self.assertIn(label, response.text)
        for tracker_type in ("timeline", "relationship", "npc_whoswho", "character_pov_notes"):
            self.assertIn(f'data-tracker-type="{tracker_type}"', response.text)
            self.assertIn(f'id="tracker-content-{tracker_type}"', response.text)
            self.assertIn(f'id="tracker-meta-{tracker_type}"', response.text)
            self.assertIn(f'id="tracker-status-{tracker_type}"', response.text)
        self.assertIn(f'id="tracker-chat-id" value="{CHAT_ID}"', response.text)
        self.assertIn(f'id="tracker-char-id" value="{CHARACTER_ID}"', response.text)

    def test_never_generated_tracker_shows_placeholder_meta(self) -> None:
        self._seed_chat_group()

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertIn("ещё не сгенерирован", response.text)

    def test_existing_tracker_content_and_watermark_are_rendered(self) -> None:
        upsert_tracker(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tracker_type="timeline",
            content="- Thursday, February 13, 2025, 7:45 PM - Milan: arrived.",
            metadata=MemoryMetadata(
                tracker_generated_at="2026-07-13T10:00:00Z",
                tracker_last_sequence_index=12,
                tracker_entries=[{"date": "Thursday, February 13, 2025", "summary": "arrived"}],
            ),
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Milan: arrived.", response.text)
        self.assertIn("сообщений с обновления", response.text)

    def test_all_chats_view_has_no_single_scope_for_trackers(self) -> None:
        self._seed_chat_group()

        response = self.client.get("/ui/tools", params={"view": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Select a single chat", response.text)


if __name__ == "__main__":
    unittest.main()
