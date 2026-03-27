import unittest
from unittest.mock import patch

from fastapi import Request

from app.routes.ui import ui_retrieve_memories, ui_store_memories
from app.schemas import (
    ListMemoriesResponse,
    MemoryItem,
    MemoryMetadata,
    RetrieveCandidateDebug,
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


class UiMetricsSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.empty_memories = ListMemoriesResponse(items=[], total=0, limit=50, offset=0)

    def test_store_summary_aggregates_counts_and_debug_breakdown(self) -> None:
        with (
            patch("app.routes.ui.list_memories", return_value=self.empty_memories),
            patch("app.routes.ui.store_memories") as store_mock,
        ):
            store_mock.return_value = StoreMemoryResponse(
                stored=1,
                updated=2,
                skipped=3,
                items=[_memory("memory-1", "Alice planned the Rome museum trip.")],
                debug=StoreDebugPayload(
                    candidates=[
                        StoreCandidateDebug(
                            content="Okay",
                            normalized_content="okay",
                            decision="skipped_low_value",
                            reason="low_value_pattern",
                            branch="quality_gate",
                        ),
                        StoreCandidateDebug(
                            content="Alice likes tea",
                            normalized_content="alice likes tea",
                            decision="updated",
                            reason="exact_match_auto_updated",
                            branch="exact",
                            matched_existing_id="memory-1",
                        ),
                        StoreCandidateDebug(
                            content="Alice planned the Rome museum trip.",
                            normalized_content="alice planned the rome museum trip",
                            decision="stored",
                            reason="new_memory_created",
                            branch="new",
                        ),
                    ]
                ),
            )

            response = ui_store_memories(
                _request("/ui/store"),
                chat_id="chat-1",
                character_id="char-1",
                messages="Alice planned the Rome museum trip.",
                debug=True,
            )

        body = response.body.decode()
        self.assertIn("Store Summary Breakdown", body)
        self.assertIn("Stored", body)
        self.assertIn("Updated", body)
        self.assertIn("Skipped", body)
        self.assertIn("skipped_low_value=1", body)
        self.assertIn("exact=1", body)
        self.assertIn("new=1", body)

    def test_retrieve_summary_aggregates_total_selected_threshold_and_diversity(self) -> None:
        with (
            patch("app.routes.ui.list_memories", return_value=self.empty_memories),
            patch("app.routes.ui.retrieve_memories") as retrieve_mock,
        ):
            retrieve_mock.return_value = RetrieveMemoryResponse(
                items=[_memory("memory-1", "Alice planned the Rome museum trip.")],
                memory_block="[Relevant Memory]\n- [EPISODIC] Alice planned the Rome museum trip.",
                total_candidates=4,
                debug=RetrieveDebugPayload(
                    query_keywords=["alice", "trip"],
                    query_entities=["Alice"],
                    recent_keywords=["rome"],
                    recent_entities=[],
                    input_keywords=["alice", "trip", "rome"],
                    input_entities=["Alice"],
                    summary_candidates=0,
                    stable_candidates=0,
                    episodic_candidates=3,
                    selected_summary=0,
                    selected_stable=0,
                    selected_episodic=1,
                    candidates=[
                        RetrieveCandidateDebug(
                            memory_id="memory-1",
                            layer="episodic",
                            score=0.9,
                            keyword_overlap=0.8,
                            entity_overlap=1.0,
                            recency=0.1,
                            passed_threshold=True,
                            selected=True,
                            selected_from_layer="episodic",
                            rank=1,
                            reason="selected_top",
                        ),
                        RetrieveCandidateDebug(
                            memory_id="memory-2",
                            layer="episodic",
                            score=0.6,
                            keyword_overlap=0.7,
                            entity_overlap=1.0,
                            recency=0.1,
                            passed_threshold=True,
                            filtered_by_diversity=True,
                            reason="filtered_near_duplicate",
                        ),
                        RetrieveCandidateDebug(
                            memory_id="memory-3",
                            layer="episodic",
                            score=0.05,
                            keyword_overlap=0.0,
                            entity_overlap=0.0,
                            recency=0.0,
                            passed_threshold=False,
                            reason="below_threshold",
                        ),
                    ],
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

        body = response.body.decode()
        self.assertIn("Retrieve Summary Breakdown", body)
        self.assertIn("Total Candidates", body)
        self.assertIn("Selected", body)
        self.assertIn("Top Score", body)
        self.assertIn("Avg Selected Score", body)
        self.assertIn("Below Threshold:</strong> 1", body)
        self.assertIn("Filtered by Diversity:</strong> 1", body)
        self.assertIn("Selected Top:</strong> 1", body)
        self.assertIn("below_threshold=1", body)
        self.assertIn("filtered_near_duplicate=1", body)
        self.assertIn("selected_top=1", body)

    def test_summary_sections_do_not_break_render_when_debug_is_disabled(self) -> None:
        with (
            patch("app.routes.ui.list_memories", return_value=self.empty_memories),
            patch("app.routes.ui.retrieve_memories") as retrieve_mock,
        ):
            retrieve_mock.return_value = RetrieveMemoryResponse(
                items=[_memory("memory-1", "Alice planned the Rome museum trip.")],
                memory_block="[Relevant Memory]\n- [EPISODIC] Alice planned the Rome museum trip.",
                total_candidates=1,
                debug=None,
            )

            response = ui_retrieve_memories(
                _request("/ui/retrieve"),
                chat_id="chat-1",
                character_id="char-1",
                user_input="Alice trip",
                recent_messages="",
                limit=5,
                include_archived=False,
                debug=False,
            )

        body = response.body.decode()
        self.assertIn("Retrieve Result", body)
        self.assertNotIn("Retrieve Summary Breakdown", body)
        self.assertNotIn("Retrieve Diagnostics", body)


if __name__ == "__main__":
    unittest.main()
