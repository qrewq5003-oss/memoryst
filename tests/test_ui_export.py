"""The export must be a complete picture of what the database holds.

It ran through list_memories, which hides trackers, so a "full export" silently
omitted every tracker document. There is no import path yet - which is exactly why
this matters: the export is what a manual restore would be rebuilt from.
"""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.repositories.memory_repo import create_memory, upsert_tracker
from app.schemas import CreateMemoryRequest, MemoryMetadata

CHAT_ID = "chat-export"
CHARACTER_ID = "20"


class UiExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()
        self.client = TestClient(app)

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _make_memory(self, content: str):
        return create_memory(
            CreateMemoryRequest(
                chat_id=CHAT_ID,
                character_id=CHARACTER_ID,
                type="event",
                content=content,
                source="manual",
                layer="episodic",
                importance=0.7,
                metadata=MemoryMetadata(),
            )
        )

    def _export(self) -> list[dict]:
        response = self.client.get(
            "/ui/export", params={"chat_id": CHAT_ID, "character_id": CHARACTER_ID}
        )
        self.assertEqual(response.status_code, 200)
        body = response.text.strip()
        return [json.loads(line) for line in body.splitlines()] if body else []

    def test_export_includes_trackers_with_their_type(self) -> None:
        upsert_tracker(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tracker_type="timeline",
            content="- Thursday, February 13, 2025 - Milan: arrived.",
            metadata=MemoryMetadata(),
        )
        self._make_memory("She likes espresso.")

        records = self._export()

        trackers = [r for r in records if r["type"] == "tracker"]
        self.assertEqual(len(trackers), 1)
        self.assertEqual(trackers[0]["tracker_type"], "timeline")
        self.assertIn("Milan", trackers[0]["content"])

    def test_ordinary_memories_carry_no_tracker_type_key(self) -> None:
        self._make_memory("She likes espresso.")

        records = self._export()

        self.assertEqual(len(records), 1)
        self.assertNotIn("tracker_type", records[0])

    def test_export_is_not_truncated_by_a_page_boundary(self) -> None:
        """The old code capped at a bare limit=10000 and would have truncated in
        silence. Paging is verified here across a boundary rather than at scale."""
        from app.routes import ui

        original_page_size = ui.EXPORT_PAGE_SIZE
        ui.EXPORT_PAGE_SIZE = 10
        self.addCleanup(setattr, ui, "EXPORT_PAGE_SIZE", original_page_size)

        for index in range(25):
            self._make_memory(f"Событие {index}.")

        records = self._export()

        self.assertEqual(len(records), 25)
        self.assertEqual(
            {r["content"] for r in records},
            {f"Событие {index}." for index in range(25)},
        )


if __name__ == "__main__":
    unittest.main()
