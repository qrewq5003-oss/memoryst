import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.services.chat_buffer_service import HOT_BUFFER_SIZE, add_message, reset_all_buffers
from app.schemas import MemoryItem, MemoryMetadata, RetrieveMemoryRequest
from app.services.retrieve_service import retrieve_memories


def _memory(
    memory_id: str,
    *,
    keywords: list[str],
    entities: list[str] | None = None,
    importance: float = 0.5,
    updated_at: str = "2026-03-20T00:00:00+00:00",
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id="chat-1",
        character_id="char-1",
        type="event",
        content=memory_id,
        normalized_content=memory_id,
        source="manual",
        layer="episodic",
        importance=importance,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at=updated_at,
        last_accessed_at=None,
        access_count=0,
        pinned=False,
        archived=False,
        metadata=MemoryMetadata(keywords=keywords, entities=entities or []),
    )


class RawHistoryFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()
        reset_all_buffers()
        self.addCleanup(reset_all_buffers)

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _retrieve(self, memories: list[MemoryItem], **kwargs):
        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=memories),
            patch("app.services.retrieve_service.increment_access_count"),
            patch("app.services.retrieve_service.format_memory_block", return_value="formatted"),
        ):
            return retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    limit=5,
                    **kwargs,
                )
            )

    # -- Automatic trigger -------------------------------------------------

    def test_automatic_fallback_fires_when_no_memory_passes_threshold(self) -> None:
        for i in range(HOT_BUFFER_SIZE + 1):
            add_message("chat-1", "char-1", "user", f"мы говорили про драконов {i}")

        response = self._retrieve([], user_input="драконов")

        self.assertEqual(response.items, [])
        self.assertEqual(len(response.raw_fallback), 1)
        self.assertEqual(response.raw_fallback[0].trigger, "automatic")
        self.assertTrue(any("драконов" in m.text for m in response.raw_fallback[0].messages))

    def test_automatic_fallback_does_not_fire_when_a_strong_memory_is_found(self) -> None:
        for i in range(HOT_BUFFER_SIZE + 1):
            add_message("chat-1", "char-1", "user", f"мы говорили про драконов {i}")

        strong = _memory(
            "strong",
            keywords=["алиса", "драконов", "пазл", "париж", "музей"],
            entities=["Алиса"],
        )

        response = self._retrieve([strong], user_input="Алиса драконов пазл Париж музей")

        self.assertEqual([item.id for item in response.items], ["strong"])
        self.assertEqual(response.raw_fallback, [])

    def test_automatic_fallback_is_scoped_to_chat_and_character(self) -> None:
        for i in range(HOT_BUFFER_SIZE + 1):
            add_message("chat-1", "char-1", "user", f"уникальныйтерм{i} про чат один")
        for i in range(HOT_BUFFER_SIZE + 1):
            add_message("chat-2", "char-1", "user", f"уникальныйтерм{i} про чат два")

        response = self._retrieve([], user_input="уникальныйтерм0")

        self.assertEqual(len(response.raw_fallback), 1)
        for message in response.raw_fallback[0].messages:
            self.assertEqual(message.chat_id, "chat-1")

    def test_automatic_fallback_skipped_when_no_usable_keywords(self) -> None:
        # Single short/stopword-like input has no extractable keywords, so
        # there is nothing sensible to search the FTS index with.
        response = self._retrieve([], user_input="и")

        self.assertEqual(response.raw_fallback, [])

    def test_raw_fallback_results_are_kept_out_of_consolidated_items(self) -> None:
        for i in range(HOT_BUFFER_SIZE + 1):
            add_message("chat-1", "char-1", "user", f"мы говорили про драконов {i}")

        response = self._retrieve([], user_input="драконов")

        self.assertEqual(response.items, [])
        self.assertNotEqual(response.raw_fallback, [])

    # -- Manual trigger ------------------------------------------------------

    def test_manual_trigger_resolves_requested_source_message_ids(self) -> None:
        added = [
            add_message("chat-1", "char-1", "user", f"message {i}")
            for i in range(HOT_BUFFER_SIZE + 1)
        ]
        cooled_id = added[0].id

        strong = _memory(
            "strong",
            keywords=["alice", "puzzle", "paris", "project", "museum"],
            entities=["Alice"],
        )

        response = self._retrieve(
            [strong],
            user_input="Alice puzzle Paris project museum",
            manual_source_message_ids=[cooled_id],
        )

        self.assertEqual([item.id for item in response.items], ["strong"])
        manual_results = [r for r in response.raw_fallback if r.trigger == "manual"]
        self.assertEqual(len(manual_results), 1)
        self.assertEqual([m.id for m in manual_results[0].messages], [cooled_id])
        self.assertEqual(manual_results[0].messages[0].text, "message 0")

    def test_manual_trigger_ignores_ids_from_a_different_chat(self) -> None:
        other_chat_added = [
            add_message("chat-2", "char-1", "user", f"message {i}")
            for i in range(HOT_BUFFER_SIZE + 1)
        ]
        foreign_id = other_chat_added[0].id

        response = self._retrieve(
            [],
            user_input="hello there",
            manual_source_message_ids=[foreign_id],
        )

        manual_results = [r for r in response.raw_fallback if r.trigger == "manual"]
        self.assertEqual(manual_results, [])

    def test_manual_trigger_ignores_unknown_ids(self) -> None:
        response = self._retrieve(
            [],
            user_input="hello there",
            manual_source_message_ids=["00000000-0000-0000-0000-000000000000"],
        )

        manual_results = [r for r in response.raw_fallback if r.trigger == "manual"]
        self.assertEqual(manual_results, [])

    def test_both_triggers_can_fire_in_the_same_request(self) -> None:
        added = [
            add_message("chat-1", "char-1", "user", f"мы говорили про драконов {i}")
            for i in range(HOT_BUFFER_SIZE + 1)
        ]
        cooled_id = added[0].id

        response = self._retrieve(
            [],
            user_input="драконов",
            manual_source_message_ids=[cooled_id],
        )

        triggers = {r.trigger for r in response.raw_fallback}
        self.assertEqual(triggers, {"automatic", "manual"})

    # -- Graceful degradation -------------------------------------------------

    def test_automatic_fallback_degrades_gracefully_on_db_error(self) -> None:
        with patch(
            "app.services.retrieve_service.search_chat_messages_fts",
            side_effect=sqlite3.OperationalError("no such table: chat_messages"),
        ):
            response = self._retrieve([], user_input="драконов")

        self.assertEqual(response.raw_fallback, [])
        # The rest of the response still comes back normally.
        self.assertEqual(response.items, [])

    def test_manual_fallback_degrades_gracefully_on_db_error(self) -> None:
        with patch(
            "app.services.retrieve_service.get_chat_message_by_id",
            side_effect=sqlite3.OperationalError("no such table: chat_messages"),
        ):
            response = self._retrieve(
                [],
                user_input="и",
                manual_source_message_ids=["some-id"],
            )

        self.assertEqual(response.raw_fallback, [])


if __name__ == "__main__":
    unittest.main()
