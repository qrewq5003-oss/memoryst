"""
Regression tests for the memory_api router's route *declaration order*.

FastAPI/Starlette match routes in the order they were registered, not by
specificity. A parameterized single-segment path like GET/PATCH/DELETE
"/{id}" will silently swallow same-method requests to any static
single-segment path (GET /keys, GET /models, DELETE /keys, ...) declared
after it, treating the static segment as the id. That bug is invisible to
tests that call endpoint functions directly (as most of this suite does) -
it only shows up when requests go through the actual router, which is why
this file uses TestClient instead of importing the handler functions.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.services import llm_client


class RouteOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_key = config.API_KEY
        self.original_db_path = config.DATABASE_PATH
        self.original_provider_override = llm_client._active_provider_override
        config.API_KEY = ""
        llm_client._active_provider_override = None
        self.addCleanup(self._restore_config)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        init_schema()

        self.client = TestClient(app)

    def _restore_config(self) -> None:
        config.API_KEY = self.original_api_key
        config.DATABASE_PATH = self.original_db_path
        llm_client._active_provider_override = self.original_provider_override

    def test_get_models_is_not_shadowed_by_parameterized_id_route(self) -> None:
        response = self.client.get("/memory/models")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("providers", body)
        self.assertIn("active_provider", body)
        self.assertIn("models", body)

    def test_get_keys_is_not_shadowed_by_parameterized_id_route(self) -> None:
        response = self.client.get("/memory/keys")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_delete_keys_is_not_shadowed_by_parameterized_id_route(self) -> None:
        # A malformed/absent body should 422 (missing "key" field), which only
        # happens if this actually reached remove_key_endpoint's RemoveKeyRequest
        # body — not a 404 "Memory not found" from delete_memory_endpoint("keys").
        response = self.client.request("DELETE", "/memory/keys", json={})
        self.assertEqual(response.status_code, 422)

    def test_get_by_id_still_works_for_a_genuine_unknown_id(self) -> None:
        response = self.client.get("/memory/some-id-that-does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Memory not found")

    def test_post_provider_switch_then_get_models_round_trip(self) -> None:
        switch = self.client.post("/memory/provider", json={"provider": "openai"})
        self.assertEqual(switch.status_code, 200)
        self.assertEqual(switch.json()["active_provider"], "openai")

        models = self.client.get("/memory/models")
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["active_provider"], "openai")


if __name__ == "__main__":
    unittest.main()
