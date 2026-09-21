"""The embedding paths that carry every vector in production, and had no tests.

Measured 2026-09-21 with coverage: `vector_store.py` sat at 46%, and the uncovered lines
were the provider dispatch, both HTTP clients and the batch splitter - all written the
same day, all on the live path. The batch splitter in particular was written *in response
to* a production failure and then never exercised by anything but that failure.
"""

import unittest
from unittest.mock import patch

from app.config import config
from app.services import vector_store


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _openai_payload(vectors):
    # Deliberately out of order: the client must sort by index, because a provider is
    # free to answer a batch in any order and a silently shuffled batch would attach
    # every vector to the wrong memory.
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    return {"data": list(reversed(data))}


class ProviderDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        for attr in ("EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "LLM_API_KEY", "COHERE_API_KEY"):
            self.addCleanup(setattr, config, attr, getattr(config, attr))

    def test_nanogpt_provider_posts_openai_shaped_and_sorts_by_index(self) -> None:
        config.EMBEDDING_PROVIDER = "nanogpt"
        config.EMBEDDING_MODEL = "bge-m3"
        config.LLM_API_KEY = "k1"
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, json=json)
            return _Response(payload=_openai_payload([[1.0, 0.0], [0.0, 1.0]]))

        with patch.object(vector_store.httpx, "post", fake_post):
            out = vector_store.embed_batch(["a", "b"])

        self.assertEqual(out, [[1.0, 0.0], [0.0, 1.0]])
        self.assertIn("/embeddings", captured["url"])
        self.assertEqual(captured["json"]["model"], "bge-m3")
        self.assertEqual(captured["json"]["input"], ["a", "b"])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer k1")

    def test_nanogpt_uses_only_the_first_key_of_the_pool(self) -> None:
        # LLM_API_KEY is a comma-separated pool for the chat client; the embedding path
        # does not rotate, so it must at least pick a single valid key rather than send
        # the whole string as one.
        config.EMBEDDING_PROVIDER = "nanogpt"
        config.LLM_API_KEY = "first,second"
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(headers=headers)
            return _Response(payload=_openai_payload([[1.0]]))

        with patch.object(vector_store.httpx, "post", fake_post):
            vector_store.embed_text("x")

        self.assertEqual(captured["headers"]["Authorization"], "Bearer first")

    def test_nanogpt_without_a_key_fails_loudly(self) -> None:
        config.EMBEDDING_PROVIDER = "nanogpt"
        config.LLM_API_KEY = ""
        with self.assertRaises(RuntimeError) as caught:
            vector_store.embed_text("x")
        self.assertIn("LLM_API_KEY", str(caught.exception))

    def test_a_provider_error_carries_the_body_not_just_the_status(self) -> None:
        # The 402 that stopped a backfill said "Insufficient balance ... 0.000095 USD" in
        # the body; the status alone would have read as a generic failure.
        config.EMBEDDING_PROVIDER = "nanogpt"
        config.LLM_API_KEY = "k"
        with patch.object(vector_store.httpx, "post",
                          lambda *a, **k: _Response(402, text='{"error":"Insufficient balance"}')):
            with self.assertRaises(RuntimeError) as caught:
                vector_store.embed_text("x")
        self.assertIn("402", str(caught.exception))
        self.assertIn("Insufficient balance", str(caught.exception))

    def test_cohere_embeds_a_query_differently_from_a_document(self) -> None:
        # input_type is the only reason the cohere path exists; without it this is just
        # another symmetric embedder and the whole measurement that chose it is void.
        config.EMBEDDING_PROVIDER = "cohere"
        config.EMBEDDING_MODEL = "embed-multilingual-v3.0"
        config.COHERE_API_KEY = "ck"
        seen = []

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.append(json["input_type"])
            return _Response(payload={"embeddings": {"float": [[1.0]]}})

        with patch.object(vector_store.httpx, "post", fake_post):
            vector_store.embed_text("q", is_query=True)
            vector_store.embed_text("d", is_query=False)

        self.assertEqual(seen, ["search_query", "search_document"])

    def test_an_unknown_provider_falls_back_to_google_rather_than_crashing(self) -> None:
        config.EMBEDDING_PROVIDER = "typo"
        with patch.object(vector_store, "_call_embed", return_value=[[0.5]]) as google:
            self.assertEqual(vector_store.embed_text("x"), [0.5])
        google.assert_called_once()


class BatchSplittingTests(unittest.TestCase):
    """bge-m3 caps the request, not the text: 50 short facts fit, 50 scene excerpts do not.

    One batch of 50 in 95 blew the limit during the first backfill and all 50 were lost,
    because the failure was counted and skipped. Halving is enough since the limit is on
    the sum.
    """

    def setUp(self) -> None:
        self.stored = []
        self.addCleanup(setattr, config, "EMBEDDING_PROVIDER", config.EMBEDDING_PROVIDER)

    def _items(self, n):
        return [(f"m{i}", f"text {i}", {"chat_id": "c", "character_id": "x"}) for i in range(n)]

    def test_an_oversized_batch_is_halved_until_it_fits(self) -> None:
        calls = []

        def fake_embed(texts, is_query=False):
            calls.append(len(texts))
            if len(texts) > 2:
                raise RuntimeError("Embedding API error 400: context_length_exceeded")
            return [[1.0] for _ in texts]

        with patch.object(vector_store, "embed_batch", fake_embed), \
             patch.object(vector_store, "_sqlite_add", lambda *a, **k: self.stored.append(a[0])):
            stored = vector_store.add_memories_batch(self._items(4))

        self.assertEqual(stored, 4, "every memory must still be stored after the split")
        self.assertEqual(calls[0], 4, "the first attempt is the whole batch")
        self.assertTrue(all(c <= 4 for c in calls))
        self.assertEqual(len(self.stored), 4)

    def test_one_item_too_long_is_dropped_alone_not_with_its_neighbours(self) -> None:
        def fake_embed(texts, is_query=False):
            if any(t == "text 1" for t in texts):
                raise RuntimeError("context_length_exceeded")
            return [[1.0] for _ in texts]

        with patch.object(vector_store, "embed_batch", fake_embed), \
             patch.object(vector_store, "_sqlite_add", lambda *a, **k: self.stored.append(a[0])):
            stored = vector_store.add_memories_batch(self._items(4))

        # The three that fit are stored; only the oversized one is lost.
        self.assertEqual(stored, 3)
        self.assertNotIn("m1", self.stored)

    def test_an_unrelated_error_is_raised_rather_than_split_against(self) -> None:
        # Halving a batch because the key is out of balance would turn one failed request
        # into a cascade of them.
        def fake_embed(texts, is_query=False):
            raise RuntimeError("Embedding API error 402: Insufficient balance")

        with patch.object(vector_store, "embed_batch", fake_embed):
            with self.assertRaises(RuntimeError):
                vector_store.add_memories_batch(self._items(4))

    def test_an_empty_batch_costs_no_request(self) -> None:
        with patch.object(vector_store, "embed_batch",
                          side_effect=AssertionError("must not be called")):
            self.assertEqual(vector_store.add_memories_batch([]), 0)


if __name__ == "__main__":
    unittest.main()
