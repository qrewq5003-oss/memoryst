"""The gate that keeps the vector layer from making retrieval worse.

Before this existed, `query_similar` returned the nearest ten memories and every one of
them got the full `semantic_boost`. Measured over data/memory.db on 2026-09-20 with
gemini-embedding-2-preview: inside a single chat the median cosine between two arbitrary
memories is 0.591 and 45% of pairs clear 0.60, because every memory in a chat shares a
cast, a setting and a narrator. So "the nearest ten" was very nearly "ten at random", and
a flat +0.15 is on its own enough to push a candidate with no lexical match whatsoever
past min_retrieval_score.

These tests pin the two things that stop that: a floor measured against the scanned
distribution rather than an absolute number, and a boost graded by how far above it a
match actually sits.
"""

import tempfile
import unittest
from pathlib import Path

from app.config import config
from app.db import get_connection, init_schema
from app.services.retrieval_config import (
    MIN_RETRIEVAL_SCORE,
    SEMANTIC_BOOST,
    SEMANTIC_FULL_STRENGTH_MARGIN,
    SEMANTIC_MIN_SIMILARITY,
    SEMANTIC_RELATIVE_MARGIN,
)
from app.services.retrieve_service import (
    _score_semantic_matches,
    _semantic_ceiling,
    _semantic_floor,
)


def _hit(memory_id: str, similarity: float, median: float | None = 0.591) -> dict:
    hit = {"id": memory_id, "similarity": similarity}
    if median is not None:
        hit["scanned_median_similarity"] = median
    return hit


class SemanticFloorTests(unittest.TestCase):
    def test_the_floor_follows_the_scanned_distribution(self) -> None:
        # Two chats with different baselines must not share one threshold.
        self.assertAlmostEqual(_semantic_floor(_hit("a", 0.9, median=0.591)), 0.591 + SEMANTIC_RELATIVE_MARGIN)
        self.assertAlmostEqual(_semantic_floor(_hit("a", 0.9, median=0.700)), 0.700 + SEMANTIC_RELATIVE_MARGIN)

    def test_a_backend_with_no_distribution_falls_back_to_the_absolute_floor(self) -> None:
        # The Chroma path returns only its own top-N, so there is nothing to measure
        # against and the backstop is all there is.
        self.assertEqual(_semantic_floor(_hit("a", 0.9, median=None)), SEMANTIC_MIN_SIMILARITY)

    def test_a_low_baseline_never_drops_below_the_absolute_floor(self) -> None:
        floor = _semantic_floor(_hit("a", 0.9, median=0.05))
        self.assertGreaterEqual(floor, SEMANTIC_MIN_SIMILARITY)


class SemanticStrengthTests(unittest.TestCase):
    def test_a_match_at_the_median_is_dropped_entirely(self) -> None:
        # "Nearest of a uniformly similar set" is not a semantic match.
        self.assertEqual(_score_semantic_matches([_hit("median", 0.591)]), {})

    def test_the_old_flat_boost_can_no_longer_lift_a_lexical_miss(self) -> None:
        # The regression this whole gate exists to prevent: a candidate scoring 0 on
        # keywords and entities must not clear the retrieval threshold on a mid-pack
        # semantic hit alone.
        strengths = _score_semantic_matches([_hit("noise", 0.64)])
        boost = SEMANTIC_BOOST * strengths.get("noise", 0.0)
        self.assertLess(boost, MIN_RETRIEVAL_SCORE)

    def test_an_unmistakable_match_gets_the_full_boost(self) -> None:
        ceiling = 0.591 + SEMANTIC_FULL_STRENGTH_MARGIN
        strengths = _score_semantic_matches([_hit("same_subject", ceiling + 0.05)])
        self.assertAlmostEqual(strengths["same_subject"], 1.0)

    def test_the_ceiling_follows_the_median_like_the_floor_does(self) -> None:
        # An absolute ceiling was the first cut and it was wrong for the same reason an
        # absolute floor is: a query is prose and a memory is a terse fact, so
        # query-to-memory similarity tops out well below memory-to-memory similarity.
        # Calibrated on the latter, every real match scored near zero.
        self.assertAlmostEqual(_semantic_ceiling(_hit("a", 0.9, median=0.571)), 0.571 + SEMANTIC_FULL_STRENGTH_MARGIN)
        self.assertAlmostEqual(_semantic_ceiling(_hit("a", 0.9, median=0.700)), 0.700 + SEMANTIC_FULL_STRENGTH_MARGIN)

    def test_strength_is_graded_between_the_floor_and_the_ceiling(self) -> None:
        floor = 0.591 + SEMANTIC_RELATIVE_MARGIN
        ceiling = 0.591 + SEMANTIC_FULL_STRENGTH_MARGIN
        midpoint = (floor + ceiling) / 2
        strength = _score_semantic_matches([_hit("mid", midpoint)])["mid"]
        self.assertGreater(strength, 0.0)
        self.assertLess(strength, 1.0)
        self.assertAlmostEqual(strength, 0.5, places=2)

    def test_strength_rises_with_similarity(self) -> None:
        strengths = _score_semantic_matches([
            _hit("near", 0.72),
            _hit("nearer", 0.75),
            _hit("nearest", 0.80),
        ])
        self.assertLess(strengths["near"], strengths["nearer"])
        self.assertLessEqual(strengths["nearer"], strengths["nearest"])

    def test_similarity_is_derived_from_distance_when_absent(self) -> None:
        # Rows from a backend that reports only distance must still be usable.
        strengths = _score_semantic_matches([
            {"id": "d", "distance": 0.14, "scanned_median_similarity": 0.591}
        ])
        self.assertIn("d", strengths)

    def test_a_row_with_neither_similarity_nor_distance_is_skipped(self) -> None:
        self.assertEqual(_score_semantic_matches([{"id": "x"}]), {})


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore)
        init_schema()

    def _restore(self) -> None:
        config.DATABASE_PATH = self.original_db_path


