import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import config
from app.db import init_schema
from app.routes.memory_api import (
    SetActiveProviderRequest,
    list_models_endpoint,
    set_active_provider_endpoint,
)
from app.services import llm_client


def _mock_response(json_data: dict, status_ok: bool = True) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = json_data
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = Exception("HTTP error")
    return resp


class ProviderConfigTestCase(unittest.TestCase):
    """Base class that snapshots/restores every provider-related config field
    and the in-process active-provider override around each test, and gives
    each test its own throwaway SQLite database (get_active_provider/
    set_active_provider now read and write app_settings there)."""

    def setUp(self) -> None:
        self._snapshot = {
            "ACTIVE_LLM_PROVIDER": config.ACTIVE_LLM_PROVIDER,
            "LLM_API_BASE": config.LLM_API_BASE,
            "LLM_API_KEY": config.LLM_API_KEY,
            "LLM_MODEL": config.LLM_MODEL,
            "OPENAI_API_BASE": config.OPENAI_API_BASE,
            "OPENAI_API_KEY": config.OPENAI_API_KEY,
            "OPENAI_MODEL": config.OPENAI_MODEL,
            "ANTHROPIC_API_BASE": config.ANTHROPIC_API_BASE,
            "ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY,
            "ANTHROPIC_MODEL": config.ANTHROPIC_MODEL,
            "DATABASE_PATH": config.DATABASE_PATH,
        }
        self._original_override = llm_client._active_provider_override
        self.addCleanup(self._restore)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        init_schema()

    def _restore(self) -> None:
        for key, value in self._snapshot.items():
            setattr(config, key, value)
        llm_client._active_provider_override = self._original_override


class NanoGptProviderTests(ProviderConfigTestCase):
    """NanoGPT and OpenAI both use the OpenAI-compatible request path."""

    def setUp(self) -> None:
        super().setUp()
        config.ACTIVE_LLM_PROVIDER = "nanogpt"
        llm_client._active_provider_override = None
        config.LLM_API_BASE = "https://nano-gpt.example/api"
        config.LLM_API_KEY = "nano-key"
        config.LLM_MODEL = "nano-default-model"

    def test_is_llm_enabled_true_when_api_base_set(self) -> None:
        self.assertTrue(llm_client.is_llm_enabled())

    def test_is_llm_enabled_false_without_api_base(self) -> None:
        config.LLM_API_BASE = ""
        self.assertFalse(llm_client.is_llm_enabled())

    def test_list_models_falls_back_to_default_when_provider_disabled(self) -> None:
        """Regression: an unconfigured provider (no LLM_API_BASE) used to make
        list_models() return [] outright - the disabled-guard short-circuited
        before the /v1/models request (and its except-branch fallback) ever
        ran, leaving the UI model selector empty. It must return the
        configured default model instead, same as a failed request would."""
        config.LLM_API_BASE = ""
        with patch("httpx.get") as mock_get:
            models = llm_client.list_models()

        mock_get.assert_not_called()
        self.assertEqual(models, ["nano-default-model"])

    def test_list_models_hits_openai_compatible_endpoint(self) -> None:
        response = _mock_response({"data": [{"id": "model-a"}, {"id": "model-b"}]})
        with patch("httpx.get", return_value=response) as mock_get:
            models = llm_client.list_models()

        self.assertEqual(models, ["model-a", "model-b"])
        called_url, called_kwargs = mock_get.call_args[0][0], mock_get.call_args[1]
        self.assertEqual(called_url, "https://nano-gpt.example/api/v1/models")
        self.assertEqual(called_kwargs["headers"]["Authorization"], "Bearer nano-key")

    def test_list_models_falls_back_to_default_on_error(self) -> None:
        with patch("httpx.get", side_effect=Exception("boom")):
            models = llm_client.list_models()
        self.assertEqual(models, ["nano-default-model"])

    def test_chat_completion_posts_openai_shaped_payload(self) -> None:
        response = _mock_response(
            {"choices": [{"message": {"content": "hello from nanogpt"}}]}
        )
        with patch("httpx.post", return_value=response) as mock_post:
            result = llm_client.chat_completion(
                [{"role": "user", "content": "hi"}],
                max_tokens=123,
                temperature=0.5,
            )

        self.assertEqual(result, "hello from nanogpt")
        called_url = mock_post.call_args[0][0]
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(called_url, "https://nano-gpt.example/api/v1/chat/completions")
        self.assertEqual(payload["model"], "nano-default-model")
        self.assertEqual(payload["max_tokens"], 123)
        self.assertEqual(payload["temperature"], 0.5)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hi"}])

    def test_chat_completion_model_override(self) -> None:
        response = _mock_response({"choices": [{"message": {"content": "ok"}}]})
        with patch("httpx.post", return_value=response) as mock_post:
            llm_client.chat_completion([{"role": "user", "content": "hi"}], model="custom-model")
        self.assertEqual(mock_post.call_args[1]["json"]["model"], "custom-model")


