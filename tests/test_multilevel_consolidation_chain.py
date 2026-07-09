import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import (
    create_memory,
    get_memory_by_id,
    list_retrieval_candidates,
)
from app.schemas import CreateMemoryRequest, MemoryMetadata, RetrieveMemoryRequest
from app.services import text_features
from app.services.retrieve_service import retrieve_memories
from app.services.summary_service import (
    CONSOLIDATED_REVIEW_STATUS,
    generate_tiered_consolidation,
)


def _create_episode(chat_id, character_id, content, source_message_ids=None):
    return create_memory(
        CreateMemoryRequest(
            chat_id=chat_id,
            character_id=character_id,
            type="event",
            content=content,
            source="manual",
            layer="episodic",
            importance=0.7,
            metadata=MemoryMetadata(
                entities=text_features.extract_entities(content),
                keywords=text_features.extract_keywords(content),
                source_message_ids=source_message_ids or [],
            ),
        )
    )


@contextmanager
def _stub_llm(summary_text):
    # Force the LLM path with a deterministic, distinctive text so each tier's
    # summary is uniquely retrievable by its own content (the deterministic
    # fallback would echo source phrasing and trip retrieval's near-duplicate
    # diversity filter, confounding the "still reachable" assertions).
    with (
        patch("app.services.summary_service.is_llm_enabled", return_value=True),
        patch("app.services.summary_service.chat_completion", return_value=summary_text),
    ):
        yield


