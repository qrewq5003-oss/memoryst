import unittest
from unittest.mock import patch

from fastapi import Request

from app.routes.ui import ui_consolidate_memory, ui_memories_page
from app.schemas import ListMemoriesResponse, MemoryItem, MemoryMetadata


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST" if path.endswith("/consolidate") else "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        }
    )


def _memory(
    memory_id: str,
    content: str,
    *,
    archived: bool = False,
    metadata: MemoryMetadata | None = None,
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
        created_at="2026-03-01T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        last_accessed_at=None,
        access_count=0,
        pinned=False,
        archived=archived,
        metadata=metadata or MemoryMetadata(entities=["Alice"], keywords=["rome", "trip"]),
    )


class UiManualConsolidationWorkflowTests(unittest.TestCase):
    def test_archive_action_updates_memory_and_renders_result(self) -> None:
        original = _memory("memory-1", "Alice planned the Rome museum trip.")
        archived = _memory(
            "memory-1",
            "Alice planned the Rome museum trip.",
            archived=True,
            metadata=MemoryMetadata(
                entities=["Alice"],
                keywords=["rome", "trip"],
                review_status="consolidated_archive",
                consolidation_note="merged into stable fact",
            ),
        )

        with (
            patch("app.routes.ui.get_memory_by_id", side_effect=[original, archived]),
            patch("app.routes.ui.update_memory") as update_mock,
            patch(
                "app.routes.ui.list_memories",
                return_value=ListMemoriesResponse(items=[archived], total=1, limit=50, offset=0),
            ),
        ):
            response = ui_consolidate_memory(
                _request("/ui/memory-1/consolidate"),
                "memory-1",
                action="mark_consolidated_archive",
                related_memory_id="",
                note="merged into stable fact",
            )

        payload = update_mock.call_args.args[1]
        self.assertTrue(payload.archived)
        self.assertEqual(payload.metadata.review_status, "consolidated_archive")
        self.assertEqual(payload.metadata.consolidation_note, "merged into stable fact")
        body = response.body.decode()
        self.assertIn("Candidate archived for consolidation review.", body)
        self.assertIn("Archived", body)
        self.assertIn("merged into stable fact", body)

    def test_link_action_saves_related_memory_id_and_note(self) -> None:
        original = _memory("memory-1", "Alice planned the Rome museum trip.")
        linked = _memory(
            "memory-1",
            "Alice planned the Rome museum trip.",
            metadata=MemoryMetadata(
                entities=["Alice"],
                keywords=["rome", "trip"],
                review_status="linked_to_related",
                consolidation_note="same topic as profile memory",
                related_memory_id="memory-2",
            ),
        )

        with (
            patch("app.routes.ui.get_memory_by_id", side_effect=[original, linked]),
            patch("app.routes.ui.update_memory") as update_mock,
            patch(
                "app.routes.ui.list_memories",
                return_value=ListMemoriesResponse(items=[linked], total=1, limit=50, offset=0),
            ),
        ):
            response = ui_consolidate_memory(
                _request("/ui/memory-1/consolidate"),
                "memory-1",
                action="link_to_related_memory",
                related_memory_id="memory-2",
                note="same topic as profile memory",
            )

        payload = update_mock.call_args.args[1]
        self.assertEqual(payload.metadata.related_memory_id, "memory-2")
        self.assertEqual(payload.metadata.consolidation_note, "same topic as profile memory")
        self.assertEqual(payload.metadata.review_status, "linked_to_related")
        body = response.body.decode()
        self.assertIn("Candidate linked to related memory.", body)
        self.assertIn("memory-2", body)
        self.assertIn("same topic as profile memory", body)

    def test_reviewed_keep_state_suppresses_candidate_badge_and_keeps_render_working(self) -> None:
        kept = _memory(
            "memory-1",
            "Alice planned the Rome museum trip.",
            metadata=MemoryMetadata(
                entities=["Alice"],
                keywords=["rome", "trip"],
                review_status="reviewed_keep",
                consolidation_note="keep for manual context",
            ),
        )

        with patch(
            "app.routes.ui.list_memories",
            return_value=ListMemoriesResponse(items=[kept], total=1, limit=50, offset=0),
        ):
            response = ui_memories_page(_request("/ui"))

        body = response.body.decode()
        self.assertIn("keep for manual context", body)
        self.assertIn("reviewed_keep", body)
        self.assertNotIn("Consolidation Candidate", body)


if __name__ == "__main__":
    unittest.main()
