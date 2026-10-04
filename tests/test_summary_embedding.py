"""A summary gets a vector, and keeps it current when it is rewritten.

Summaries were the one kind of memory that never got one. `store_service` embeds each
extracted fact right after writing it; `summary_service` never called `vector_store` at
all. Measured 2026-10-03: of 57 summaries, the 52 written before 2026-09-21 had a vector
from a one-off backfill and all 5 written since had none - invisible to semantic retrieval
with nothing reporting it.

The update half matters as much as the create half. A rolling summary is rewritten in
place, so a vector made from its previous text goes on describing a summary that no longer
exists - the stale-row failure CLAUDE.md warns about, and the row still looks embedded.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory
from app.schemas import CreateMemoryRequest, MemoryMetadata
from app.services.summary_service import generate_rolling_summary


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.original = config.DATABASE_PATH
        self.addCleanup(setattr, config, "DATABASE_PATH", self.original)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config.DATABASE_PATH = str(Path(self.tmp.name) / "test.db")
        init_schema()

    def _seed(self, count: int, prefix: str = "факт") -> None:
        for index in range(count):
            create_memory(
                CreateMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    type="event",
                    content=f"{prefix} номер {index}: Алина работала в кафе",
                    source="manual",
                    layer="episodic",
                    importance=0.7,
                    metadata=MemoryMetadata(),
                )
            )

    def _run(self, **kwargs):
        return generate_rolling_summary(chat_id="chat-1", character_id="char-1", **kwargs)


class SummaryEmbeddingTests(_IsolatedDatabase):
    def test_a_new_summary_is_embedded(self) -> None:
        self._seed(8)
        with patch("app.services.summary_service.vector_store.add_memory") as embed:
            result = self._run(window_size=8, min_new_memories_for_refresh=1)

        self.assertEqual(result.action, "created")
        embed.assert_called_once()
        memory_id, content, metadata = embed.call_args.args
        self.assertEqual(memory_id, result.summary_memory_id)
        self.assertEqual(content, result.summary_text)
        self.assertEqual(metadata, {"chat_id": "chat-1", "character_id": "char-1"})

    def test_rewriting_a_summary_re_embeds_the_new_text(self) -> None:
        # The half that is easy to miss: the row already has a vector, so nothing looks
        # wrong while that vector goes on describing text the summary no longer contains.
        self._seed(8)
        self._run(window_size=8, min_new_memories_for_refresh=1)

        self._seed(8, prefix="новый факт")
        with patch("app.services.summary_service.vector_store.add_memory") as embed:
            result = self._run(window_size=8, min_new_memories_for_refresh=1)

        self.assertEqual(result.action, "updated")
        embed.assert_called_once()
        _memory_id, content, _metadata = embed.call_args.args
        self.assertEqual(content, result.summary_text, "embedded the old text, not the new")

    def test_a_skipped_run_embeds_nothing(self) -> None:
        # No write, no call - otherwise every background scheduler tick would pay for an
        # embedding of text that did not change.
        self._seed(2)
        with patch("app.services.summary_service.vector_store.add_memory") as embed:
            result = self._run(window_size=8, min_new_memories_for_refresh=50)

        self.assertTrue(result.action.startswith("skipped"), result.action)
        embed.assert_not_called()

    def test_a_provider_outage_does_not_cost_the_summary(self) -> None:
        """The failure is injected at the provider, not at add_memory.

        add_memory already swallows its own exceptions - that guard exists because an
        exhausted Google quota once came back out through /memory/store. Patching
        add_memory itself would have tested the mock and proved nothing about the summary
        path, which is what a first draft of this test did.
        """
        self._seed(8)
        with patch("app.services.vector_store.is_vector_store_enabled", return_value=True), \
             patch("app.services.vector_store.embed_text", side_effect=RuntimeError("provider down")):
            result = self._run(window_size=8, min_new_memories_for_refresh=1)

        self.assertEqual(result.action, "created", "a dead embedding provider lost the summary")
        self.assertTrue(result.summary_text)


if __name__ == "__main__":
    unittest.main()
