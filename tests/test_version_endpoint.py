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

import io
import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import version
from app.config import config
from app.db import init_schema
from app.main import app


class _VersionEndpointCase(unittest.TestCase):
    """Shared fixture: unauthenticated by default, against a throwaway database."""

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


class VersionEndpointTests(_VersionEndpointCase):
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


class HandshakeBuildReportingTests(_VersionEndpointCase):
    """The handshake carries the extension's build so a reload alone identifies it.

    Before this, the build appeared only inside an audit record, and a turn writes those.
    After a reload with no turn yet, the newest audit still named the *previous* build -
    so the stamp could not answer the question it exists for. Measured 2026-09-21: the
    last audit read 29a6f53 while the server was serving 30a8ea5.
    """

    def test_a_reported_build_is_accepted_and_logged(self) -> None:
        with self.assertLogs("app.main", level="INFO") as logs:
            response = self.client.get("/memory/version?build=30a8ea5")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("30a8ea5" in line for line in logs.output), logs.output)

    def test_the_payload_is_unchanged_by_the_parameter(self) -> None:
        plain = self.client.get("/memory/version").json()
        with_build = self.client.get("/memory/version?build=30a8ea5").json()
        self.assertEqual(plain, with_build)

    def test_an_older_extension_sending_no_build_still_handshakes(self) -> None:
        # The whole point of the handshake is diagnosing a stale extension, so the
        # stale extension must not be the one that fails it.
        with self.assertLogs("app.main", level="INFO") as logs:
            response = self.client.get("/memory/version")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("not reported" in line for line in logs.output), logs.output)

    def test_it_stays_unauthenticated_with_the_parameter(self) -> None:
        config.API_KEY = "secret-key"
        response = self.client.get("/memory/version?build=30a8ea5")
        self.assertEqual(response.status_code, 200)

    def test_a_hostile_build_value_is_capped_not_echoed_whole(self) -> None:
        # Unauthenticated endpoint, so the value is attacker-controlled in principle;
        # it must not be able to flood the log with one request.
        with self.assertLogs("app.main", level="INFO") as logs:
            response = self.client.get("/memory/version?build=" + "A" * 5000)
        self.assertEqual(response.status_code, 200)
        logged = "".join(logs.output)
        self.assertNotIn("A" * 100, logged)
        self.assertIn("A" * 64, logged)


class AppLoggingReachesAStreamTests(unittest.TestCase):
    """`assertLogs` is not evidence that a log line is ever written.

    It installs its own handler for the duration, so it passes against a logger that has
    none - which is exactly what happened here. uvicorn configures only its own loggers,
    so `app.*` propagated to a bare root logger and everything below WARNING vanished.
    The handshake test above was green while data/server.log showed the access-log line
    and nothing beside it.

    So this asserts the plumbing rather than the message: `app` has a handler of its own,
    INFO is enabled, and propagation is off so a root handler cannot double the output.
    """

    def test_app_logger_has_its_own_handler(self) -> None:
        app_logger = logging.getLogger("app")
        self.assertTrue(app_logger.handlers, "app logger has no handler; INFO is dropped")

    def test_info_is_enabled_for_a_module_logger(self) -> None:
        self.assertTrue(logging.getLogger("app.main").isEnabledFor(logging.INFO))

    def test_records_do_not_also_propagate_to_root(self) -> None:
        self.assertFalse(logging.getLogger("app").propagate)

    def test_an_info_record_actually_reaches_a_stream(self) -> None:
        # The end-to-end version: write through the real handler chain and read it back.
        stream = io.StringIO()
        app_logger = logging.getLogger("app")
        handler = logging.StreamHandler(stream)
        app_logger.addHandler(handler)
        self.addCleanup(app_logger.removeHandler, handler)

        logging.getLogger("app.main").info("handshake probe %s", "abc1234")
        self.assertIn("abc1234", stream.getvalue())


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
