import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import Request

from app.routes.ui import (
    _get_activity_bucket,
    _get_freshness_bucket,
    ui_memories_page,
)
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
    updated_days_ago: int,
    access_count: int,
    last_accessed_days_ago: int | None,
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id="chat-1",
        character_id="char-1",
        type="event",
        content=content,
        normalized_content=content.lower(),
        source="manual",
        layer="episodic",
        importance=0.5,
        created_at=_iso(updated_days_ago + 3),
        updated_at=_iso(updated_days_ago),
        last_accessed_at=_iso(last_accessed_days_ago) if last_accessed_days_ago is not None else None,
        access_count=access_count,
        pinned=False,
        archived=False,
        metadata=MemoryMetadata(entities=[], keywords=[]),
    )


class UiMemoryFreshnessActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 3, 27, tzinfo=timezone.utc)

    def test_freshness_and_activity_helpers_classify_obvious_cases(self) -> None:
        fresh = _memory("fresh", "Fresh", updated_days_ago=2, access_count=0, last_accessed_days_ago=None)
        warm = _memory("warm", "Warm", updated_days_ago=20, access_count=2, last_accessed_days_ago=25)
        stale = _memory("stale", "Stale", updated_days_ago=60, access_count=7, last_accessed_days_ago=2)

        with patch("app.routes.ui._utc_now", return_value=self.now):
            self.assertEqual(_get_freshness_bucket(fresh), "fresh")
            self.assertEqual(_get_freshness_bucket(warm), "warm")
            self.assertEqual(_get_freshness_bucket(stale), "stale")

            self.assertEqual(_get_activity_bucket(fresh), "never_used")
            self.assertEqual(_get_activity_bucket(warm), "low_use")
            self.assertEqual(_get_activity_bucket(stale), "active")

    def test_ui_filter_and_sort_respect_freshness_and_activity_controls(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory("fresh-active", "Fresh active memory", updated_days_ago=2, access_count=9, last_accessed_days_ago=1),
                _memory("warm-low", "Warm low-use memory", updated_days_ago=21, access_count=2, last_accessed_days_ago=25),
                _memory("stale-unused", "Stale unused memory", updated_days_ago=70, access_count=0, last_accessed_days_ago=None),
            ],
            total=3,
            limit=50,
            offset=0,
        )

        with (
            patch("app.routes.ui.list_memories", return_value=memories),
            patch("app.routes.ui._utc_now", return_value=self.now),
        ):
            response = ui_memories_page(
                _request(),
                freshness="stale",
                activity="never_used",
                sort="stalest_first",
            )

        body = response.body.decode()
        self.assertIn("Stale unused memory", body)
        self.assertNotIn("Fresh active memory", body)
        self.assertNotIn("Warm low-use memory", body)
        self.assertIn('<option value="stale" selected>', body)
        self.assertIn('<option value="never_used" selected>', body)
        self.assertIn('<option value="stalest_first" selected>', body)

    def test_rendered_html_contains_freshness_and_activity_badges(self) -> None:
        memories = ListMemoriesResponse(
            items=[
                _memory("fresh-active", "Fresh active memory", updated_days_ago=2, access_count=9, last_accessed_days_ago=1),
            ],
            total=1,
            limit=50,
            offset=0,
        )

        with (
            patch("app.routes.ui.list_memories", return_value=memories),
            patch("app.routes.ui._utc_now", return_value=self.now),
        ):
            response = ui_memories_page(_request(), sort="access_count_desc")

        body = response.body.decode()
        self.assertIn("badge-freshness-fresh", body)
        self.assertIn("badge-activity-active", body)
        self.assertIn("Recently accessed 1d ago", body)


if __name__ == "__main__":
    unittest.main()
