import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, list_memories
from app.schemas import CreateMemoryRequest, MemoryMetadata, MessageInput, StoreMemoryRequest
from app.services.store_service import passes_memory_quality_gate, store_memories


def _candidate(
    content: str,
    *,
    keywords: list[str] | None = None,
    entities: list[str] | None = None,
) -> CreateMemoryRequest:
    return CreateMemoryRequest(
        chat_id="chat-1",
        character_id="char-1",
        type="event",
        content=content,
        source="auto",
        layer="episodic",
        importance=0.6,
        metadata=MemoryMetadata(
            keywords=keywords or [],
            entities=entities or [],
        ),
    )


class StoreQualityGuardrailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _store_candidates(self, candidates: list[CreateMemoryRequest]):
        request = StoreMemoryRequest(
            chat_id="chat-1",
            character_id="char-1",
            messages=[MessageInput(role="user", text="irrelevant")],
        )
        with patch("app.services.store_service.extract_memories", return_value=candidates):
            return store_memories(request)

    def test_low_value_candidate_is_skipped(self) -> None:
        response = self._store_candidates([
            _candidate("Okay, yes.", keywords=[], entities=[]),
        ])

        self.assertEqual(response.stored, 0)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 1)
        self.assertEqual(list_memories().total, 0)

    def test_informative_candidate_is_stored_normally(self) -> None:
        response = self._store_candidates([
            _candidate(
                "Alice planned the Rome museum trip for Friday.",
                keywords=["alice", "rome", "museum"],
                entities=["Alice"],
            ),
        ])

        self.assertEqual(response.stored, 1)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 0)
        self.assertEqual(list_memories().total, 1)

    def test_candidate_with_meaningful_content_and_features_passes(self) -> None:
        response = self._store_candidates([
            _candidate(
                "Elena discussed the budget for the short film.",
                keywords=["budget", "film"],
                entities=["Elena"],
            ),
        ])

        self.assertEqual(response.stored, 1)
        self.assertEqual(response.skipped, 0)
        self.assertEqual(list_memories().total, 1)

    def test_short_garbage_candidate_is_not_saved(self) -> None:
        response = self._store_candidates([
            _candidate("yes", keywords=["yes"], entities=[]),
        ])

        self.assertEqual(response.stored, 0)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 1)
        self.assertEqual(list_memories().total, 0)

    def test_manual_candidate_explicitly_passes_quality_gate(self) -> None:
        manual_candidate = _candidate(
            "ok",
            keywords=[],
            entities=[],
        ).model_copy(update={"source": "manual"})

        self.assertTrue(passes_memory_quality_gate(manual_candidate))
    def test_store_response_counters_remain_correct_for_mixed_candidates(self) -> None:
        response = self._store_candidates([
            _candidate(
                "Alice planned the Rome museum trip for Friday.",
                keywords=["alice", "rome", "museum"],
                entities=["Alice"],
            ),
            _candidate("Okay, yes.", keywords=[], entities=[]),
        ])

        self.assertEqual(response.stored, 1)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 1)
        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].content, "Alice planned the Rome museum trip for Friday.")

    def test_manual_create_path_is_not_affected(self) -> None:
        created = create_memory(
            CreateMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                type="event",
                content="ok",
                source="manual",
                layer="episodic",
                importance=0.5,
                metadata=MemoryMetadata(),
            )
        )

        self.assertEqual(created.source, "manual")
        self.assertEqual(created.content, "ok")
        self.assertEqual(list_memories().total, 1)


if __name__ == "__main__":
    unittest.main()
