import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Request

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, get_memory_by_id
from app.routes.ui import ui_memories_page
from app.schemas import CreateMemoryRequest, MemoryMetadata, RetrieveMemoryRequest
from app.services import text_features
from app.services.conflict_resolver import SUPERSEDED_REVIEW_STATUS
from app.services.retrieve_service import retrieve_memories
from app.services.summary_service import CONSOLIDATED_REVIEW_STATUS, generate_tiered_consolidation


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


def _create_memory(chat_id: str, character_id: str, content: str):
    entities = text_features.extract_entities(content)
    keywords = text_features.extract_keywords(content)
    return create_memory(
        CreateMemoryRequest(
            chat_id=chat_id,
            character_id=character_id,
            type="event",
            content=content,
            source="manual",
            layer="episodic",
            importance=0.7,
            metadata=MemoryMetadata(entities=entities, keywords=keywords),
        )
    )


class ConsolidationReviewStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _seed_and_consolidate(self):
        # Distinct entities/topics per memory so conflict_resolver doesn't mark
        # any of them "superseded" - that would confound the updated_at check
        # in test_bulk_consolidation_marks_sources_consolidated_without_touching_updated_at,
        # since supersession legitimately bumps updated_at (unrelated to this feature).
        memories = [
            _create_memory("chat-1", "char-1", "Иван купил новый ноутбук для работы."),
            _create_memory("chat-1", "char-1", "Мария посадила розы в саду."),
            _create_memory("chat-1", "char-1", "Они запланировали поездку в горы."),
        ]
        # Deliberately wordy/generic and distinct from the sources' phrasing: the
        # deterministic fallback summary otherwise echoes source content near-
        # verbatim, which trips retrieval's near-duplicate diversity filter and
        # would confound the "source still reachable by retrieval" test below.
        with (
            patch("app.services.summary_service.is_llm_enabled", return_value=True),
            patch(
                "app.services.summary_service.chat_completion",
                return_value="Обзор недавних личных дел без конкретики.",
            ),
        ):
            result = generate_tiered_consolidation(
                "chat-1", "char-1", tier="arc", source_ids=[m.id for m in memories],
            )
        self.assertEqual(result.action, "created")
        return memories, result

    def test_bulk_consolidation_marks_sources_consolidated_without_touching_updated_at(self) -> None:
        memories, _ = self._seed_and_consolidate()

        for original in memories:
            refreshed = get_memory_by_id(original.id)
            self.assertEqual(refreshed.metadata.review_status, CONSOLIDATED_REVIEW_STATUS)
            self.assertFalse(refreshed.archived)
            self.assertEqual(refreshed.updated_at, original.updated_at)

    def test_consolidated_sources_hidden_from_default_ui_list_but_shown_when_requested(self) -> None:
        memories, _ = self._seed_and_consolidate()
        marker = memories[0].content

        default_response = ui_memories_page(
            _request(), selected_chat_id="chat-1", selected_character_id="char-1", limit=50,
        )
        default_body = default_response.body.decode()
        self.assertNotIn(marker, default_body)

        shown_response = ui_memories_page(
            _request(),
            selected_chat_id="chat-1",
            selected_character_id="char-1",
            show_consolidated="true",
            limit=50,
        )
        shown_body = shown_response.body.decode()
        self.assertIn(marker, shown_body)

    def test_consolidated_memory_still_reachable_by_exact_content_retrieval(self) -> None:
        memories, _ = self._seed_and_consolidate()
        target = memories[0]
        refreshed_target = get_memory_by_id(target.id)
        self.assertEqual(refreshed_target.metadata.review_status, CONSOLIDATED_REVIEW_STATUS)

        response = retrieve_memories(
            RetrieveMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                user_input=target.content,
                limit=5,
            )
        )

        self.assertIn(target.id, [item.id for item in response.items])


class ConsolidationSupersededInteractionTests(unittest.TestCase):
    """A source memory that conflict_resolver already marked "superseded" in the
    same consolidation batch must keep that more specific status - not get
    silently relabelled "consolidated" - while still disappearing from the
    default /ui list like any other folded-away record."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def test_superseded_source_keeps_its_status_and_stays_hidden_from_default_ui(self) -> None:
        # These three share the "алиса"/"маркус" entities, so conflict_resolver
        # detects the first as superseded by the second before consolidation runs.
        memories = [
            _create_memory("chat-1", "char-1", "Алиса и Маркус поссорились из-за плана."),
            _create_memory("chat-1", "char-1", "Маркус извинился перед Алисой."),
            _create_memory("chat-1", "char-1", "Они решили продолжить работу вместе."),
        ]
        superseded_source = memories[0]

        with patch("app.services.summary_service.is_llm_enabled", return_value=False):
            result = generate_tiered_consolidation(
                "chat-1", "char-1", tier="arc", source_ids=[m.id for m in memories],
            )
        self.assertEqual(result.action, "created")

        refreshed = get_memory_by_id(superseded_source.id)
        self.assertEqual(refreshed.metadata.review_status, SUPERSEDED_REVIEW_STATUS)
        self.assertEqual(len(refreshed.metadata.consolidation_history), 1)

        default_response = ui_memories_page(
            _request(), selected_chat_id="chat-1", selected_character_id="char-1", limit=50,
        )
        self.assertNotIn(superseded_source.content, default_response.body.decode())


if __name__ == "__main__":
    unittest.main()
