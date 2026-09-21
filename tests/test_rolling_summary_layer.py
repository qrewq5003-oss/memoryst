import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, list_memories
from app.schemas import CreateMemoryRequest, MemoryMetadata
from app.services.formatter import format_memory_block
from app.services.summary_service import (
    ROLLING_SUMMARY_KIND,
    build_rolling_summary_text,
    generate_rolling_summary,
)


def _create_memory(
    *,
    chat_id: str,
    character_id: str,
    content: str,
    layer: str,
    memory_type: str = "event",
    source: str = "manual",
    importance: float = 0.7,
) -> None:
    create_memory(
        CreateMemoryRequest(
            chat_id=chat_id,
            character_id=character_id,
            type=memory_type,
            content=content,
            source=source,
            layer=layer,
            importance=importance,
            metadata=MemoryMetadata(),
        )
    )


class RollingSummaryLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def test_summary_generation_is_deterministic_for_recent_episodic_memories(self) -> None:
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса поссорилась с Маркусом из-за бюджета фильма.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Позже Алиса и Маркус договорились продолжить поездку в Рим.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса хочет закончить монтаж до конца недели.",
            layer="episodic",
        )

        first = generate_rolling_summary("chat-1", "char-1", window_size=5)
        second = generate_rolling_summary("chat-1", "char-1", window_size=5)

        self.assertEqual(first.summary_text, second.summary_text)
        self.assertEqual(second.action, "skipped_not_enough_new_inputs")
        self.assertEqual(second.new_input_count, 0)
        self.assertEqual(second.refresh_threshold_used, 3)
        self.assertIn("Краткая сводка последних эпизодов", first.summary_text)
        self.assertIn("Изменения в отношениях", first.summary_text)
        self.assertIn("Текущие цели и состояние", first.summary_text)

    def test_summary_is_scoped_per_chat_and_character(self) -> None:
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса встретила Маркуса в Риме.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Они решили вернуться к разговору о фильме.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса переживает из-за бюджета.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-2",
            character_id="char-2",
            content="Боб обсуждал отпуск у моря.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-2",
            character_id="char-2",
            content="Боб купил новый чемодан.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-2",
            character_id="char-2",
            content="Боб хочет выехать утром.",
            layer="episodic",
        )

        result = generate_rolling_summary("chat-1", "char-1", window_size=5)
        summaries = [
            memory for memory in list_memories(chat_id="chat-1", character_id="char-1", limit=20).items
            if memory.metadata.is_summary
        ]

        self.assertEqual(result.action, "created")
        self.assertEqual(result.refresh_threshold_used, 3)
        self.assertEqual(len(summaries), 1)
        self.assertTrue(all(memory.chat_id == "chat-1" for memory in summaries))
        self.assertNotIn("Боб", summaries[0].content)

    def test_summary_representation_is_distinct_and_reuses_only_episodic_inputs(self) -> None:
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса любит джаз и тихие бары.",
            layer="stable",
            memory_type="profile",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса спорила с Маркусом о съёмочном плане.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Они позже согласовали новую дату поездки.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса хочет удержать команду вместе.",
            layer="episodic",
        )

        result = generate_rolling_summary("chat-1", "char-1", window_size=6)
        summary = next(
            memory for memory in list_memories(chat_id="chat-1", character_id="char-1", limit=20).items
            if memory.metadata.is_summary
        )

        self.assertEqual(result.summary_memory_id, summary.id)
        self.assertEqual(result.refresh_threshold_used, 3)
        self.assertTrue(summary.metadata.is_summary)
        self.assertEqual(summary.metadata.summary_kind, ROLLING_SUMMARY_KIND)
        self.assertEqual(summary.layer, "stable")
        self.assertEqual(summary.type, "summary")
        self.assertEqual(summary.metadata.summarized_memory_count, 3)
        self.assertEqual(len(summary.metadata.summary_source_memory_ids), 3)

    def test_repeated_summary_generation_keeps_distinct_summary_type(self) -> None:
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса спорила с Маркусом о бюджете.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Позже они решили не отменять поездку.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса хочет удержать проект на плаву.",
            layer="episodic",
        )

        first = generate_rolling_summary("chat-1", "char-1", window_size=5)
        second = generate_rolling_summary("chat-1", "char-1", window_size=5)
        summary = next(
            memory for memory in list_memories(chat_id="chat-1", character_id="char-1", limit=20).items
            if memory.metadata.is_summary
        )

        self.assertEqual(first.action, "created")
        self.assertEqual(second.action, "skipped_not_enough_new_inputs")
        self.assertEqual(summary.type, "summary")
        self.assertEqual(summary.id, first.summary_memory_id)
        self.assertEqual(second.refresh_threshold_used, 3)

    def test_summary_memory_formats_with_summary_label(self) -> None:
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса поссорилась с Маркусом из-за бюджета.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Позже они договорились о новом плане.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса хочет закончить фильм к выходным.",
            layer="episodic",
        )

        generate_rolling_summary("chat-1", "char-1", window_size=5)
        summary = next(
            memory for memory in list_memories(chat_id="chat-1", character_id="char-1", limit=20).items
            if memory.metadata.is_summary
        )

        block = format_memory_block([summary])
        self.assertIn("[SUMMARY]", block)

    def test_summary_skips_when_not_enough_episodic_memories_exist(self) -> None:
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса встретила Маркуса в Риме.",
            layer="episodic",
        )
        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Алиса любит джаз.",
            layer="stable",
            memory_type="profile",
        )

        result = generate_rolling_summary("chat-1", "char-1", window_size=5)

        self.assertEqual(result.action, "skipped_not_enough_inputs")
        self.assertEqual(result.summarized_count, 1)
        self.assertEqual(result.new_input_count, 1)
        self.assertEqual(result.refresh_threshold_used, 3)

    def test_summary_updates_only_after_enough_new_episodic_memories(self) -> None:
        for content in (
            "Алиса поссорилась с Маркусом из-за бюджета.",
            "Позже они договорились не отменять поездку.",
            "Алиса хочет удержать проект на плаву.",
        ):
            _create_memory(
                chat_id="chat-1",
                character_id="char-1",
                content=content,
                layer="episodic",
            )

        first = generate_rolling_summary("chat-1", "char-1", window_size=8)

        for content in (
            "Алиса переживает из-за новых сроков.",
            "Маркус пообещал помочь с графиком.",
        ):
            _create_memory(
                chat_id="chat-1",
                character_id="char-1",
                content=content,
                layer="episodic",
            )

        skipped = generate_rolling_summary("chat-1", "char-1", window_size=8)

        _create_memory(
            chat_id="chat-1",
            character_id="char-1",
            content="Они решили провести встречу утром.",
            layer="episodic",
        )

        updated = generate_rolling_summary("chat-1", "char-1", window_size=8)

        self.assertEqual(first.action, "created")
        self.assertEqual(skipped.action, "skipped_not_enough_new_inputs")
        self.assertEqual(skipped.new_input_count, 2)
        self.assertEqual(updated.action, "updated")
        self.assertEqual(updated.new_input_count, 3)
        self.assertEqual(updated.summary_memory_id, first.summary_memory_id)
        self.assertEqual(updated.refresh_threshold_used, 3)

    def test_stable_memories_do_not_count_toward_refresh_threshold(self) -> None:
        for content in (
            "Алиса поссорилась с Маркусом из-за бюджета.",
            "Позже они договорились не отменять поездку.",
            "Алиса хочет удержать проект на плаву.",
        ):
            _create_memory(
                chat_id="chat-1",
                character_id="char-1",
                content=content,
                layer="episodic",
            )

        first = generate_rolling_summary("chat-1", "char-1", window_size=8)

        for content in (
            "Алиса любит джаз.",
            "Маркус доверяет Алисе.",
        ):
            _create_memory(
                chat_id="chat-1",
                character_id="char-1",
                content=content,
                layer="stable",
                memory_type="profile",
            )

        skipped = generate_rolling_summary("chat-1", "char-1", window_size=8)

        self.assertEqual(first.action, "created")
        self.assertEqual(skipped.action, "skipped_not_enough_new_inputs")
        self.assertEqual(skipped.new_input_count, 0)
        self.assertEqual(skipped.refresh_threshold_used, 3)

    def test_custom_refresh_threshold_changes_update_decision_predictably(self) -> None:
        for content in (
            "Алиса поссорилась с Маркусом из-за бюджета.",
            "Позже они договорились не отменять поездку.",
            "Алиса хочет удержать проект на плаву.",
        ):
            _create_memory(
                chat_id="chat-1",
                character_id="char-1",
                content=content,
                layer="episodic",
            )

        first = generate_rolling_summary("chat-1", "char-1", window_size=8, min_new_memories_for_refresh=4)

        for content in (
            "Алиса переживает из-за новых сроков.",
            "Маркус пообещал помочь с графиком.",
            "Они решили провести встречу утром.",
        ):
            _create_memory(
                chat_id="chat-1",
                character_id="char-1",
                content=content,
                layer="episodic",
            )

        skipped = generate_rolling_summary("chat-1", "char-1", window_size=8, min_new_memories_for_refresh=4)
        updated = generate_rolling_summary("chat-1", "char-1", window_size=8, min_new_memories_for_refresh=3)

        self.assertEqual(first.action, "created")
        self.assertEqual(skipped.action, "skipped_not_enough_new_inputs")
        self.assertEqual(skipped.new_input_count, 3)
        self.assertEqual(skipped.refresh_threshold_used, 4)
        self.assertEqual(updated.action, "updated")
        self.assertEqual(updated.refresh_threshold_used, 3)

    def test_summary_text_builder_is_stable_for_russian_long_chat_case(self) -> None:
        memories = [
            type("Memory", (), {"content": "Алиса поссорилась с Маркусом из-за бюджета фильма."})(),
            type("Memory", (), {"content": "Позже они договорились не отменять поездку в Рим."})(),
            type("Memory", (), {"content": "Алиса хочет закончить монтаж до конца недели."})(),
        ]
        summary_text = build_rolling_summary_text(memories)  # type: ignore[arg-type]

        self.assertIn("Недавние события", summary_text)
        self.assertIn("Изменения в отношениях", summary_text)
        self.assertIn("Текущие цели и состояние", summary_text)


