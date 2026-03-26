import tempfile
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.modules.setdefault("pymorphy3", SimpleNamespace(MorphAnalyzer=lambda: None))

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import (
    create_memory,
    get_memory_by_id,
    list_memories,
)
from app.schemas import (
    CreateMemoryRequest,
    MemoryMetadata,
    MessageInput,
    StoreMemoryRequest,
)
from app.services.store_service import store_memories


def _candidate(
    content: str,
    *,
    entities: list[str] | None = None,
    keywords: list[str] | None = None,
) -> CreateMemoryRequest:
    return CreateMemoryRequest(
        chat_id="chat-1",
        character_id="char-1",
        type="event",
        content=content,
        source="auto",
        layer="episodic",
        importance=0.5,
        metadata=MemoryMetadata(
            entities=entities or ["Alice"],
            keywords=keywords or ["birthday", "party"],
        ),
    )


class StoreAutoUpdateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _store_from_candidate(self, candidate: CreateMemoryRequest):
        request = StoreMemoryRequest(
            chat_id=candidate.chat_id,
            character_id=candidate.character_id,
            messages=[MessageInput(role="user", text="irrelevant")],
        )
        with patch("app.services.store_service.extract_memories", return_value=[candidate]):
            return store_memories(request)

    def test_exact_duplicate_of_auto_memory_updates(self) -> None:
        existing = create_memory(_candidate("Alice likes tea."))

        response = self._store_from_candidate(
            _candidate(
                "  Alice likes tea!  ",
                entities=["Alice"],
                keywords=["birthday", "party", "tea"],
            )
        )

        updated = get_memory_by_id(existing.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(response.stored, 0)
        self.assertEqual(response.updated, 1)
        self.assertEqual(response.skipped, 0)
        self.assertEqual(updated.content, "Alice likes tea.")
        self.assertAlmostEqual(updated.importance, 0.55)
        self.assertEqual(updated.metadata.keywords, ["birthday", "party", "tea"])
        self.assertEqual(list_memories().total, 1)

    def test_exact_duplicate_of_manual_memory_is_skipped(self) -> None:
        existing = create_memory(
            _candidate("Alice likes tea.").model_copy(update={"source": "manual"})
        )

        response = self._store_from_candidate(
            _candidate(" Alice likes tea! ")
        )

        current = get_memory_by_id(existing.id)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(response.stored, 0)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 1)
        self.assertEqual(current.content, "Alice likes tea.")
        self.assertEqual(current.source, "manual")
        self.assertEqual(list_memories().total, 1)

    def test_exact_duplicate_of_pinned_auto_memory_is_skipped(self) -> None:
        existing = create_memory(
            _candidate("Alice likes tea.").model_copy(update={"pinned": True})
        )

        response = self._store_from_candidate(
            _candidate(" Alice likes tea! ")
        )

        current = get_memory_by_id(existing.id)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(response.stored, 0)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 1)
        self.assertTrue(current.pinned)
        self.assertEqual(current.content, "Alice likes tea.")
        self.assertEqual(list_memories().total, 1)

    def test_exact_duplicate_of_archived_auto_memory_is_skipped(self) -> None:
        existing = create_memory(
            _candidate("Alice likes tea.").model_copy(update={"archived": True})
        )

        response = self._store_from_candidate(
            _candidate(" Alice likes tea! ")
        )

        current = get_memory_by_id(existing.id)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(response.stored, 0)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 1)
        self.assertTrue(current.archived)
        self.assertEqual(current.content, "Alice likes tea.")
        self.assertEqual(list_memories().total, 1)

    def test_soft_match_against_manual_memory_does_not_auto_update(self) -> None:
        existing = create_memory(
            _candidate(
                "Alice birthday party is next week.",
                entities=["Alice"],
                keywords=["birthday", "party", "week"],
            ).model_copy(update={"source": "manual"})
        )

        response = self._store_from_candidate(
            _candidate(
                "Alice party planning starts tomorrow.",
                entities=["Alice"],
                keywords=["birthday", "party", "planning"],
            )
        )

        current = get_memory_by_id(existing.id)
        self.assertIsNotNone(current)
        assert current is not None
        memories = list_memories()
        self.assertEqual(response.stored, 1)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 0)
        self.assertEqual(current.content, "Alice birthday party is next week.")
        self.assertEqual(current.source, "manual")
        self.assertEqual(memories.total, 2)


if __name__ == "__main__":
    unittest.main()
