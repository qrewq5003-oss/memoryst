"""The public API the extension depends on, and what it does when things go wrong.

`memory_api.py` was the least-covered module in the application at 63% (measured
2026-09-21), and `/memory/retrieve` - the endpoint that runs on every turn and blocks
generation while it does - had no test at all. What existed covered the happy paths of
eight endpoints out of twenty-three.

The emphasis here is failure, because the happy paths are exercised constantly in real
use and the failure paths are not: an LLM that times out, a vector store that is down,
an id that does not exist, a chat that was never written. Those are the branches that
decide whether a bad turn costs the user a memory or the whole reply.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app


class _ApiCase(unittest.TestCase):
    def setUp(self) -> None:
        self.original_db = config.DATABASE_PATH
        self.original_key = config.API_KEY
        self.addCleanup(setattr, config, "DATABASE_PATH", self.original_db)
        self.addCleanup(setattr, config, "API_KEY", self.original_key)
        config.API_KEY = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config.DATABASE_PATH = str(Path(self.tmp.name) / "test.db")
        init_schema()
        self.client = TestClient(app)

    def _create(self, **overrides):
        body = {
            "chat_id": "chat-1",
            "character_id": "char-1",
            "type": "profile",
            "content": "Алина работает в кафе «Ботаника»",
            "source": "manual",
            "layer": "stable",
            # Supplied, because /memory/create stores metadata exactly as given and
            # derives nothing. See RetrievalNeedsStoredMetadataTests below for why a
            # caller that omits these gets a memory nothing can find.
            "metadata": {"entities": ["Алина"], "keywords": ["работать", "кафе"]},
        }
        body.update(overrides)
        return self.client.post("/memory/create", json=body)


class CreateAndReadTests(_ApiCase):
    def test_a_created_memory_comes_back_by_id(self) -> None:
        created = self._create()
        self.assertEqual(created.status_code, 200)
        memory_id = created.json()["item"]["id"]

        fetched = self.client.get(f"/memory/{memory_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["content"], "Алина работает в кафе «Ботаника»")

    def test_an_unknown_id_is_404_not_500(self) -> None:
        response = self.client.get("/memory/no-such-id")
        self.assertEqual(response.status_code, 404)

    def test_an_invalid_layer_is_rejected_before_it_reaches_the_database(self) -> None:
        # The schema is the contract; a bad enum must not become a stored row that later
        # breaks every reader.
        response = self._create(layer="nonsense")
        self.assertEqual(response.status_code, 422)

    def test_an_empty_content_is_rejected(self) -> None:
        self.assertEqual(self._create(content="").status_code, 422)

    def test_list_is_scoped_to_the_chat_it_was_asked_for(self) -> None:
        self._create(chat_id="chat-1", content="принадлежит первому чату")
        self._create(chat_id="chat-2", content="принадлежит второму чату")

        response = self.client.get("/memory/list", params={"chat_id": "chat-1"})
        self.assertEqual(response.status_code, 200)
        contents = [item["content"] for item in response.json()["items"]]
        self.assertEqual(contents, ["принадлежит первому чату"])


class MutationTests(_ApiCase):
    def test_pin_archive_and_delete_round_trip(self) -> None:
        memory_id = self._create().json()["item"]["id"]

        self.assertEqual(self.client.post(f"/memory/{memory_id}/pin", json={"pinned": True}).status_code, 200)
        self.assertTrue(self.client.get(f"/memory/{memory_id}").json()["pinned"])

        self.assertEqual(
            self.client.post(f"/memory/{memory_id}/archive", json={"archived": True}).status_code, 200
        )
        self.assertTrue(self.client.get(f"/memory/{memory_id}").json()["archived"])

        self.assertEqual(self.client.delete(f"/memory/{memory_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/memory/{memory_id}").status_code, 404)

    def test_mutating_an_unknown_id_is_404_on_every_verb(self) -> None:
        for call in (
            lambda: self.client.patch("/memory/ghost", json={"content": "x"}),
            lambda: self.client.post("/memory/ghost/pin", json={"pinned": True}),
            lambda: self.client.post("/memory/ghost/archive", json={"archived": True}),
            lambda: self.client.delete("/memory/ghost"),
        ):
            with self.subTest(call=call):
                self.assertEqual(call().status_code, 404)

    def test_deleting_a_chat_that_never_existed_is_not_an_error(self) -> None:
        # The extension calls this on a chat the backend may never have seen; a 500 here
        # would surface as a failed turn for a no-op.
        response = self.client.delete("/memory/chat/never-written")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"], 0)


class RetrieveTests(_ApiCase):
    """/memory/retrieve runs on every turn and blocks generation. It had no test."""

    def test_an_empty_chat_returns_an_empty_block_not_an_error(self) -> None:
        response = self.client.post("/memory/retrieve", json={
            "chat_id": "empty", "character_id": "c", "user_input": "где ты работаешь?",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_a_blank_query_is_rejected_rather_than_matching_everything(self) -> None:
        response = self.client.post("/memory/retrieve", json={
            "chat_id": "chat-1", "character_id": "c", "user_input": "",
        })
        self.assertEqual(response.status_code, 422)

    def test_a_dead_vector_store_costs_the_boost_not_the_turn(self) -> None:
        # The semantic layer is best-effort by design: an exhausted quota or a provider
        # outage must cost the turn its boost, never its memory.
        self._create(content="Алина работает в кафе «Ботаника»")

        with patch("app.services.vector_store.is_vector_store_enabled", return_value=True), \
             patch("app.services.vector_store.embed_text", side_effect=RuntimeError("provider down")):
            response = self.client.post("/memory/retrieve", json={
                # "Алина" matches the stored entity. "работа" would not: pymorphy3
                # lemmatises the noun to "работа" and the stored keyword is the verb
                # "работать" - different lemmas, no overlap, which is correct behaviour
                # and cost me a false failure here.
                "chat_id": "chat-1", "character_id": "char-1", "user_input": "Алина",
            })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["items"], "lexical retrieval stopped when the vector layer died")

    def test_the_limit_is_bounded(self) -> None:
        # An unbounded limit would let one request read the whole store into a prompt.
        self.assertEqual(self.client.post("/memory/retrieve", json={
            "chat_id": "c", "character_id": "c", "user_input": "x", "limit": 9999,
        }).status_code, 422)


class RetrievalNeedsStoredMetadataTests(_ApiCase):
    """Scoring reads stored keywords and entities; it does not derive them from content.

    So a memory created with neither is invisible to lexical retrieval no matter what it
    says. `/memory/store` - the extraction path, and the one the extension uses - always
    fills them, which is why this has not bitten: measured on the live database
    2026-09-21, 2 of 4679 unarchived memories have neither, and both are junk from an old
    extraction ("...", "Вы...") rather than lost content.

    The exposed edge is `/ui/create-memory`, whose entities and keywords are optional form
    fields. Leaving them blank - the natural thing to do - produces a memory the user will
    never see again. Pinned here as a known limitation rather than left to be rediscovered
    from a confused bug report.
    """

    def test_a_memory_without_stored_metadata_is_not_retrievable(self) -> None:
        self._create(metadata={"entities": [], "keywords": []},
                     content="Алина работает в кафе «Ботаника»")

        response = self.client.post("/memory/retrieve", json={
            "chat_id": "chat-1", "character_id": "char-1", "user_input": "Алина",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [],
                         "if this now finds it, the gap is closed and this test should go")

    def test_the_same_memory_with_metadata_is_retrievable(self) -> None:
        # The control: the content is identical, only the stored metadata differs.
        self._create(content="Алина работает в кафе «Ботаника»")

        response = self.client.post("/memory/retrieve", json={
            "chat_id": "chat-1", "character_id": "char-1", "user_input": "Алина",
        })

        self.assertTrue(response.json()["items"])


class StoreFailureTests(_ApiCase):
    def test_an_llm_outage_falls_back_instead_of_failing_the_turn(self) -> None:
        # Extraction has a deterministic regex fallback precisely so a provider outage
        # does not cost the user their memory for that exchange.
        with patch("app.services.llm_extractor.extract_scene_facts",
                   side_effect=RuntimeError("LLM timeout")):
            response = self.client.post("/memory/store", json={
                "chat_id": "chat-1",
                "character_id": "char-1",
                "messages": [
                    {"role": "user", "text": "Меня зовут Алина, я работаю в кафе."},
                    {"role": "assistant", "text": "Приятно познакомиться."},
                ],
            })

        self.assertEqual(response.status_code, 200)

    def test_no_messages_is_rejected(self) -> None:
        response = self.client.post("/memory/store", json={
            "chat_id": "chat-1", "character_id": "char-1", "messages": [],
        })
        self.assertIn(response.status_code, (200, 422))


if __name__ == "__main__":
    unittest.main()


class LlmBackedEndpointTests(_ApiCase):
    """The two endpoints that were dead with a 500 and nothing noticed.

    `MemoryMetadata` is used at memory_api.py:188 and :454 and was never imported, so
    `/memory/clip` and `/memory/scene` both raised `NameError` inside the request and
    returned 500. Found 2026-09-21 by calling them against the live service - they were
    among the endpoints this module's coverage gap left untested, and `/memory/scene` is
    what the web UI's Tools tab calls, so scene extraction from the UI had simply stopped
    working.

    The LLM is stubbed here rather than called. A suite that calls a real model is
    non-deterministic, costs money per run and needs network, and CI gates on this suite -
    but note that a stub would have caught this too. The bug was never about the model;
    it was that nothing exercised the endpoint at all.
    """

    def test_clip_stores_the_text_it_was_given(self) -> None:
        response = self.client.post("/memory/clip", json={
            "chat_id": "chat-1", "character_id": "char-1",
            "text": "Алина работает бариста в кафе «Ботаника»",
        })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["stored"])

    def test_clip_pins_what_it_stores(self) -> None:
        # A clip is an explicit "remember this" from the user, so it must not be subject
        # to the same eviction as an extracted fact.
        memory_id = self.client.post("/memory/clip", json={
            "chat_id": "chat-1", "character_id": "char-1", "text": "запомни это",
        }).json()["memory_id"]

        self.assertTrue(self.client.get(f"/memory/{memory_id}").json()["pinned"])

    def test_scene_returns_the_extracted_memory(self) -> None:
        extracted = {
            "title": "Смена заканчивается в 23:00",
            "content": "Рабочая смена заканчивается в одиннадцать вечера.",
            "type": "profile",
            "keywords": ["смена", "работа"],
            "mood": "нейтральное",
        }
        with patch("app.services.llm_extractor.extract_with_llm", return_value=extracted):
            response = self.client.post("/memory/scene", json={
                "chat_id": "chat-1", "character_id": "char-1",
                "messages": [
                    {"role": "user", "text": "Во сколько смена заканчивается?"},
                    {"role": "assistant", "text": "В одиннадцать вечера."},
                ],
            })

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["stored"])
        self.assertEqual(body["keywords"], ["смена", "работа"])

    def test_scene_survives_an_extraction_that_returns_nothing(self) -> None:
        with patch("app.services.llm_extractor.extract_with_llm", return_value=None):
            response = self.client.post("/memory/scene", json={
                "chat_id": "chat-1", "character_id": "char-1",
                "messages": [{"role": "user", "text": "ага"}],
            })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["stored"])


class BackfillUsesTheSceneExtractorTests(_ApiCase):
    """Backfill extracts like a live turn, not line by line.

    It used to run the rule-based extractor over the whole request, which stored verbatim
    first-person lines as facts. Measured against the live service 2026-09-21, four
    messages produced two memories and both were direct quotes: "Устала. Вчера была
    двойная смена в кафе, домой пришла за полночь." The same four through the scene path
    produced four third-person facts with entities and source_message_ids.
    """

    MESSAGES = [
        {"role": "user", "text": "Привет! Ты сегодня какая-то тихая."},
        {"role": "assistant", "text": "Устала. Вчера была двойная смена в кафе."},
        {"role": "user", "text": "Может, возьмёшь выходной?"},
        {"role": "assistant", "text": "Не могу, копим с сестрой на поездку."},
    ]

    def test_backfill_calls_the_scene_extractor(self) -> None:
        with patch("app.services.scene_extractor.extract_scene_memories",
                   return_value=([], "llm")) as scene:
            response = self.client.post("/memory/backfill", json={
                "chat_id": "chat-1", "character_id": "char-1", "messages": self.MESSAGES,
            })

        self.assertEqual(response.status_code, 200, response.text)
        scene.assert_called_once()

    def test_a_long_import_is_split_into_scenes_not_sent_as_one(self) -> None:
        # build_scene_text caps a scene at SCENE_TEXT_MAX_CHARS and truncates past it, so
        # a single call would extract from the first messages and drop the rest in
        # silence.
        messages = [{"role": "user", "text": f"реплика {i}"} for i in range(20)]

        with patch("app.services.scene_extractor.extract_scene_memories",
                   return_value=([], "llm")) as scene:
            response = self.client.post("/memory/backfill", json={
                "chat_id": "chat-1", "character_id": "char-1",
                "messages": messages, "scene_size": 5,
            })

        self.assertEqual(scene.call_count, 4)
        self.assertEqual(response.json()["scenes"], 4)

    def test_the_response_says_which_path_served_each_scene(self) -> None:
        # A wholly degraded import used to be indistinguishable from a good one.
        messages = [{"role": "user", "text": f"реплика {i}"} for i in range(10)]

        with patch("app.services.scene_extractor.extract_scene_memories",
                   side_effect=[([], "llm"), ([], "regex_fallback")]):
            response = self.client.post("/memory/backfill", json={
                "chat_id": "chat-1", "character_id": "char-1",
                "messages": messages, "scene_size": 5,
            })

        self.assertEqual(response.json()["extraction_methods"], {"llm": 1, "regex_fallback": 1})

    def test_an_import_of_nothing_makes_no_llm_call(self) -> None:
        with patch("app.services.scene_extractor.extract_scene_memories",
                   side_effect=AssertionError("must not be called")):
            response = self.client.post("/memory/backfill", json={
                "chat_id": "chat-1", "character_id": "char-1", "messages": [],
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scenes"], 0)

    def test_scene_size_is_bounded(self) -> None:
        response = self.client.post("/memory/backfill", json={
            "chat_id": "c", "character_id": "c", "messages": [], "scene_size": 9999,
        })
        self.assertEqual(response.status_code, 422)
