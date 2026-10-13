"""`get_collection_count` reports what the SQLite store really holds.

It is the last function of `vector_store.py` the 2026-10-03 coverage audit (finding 6)
left uncovered. Its one caller is the stats line of `scripts/embed_memories.py`
("N memories to embed, M already in the store"), so a wrong number would not break
retrieval - it would mislead whoever is deciding whether a backfill is needed.

The test goes through the real write and delete paths rather than seeding the table by
hand, so the count is checked against what `add_memory` and `delete_memory` actually do.
Only the embedding provider is faked.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.services import vector_store


class CollectionCountTests(unittest.TestCase):
    def setUp(self) -> None:
        original = config.DATABASE_PATH
        self.addCleanup(setattr, config, "DATABASE_PATH", original)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config.DATABASE_PATH = str(Path(tmp.name) / "test.db")
        init_schema()

        for target, value in (
            ("app.services.vector_store.HAS_CHROMADB", False),
            ("app.services.vector_store.embed_text", lambda text, **_: [0.1, 0.2, 0.3]),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _enabled(self, value: bool):
        return patch("app.services.vector_store.is_vector_store_enabled", return_value=value)

    def test_counts_rows_written_and_forgets_deleted_ones(self) -> None:
        with self._enabled(True):
            self.assertEqual(vector_store.get_collection_count(), 0)
            for memory_id in ("m1", "m2", "m3"):
                vector_store.add_memory(memory_id, f"fact {memory_id}", {"chat_id": "chat-1"})
            self.assertEqual(vector_store.get_collection_count(), 3)

            vector_store.delete_memory("m2")
            self.assertEqual(vector_store.get_collection_count(), 2)

    def test_re_embedding_a_memory_does_not_count_it_twice(self) -> None:
        with self._enabled(True):
            vector_store.add_memory("m1", "first text", {"chat_id": "chat-1"})
            vector_store.add_memory("m1", "rewritten text", {"chat_id": "chat-1"})
            self.assertEqual(vector_store.get_collection_count(), 1)

    def test_reports_zero_when_the_layer_is_off_even_with_rows_on_disk(self) -> None:
        with self._enabled(True):
            vector_store.add_memory("m1", "fact", {"chat_id": "chat-1"})
        with self._enabled(False):
            self.assertEqual(vector_store.get_collection_count(), 0)


if __name__ == "__main__":
    unittest.main()
