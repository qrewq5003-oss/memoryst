"""The `extension` branch is what SillyTavern's Install button clones.

It is a publication, not a source of truth, so the thing that can go wrong is drift:
the extension changes, the branch does not, and a user installs a build that stopped
existing weeks ago. These tests pin the two properties that make drift detectable and
the one that makes the install work at all - manifest.json at the root.
"""

import json
import unittest
from pathlib import Path

from scripts.publish_extension_branch import (
    BRANCH,
    EXTENSION_DIR,
    PUBLISHED_SUFFIXES,
    branch_exists,
    diff_against_branch,
    published_files,
)


class PublishedPayloadTests(unittest.TestCase):
    def test_manifest_lands_at_the_branch_root(self) -> None:
        # The whole reason the branch exists: getManifest reads manifest.json from the
        # clone root, and in this repo it is one directory down.
        self.assertIn(Path("manifest.json"), published_files())

    def test_every_file_the_manifest_names_is_published(self) -> None:
        manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
        names = {str(path) for path in published_files()}
        for key in ("js", "css"):
            with self.subTest(key=key):
                self.assertIn(manifest[key], names)

    def test_every_module_the_entry_point_can_reach_is_published(self) -> None:
        # main.mjs is loaded by index.js and imports the rest. A module left behind
        # would only fail in the browser, at load time, for someone else.
        names = {str(path) for path in published_files()}
        for module in EXTENSION_DIR.glob("*.mjs"):
            with self.subTest(module=module.name):
                self.assertIn(module.name, names)

    def test_backend_files_are_not_published(self) -> None:
        # An install copies this into SillyTavern's tree; Python has no business there.
        for path in published_files():
            with self.subTest(path=path):
                self.assertIn(path.suffix, PUBLISHED_SUFFIXES)
                self.assertNotEqual(path.suffix, ".py")

    def test_the_published_set_is_the_whole_extension_directory(self) -> None:
        # Guards the other direction: a new file added to the extension must either be
        # published or have a suffix this deliberately excludes, never be forgotten.
        on_disk = {
            path.name
            for path in EXTENSION_DIR.iterdir()
            if path.is_file() and path.suffix in PUBLISHED_SUFFIXES
        }
        self.assertEqual(on_disk, {str(path) for path in published_files()})


class BranchFreshnessTests(unittest.TestCase):
    def test_the_branch_is_in_sync_with_the_sources(self) -> None:
        if not branch_exists():
            self.skipTest(f"branch '{BRANCH}' does not exist in this clone")

        differences = diff_against_branch()
        self.assertEqual(
            differences,
            [],
            f"branch '{BRANCH}' is stale; run "
            "`python -m scripts.publish_extension_branch --push` after committing",
        )


if __name__ == "__main__":
    unittest.main()