class MultiLevelConsolidationChainTests(unittest.TestCase):
    """End-to-end Episode -> Arc -> Chapter consolidation.

    Covers the recursive scenario flagged in the backlog: an 'arc' summary that
    is itself already marked `consolidated` becomes a source for a 'chapter'.
    Verifies review_status transitions and retrieval reachability at BOTH levels,
    plus transitive source_message_ids aggregation up the whole chain.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self):
        config.DATABASE_PATH = self.original_db_path

    # Three thematically distinct arcs (distinct entities per episode) so
    # conflict_resolver never marks a source "superseded" - that is a different
    # terminal status and would confound the "consolidated" assertions below.
    ARCS = [
        {
            "episodes": [
                "Иван начал новый проект в компании Орион.",
                "Сергей нанял двух инженеров в команду разработки.",
                "Ольга выпустила первый релиз продукта Орбита.",
            ],
            "summary": "Карьерная дуга: запуск Ориона и найм команды. МаркерАркаОдин.",
        },
        {
            "episodes": [
                "Мария посадила розы вдоль каменной дорожки.",
                "Клара построила стеклянную теплицу за домом.",
                "Нина собрала первый урожай томатов и перца.",
            ],
            "summary": "Садовая дуга: розы, теплица и урожай. МаркерАркаДва.",
        },
        {
            "episodes": [
                "Пётр проложил маршрут через Кавказский хребет.",
                "Артём закупил альпинистское снаряжение и палатку.",
                "Глеб достиг вершины Эльбруса и вернулся домой.",
            ],
            "summary": "Дуга экспедиции: от маршрута до вершины Эльбруса. МаркерАркаТри.",
        },
    ]
    CHAPTER_SUMMARY = "Глава хроники: карьера Ивана, сад Марии и экспедиция Петра. МаркерГлавы."

    def _build_arcs(self, chat_id, character_id):
        arcs = []
        for index, arc in enumerate(self.ARCS):
            episodes = [
                _create_episode(
                    chat_id,
                    character_id,
                    content,
                    source_message_ids=[f"msg-{index}-{pos}"],
                )
                for pos, content in enumerate(arc["episodes"])
            ]
            with _stub_llm(arc["summary"]):
                result = generate_tiered_consolidation(
                    chat_id,
                    character_id,
                    tier="arc",
                    source_ids=[e.id for e in episodes],
                )
            self.assertEqual(result.action, "created")
            arcs.append({"episodes": episodes, "result": result})
        return arcs

    def test_full_episode_arc_chapter_chain(self):
        chat_id, character_id = "chat-1", "char-1"
        arcs = self._build_arcs(chat_id, character_id)

        # --- Level 1: Episode -> Arc -------------------------------------
        for arc in arcs:
            arc_summary = get_memory_by_id(arc["result"].summary_memory_id)
            self.assertEqual(arc_summary.type, "summary")
            self.assertEqual(arc_summary.metadata.summary_kind, "tiered_arc")
            # The arc summary itself is freshly minted - not yet folded away.
            self.assertNotEqual(arc_summary.metadata.review_status, CONSOLIDATED_REVIEW_STATUS)
            # Its episode sources are now marked consolidated...
            for episode in arc["episodes"]:
                self.assertEqual(
                    get_memory_by_id(episode.id).metadata.review_status,
                    CONSOLIDATED_REVIEW_STATUS,
                )

        arc_ids = [arc["result"].summary_memory_id for arc in arcs]

        # --- Level 2: Arc -> Chapter -------------------------------------
        with _stub_llm(self.CHAPTER_SUMMARY):
            chapter_result = generate_tiered_consolidation(
                chat_id,
                character_id,
                tier="chapter",
                source_ids=arc_ids,
            )
        self.assertEqual(chapter_result.action, "created")

        chapter = get_memory_by_id(chapter_result.summary_memory_id)
        self.assertEqual(chapter.metadata.summary_kind, "tiered_chapter")
        self.assertNotEqual(chapter.metadata.review_status, CONSOLIDATED_REVIEW_STATUS)
        self.assertCountEqual(chapter.metadata.summary_source_memory_ids, arc_ids)

        # review_status does not break at the second level: each arc summary,
        # having become a chapter source, is now itself marked consolidated.
        for arc_id in arc_ids:
            self.assertEqual(
                get_memory_by_id(arc_id).metadata.review_status,
                CONSOLIDATED_REVIEW_STATUS,
            )

        # --- Retrieval reachability at every level -----------------------
        # Consolidation must never drop a record from the retrieval candidate
        # set - it only hides it from the default UI list.
        candidate_ids = {c.id for c in list_retrieval_candidates(chat_id, character_id)}
        for arc in arcs:
            for episode in arc["episodes"]:
                self.assertIn(episode.id, candidate_ids)
        for arc_id in arc_ids:
            self.assertIn(arc_id, candidate_ids)
        self.assertIn(chapter.id, candidate_ids)

        # A consolidated arc summary still surfaces from actual retrieval when
        # queried with its own content, exactly like a live memory.
        for arc in arcs:
            arc_id = arc["result"].summary_memory_id
            arc_content = get_memory_by_id(arc_id).content
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id=chat_id,
                    character_id=character_id,
                    user_input=arc_content,
                    limit=5,
                )
            )
            self.assertIn(arc_id, [item.id for item in response.items])

        # The chapter summary is likewise reachable.
        chapter_response = retrieve_memories(
            RetrieveMemoryRequest(
                chat_id=chat_id,
                character_id=character_id,
                user_input=chapter.content,
                limit=5,
            )
        )
        self.assertIn(chapter.id, [item.id for item in chapter_response.items])

    def test_source_message_ids_aggregate_transitively_to_chapter(self):
        chat_id, character_id = "chat-2", "char-2"
        arcs = self._build_arcs(chat_id, character_id)
        arc_ids = [arc["result"].summary_memory_id for arc in arcs]

        expected_message_ids = {
            mid
            for arc in arcs
            for episode in arc["episodes"]
            for mid in episode.metadata.source_message_ids
        }
        # Sanity: arcs already carry their episodes' raw message ids.
        for arc_id in arc_ids:
            arc_message_ids = set(get_memory_by_id(arc_id).metadata.source_message_ids)
            self.assertTrue(arc_message_ids)

        with _stub_llm(self.CHAPTER_SUMMARY):
            chapter_result = generate_tiered_consolidation(
                chat_id,
                character_id,
                tier="chapter",
                source_ids=arc_ids,
            )

        chapter = get_memory_by_id(chapter_result.summary_memory_id)
        # The whole episode -> arc -> chapter chain of raw message ids climbs up
        # without walking the tree explicitly (arcs already carry their episodes').
        self.assertEqual(set(chapter.metadata.source_message_ids), expected_message_ids)


if __name__ == "__main__":
    unittest.main()