class SqliteVectorStoreTests(_IsolatedDatabase):
    def setUp(self) -> None:
        super().setUp()
        from app.services import vector_store

        self.vs = vector_store

    def test_a_vector_survives_the_round_trip(self) -> None:
        self.vs._sqlite_add("a", [1.0, 0.0, 0.0], {"chat_id": "c", "character_id": "x"})
        hits = self.vs._sqlite_query([1.0, 0.0, 0.0], 5, {"chat_id": "c", "character_id": "x"})
        self.assertEqual(hits[0]["id"], "a")
        self.assertAlmostEqual(hits[0]["similarity"], 1.0, places=5)

    def test_a_query_never_leaves_its_chat(self) -> None:
        self.vs._sqlite_add("mine", [1.0, 0.0], {"chat_id": "c", "character_id": "x"})
        self.vs._sqlite_add("theirs", [1.0, 0.0], {"chat_id": "other", "character_id": "x"})
        hits = self.vs._sqlite_query([1.0, 0.0], 5, {"chat_id": "c", "character_id": "x"})
        self.assertEqual([hit["id"] for hit in hits], ["mine"])

    def test_results_carry_the_scanned_median(self) -> None:
        for index, vector in enumerate([[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]):
            self.vs._sqlite_add(f"m{index}", vector, {"chat_id": "c", "character_id": "x"})
        hits = self.vs._sqlite_query([1.0, 0.0], 5, {"chat_id": "c", "character_id": "x"})
        self.assertIn("scanned_median_similarity", hits[0])
        self.assertEqual(hits[0]["scanned_count"], 3)

    def test_a_vector_of_another_dimension_is_skipped_not_compared(self) -> None:
        # A row from an older backfill or a different model shares no vector space with
        # this query; comparing a prefix would return a confident, meaningless number.
        self.vs._sqlite_add("right", [1.0, 0.0, 0.0], {"chat_id": "c", "character_id": "x"})
        self.vs._sqlite_add("stale", [1.0, 0.0], {"chat_id": "c", "character_id": "x"})
        hits = self.vs._sqlite_query([1.0, 0.0, 0.0], 5, {"chat_id": "c", "character_id": "x"})
        self.assertEqual([hit["id"] for hit in hits], ["right"])

    def test_re_adding_a_memory_replaces_its_vector(self) -> None:
        self.vs._sqlite_add("a", [1.0, 0.0], {"chat_id": "c", "character_id": "x"})
        self.vs._sqlite_add("a", [0.0, 1.0], {"chat_id": "c", "character_id": "x"})
        self.assertEqual(self.vs._sqlite_count(), 1)
        hits = self.vs._sqlite_query([0.0, 1.0], 5, {"chat_id": "c", "character_id": "x"})
        self.assertAlmostEqual(hits[0]["similarity"], 1.0, places=5)


class EmbeddingCleanupTests(_IsolatedDatabase):
    def test_deleting_a_memory_takes_its_embedding_with_it(self) -> None:
        # Every delete path except whole-chat cleanup used to leave the vector behind -
        # including the extension undoing a rejected swipe, which deletes one per turn.
        from app.repositories.memory_repo import create_memory, delete_memory
        from app.schemas import CreateMemoryRequest, MemoryMetadata
        from app.services import vector_store

        created = create_memory(
            CreateMemoryRequest(
                chat_id="c",
                character_id="x",
                type="event",
                content="Алина уехала в Казань на неделю",
                source="auto",
                layer="episodic",
                importance=0.5,
                metadata=MemoryMetadata(),
            )
        )
        vector_store._sqlite_add(created.id, [1.0, 0.0], {"chat_id": "c", "character_id": "x"})
        self.assertEqual(vector_store._sqlite_count(), 1)

        self.assertTrue(delete_memory(created.id))
        self.assertEqual(vector_store._sqlite_count(), 0)

    def test_deleting_a_memory_without_an_embedding_still_reports_success(self) -> None:
        from app.repositories.memory_repo import create_memory, delete_memory
        from app.schemas import CreateMemoryRequest, MemoryMetadata

        created = create_memory(
            CreateMemoryRequest(
                chat_id="c",
                character_id="x",
                type="event",
                content="Марк починил мотоцикл брата",
                source="auto",
                layer="episodic",
                importance=0.5,
                metadata=MemoryMetadata(),
            )
        )
        self.assertTrue(delete_memory(created.id))


class SchemaTests(_IsolatedDatabase):
    def test_the_embeddings_table_and_its_scope_index_exist(self) -> None:
        conn = get_connection()
        try:
            names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
            }
        finally:
            conn.close()
        self.assertIn("memory_embeddings", names)
        # Without it every query deserializes every vector in the database.
        self.assertIn("idx_memory_embeddings_scope", names)


if __name__ == "__main__":
    unittest.main()
