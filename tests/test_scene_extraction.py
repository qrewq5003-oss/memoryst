import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import list_memories
from app.schemas import ChatMessageItem, MessageInput, StoreMemoryRequest
from app.services import chat_buffer_service
from app.services.llm_client import chat_completion
from app.services.llm_extractor import build_indexed_scene_text, extract_scene_facts
from app.services.scene_extractor import extract_scene_memories
from app.services.store_service import store_memories


def _msg(text: str, *, role: str = "assistant", message_id: str = "id-0", index: int = 0) -> ChatMessageItem:
    return ChatMessageItem(
        id=message_id,
        chat_id="chat-1",
        character_id="char-1",
        role=role,
        text=text,
        created_at="2026-07-01T00:00:00+00:00",
        sequence_index=index,
    )


class ScenePreFilterTests(unittest.TestCase):
    """Regex markers gate whether the LLM is called at all (Stage 3.4)."""

    def test_no_marker_anywhere_skips_llm_entirely(self) -> None:
        messages = [_msg("Привет!", message_id="m0"), _msg("Как дела?", role="user", message_id="m1", index=1)]

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts"
        ) as facts_mock:
            candidates, method = extract_scene_memories("chat-1", "char-1", messages)

        facts_mock.assert_not_called()
        self.assertEqual(candidates, [])
        self.assertEqual(method, "regex_fallback")

    def test_marker_hit_triggers_llm_call(self) -> None:
        messages = [_msg("Алиса работает врачом в Риме.", message_id="m0")]

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts", return_value=[]
        ) as facts_mock:
            extract_scene_memories("chat-1", "char-1", messages)

        facts_mock.assert_called_once()

    def test_empty_message_list_returns_empty_without_calling_llm(self) -> None:
        with patch("app.services.llm_extractor.extract_scene_facts") as facts_mock:
            candidates, method = extract_scene_memories("chat-1", "char-1", [])

        facts_mock.assert_not_called()
        self.assertEqual(candidates, [])
        self.assertIsNone(method)


class SceneLLMExtractionTests(unittest.TestCase):
    """LLM does the actual classification/extraction over the whole scene (Stage 3.2/3.3)."""

    def test_llm_facts_become_candidates_with_source_message_ids(self) -> None:
        messages = [
            _msg("Алиса работает врачом в Риме.", message_id="msg-uuid-0", index=0),
            _msg("Звучит интересно!", role="user", message_id="msg-uuid-1", index=1),
        ]
        fake_facts = [
            {
                "content": "Алиса работает врачом в Риме.",
                "type": "profile",
                "layer": "stable",
                "keywords": ["алиса", "рим", "врач"],
                "entities": ["Алиса"],
                "source_message_ids": ["msg-uuid-0"],
            }
        ]

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts", return_value=fake_facts
        ):
            candidates, method = extract_scene_memories("chat-1", "char-1", messages)

        self.assertEqual(method, "llm")
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.type, "profile")
        self.assertEqual(candidate.layer, "stable")
        self.assertEqual(candidate.metadata.source_message_ids, ["msg-uuid-0"])
        self.assertEqual(candidate.metadata.keywords, ["алиса", "рим", "врач"])

    def test_invalid_type_from_llm_is_dropped(self) -> None:
        messages = [_msg("Алиса работает врачом в Риме.", message_id="m0")]
        fake_facts = [
            {
                "content": "irrelevant",
                "type": "not-a-real-type",
                "layer": "stable",
                "keywords": [],
                "entities": [],
                "source_message_ids": [],
            }
        ]

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts", return_value=fake_facts
        ):
            candidates, method = extract_scene_memories("chat-1", "char-1", messages)

        self.assertEqual(method, "llm")
        self.assertEqual(candidates, [])

    def test_missing_layer_from_llm_falls_back_to_heuristic_layer(self) -> None:
        messages = [_msg("Алиса работает врачом в Риме.", message_id="m0")]
        fake_facts = [
            {
                "content": "Алиса работает врачом в Риме.",
                "type": "profile",
                "layer": "not-a-valid-layer",
                "keywords": [],
                "entities": [],
                "source_message_ids": [],
            }
        ]

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts", return_value=fake_facts
        ):
            candidates, method = extract_scene_memories("chat-1", "char-1", messages)

        self.assertEqual(method, "llm")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].layer, "stable")


