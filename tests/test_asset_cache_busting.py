"""A stylesheet the phone can never receive is worse than no stylesheet.

The service worker precached /static/styles.css and served it cache-first with
no revalidation, under a cache name that was hardcoded and never bumped. The
first stylesheet a device fetched was therefore the one it kept: CSS changes
could not reach it, and the activate-time cleanup could never fire because the
name never changed. It is the same trap the SillyTavern extension hit with
cached ES modules, which is why its imports carry a build stamp.
"""
import pathlib
import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.version import get_asset_version

STYLES = Path("app/static/styles.css")
SW = Path("app/static/sw.js")


class AssetVersionTests(unittest.TestCase):
    def test_the_fingerprint_follows_content(self) -> None:
        original = STYLES.read_bytes()
        self.addCleanup(STYLES.write_bytes, original)
        before = get_asset_version()

        STYLES.write_bytes(original + b"\n/* changed */\n")
        self.assertNotEqual(get_asset_version(), before)

    def test_the_fingerprint_ignores_a_mere_touch(self) -> None:
        """mtime changes on checkout and on touch. Busting the cache for a file
        that did not change costs a download every deploy for no reason."""
        original = STYLES.read_bytes()
        self.addCleanup(STYLES.write_bytes, original)
        before = get_asset_version()

        STYLES.write_bytes(original)  # same bytes, new mtime
        self.assertEqual(get_asset_version(), before)

    def test_a_missing_asset_does_not_raise(self) -> None:
        self.assertRegex(get_asset_version(), r"^[0-9a-f]{8}$")


class ServiceWorkerTests(unittest.TestCase):
    def test_the_stylesheet_is_not_precached_under_a_fixed_path(self) -> None:
        """Its URL carries a fingerprint, so a fixed path would cache an entry
        nothing ever requests - and would be the exact stale copy again."""
        source = SW.read_text()
        install = source[source.index("STATIC_ASSETS"):source.index("addEventListener('install'")]

        self.assertNotIn("styles.css", install)

    def test_static_requests_revalidate(self) -> None:
        """Cache-first with no refetch is what made this unrecoverable. Even an
        unversioned URL must self-heal on the next load."""
        source = SW.read_text()

        self.assertIn("cache.put", source)
        self.assertNotIn("cached || fetch(event.request)\n", source)


class RenderedPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(setattr, config, "DATABASE_PATH", self.original_db_path)
        init_schema()
        self.client = TestClient(app)

    def test_the_page_links_the_stylesheet_with_its_fingerprint(self) -> None:
        html = self.client.get("/ui").text

        match = re.search(r'href="/static/styles\.css\?v=([0-9a-f]{8})"', html)
        self.assertIsNotNone(match, "stylesheet link must carry a version query")
        self.assertEqual(match.group(1), get_asset_version())

    def test_the_stylesheet_is_still_served_with_the_query(self) -> None:
        response = self.client.get(f"/static/styles.css?v={get_asset_version()}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("--accent", response.text)


if __name__ == "__main__":
    unittest.main()
