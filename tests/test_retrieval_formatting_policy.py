import unittest
from unittest.mock import patch

from app.schemas import MemoryItem, MemoryMetadata, RetrieveMemoryRequest
from app.services.formatter import (
    MAX_FORMATTED_CONTENT_LENGTH,
    MAX_FORMATTED_MEMORIES,
    format_memory_block,
)
from app.services.retrieve_service import retrieve_memories


def _memory(
    memory_id: str,
    content: str,
    *,
    layer: str = "episodic",
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
        layer=layer,
        importance=0.8,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        last_accessed_at=None,
        access_count=0,
        pinned=pinned,
        archived=False,
        metadata=MemoryMetadata(
            keywords=["alice", "trip", memory_id],
            entities=["Alice"],
        ),
    )


class RetrievalFormattingPolicyTests(unittest.TestCase):
    def test_formatted_block_limits_number_of_output_entries(self) -> None:
        items = [
            _memory(f"m{i}", f"Memory {i} about Alice and the trip.")
            for i in range(1, 7)
        ]

        block = format_memory_block(items)
        self.assertEqual(block.count("\n- "), MAX_FORMATTED_MEMORIES)

    def test_long_content_is_truncated(self) -> None:
        long_content = "Alice " + ("remembered the museum trip details " * 10)
        block = format_memory_block([_memory("long", long_content)])
        line = block.splitlines()[1]
        self.assertIn("...", line)
        self.assertLessEqual(len(line), MAX_FORMATTED_CONTENT_LENGTH + 25)

    def test_pinned_stable_and_episodic_are_visually_distinct(self) -> None:
        block = format_memory_block(
            [
                _memory("pinned", "Pinned memory.", layer="stable", pinned=True),
                _memory("stable", "Stable memory.", layer="stable"),
                _memory("episodic", "Episodic memory.", layer="episodic"),
            ]
        )

        self.assertIn("[PINNED] [STABLE] Pinned memory.", block)
        self.assertIn("[STABLE] Stable memory.", block)
        self.assertIn("[EPISODIC] Episodic memory.", block)

    def test_near_duplicate_content_is_not_repeated(self) -> None:
        block = format_memory_block(
            [
                _memory("a", "Alice planned the museum trip."),
                _memory("b", "  Alice planned the museum trip!  "),
                _memory("c", "Alice planned the museum trip?"),
            ]
        )

        self.assertEqual(block.count("\n- "), 1)

    def test_formatting_is_deterministic(self) -> None:
        items = [
            _memory("a", "Alice planned the museum trip.", layer="stable"),
            _memory("b", "Alice met Elena in Rome.", pinned=True),
        ]

        first = format_memory_block(items)
        second = format_memory_block(items)
        self.assertEqual(first, second)

    def test_retrieve_items_remain_intact_while_memory_block_is_compact(self) -> None:
        memories = [
            _memory("m1", "Alice planned the Rome museum trip."),
            _memory("m2", "Alice met Elena after the museum visit."),
            _memory("m3", "Alice booked train tickets for Rome."),
            _memory("m4", "Alice discussed the trip budget with Marcus."),
            _memory("m5", "Alice saved the hotel address for the trip."),
        ]

        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=memories),
            patch("app.services.retrieve_service.increment_access_count"),
        ):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Alice trip Rome",
                    limit=5,
                )
            )

        self.assertEqual(len(response.items), 5)
        self.assertEqual(response.memory_block.count("\n- "), MAX_FORMATTED_MEMORIES)


if __name__ == "__main__":
    unittest.main()
