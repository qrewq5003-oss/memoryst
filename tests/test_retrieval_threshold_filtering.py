import unittest
from unittest.mock import patch

from app.schemas import MemoryItem, MemoryMetadata, RetrieveMemoryRequest
from app.services.retrieve_service import retrieve_memories


def _memory(
    memory_id: str,
    *,
    keywords: list[str],
    entities: list[str],
    importance: float,
    updated_at: str,
    pinned: bool = False,
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
        pinned=pinned,
        archived=False,
        metadata=MemoryMetadata(keywords=keywords, entities=entities),
    )


class RetrievalThresholdFilteringTests(unittest.TestCase):
    def _retrieve(self, memories: list[MemoryItem], *, user_input: str, limit: int = 5):
        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=memories),
            patch("app.services.retrieve_service.increment_access_count") as increment_mock,
            patch("app.services.retrieve_service.format_memory_block", return_value="formatted") as format_mock,
        ):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input=user_input,
                    limit=limit,
                )
            )
        return response, increment_mock, format_mock

    def test_below_threshold_memory_is_not_returned(self) -> None:
        strong = _memory(
            "strong",
            keywords=["alice", "puzzle", "paris", "project", "museum"],
            entities=["Alice"],
            importance=0.5,
            updated_at="2026-03-20T00:00:00+00:00",
        )
        weak = _memory(
            "weak",
            keywords=["alice"],
            entities=[],
            importance=1.0,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        response, _, _ = self._retrieve([weak, strong], user_input="Alice puzzle Paris project museum", limit=5)
        self.assertEqual([item.id for item in response.items], ["strong"])

    def test_returns_fewer_items_than_limit_when_only_one_passes_threshold(self) -> None:
        strong = _memory(
            "strong",
            keywords=["alice", "puzzle", "paris", "project", "museum"],
            entities=["Alice"],
            importance=0.5,
            updated_at="2026-03-20T00:00:00+00:00",
        )
        weak = _memory(
            "weak",
            keywords=["alice"],
            entities=[],
            importance=1.0,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        response, _, _ = self._retrieve([strong, weak], user_input="Alice puzzle Paris project museum", limit=10)
        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].id, "strong")

    def test_returns_no_items_when_nobody_passes_threshold(self) -> None:
        weak_a = _memory(
            "weak-a",
            keywords=["alice"],
            entities=[],
            importance=1.0,
            updated_at="2026-03-26T00:00:00+00:00",
        )
        weak_b = _memory(
            "weak-b",
            keywords=["puzzle"],
            entities=[],
            importance=1.0,
            updated_at="2026-03-25T00:00:00+00:00",
        )

        response, increment_mock, format_mock = self._retrieve(
            [weak_a, weak_b],
            user_input="Alice puzzle Paris project museum",
            limit=10,
        )
        self.assertEqual(response.items, [])
        increment_mock.assert_not_called()
        format_mock.assert_called_once_with([])

    def test_access_count_updates_only_for_threshold_passed_results(self) -> None:
        strong = _memory(
            "strong",
            keywords=["alice", "puzzle", "paris", "project", "museum"],
            entities=["Alice"],
            importance=0.5,
            updated_at="2026-03-20T00:00:00+00:00",
        )
        weak = _memory(
            "weak",
            keywords=["alice"],
            entities=[],
            importance=1.0,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        _, increment_mock, _ = self._retrieve([strong, weak], user_input="Alice puzzle Paris project museum", limit=5)
        self.assertEqual([call.args[0] for call in increment_mock.call_args_list], ["strong"])

    def test_format_memory_block_receives_only_threshold_passed_items(self) -> None:
        strong = _memory(
            "strong",
            keywords=["alice", "puzzle", "paris", "project", "museum"],
            entities=["Alice"],
            importance=0.5,
            updated_at="2026-03-20T00:00:00+00:00",
        )
        weak = _memory(
            "weak",
            keywords=["alice"],
            entities=[],
            importance=1.0,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        response, _, format_mock = self._retrieve([weak, strong], user_input="Alice puzzle Paris project museum", limit=5)
        self.assertEqual([item.id for item in response.items], ["strong"])
        self.assertEqual([item.id for item in format_mock.call_args.args[0]], ["strong"])

    def test_strong_relevant_results_still_return_normally(self) -> None:
        strongest = _memory(
            "strongest",
            keywords=["alice", "puzzle", "paris"],
            entities=["Alice"],
            importance=0.6,
            updated_at="2026-03-10T00:00:00+00:00",
        )
        second = _memory(
            "second",
            keywords=["alice", "puzzle"],
            entities=["Alice"],
            importance=0.5,
            updated_at="2026-03-20T00:00:00+00:00",
        )

        response, _, _ = self._retrieve([second, strongest], user_input="Alice puzzle", limit=5)
        self.assertEqual([item.id for item in response.items], ["strongest", "second"])


if __name__ == "__main__":
    unittest.main()