class SceneFallbackTests(unittest.TestCase):
    """When the LLM is unavailable, live extraction keeps working via the rule-based path."""

    def test_llm_disabled_falls_back_to_rule_based_extractor(self) -> None:
        messages = [_msg("Алиса работает врачом в Риме.", message_id="m0")]

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=False), patch(
            "app.services.llm_extractor.extract_scene_facts"
        ) as facts_mock:
            candidates, method = extract_scene_memories("chat-1", "char-1", messages)

        facts_mock.assert_not_called()
        self.assertEqual(method, "regex_fallback")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].type, "profile")
        # Rule-based fallback has no source_message_ids - only the LLM path attaches them.
        self.assertEqual(candidates[0].metadata.source_message_ids, [])

    def test_llm_call_failure_falls_back_to_rule_based_extractor(self) -> None:
        messages = [_msg("Алиса работает врачом в Риме.", message_id="m0")]

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts", return_value=None
        ):
            candidates, method = extract_scene_memories("chat-1", "char-1", messages)

        self.assertEqual(method, "regex_fallback")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].type, "profile")


class IndexedSceneTextTests(unittest.TestCase):
    def test_build_indexed_scene_text_maps_indices_to_ids_in_order(self) -> None:
        messages = [
            _msg("первое сообщение", message_id="uuid-a", index=0),
            _msg("второе сообщение", role="user", message_id="uuid-b", index=1),
        ]

        scene_text, id_by_index = build_indexed_scene_text(messages)

        self.assertEqual(id_by_index, ["uuid-a", "uuid-b"])
        self.assertIn("[0][assistant]: первое сообщение", scene_text)
        self.assertIn("[1][user]: второе сообщение", scene_text)

    def test_extract_scene_facts_maps_llm_indices_back_to_message_ids(self) -> None:
        messages = [
            _msg("первое сообщение", message_id="uuid-a", index=0),
            _msg("второе сообщение", role="user", message_id="uuid-b", index=1),
        ]
        llm_response = json.dumps(
            {
                "facts": [
                    {
                        "content": "fact text",
                        "type": "event",
                        "layer": "episodic",
                        "keywords": [],
                        "entities": [],
                        "source_message_indices": [0, 1],
                    }
                ]
            }
        )

        with patch("app.services.llm_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.chat_completion", return_value=llm_response
        ) as completion_mock:
            facts = extract_scene_facts(messages)

        self.assertIsNotNone(facts)
        self.assertEqual(facts[0]["source_message_ids"], ["uuid-a", "uuid-b"])
        # Structured output is requested, not parsed out of free text.
        _, kwargs = completion_mock.call_args
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")

    def test_extract_scene_facts_returns_none_on_malformed_json(self) -> None:
        messages = [_msg("первое сообщение", message_id="uuid-a", index=0)]

        with patch("app.services.llm_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.chat_completion", return_value="not json"
        ):
            facts = extract_scene_facts(messages)

        self.assertIsNone(facts)

    def test_extract_scene_facts_returns_none_when_llm_disabled(self) -> None:
        messages = [_msg("первое сообщение", message_id="uuid-a", index=0)]

        with patch("app.services.llm_extractor.is_llm_enabled", return_value=False):
            facts = extract_scene_facts(messages)

        self.assertIsNone(facts)


class ChatCompletionResponseFormatTests(unittest.TestCase):
    def test_response_format_is_forwarded_in_payload(self) -> None:
        captured = {}

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": "{}"}}]}

        def fake_post(url, json, headers, timeout):
            captured["payload"] = json
            return FakeResponse()

        with patch("app.config.config.LLM_API_BASE", "https://example.test"), patch(
            "app.services.llm_client.httpx.post", side_effect=fake_post
        ):
            chat_completion(
                [{"role": "user", "content": "hi"}],
                response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": {}}},
            )

        self.assertIn("response_format", captured["payload"])
        self.assertEqual(captured["payload"]["response_format"]["type"], "json_schema")

    def test_response_format_omitted_when_not_given(self) -> None:
        captured = {}

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": "ok"}}]}

        def fake_post(url, json, headers, timeout):
            captured["payload"] = json
            return FakeResponse()

        with patch("app.config.config.LLM_API_BASE", "https://example.test"), patch(
            "app.services.llm_client.httpx.post", side_effect=fake_post
        ):
            chat_completion([{"role": "user", "content": "hi"}])

        self.assertNotIn("response_format", captured["payload"])


