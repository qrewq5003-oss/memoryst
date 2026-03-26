import unittest
from unittest.mock import patch

from fastapi import Request

from app.routes.ui import ui_retrieve_memories, ui_store_memories
from app.schemas import (
    ListMemoriesResponse,
    MemoryItem,
    MemoryMetadata,
    RetrieveDebugPayload,
    RetrieveMemoryResponse,
    StoreCandidateDebug,
    StoreDebugPayload,
    StoreMemoryResponse,
)


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        }
    )


def _memory(memory_id: str, content: str) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id="chat-1",
        character_id="char-1",
        type="event",
        content=content,
        normalized_content=memory_id,
        source="manual",
        layer="episodic",
        importance=0.5,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        last_accessed_at=None,
        access_count=0,
        pinned=False,
        archived=False,
        metadata=MemoryMetadata(entities=["Alice"], keywords=["alice", "trip"]),
    )


class UiDiagnosticsDebugTests(unittest.TestCase):
    def setUp(self) -> None:
        self.empty_memories = ListMemoriesResponse(items=[], total=0, limit=50, offset=0)

    def test_ui_store_debug_flag_is_forwarded_and_rendered(self) -> None:
        with (
            patch("app.routes.ui.list_memories", return_value=self.empty_memories),
            patch("app.routes.ui.store_memories") as store_mock,
        ):
            store_mock.return_value = StoreMemoryResponse(
                stored=0,
                updated=0,
                skipped=1,
                items=[],
                debug=StoreDebugPayload(
                    candidates=[
                        StoreCandidateDebug(
                            content="Okay",
                            normalized_content="okay",
                            decision="skipped_low_value",
                            reason="low_value_pattern",
                            branch="quality_gate",
                        )
                    ]
                ),
            )

            response = ui_store_memories(
                _request("/ui/store"),
                chat_id="chat-1",
                character_id="char-1",
                messages="Okay\nAlice planned the trip",
                debug=True,
            )

        request_model = store_mock.call_args.args[0]
        self.assertTrue(request_model.debug)
        body = response.body.decode()
        self.assertIn("Store Diagnostics", body)
        self.assertIn("skipped_low_value", body)
        self.assertIn("low_value_pattern", body)

    def test_ui_retrieve_debug_flag_is_forwarded_and_rendered(self) -> None:
        with (
            patch("app.routes.ui.list_memories", return_value=self.empty_memories),
            patch("app.routes.ui.retrieve_memories") as retrieve_mock,
        ):
            retrieve_mock.return_value = RetrieveMemoryResponse(
                items=[_memory("memory-1", "Alice planned the Rome museum trip.")],
                memory_block="[Relevant Memory]\n- [EPISODIC] Alice planned the Rome museum trip.",
                total_candidates=3,
                debug=RetrieveDebugPayload(
                    query_keywords=["alice", "trip"],
                    query_entities=["Alice"],
                    recent_keywords=["rome"],
                    recent_entities=[],
                    input_keywords=["alice", "trip", "rome"],
                    input_entities=["Alice"],
                    candidates=[],
                ),
            )

            response = ui_retrieve_memories(
                _request("/ui/retrieve"),
                chat_id="chat-1",
                character_id="char-1",
                user_input="Alice trip",
                recent_messages="Rome museum",
                limit=5,
                include_archived=False,
                debug=True,
            )

        request_model = retrieve_mock.call_args.args[0]
        self.assertTrue(request_model.debug)
        body = response.body.decode()
        self.assertIn("Retrieve Diagnostics", body)
        self.assertIn("Query Keywords", body)
        self.assertIn("alice, trip", body)

    def test_ui_without_debug_does_not_render_diagnostics_sections(self) -> None:
        with (
            patch("app.routes.ui.list_memories", return_value=self.empty_memories),
            patch("app.routes.ui.store_memories") as store_mock,
        ):
            store_mock.return_value = StoreMemoryResponse(
                stored=1,
                updated=0,
                skipped=0,
                items=[_memory("memory-1", "Alice planned the Rome museum trip.")],
                debug=None,
            )

            response = ui_store_memories(
                _request("/ui/store"),
                chat_id="chat-1",
                character_id="char-1",
                messages="Alice planned the Rome museum trip.",
                debug=False,
            )

        self.assertFalse(store_mock.call_args.args[0].debug)
        body = response.body.decode()
        self.assertNotIn("Store Diagnostics", body)


if __name__ == "__main__":
    unittest.main()