if __name__ == "__main__":
    unittest.main()


class SummaryLanguageTests(unittest.TestCase):
    """The summary language is decided here, not inferred by the model.

    These chats mix alphabets: the roleplay is Russian, the character names and some
    quoted lines are Latin. "Write in the same language as the input" is not an
    instruction a model can follow on that, and on 2026-09-21 four summaries came back in
    English from windows that were 6:2, 5:3 and 4:4 Russian to English. The rule is now
    stated last and names the language outright - the same fix this project needed once
    before, when a correct-looking prompt flipped extraction to English.
    """

    def test_a_mixed_window_that_is_mostly_russian_is_called_russian(self) -> None:
        from app.services.text_utils import dominant_language

        self.assertEqual(
            dominant_language("Валерия напоминает Wanted о его обещании пойти в спортзал"),
            "Russian",
        )

    def test_a_mostly_latin_window_is_called_english(self) -> None:
        from app.services.text_utils import dominant_language

        self.assertEqual(dominant_language("Valeria cooked arepas in Wanted's shirt"), "English")

    def test_the_prompt_names_the_language_and_says_it_last(self) -> None:
        from app.services.summary_prompts import build_system_prompt

        prompt = build_system_prompt("Russian")
        self.assertNotIn("{language}", prompt)
        self.assertIn("write the entire reply in Russian", prompt)
        # Last, not first. The old prompt put the language rule at the top of the list
        # and a loud character-limit rule between it and the generation.
        self.assertGreater(prompt.index("LANGUAGE:"), prompt.index("under 1200 characters"))

    def test_the_language_reaches_the_model(self) -> None:
        from app.schemas import MemoryItem, MemoryMetadata
        from app.services import summary_service

        memory = MemoryItem(
            id="m1", chat_id="c", character_id="x", type="event",
            content="Валерия солгала маме, что уезжает по работе",
            normalized_content="", source="auto", layer="episodic", importance=0.5,
            created_at="2026-09-21T00:00:00+00:00", updated_at="2026-09-21T00:00:00+00:00",
            last_accessed_at=None, access_count=0, pinned=False, archived=False,
            metadata=MemoryMetadata(),
        )

        captured = {}

        def fake_chat_completion(messages, **kwargs):
            captured["system"] = messages[0]["content"]
            return "## Chronology\nТекст."

        with patch.object(summary_service, "is_llm_enabled", return_value=True), \
             patch.object(summary_service, "chat_completion", side_effect=fake_chat_completion):
            summary_service.build_llm_summary_text([memory])

        self.assertIn("write the entire reply in Russian", captured["system"])
