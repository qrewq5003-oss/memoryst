"""The mirror must not keep what the mirror excludes.

Found 2026-09-21 on the live mirror: `/storage/emulated/0/Download/memoryst/.env` had been
on Android shared storage - readable by any app holding the storage permission - since
2026-06-28, holding a Google API key that still answered 200 when tested. Next to it sat a
copy of memory.db, a chromadb directory, and 670 MB of virtualenvs. The mirror was 736 MB
of which about 3 MB was the source it exists to show.

Nothing was copying those: the exclusion list had been correct for months. The delete loop
was skipping them. Anything copied *before* an exclusion was added became permanent - the
copy loop stopped refreshing it and the delete loop refused to remove it, so the only
trace was on a filesystem nobody reads.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "mirror_to_downloads",
    Path(__file__).resolve().parent.parent / "scripts" / "mirror_to_downloads.py",
)
mirror = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mirror)


class MirrorPurgesExcludedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = Path(self.tmp.name) / "src"
        self.dst = Path(self.tmp.name) / "dst"
        (self.src / "app").mkdir(parents=True)
        (self.src / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")

    def test_a_secret_left_by_an_older_run_is_removed(self) -> None:
        # The exact incident: .env copied before it was excluded, then stranded.
        self.dst.mkdir()
        (self.dst / ".env").write_text("GOOGLE_API_KEYS=AIza-live-key\n", encoding="utf-8")

        mirror.sync(str(self.src), str(self.dst))

        self.assertFalse((self.dst / ".env").exists(), "the stranded .env survived the sync")

    def test_excluded_directories_are_removed_too(self) -> None:
        self.dst.mkdir()
        for name in (".venv", "data", "__pycache__"):
            (self.dst / name).mkdir()
            (self.dst / name / "leftover").write_text("junk", encoding="utf-8")

        mirror.sync(str(self.src), str(self.dst))

        for name in (".venv", "data", "__pycache__"):
            self.assertFalse((self.dst / name).exists(), f"{name} survived the sync")

    def test_the_source_still_arrives(self) -> None:
        # The purge must not be so eager that it takes the mirror's actual job with it.
        mirror.sync(str(self.src), str(self.dst))

        self.assertEqual((self.dst / "app" / "main.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_an_excluded_name_in_the_source_is_still_never_copied(self) -> None:
        (self.src / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (self.src / ".venv").mkdir()

        mirror.sync(str(self.src), str(self.dst))

        self.assertFalse((self.dst / ".env").exists())
        self.assertFalse((self.dst / ".venv").exists())

    def test_a_file_the_source_dropped_is_still_removed(self) -> None:
        # The behaviour that already worked, pinned so the fix did not trade it away.
        self.dst.mkdir()
        (self.dst / "gone.md").write_text("old", encoding="utf-8")

        mirror.sync(str(self.src), str(self.dst))

        self.assertFalse((self.dst / "gone.md").exists())


if __name__ == "__main__":
    unittest.main()