class OpenAiProviderTests(ProviderConfigTestCase):
    """OpenAI shares NanoGPT's code path, just pointed at a different base/key."""

    def setUp(self) -> None:
        super().setUp()
        config.ACTIVE_LLM_PROVIDER = "openai"
        llm_client._active_provider_override = None
        config.OPENAI_API_BASE = "https://api.openai.com"
        config.OPENAI_API_KEY = "openai-key"
        config.OPENAI_MODEL = "gpt-4o-mini"

    def test_is_llm_enabled_true_when_api_base_set(self) -> None:
        self.assertTrue(llm_client.is_llm_enabled())

    def test_chat_completion_uses_openai_base_and_key(self) -> None:
        response = _mock_response(
            {"choices": [{"message": {"content": "hello from openai"}}]}
        )
        with patch("httpx.post", return_value=response) as mock_post:
            result = llm_client.chat_completion([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "hello from openai")
        called_url = mock_post.call_args[0][0]
        headers = mock_post.call_args[1]["headers"]
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(called_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer openai-key")
        self.assertEqual(payload["model"], "gpt-4o-mini")

    def test_response_format_passed_through_verbatim(self) -> None:
        response = _mock_response({"choices": [{"message": {"content": "{}"}}]})
        schema = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
        with patch("httpx.post", return_value=response) as mock_post:
            llm_client.chat_completion(
                [{"role": "user", "content": "hi"}], response_format=schema
            )
        self.assertEqual(mock_post.call_args[1]["json"]["response_format"], schema)


class AnthropicProviderTests(ProviderConfigTestCase):
    def setUp(self) -> None:
        super().setUp()
        config.ACTIVE_LLM_PROVIDER = "anthropic"
        llm_client._active_provider_override = None
        config.ANTHROPIC_API_BASE = "https://api.anthropic.com"
        config.ANTHROPIC_API_KEY = "anthropic-key"
        config.ANTHROPIC_MODEL = "claude-opus-4-8"

    def test_is_llm_enabled_requires_api_key_not_api_base(self) -> None:
        self.assertTrue(llm_client.is_llm_enabled())
        config.ANTHROPIC_API_KEY = ""
        self.assertFalse(llm_client.is_llm_enabled())

    def test_list_models_falls_back_to_default_when_provider_disabled(self) -> None:
        config.ANTHROPIC_API_KEY = ""
        with patch("httpx.get") as mock_get:
            models = llm_client.list_models()

        mock_get.assert_not_called()
        self.assertEqual(models, ["claude-opus-4-8"])

    def test_list_models_uses_x_api_key_header(self) -> None:
        response = _mock_response({"data": [{"id": "claude-opus-4-8"}, {"id": "claude-haiku-4-5"}]})
        with patch("httpx.get", return_value=response) as mock_get:
            models = llm_client.list_models()

        self.assertEqual(models, ["claude-opus-4-8", "claude-haiku-4-5"])
        called_url, called_kwargs = mock_get.call_args[0][0], mock_get.call_args[1]
        self.assertEqual(called_url, "https://api.anthropic.com/v1/models")
        self.assertEqual(called_kwargs["headers"]["x-api-key"], "anthropic-key")
        self.assertEqual(called_kwargs["headers"]["anthropic-version"], llm_client.ANTHROPIC_API_VERSION)
        self.assertNotIn("Authorization", called_kwargs["headers"])

    def test_chat_completion_splits_system_and_conversation(self) -> None:
        response = _mock_response(
            {"content": [{"type": "text", "text": "hello from claude"}], "stop_reason": "end_turn"}
        )
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hi"},
        ]
        with patch("httpx.post", return_value=response) as mock_post:
            result = llm_client.chat_completion(messages, max_tokens=200)

        self.assertEqual(result, "hello from claude")
        called_url = mock_post.call_args[0][0]
        headers = mock_post.call_args[1]["headers"]
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(called_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "anthropic-key")
        self.assertEqual(headers["anthropic-version"], llm_client.ANTHROPIC_API_VERSION)
        self.assertEqual(payload["system"], "You are a helpful assistant.")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(payload["max_tokens"], 200)
        self.assertNotIn("temperature", payload)

    def test_chat_completion_translates_json_schema_response_format(self) -> None:
        response = _mock_response(
            {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"}
        )
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        response_format = {"type": "json_schema", "json_schema": {"name": "x", "strict": True, "schema": schema}}

        with patch("httpx.post", return_value=response) as mock_post:
            llm_client.chat_completion(
                [{"role": "user", "content": "hi"}], response_format=response_format
            )

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(
            payload["output_config"],
            {"format": {"type": "json_schema", "schema": schema}},
        )

    def test_chat_completion_raises_on_refusal(self) -> None:
        response = _mock_response({"content": [], "stop_reason": "refusal"})
        with patch("httpx.post", return_value=response):
            with self.assertRaises(RuntimeError):
                llm_client.chat_completion([{"role": "user", "content": "hi"}])

    def test_chat_completion_model_override(self) -> None:
        response = _mock_response(
            {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
        )
        with patch("httpx.post", return_value=response) as mock_post:
            llm_client.chat_completion([{"role": "user", "content": "hi"}], model="claude-haiku-4-5")
        self.assertEqual(mock_post.call_args[1]["json"]["model"], "claude-haiku-4-5")


class ActiveProviderSwitchTests(ProviderConfigTestCase):
    def setUp(self) -> None:
        super().setUp()
        config.ACTIVE_LLM_PROVIDER = "nanogpt"
        llm_client._active_provider_override = None

    def test_get_active_provider_defaults_to_config(self) -> None:
        self.assertEqual(llm_client.get_active_provider(), "nanogpt")

    def test_set_active_provider_overrides_config(self) -> None:
        llm_client.set_active_provider("anthropic")
        self.assertEqual(llm_client.get_active_provider(), "anthropic")
        # The .env-derived default is untouched — the override lives in the
        # in-process cache and SQLite, never in config.ACTIVE_LLM_PROVIDER.
        self.assertEqual(config.ACTIVE_LLM_PROVIDER, "nanogpt")

    def test_set_active_provider_rejects_unknown_provider(self) -> None:
        with self.assertRaises(ValueError):
            llm_client.set_active_provider("not-a-real-provider")
        self.assertEqual(llm_client.get_active_provider(), "nanogpt")

    def test_switching_provider_changes_which_backend_chat_completion_calls(self) -> None:
        config.OPENAI_API_BASE = "https://api.openai.com"
        config.OPENAI_API_KEY = "openai-key"
        config.OPENAI_MODEL = "gpt-4o-mini"

        llm_client.set_active_provider("openai")
        response = _mock_response({"choices": [{"message": {"content": "via openai"}}]})
        with patch("httpx.post", return_value=response) as mock_post:
            result = llm_client.chat_completion([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "via openai")
        self.assertIn("api.openai.com", mock_post.call_args[0][0])

    def test_endpoint_switches_active_provider(self) -> None:
        response = set_active_provider_endpoint(SetActiveProviderRequest(provider="anthropic"))
        self.assertEqual(response.active_provider, "anthropic")
        self.assertEqual(llm_client.get_active_provider(), "anthropic")

    def test_endpoint_rejects_unknown_provider_with_400(self) -> None:
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as exc:
            set_active_provider_endpoint(SetActiveProviderRequest(provider="not-a-real-provider"))
        self.assertEqual(exc.exception.status_code, 400)

    def test_models_endpoint_reflects_active_provider(self) -> None:
        config.ANTHROPIC_API_KEY = "anthropic-key"
        config.ANTHROPIC_MODEL = "claude-opus-4-8"
        llm_client.set_active_provider("anthropic")

        response = _mock_response({"data": [{"id": "claude-opus-4-8"}]})
        with patch("httpx.get", return_value=response):
            result = list_models_endpoint()

        self.assertEqual(result["active_provider"], "anthropic")
        self.assertEqual(result["providers"], list(llm_client.PROVIDERS))
        self.assertEqual(result["models"], ["claude-opus-4-8"])
        self.assertEqual(result["default"], "claude-opus-4-8")


class ProviderPersistenceTests(ProviderConfigTestCase):
    """The active provider must survive a server restart, via the app_settings
    SQLite table rather than the in-process cache alone."""

    def setUp(self) -> None:
        super().setUp()
        config.ACTIVE_LLM_PROVIDER = "nanogpt"
        llm_client._active_provider_override = None

    def _simulate_restart(self) -> None:
        """The only in-process state llm_client keeps is the override cache,
        so clearing it reproduces a freshly-imported module's initial state."""
        llm_client._active_provider_override = None

    def test_switched_provider_survives_simulated_restart(self) -> None:
        llm_client.set_active_provider("anthropic")
        self.assertEqual(llm_client.get_active_provider(), "anthropic")

        self._simulate_restart()

        self.assertEqual(llm_client.get_active_provider(), "anthropic")

    def test_endpoint_switch_persists_to_db_not_just_memory(self) -> None:
        set_active_provider_endpoint(SetActiveProviderRequest(provider="openai"))

        self._simulate_restart()

        self.assertEqual(llm_client.get_active_provider(), "openai")

    def test_second_switch_overwrites_first_persisted_value(self) -> None:
        llm_client.set_active_provider("openai")
        llm_client.set_active_provider("anthropic")

        self._simulate_restart()

        self.assertEqual(llm_client.get_active_provider(), "anthropic")

    def test_falls_back_to_env_default_when_nothing_ever_persisted(self) -> None:
        config.ACTIVE_LLM_PROVIDER = "openai"

        self._simulate_restart()

        self.assertEqual(llm_client.get_active_provider(), "openai")

    def test_falls_back_to_env_default_when_settings_table_is_missing(self) -> None:
        """A brand-new database file with no schema yet (e.g. init_schema()
        hasn't run) must degrade to the .env default, not crash."""
        fresh_dir = tempfile.TemporaryDirectory()
        self.addCleanup(fresh_dir.cleanup)
        config.DATABASE_PATH = str(Path(fresh_dir.name) / "unmigrated.db")
        config.ACTIVE_LLM_PROVIDER = "nanogpt"

        self._simulate_restart()

        self.assertEqual(llm_client.get_active_provider(), "nanogpt")

    def test_repo_get_setting_returns_none_when_unset(self) -> None:
        from app.repositories.app_settings_repo import get_setting

        self.assertIsNone(get_setting("some-key-nobody-set"))

    def test_repo_set_then_get_round_trips(self) -> None:
        from app.repositories.app_settings_repo import get_setting, set_setting

        set_setting("some-key", "some-value")
        self.assertEqual(get_setting("some-key"), "some-value")

        set_setting("some-key", "updated-value")
        self.assertEqual(get_setting("some-key"), "updated-value")


if __name__ == "__main__":
    unittest.main()
