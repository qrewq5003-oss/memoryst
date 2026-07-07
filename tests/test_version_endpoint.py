"""
Tests for the /memory/version compatibility handshake endpoint.

The endpoint lets the SillyTavern extension detect a stale/incompatible
pairing (e.g. a broken symlink into public/ leaving an old extension against an
updated backend). It must:
  - report protocol_version, service_version, git_commit;
  - stay unauthenticated even when API_KEY is set (it is a diagnostic handshake
    carrying no sensitive data, and a misconfigured key is itself a likely
    symptom of a stale extension);
  - not be shadowed by the /memory/{id} catch-all route.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import version
from app.config import config
from app.db import init_schema
from app.main import app


class VersionEndpointTests(unittest.TestCase):
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

    def test_version_payload_shape(self) -> None:
        response = self.client.get("/memory/version")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["service_version"], version.SERVICE_VERSION)
        self.assertEqual(body["protocol_version"], version.PROTOCOL_VERSION)
        self.assertIn("git_commit", body)  # value may be None off a git checkout

    def test_version_is_unauthenticated_even_with_api_key_set(self) -> None:
        config.API_KEY = "secret-key"
        # No X-API-Key header supplied - a protected /memory route would 401.
        response = self.client.get("/memory/version")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["protocol_version"], version.PROTOCOL_VERSION)

    def test_version_not_shadowed_by_id_route(self) -> None:
        # Must return the version payload, not a 404 "Memory not found" from
        # get_memory_endpoint("version").
        response = self.client.get("/memory/version")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("detail", response.json())


class GitCommitReaderTests(unittest.TestCase):
    def test_returns_none_outside_git_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(version._read_git_commit(Path(tmp)))

    def test_reads_loose_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_dir = root / ".git"
            (git_dir / "refs" / "heads").mkdir(parents=True)
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (git_dir / "refs" / "heads" / "main").write_text(
                "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8"
            )
            self.assertEqual(version._read_git_commit(root), "abcdef123456")

    def test_reads_packed_ref_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (git_dir / "packed-refs").write_text(
                "# pack-refs with: peeled fully-peeled sorted\n"
                "abcdef1234567890abcdef1234567890abcdef12 refs/heads/main\n",
                encoding="utf-8",
            )
            self.assertEqual(version._read_git_commit(root), "abcdef123456")

    def test_reads_detached_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "HEAD").write_text(
                "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8"
            )
            self.assertEqual(version._read_git_commit(root), "abcdef123456")


if __name__ == "__main__":
    unittest.main()
