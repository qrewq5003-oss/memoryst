import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, get_memory_by_id
from app.schemas import CreateMemoryRequest, MemoryMetadata
from app.services.summary_service import (
    CONSOLIDATION_CONTENT_MAX_LENGTH,
    _truncate_to_content_limit,
    generate_tiered_consolidation,
)


def _create_memory(chat_id: str, character_id: str, content: str, entities: list[str] | None = None):
    return create_memory(
        CreateMemoryRequest(
            chat_id=chat_id,
            character_id=character_id,
            type="event",
            content=content,
            source="manual",
            layer="episodic",
            importance=0.7,
            metadata=MemoryMetadata(entities=entities or []),
        )
    )


class ConsolidationContentLengthSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _seed_memories(self) -> list:
        return [
            _create_memory("chat-1", "char-1", "Алиса и Маркус поссорились из-за плана.", ["Алиса", "Маркус"]),
            _create_memory("chat-1", "char-1", "Маркус извинился перед Алисой.", ["Маркус", "Алиса"]),
            _create_memory("chat-1", "char-1", "Они решили продолжить работу вместе.", ["Алиса", "Маркус"]),
        ]

    # -- _truncate_to_content_limit (unit-level) ----------------------------

    def test_truncate_leaves_short_text_untouched(self) -> None:
        text = "Короткая сводка событий."
        self.assertEqual(_truncate_to_content_limit(text), text)

    def test_truncate_cuts_at_sentence_boundary_not_mid_word(self) -> None:
        long_text = "Алиса и Маркус долго разговаривали о будущем. " * 400
        self.assertGreater(len(long_text), CONSOLIDATION_CONTENT_MAX_LENGTH)

        result = _truncate_to_content_limit(long_text)

        self.assertLessEqual(len(result), CONSOLIDATION_CONTENT_MAX_LENGTH)
        self.assertTrue(result.endswith("."))
        self.assertTrue(long_text.startswith(result))
        # What follows the cut must be the start of a fresh sentence, not a
        # continuation of a word that was sliced in half.
        remainder = long_text[len(result):]
        self.assertTrue(remainder == "" or remainder.startswith(" "))

    def test_truncate_falls_back_to_word_boundary_without_sentence_punctuation(self) -> None:
        long_text = "словослово " * 1000  # no sentence-ending punctuation anywhere
        self.assertGreater(len(long_text), CONSOLIDATION_CONTENT_MAX_LENGTH)

        result = _truncate_to_content_limit(long_text)

        self.assertLessEqual(len(result), CONSOLIDATION_CONTENT_MAX_LENGTH)
        self.assertTrue(long_text.startswith(result))
        remainder = long_text[len(result):]
        self.assertTrue(remainder == "" or remainder.startswith(" "))

    # -- generate_tiered_consolidation integration --------------------------

    def test_normal_summary_within_limit_is_stored_unmodified(self) -> None:
        memories = self._seed_memories()
        summary = "Алиса и Маркус поссорились, но затем помирились и решили продолжить совместную работу."

        with (
            patch("app.services.summary_service.is_llm_enabled", return_value=True),
            patch("app.services.summary_service.chat_completion", return_value=summary),
        ):
            result = generate_tiered_consolidation(
                "chat-1", "char-1", tier="arc", source_ids=[m.id for m in memories],
            )

        self.assertEqual(result.action, "created")
        self.assertEqual(result.summary_text, summary)
        stored = get_memory_by_id(result.summary_memory_id)
        self.assertEqual(stored.content, summary)

    def test_oversized_llm_response_is_truncated_instead_of_crashing(self) -> None:
        memories = self._seed_memories()
        # Far larger than CreateMemoryRequest.content's max_length=5000 - simulates
        # an LLM that ignored the prompt's length instructions.
        oversized_summary = "Алиса и Маркус продолжали разговаривать о будущем. " * 200
        self.assertGreater(len(oversized_summary), 5000)

        with (
            patch("app.services.summary_service.is_llm_enabled", return_value=True),
            patch("app.services.summary_service.chat_completion", return_value=oversized_summary),
        ):
            result = generate_tiered_consolidation(
                "chat-1", "char-1", tier="arc", source_ids=[m.id for m in memories],
            )

        self.assertEqual(result.action, "created")
        self.assertLessEqual(len(result.summary_text), CONSOLIDATION_CONTENT_MAX_LENGTH)
        stored = get_memory_by_id(result.summary_memory_id)
        self.assertLessEqual(len(stored.content), CONSOLIDATION_CONTENT_MAX_LENGTH)
        self.assertTrue(stored.content.endswith("."))
        self.assertTrue(oversized_summary.startswith(stored.content))


if __name__ == "__main__":
    unittest.main()
