import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, list_retrieval_candidates
from app.schemas import CreateMemoryRequest, MemoryMetadata, RetrieveMemoryRequest
from app.services.retrieve_service import retrieve_memories


def _memory_request(
    content: str,
    *,
    importance: float,
    updated_word: str,
    archived: bool = False,
) -> CreateMemoryRequest:
    return CreateMemoryRequest(
        chat_id="chat-1",
        character_id="char-1",
        type="event",
        content=content,
        source="manual",
        layer="episodic",
        importance=importance,
        archived=archived,
        metadata=MemoryMetadata(
            entities=["Alice"],
            keywords=["alice", updated_word],
        ),
    )


class RetrievalCandidateSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def test_retrieve_uses_retrieval_specific_repository_path(self) -> None:
        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=[]) as candidates_mock,
            patch("app.services.retrieve_service.list_memories", side_effect=AssertionError("should not use list_memories"), create=True),
        ):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Alice",
                    limit=5,
                )
            )

        self.assertEqual(response.items, [])
        candidates_mock.assert_called_once_with(
            chat_id="chat-1",
            character_id="char-1",
            include_archived=False,
        )

    def test_retrieve_uses_shared_text_features_for_user_input_and_recent_messages(self) -> None:
        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=[]),
            patch("app.services.text_features.extract_keywords", side_effect=[["alice"], ["trip"]]) as keywords_mock,
            patch("app.services.text_features.extract_entities", side_effect=[["Alice"], ["Paris"]]) as entities_mock,
        ):
            retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Tell me about Alice",
                    recent_messages=[{"role": "user", "text": "We planned a trip to Paris"}],
                    limit=5,
                )
            )

        self.assertEqual(
            [call.args[0] for call in keywords_mock.call_args_list],
            ["Tell me about Alice", "We planned a trip to Paris"],
        )
        self.assertEqual(
            [call.args[0] for call in entities_mock.call_args_list],
            ["Tell me about Alice", "We planned a trip to Paris"],
        )

    def test_old_relevant_memory_beats_fresher_weaker_memory(self) -> None:
        old_relevant = create_memory(
            _memory_request(
                "Alice solved the puzzle in Paris.",
                importance=0.9,
                updated_word="puzzle",
            )
        )
        fresh_weaker = create_memory(
            _memory_request(
                "Alice walked outside.",
                importance=0.3,
                updated_word="walked",
            )
        )

        with patch("app.services.retrieve_service.increment_access_count"):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Tell me about Alice puzzle",
                    limit=2,
                )
            )

        self.assertEqual(response.items[0].id, old_relevant.id)
        self.assertEqual(response.items[1].id, fresh_weaker.id)

    def test_archived_memories_are_excluded_from_default_candidate_selection(self) -> None:
        visible = create_memory(
            _memory_request(
                "Alice solved the puzzle in Paris.",
                importance=0.8,
                updated_word="puzzle",
            )
        )
        create_memory(
            _memory_request(
                "Alice archived puzzle detail.",
                importance=1.0,
                updated_word="puzzle",
                archived=True,
            )
        )

        candidates = list_retrieval_candidates("chat-1", "char-1")
        self.assertEqual([item.id for item in candidates], [visible.id])

        with patch("app.services.retrieve_service.increment_access_count"):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Alice puzzle",
                    limit=5,
                )
            )

        self.assertEqual([item.id for item in response.items], [visible.id])

    def test_access_count_is_incremented_only_for_returned_top_items(self) -> None:
        first = create_memory(
            _memory_request(
                "Alice solved the puzzle in Paris.",
                importance=0.9,
                updated_word="puzzle",
            )
        )
        second = create_memory(
            _memory_request(
                "Alice planned a puzzle night.",
                importance=0.8,
                updated_word="planned",
            )
        )
        create_memory(
            _memory_request(
                "Alice discussed weather.",
                importance=0.2,
                updated_word="weather",
            )
        )

        with patch("app.services.retrieve_service.increment_access_count") as increment_mock:
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Alice puzzle",
                    limit=2,
                )
            )

        self.assertEqual(len(response.items), 2)
        self.assertEqual(
            [call.args[0] for call in increment_mock.call_args_list],
            [first.id, second.id],
        )


if __name__ == "__main__":
    unittest.main()
