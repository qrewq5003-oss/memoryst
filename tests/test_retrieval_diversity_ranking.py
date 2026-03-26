import unittest
from unittest.mock import patch

from app.schemas import MemoryItem, MemoryMetadata, RetrieveMemoryRequest
from app.services.retrieve_service import retrieve_memories


def _memory(
    memory_id: str,
    content: str,
    *,
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
        metadata=MemoryMetadata(
            keywords=["alice", "rome", "museum", memory_id],
            entities=["Alice"],
        ),
    )


class RetrievalDiversityRankingTests(unittest.TestCase):
    def _retrieve(self, memories: list[MemoryItem], *, user_input: str, limit: int = 3):
        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=memories),
            patch("app.services.retrieve_service.increment_access_count") as increment_mock,
        ):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input=user_input,
                    limit=limit,
                )
            )
        return response, increment_mock

    def test_near_duplicates_do_not_take_multiple_top_slots(self) -> None:
        first = _memory("first", "Alice planned the Rome museum trip for Friday.")
        duplicate = _memory("duplicate", "Alice planned a Rome museum trip for Friday!")
        different = _memory("different", "Alice met Elena after the museum trip in Rome.")

        response, _ = self._retrieve(
            [first, duplicate, different],
            user_input="Alice Rome museum trip",
            limit=2,
        )

        self.assertEqual([item.id for item in response.items], ["first", "different"])

    def test_genuinely_different_memories_are_preserved(self) -> None:
        trip = _memory("trip", "Alice planned the Rome museum trip for Friday.")
        meeting = _memory("meeting", "Alice met Elena in Rome after the museum closed.")

        response, _ = self._retrieve(
            [trip, meeting],
            user_input="Alice Rome museum",
            limit=2,
        )

        self.assertEqual([item.id for item in response.items], ["trip", "meeting"])

    def test_ranking_diversity_reduces_response_redundancy_not_only_memory_block(self) -> None:
        first = _memory("first", "Alice planned the Rome museum trip for Friday.")
        duplicate = _memory("duplicate", "Alice planned a Rome museum trip for Friday!")
        different = _memory("different", "Alice met Elena after the museum trip in Rome.")

        response, _ = self._retrieve(
            [first, duplicate, different],
            user_input="Alice Rome museum trip",
            limit=3,
        )

        self.assertEqual([item.id for item in response.items], ["first", "different"])
        self.assertEqual(response.memory_block.count("\n- "), 2)

    def test_selection_is_deterministic(self) -> None:
        first = _memory("first", "Alice planned the Rome museum trip for Friday.")
        duplicate = _memory("duplicate", "Alice planned a Rome museum trip for Friday!")
        different = _memory("different", "Alice met Elena after the museum trip in Rome.")
        memories = [first, duplicate, different]

        first_run, _ = self._retrieve(memories, user_input="Alice Rome museum trip", limit=3)
        second_run, _ = self._retrieve(memories, user_input="Alice Rome museum trip", limit=3)

        self.assertEqual(
            [item.id for item in first_run.items],
            [item.id for item in second_run.items],
        )

    def test_access_count_updates_only_for_diversified_selected_items(self) -> None:
        first = _memory("first", "Alice planned the Rome museum trip for Friday.")
        duplicate = _memory("duplicate", "Alice planned a Rome museum trip for Friday!")
        different = _memory("different", "Alice met Elena after the museum trip in Rome.")

        _, increment_mock = self._retrieve(
            [first, duplicate, different],
            user_input="Alice Rome museum trip",
            limit=2,
        )

        self.assertEqual(
            [call.args[0] for call in increment_mock.call_args_list],
            ["first", "different"],
        )


if __name__ == "__main__":
    unittest.main()
