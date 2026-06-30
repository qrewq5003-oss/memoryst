import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, get_memory_by_id, list_memories
from app.schemas import CreateMemoryRequest, MemoryMetadata
from app.services.conflict_resolver import (
    apply_conflict_resolutions,
    detect_fact_conflicts,
    format_conflict_resolution_notes,
)
from app.services.summary_service import _build_summary_metadata, generate_rolling_summary


def _create_memory(
    *,
    chat_id: str,
    character_id: str,
    content: str,
    layer: str,
    memory_type: str = "event",
    entities: list[str] | None = None,
    keywords: list[str] | None = None,
    source_message_ids: list[str] | None = None,
    source: str = "manual",
    importance: float = 0.7,
):
    return create_memory(
        CreateMemoryRequest(
            chat_id=chat_id,
            character_id=character_id,
            type=memory_type,
            content=content,
            source=source,
            layer=layer,
            importance=importance,
            metadata=MemoryMetadata(
                entities=entities or [],
                keywords=keywords or [],
                source_message_ids=source_message_ids or [],
            ),
        )
    )


class ConsolidationConflictResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    # -- detect_fact_conflicts -------------------------------------------------

    def test_detect_fact_conflicts_keeps_latest_fact_as_current(self) -> None:
        old = _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса не пьёт кофе.",
            layer="episodic",
            memory_type="profile",
            entities=["Алиса"],
        )
        new = _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса теперь предпочитает чай по утрам и работает допоздна.",
            layer="episodic",
            memory_type="profile",
            entities=["Алиса"],
        )

        conflicts = detect_fact_conflicts([old, new])

        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict.entity, "алиса")
        self.assertEqual(conflict.current.id, new.id)
        self.assertEqual([m.id for m in conflict.superseded], [old.id])

    def test_paraphrased_facts_about_same_entity_are_not_a_conflict(self) -> None:
        first = _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса любит джаз и тихие бары.",
            layer="stable",
            memory_type="profile",
            entities=["Алиса"],
        )
        second = _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса любит джаз и тихие бары вечером.",
            layer="stable",
            memory_type="profile",
            entities=["Алиса"],
        )

        conflicts = detect_fact_conflicts([first, second])

        self.assertEqual(conflicts, [])

    def test_format_conflict_resolution_notes_states_old_and_new_value(self) -> None:
        old = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса не пьёт кофе.", layer="episodic",
            memory_type="profile", entities=["Алиса"],
        )
        new = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса теперь предпочитает чай по утрам и работает допоздна.",
            layer="episodic", memory_type="profile", entities=["Алиса"],
        )

        notes = format_conflict_resolution_notes(detect_fact_conflicts([old, new]))

        self.assertIn("Алиса не пьёт кофе", notes)
        self.assertIn("чай по утрам", notes)
        self.assertIn("алиса", notes.lower())

    # -- apply_conflict_resolutions ---------------------------------------------

    def test_apply_conflict_resolutions_marks_superseded_memory_metadata(self) -> None:
        old = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса не пьёт кофе.", layer="episodic",
            memory_type="profile", entities=["Алиса"],
        )
        new = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса теперь предпочитает чай по утрам и работает допоздна.",
            layer="episodic", memory_type="profile", entities=["Алиса"],
        )

        apply_conflict_resolutions(detect_fact_conflicts([old, new]))

        refreshed_old = get_memory_by_id(old.id)
        self.assertEqual(refreshed_old.metadata.review_status, "superseded")
        self.assertEqual(refreshed_old.metadata.related_memory_id, new.id)
        self.assertIn("чай по утрам", refreshed_old.metadata.consolidation_note)
        self.assertEqual(len(refreshed_old.metadata.consolidation_history), 1)
        self.assertEqual(refreshed_old.metadata.consolidation_history[0].action, "superseded_by_newer_fact")
        self.assertEqual(refreshed_old.metadata.consolidation_history[0].related_memory_id, new.id)

        refreshed_new = get_memory_by_id(new.id)
        self.assertIsNone(refreshed_new.metadata.review_status)

    def test_apply_conflict_resolutions_is_idempotent_on_repeat_calls(self) -> None:
        old = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса не пьёт кофе.", layer="episodic",
            memory_type="profile", entities=["Алиса"],
        )
        new = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса теперь предпочитает чай по утрам и работает допоздна.",
            layer="episodic", memory_type="profile", entities=["Алиса"],
        )

        conflicts = detect_fact_conflicts([old, new])
        apply_conflict_resolutions(conflicts)
        apply_conflict_resolutions(conflicts)

        refreshed_old = get_memory_by_id(old.id)
        self.assertEqual(len(refreshed_old.metadata.consolidation_history), 1)

    # -- _build_summary_metadata transitive source_message_ids -------------------

    def test_summary_metadata_aggregates_source_message_ids_from_direct_memories(self) -> None:
        memory_a = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса поссорилась с Маркусом.", layer="episodic",
            source_message_ids=["msg-1"],
        )
        memory_b = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Они помирились позже.", layer="episodic",
            source_message_ids=["msg-2", "msg-3"],
        )

        metadata = _build_summary_metadata([memory_a, memory_b], "Сводка событий.")

        self.assertEqual(metadata.source_message_ids, ["msg-1", "msg-2", "msg-3"])

    def test_summary_metadata_aggregates_source_message_ids_transitively_through_summary_of_summaries(self) -> None:
        chapter_summary = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Сводка первой главы.", layer="stable",
            memory_type="summary",
            source_message_ids=["msg-1", "msg-2"],
        )
        another_chapter_summary = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Сводка второй главы.", layer="stable",
            memory_type="summary",
            source_message_ids=["msg-3"],
        )

        book_metadata = _build_summary_metadata(
            [chapter_summary, another_chapter_summary], "Сводка книги."
        )

        self.assertEqual(book_metadata.source_message_ids, ["msg-1", "msg-2", "msg-3"])

    # -- end-to-end through generate_rolling_summary ------------------------------

    def test_rolling_summary_resolves_conflicting_facts_explicitly(self) -> None:
        old = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса не пьёт кофе.", layer="episodic",
            memory_type="profile", entities=["Алиса"],
        )
        _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Маркус закончил монтаж первой сцены.", layer="episodic",
            entities=["Маркус"],
        )
        new = _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса теперь предпочитает чай по утрам и работает допоздна.",
            layer="episodic", memory_type="profile", entities=["Алиса"],
        )

        result = generate_rolling_summary("chat-1", "char-1", window_size=5)

        self.assertEqual(result.action, "created")
        self.assertIn("Обновлённые факты", result.summary_text)
        self.assertIn("чай по утрам", result.summary_text)
        # The superseded fact's raw text should not be repeated as if it were still true.
        self.assertNotIn("Алиса не пьёт кофе", result.summary_text)

        refreshed_old = get_memory_by_id(old.id)
        self.assertEqual(refreshed_old.metadata.review_status, "superseded")
        self.assertEqual(refreshed_old.metadata.related_memory_id, new.id)

    # -- LLM prompt gets the conflict notes injected ------------------------------

    def test_llm_consolidation_prompt_includes_conflict_resolution_notes(self) -> None:
        _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса не пьёт кофе.", layer="episodic",
            memory_type="profile", entities=["Алиса"],
        )
        _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Маркус закончил монтаж первой сцены.", layer="episodic",
            entities=["Маркус"],
        )
        _create_memory(
            chat_id="chat-1", character_id="char-1",
            content="Алиса теперь предпочитает чай по утрам и работает допоздна.",
            layer="episodic", memory_type="profile", entities=["Алиса"],
        )

        captured_messages = []

        def _fake_chat_completion(messages, **kwargs):
            captured_messages.append(messages)
            return "LLM summary text"

        with patch("app.services.summary_service.is_llm_enabled", return_value=True), patch(
            "app.services.summary_service.chat_completion", side_effect=_fake_chat_completion
        ):
            result = generate_rolling_summary("chat-1", "char-1", window_size=5)

        self.assertEqual(result.summary_text, "LLM summary text")
        self.assertEqual(len(captured_messages), 1)
        user_prompt = captured_messages[0][1]["content"]
        self.assertIn("Resolved fact updates", user_prompt)
        self.assertIn("чай по утрам", user_prompt)


if __name__ == "__main__":
    unittest.main()
