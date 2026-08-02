"""
HTTP-level smoke test for the /ui page.

Every other test_ui_*.py file calls `ui_memories_page`/`_render_memories_page`
directly as a Python function, or unit-tests a helper in isolation - none of
them go through the real ASGI app + TestClient, so none of them actually
render app/templates/memories.html end-to-end the way a browser hitting /ui
does. That gap let a template referencing a context variable ui.py doesn't
provide (or vice versa) go unnoticed: Jinja2Templates auto-reloads .html
files from disk on every render, but a long-running server process only
loads routes/ui.py once at startup, so the two can silently drift apart
across a deploy that changes both. A real TestClient request exercises the
same Jinja render path a live server does and would fail loudly the same way.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app


class UiHttpSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_key = config.API_KEY
        self.original_db_path = config.DATABASE_PATH
        self.addCleanup(self._restore_config)

        config.API_KEY = ""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        init_schema()

        self.client = TestClient(app)

    def _restore_config(self) -> None:
        config.API_KEY = self.original_api_key
        config.DATABASE_PATH = self.original_db_path

    def test_ui_root_renders(self) -> None:
        response = self.client.get("/ui/tools")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Consolidation", response.text)
        self.assertIn("Инструменты разработчика", response.text)

    def test_ui_with_selected_chat_renders(self) -> None:
        response = self.client.get(
            "/ui",
            params={
                "selected_chat_id": "smoke-test-chat",
                "selected_character_id": "smoke-test-char",
                "sort": "updated_desc",
                "limit": 50,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Consolidation", response.text)


if __name__ == "__main__":
    unittest.main()
