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


class RetrievalScoringTuningTests(unittest.TestCase):
    def _retrieve(self, memories: list[MemoryItem], *, user_input: str, limit: int = 5) -> list[str]:
        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=memories),
            patch("app.services.retrieve_service.increment_access_count"),
            patch("app.services.retrieve_service.format_memory_block", return_value="formatted"),
        ):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input=user_input,
                    limit=limit,
                )
            )
        return [item.id for item in response.items]

    def test_strong_keyword_overlap_beats_fresher_weaker_match(self) -> None:
        strong = _memory(
            "strong",
            keywords=["alice", "puzzle", "paris"],
            entities=["Alice"],
            importance=0.4,
            updated_at="2026-03-01T00:00:00+00:00",
        )
        fresh_weak = _memory(
            "fresh-weak",
            keywords=["alice"],
            entities=[],
            importance=1.0,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        ranked_ids = self._retrieve([fresh_weak, strong], user_input="Alice puzzle")
        self.assertEqual(ranked_ids[:2], ["strong", "fresh-weak"])

    def test_keyword_and_entity_match_beats_single_weak_signal(self) -> None:
        both = _memory(
            "both",
            keywords=["project", "rome"],
            entities=["Elena"],
            importance=0.5,
            updated_at="2026-03-15T00:00:00+00:00",
        )
        weak = _memory(
            "weak",
            keywords=["project"],
            entities=[],
            importance=0.9,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        ranked_ids = self._retrieve([weak, both], user_input="Elena project")
        self.assertEqual(ranked_ids[:2], ["both", "weak"])

    def test_pinned_bonus_is_moderate_and_does_not_beat_irrelevant_memory(self) -> None:
        relevant = _memory(
            "relevant",
            keywords=["alice", "puzzle"],
            entities=["Alice"],
            importance=0.4,
            updated_at="2026-03-10T00:00:00+00:00",
        )
        pinned_irrelevant = _memory(
            "pinned-irrelevant",
            keywords=["weather"],
            entities=["Bob"],
            importance=1.0,
            updated_at="2026-03-26T00:00:00+00:00",
            pinned=True,
        )

        ranked_ids = self._retrieve([pinned_irrelevant, relevant], user_input="Alice puzzle")
        self.assertEqual(ranked_ids, ["relevant"])

    def test_recency_breaks_ties_between_similarly_relevant_candidates(self) -> None:
        older = _memory(
            "older",
            keywords=["alice", "trip"],
            entities=[],
            importance=0.5,
            updated_at="2026-03-01T00:00:00+00:00",
        )
        fresher = _memory(
            "fresher",
            keywords=["alice", "trip"],
            entities=[],
            importance=0.5,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        ranked_ids = self._retrieve([older, fresher], user_input="Alice trip")
        self.assertEqual(ranked_ids[:2], ["fresher", "older"])

    def test_scoring_is_deterministic_for_same_inputs(self) -> None:
        first = _memory(
            "first",
            keywords=["alice", "trip"],
            entities=["Alice"],
            importance=0.6,
            updated_at="2026-03-20T00:00:00+00:00",
        )
        second = _memory(
            "second",
            keywords=["alice"],
            entities=[],
            importance=0.7,
            updated_at="2026-03-25T00:00:00+00:00",
        )
        memories = [second, first]

        first_run = self._retrieve(memories, user_input="Alice trip")
        second_run = self._retrieve(memories, user_input="Alice trip")
        self.assertEqual(first_run, second_run)


if __name__ == "__main__":
    unittest.main()
