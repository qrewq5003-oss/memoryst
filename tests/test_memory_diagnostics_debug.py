import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory
from app.schemas import (
    CreateMemoryRequest,
    MemoryItem,
    MemoryMetadata,
    MessageInput,
    RetrieveMemoryRequest,
    StoreMemoryRequest,
)
from app.services.retrieve_service import retrieve_memories
from app.services.store_service import store_memories


def _store_candidate(
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


def _memory(
    memory_id: str,
    content: str,
    *,
    keywords: list[str],
    entities: list[str],
    importance: float = 0.8,
    pinned: bool = False,
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id="chat-1",
        character_id="char-1",
        type="event",
        content=content,
        normalized_content=memory_id,
        source="manual",
        layer="episodic",
        importance=importance,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        last_accessed_at=None,
        access_count=0,
        pinned=pinned,
        archived=False,
        metadata=MemoryMetadata(keywords=keywords, entities=entities),
    )


class MemoryDiagnosticsDebugTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def test_store_debug_is_none_by_default(self) -> None:
        request = StoreMemoryRequest(
            chat_id="chat-1",
            character_id="char-1",
            messages=[MessageInput(role="user", text="irrelevant")],
        )
        with patch(
            "app.services.store_service.extract_memories",
            return_value=[
                _store_candidate(
                    "Alice planned the Rome museum trip for Friday.",
                    keywords=["alice", "rome", "museum"],
                    entities=["Alice"],
                )
            ],
        ):
            response = store_memories(request)

        self.assertIsNone(response.debug)
        self.assertEqual(response.stored, 1)

    def test_store_debug_contains_candidate_decisions(self) -> None:
        existing = create_memory(
            _store_candidate(
                "Alice likes tea.",
                keywords=["alice", "tea"],
                entities=["Alice"],
            )
        )

        request = StoreMemoryRequest(
            chat_id="chat-1",
            character_id="char-1",
            messages=[MessageInput(role="user", text="irrelevant")],
            debug=True,
        )
        with patch(
            "app.services.store_service.extract_memories",
            return_value=[
                _store_candidate("Okay"),
                _store_candidate(" Alice likes tea! ", keywords=["alice", "tea"], entities=["Alice"]),
                _store_candidate(
                    "Alice planned the Rome museum trip for Friday.",
                    keywords=["alice", "rome", "museum"],
                    entities=["Alice"],
                ),
            ],
        ):
            response = store_memories(request)

        self.assertIsNotNone(response.debug)
        assert response.debug is not None
        self.assertEqual(response.stored, 1)
        self.assertEqual(response.updated, 1)
        self.assertEqual(response.skipped, 1)
        self.assertEqual(
            [item.decision for item in response.debug.candidates],
            ["skipped_low_value", "updated", "stored"],
        )
        self.assertEqual(response.debug.candidates[0].reason, "low_value_pattern")
        self.assertEqual(response.debug.candidates[1].matched_existing_id, existing.id)
        self.assertEqual(response.debug.candidates[1].branch, "exact")
        self.assertEqual(response.debug.candidates[2].branch, "new")

    def test_retrieve_debug_is_none_by_default(self) -> None:
        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=[]),
            patch("app.services.retrieve_service.format_memory_block", return_value=""),
        ):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Alice trip",
                    limit=5,
                )
            )

        self.assertIsNone(response.debug)
        self.assertEqual(response.items, [])

    def test_retrieve_debug_contains_features_and_candidate_decisions(self) -> None:
        selected = _memory(
            "selected",
            "Alice planned the Rome museum trip for Friday.",
            keywords=["alice", "rome", "museum", "trip"],
            entities=["Alice"],
        )
        duplicate = _memory(
            "duplicate",
            "Alice planned a Rome museum trip for Friday!",
            keywords=["alice", "rome", "museum", "trip"],
            entities=["Alice"],
        )
        below_threshold = _memory(
            "below-threshold",
            "Bob talked about the weather.",
            keywords=["weather"],
            entities=["Bob"],
        )

        with (
            patch(
                "app.services.retrieve_service.list_retrieval_candidates",
                return_value=[selected, duplicate, below_threshold],
            ),
            patch("app.services.retrieve_service.increment_access_count"),
            patch("app.services.retrieve_service.format_memory_block", return_value="formatted"),
        ):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Alice Rome museum trip",
                    recent_messages=[MessageInput(role="user", text="Friday plan reminder")],
                    limit=2,
                    debug=True,
                )
            )

        self.assertIsNotNone(response.debug)
        assert response.debug is not None
        self.assertIn("alice", response.debug.input_keywords)
        self.assertIn("friday", response.debug.recent_keywords)
        decisions = {item.memory_id: item for item in response.debug.candidates}
        self.assertEqual(decisions["selected"].reason, "selected_top")
        self.assertTrue(decisions["selected"].selected)
        self.assertEqual(decisions["selected"].rank, 1)
        self.assertEqual(decisions["duplicate"].reason, "filtered_near_duplicate")
        self.assertTrue(decisions["duplicate"].filtered_by_diversity)
        self.assertEqual(decisions["below-threshold"].reason, "below_threshold")
        self.assertFalse(decisions["below-threshold"].passed_threshold)


if __name__ == "__main__":
    unittest.main()
