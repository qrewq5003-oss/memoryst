import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import Request

from app.routes.ui import _build_consolidation_data, ui_memories_page
from app.schemas import ListMemoriesResponse, MemoryItem, MemoryMetadata


def _request(path: str = "/ui") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        }
    )


def _iso(days_ago: int) -> str:
    now = datetime(2026, 3, 27, tzinfo=timezone.utc)
    return (now - timedelta(days=days_ago)).isoformat()


def _memory(
    memory_id: str,
    content: str,
    *,
    layer: str = "episodic",
    memory_type: str = "event",
    pinned: bool = False,
    updated_days_ago: int = 10,
    access_count: int = 0,
    last_accessed_days_ago: int | None = None,
    entities: list[str] | None = None,
    keywords: list[str] | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id="chat-1",
        character_id="char-1",
        type=memory_type,
        content=content,
        normalized_content=content.lower(),
        source="manual",
        layer=layer,
        importance=0.5,
        created_at=_iso(updated_days_ago + 2),
        updated_at=_iso(updated_days_ago),
        last_accessed_at=_iso(last_accessed_days_ago) if last_accessed_days_ago is not None else None,
        access_count=access_count,
        pinned=pinned,
        archived=False,
        metadata=MemoryMetadata(
            entities=entities or [],
            keywords=keywords or [],
        ),
    )


class UiConsolidationCandidatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 3, 27, tzinfo=timezone.utc)

    def test_obvious_near_duplicates_are_marked(self) -> None:
        first = _memory(
            "memory-1",
            "Alice planned the Rome museum trip for Friday.",
            entities=["Alice", "Rome"],
            keywords=["museum", "trip", "friday"],
        )
        duplicate = _memory(
            "memory-2",
            "Alice planned a Rome museum trip for Friday!",
            entities=["Alice", "Rome"],
            keywords=["museum", "trip", "friday"],
        )

        with patch("app.routes.ui._utc_now", return_value=self.now):
            candidate_map, summary = _build_consolidation_data([first, duplicate])

        self.assertTrue(any(item["type"] == "near_duplicate" for item in candidate_map[first.id]))
        self.assertTrue(any(item["type"] == "near_duplicate" for item in candidate_map[duplicate.id]))
        self.assertEqual(summary["near_duplicate"], 2)

    def test_stale_low_use_episode_is_marked_and_active_pinned_stable_memory_is_not(self) -> None:
        stale_episode = _memory(
            "memory-1",
            "Alice visited a cafe in Rome.",
            layer="episodic",
            memory_type="event",
            updated_days_ago=75,
            access_count=0,
            last_accessed_days_ago=None,
            entities=["Alice", "Rome"],
            keywords=["cafe", "visit"],
        )
        active_stable = _memory(
            "memory-2",
            "Alice loves jazz and visits Rome often.",
            layer="stable",
            memory_type="profile",
            pinned=True,
            updated_days_ago=2,
            access_count=8,
            last_accessed_days_ago=1,
            entities=["Alice", "Rome"],
            keywords=["jazz", "rome"],
        )

        with patch("app.routes.ui._utc_now", return_value=self.now):
            candidate_map, _ = _build_consolidation_data([stale_episode, active_stable])

        self.assertTrue(any(item["type"] == "stale_low_value_episode" for item in candidate_map[stale_episode.id]))
        self.assertEqual(candidate_map[active_stable.id], [])

    def test_ui_can_filter_to_consolidation_candidates_and_render_reasons(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory(
                    "memory-1",
                    "Alice planned the Rome museum trip for Friday.",
                    updated_days_ago=80,
                    entities=["Alice", "Rome"],
                    keywords=["museum", "trip", "friday"],
                ),
                _memory(
                    "memory-2",
                    "Alice planned a Rome museum trip for Friday!",
                    updated_days_ago=78,
                    entities=["Alice", "Rome"],
                    keywords=["museum", "trip", "friday"],
                ),
                _memory(
                    "memory-3",
                    "Alice loves jazz.",
                    layer="stable",
                    memory_type="profile",
                    pinned=True,
                    updated_days_ago=2,
                    access_count=10,
                    last_accessed_days_ago=1,
                    entities=["Alice"],
                    keywords=["jazz"],
                ),
            ],
            total=3,
            limit=50,
            offset=0,
        )

        with (
            patch("app.routes.ui.list_memories", return_value=memories),
            patch("app.routes.ui._utc_now", return_value=self.now),
        ):
            response = ui_memories_page(_request(), consolidation="candidates_only")

        body = response.body.decode()
        self.assertIn("Consolidation Candidate", body)
        self.assertIn("Consolidation Signals", body)
        self.assertIn("near_duplicate", body)
        self.assertIn("stale_low_value_episode", body)
        self.assertIn('<option value="candidates_only" selected>', body)
        self.assertNotIn("Alice loves jazz.", body)


if __name__ == "__main__":
    unittest.main()
