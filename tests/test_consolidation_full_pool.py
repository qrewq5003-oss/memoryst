"""Consolidation must see the whole chat, not the most recently touched page of it.

Both paths here used to page to 50 rows while ordering by updated_at DESC. Every
live chat in the real database holds 100-414 memories, so both were silently
operating on a recency-biased slice. These tests all build a chat larger than the
old window, against a real SQLite database rather than a mocked repository - a mock
would have reproduced the paging bug faithfully and still passed.
"""
import tempfile
import unittest
from pathlib import Path

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, get_memory_by_id, upsert_tracker
from app.schemas import CreateMemoryRequest, MemoryMetadata
from app.services.summary_service import (
    ROLLING_SUMMARY_KIND,
    generate_rolling_summary,
    generate_tiered_consolidation,
)

CHAT_ID = "chat-big"
CHARACTER_ID = "7"
OLD_WINDOW = 50


class ConsolidationFullPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _make_memory(self, content: str, *, layer: str = "episodic", memory_type: str = "event"):
        return create_memory(
            CreateMemoryRequest(
                chat_id=CHAT_ID,
                character_id=CHARACTER_ID,
                type=memory_type,
                content=content,
                source="manual",
                layer=layer,
                importance=0.7,
                metadata=MemoryMetadata(),
            )
        )

    def _fill_beyond_old_window(self, count: int = OLD_WINDOW + 20) -> None:
        for index in range(count):
            self._make_memory(f"Событие номер {index} произошло в Милане.")

    def test_existing_rolling_summary_is_found_past_the_old_page(self) -> None:
        """The duplicate-summary regression.

        The summary is created first, then buried under more recently updated
        memories. Under the old limit=50 it fell off the page, _list_existing_summary
        returned None, and a second summary was created instead of refreshing the
        first - forever, once a chat grew past the window.
        """
        for index in range(5):
            self._make_memory(f"Ранняя сцена {index}: разговор о бюджете.")

        first = generate_rolling_summary(chat_id=CHAT_ID, character_id=CHARACTER_ID)
        self.assertIsNotNone(first.summary_memory_id)

        self._fill_beyond_old_window()

        second = generate_rolling_summary(chat_id=CHAT_ID, character_id=CHARACTER_ID)

        self.assertEqual(
            second.summary_memory_id,
            first.summary_memory_id,
            "a chat larger than the old page must refresh its summary, not grow a second one",
        )

        summaries = [
            item
            for item in self._all_items()
            if item.type == "summary"
            or (item.metadata.is_summary and item.metadata.summary_kind == ROLLING_SUMMARY_KIND)
        ]
        self.assertEqual(len(summaries), 1)

    def _all_items(self):
        from app.repositories.memory_repo import list_memories

        return list_memories(chat_id=CHAT_ID, character_id=CHARACTER_ID, limit=10_000).items

    def test_tiered_consolidation_without_source_ids_sees_the_whole_chat(self) -> None:
        self._fill_beyond_old_window(count=OLD_WINDOW + 30)

        result = generate_tiered_consolidation(
            chat_id=CHAT_ID, character_id=CHARACTER_ID, tier="arc"
        )

        self.assertGreater(
            result.summarized_count,
            OLD_WINDOW,
            "consolidation still stops at the old 50-row window",
        )

    def test_explicit_source_ids_resolve_past_the_old_page(self) -> None:
        """Sources are named by id, so paging had no business filtering them at all.

        The oldest memory is created first and never touched again, which puts it far
        outside the page the old implementation scanned before matching ids.
        """
        oldest = self._make_memory("Самое первое событие: приезд в Милан.")
        self._fill_beyond_old_window(count=OLD_WINDOW + 60)
        newest = self._make_memory("Последнее событие: отъезд из Милана.")

        result = generate_tiered_consolidation(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tier="arc",
            source_ids=[oldest.id, newest.id],
        )

        self.assertIn(oldest.id, result.source_memory_ids)
        self.assertIn(newest.id, result.source_memory_ids)

    def test_explicit_sources_still_refuse_trackers(self) -> None:
        """The tracker guard used to come free from list_memories. Resolving ids
        directly means it has to be explicit, so it gets its own test."""
        tracker, _ = upsert_tracker(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tracker_type="timeline",
            content="- Thursday, February 13, 2025 - Milan: arrived.",
            metadata=MemoryMetadata(),
        )
        memories = [self._make_memory(f"Сцена {i}: ужин.") for i in range(4)]

        result = generate_tiered_consolidation(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tier="arc",
            source_ids=[tracker.id] + [m.id for m in memories],
        )

        self.assertNotIn(tracker.id, result.source_memory_ids)
        after = get_memory_by_id(tracker.id)
        self.assertEqual(after.type, "tracker")
        self.assertFalse(after.archived)

    def test_explicit_sources_refuse_ids_from_another_chat(self) -> None:
        """Also previously implicit in the scoped list query."""
        foreign = create_memory(
            CreateMemoryRequest(
                chat_id="some-other-chat",
                character_id=CHARACTER_ID,
                type="event",
                content="Событие из чужого чата.",
                source="manual",
                layer="episodic",
                importance=0.7,
                metadata=MemoryMetadata(),
            )
        )
        mine = [self._make_memory(f"Сцена {i}: прогулка.") for i in range(4)]

        result = generate_tiered_consolidation(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tier="arc",
            source_ids=[foreign.id] + [m.id for m in mine],
        )

        self.assertNotIn(foreign.id, result.source_memory_ids)


if __name__ == "__main__":
    unittest.main()