class StoreEndpointWiringTests(unittest.TestCase):
    """Stage 3 wiring: /memory/store cools messages through chat_buffer_service first."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()
        chat_buffer_service.reset_all_buffers()
        self.addCleanup(chat_buffer_service.reset_all_buffers)

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def test_store_assigns_stable_ids_and_extraction_sees_buffered_messages(self) -> None:
        captured_messages = {}

        def fake_extract(chat_id, character_id, messages, model=None, **kwargs):
            captured_messages["messages"] = messages
            return [], None

        with patch("app.services.store_service.extract_scene_memories", side_effect=fake_extract):
            store_memories(
                StoreMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    messages=[MessageInput(role="user", text="Hello there")],
                )
            )

        messages = captured_messages["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].text, "Hello there")
        # Stable id assigned by chat_buffer_service, not empty/missing.
        self.assertTrue(messages[0].id)
        self.assertEqual(
            chat_buffer_service.get_hot_buffer("chat-1", "char-1")[0].id,
            messages[0].id,
        )

    def test_store_filters_ooc_before_extraction(self) -> None:
        captured_messages = {}

        def fake_extract(chat_id, character_id, messages, model=None, **kwargs):
            captured_messages["messages"] = messages
            return [], None

        with patch("app.services.store_service.extract_scene_memories", side_effect=fake_extract):
            store_memories(
                StoreMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    messages=[
                        MessageInput(role="user", text="OOC: skip this"),
                        MessageInput(role="user", text="Regular in-character line"),
                    ],
                )
            )

        messages = captured_messages["messages"]
        self.assertEqual([m.text for m in messages], ["Regular in-character line"])

    def test_end_to_end_llm_fact_is_stored_with_source_message_ids(self) -> None:
        def fake_facts(messages, *, model=None, **kwargs):
            return [
                {
                    "content": "Алиса работает врачом в Риме.",
                    "type": "profile",
                    "layer": "stable",
                    "keywords": ["алиса"],
                    "entities": ["Алиса"],
                    "source_message_ids": [messages[0].id],
                }
            ]

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts", side_effect=fake_facts
        ):
            response = store_memories(
                StoreMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    messages=[MessageInput(role="assistant", text="Алиса работает врачом в Риме.")],
                )
            )

        self.assertEqual(response.stored, 1)
        self.assertEqual(response.extraction_method, "llm")
        stored_item = list_memories(chat_id="chat-1", character_id="char-1").items[0]
        self.assertEqual(len(stored_item.metadata.source_message_ids), 1)
        buffered_id = chat_buffer_service.get_hot_buffer("chat-1", "char-1")[0].id
        self.assertEqual(stored_item.metadata.source_message_ids, [buffered_id])

    def test_store_response_reports_regex_fallback_when_llm_call_fails(self) -> None:
        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts", return_value=None
        ):
            response = store_memories(
                StoreMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    messages=[MessageInput(role="assistant", text="Алиса работает врачом в Риме.")],
                )
            )

        # A failed/disabled LLM call must not look identical to a real "nothing to
        # remember" success on the wire - see CLAUDE.md's scene-extraction-llm-failing
        # investigation, where this was previously indistinguishable from the outside.
        self.assertEqual(response.extraction_method, "regex_fallback")

    def test_store_request_model_override_reaches_extract_scene_facts(self) -> None:
        captured = {}

        def fake_facts(messages, *, model=None, **kwargs):
            captured["model"] = model
            return []

        with patch("app.services.scene_extractor.is_llm_enabled", return_value=True), patch(
            "app.services.llm_extractor.extract_scene_facts", side_effect=fake_facts
        ):
            store_memories(
                StoreMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    messages=[MessageInput(role="assistant", text="Алиса работает врачом в Риме.")],
                    model="deepseek-chat",
                )
            )

        self.assertEqual(captured["model"], "deepseek-chat")


if __name__ == "__main__":
    unittest.main()


class SceneExtractionTimeoutTests(unittest.TestCase):
    """Scene extraction sends a whole scene and asks for 6000 tokens of schema'd
    JSON, but inherited the 30s default sized for short calls. Measured over
    data/server.log on 2026-08-01: 568 fallbacks against 1583 successes, with
    ReadTimeout the single largest cause."""

    def test_scene_extraction_uses_its_own_timeout_not_the_short_call_default(self) -> None:
        from app.config import config
        from app.services import llm_extractor

        self.assertGreater(config.SCENE_LLM_TIMEOUT, config.LLM_TIMEOUT)

        messages = [
            ChatMessageItem(
                id=f"m{i}",
                chat_id="c",
                character_id="7",
                role="user" if i % 2 == 0 else "assistant",
                text=f"Реплика {i}.",
                created_at="2026-08-01T00:00:00+00:00",
                sequence_index=i,
            )
            for i in range(4)
        ]

        with (
            patch("app.services.llm_extractor.is_llm_enabled", return_value=True),
            patch(
                "app.services.llm_extractor.chat_completion",
                return_value='{"facts": []}',
            ) as completion,
        ):
            llm_extractor.extract_scene_facts(messages)

        self.assertEqual(
            completion.call_args.kwargs["timeout"], config.SCENE_LLM_TIMEOUT
        )
