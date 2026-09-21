"""Links in the documentation point at things that exist.

Two kinds, both of which rot silently. A markdown link breaks when a file is renamed -
three reports were renamed on 2026-09-21 and only a hand-run check caught that the root
README and the index both had to follow. A backtick path breaks when code moves, and
nothing renders it as a link at all, so nobody ever clicks it and finds out.

Deliberately offline. This runs in CI, external URLs cannot be reached from every machine
that runs it - `docs.sillytavern.app` resolves correctly to GitHub Pages here and still
resets the connection, because this network drops GitHub Pages wholesale - and a test that
fails on someone else's network is a test people learn to ignore.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".pytest_cache", ".git", "node_modules"}

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# A repo path in backticks. Anchored on the top-level directories this project actually
# has, so prose like `some/path` in an example cannot masquerade as a reference.
BACKTICK_PATH = re.compile(
    r"`((?:app|scripts|config|tests|docs|sillytavern-extension|data)"
    r"/[A-Za-z0-9_./-]+\.(?:py|mjs|js|yaml|yml|json|md|html|log|jsonl|sh|db))`"
)

# Paths named precisely because they are absent. Each documents a fact about the system,
# and removing the mention would remove the fact.
DOCUMENTED_ABSENCES = {
    # The JSON vector store, replaced by the SQLite backend. The audit cites its absence
    # as the reason a swallowed delete in _chroma_delete is dormant rather than live.
    "data/vectors.json",
    # Written at runtime by vector_store.KEYS_FILE when a Google key rotates. The plan
    # names it to argue it belongs in .gitignore before it ever appears - which it now is.
    "data/google_keys.json",
}


def _documents():
    for path in ROOT.rglob("*.md"):
        if not any(part in SKIP_DIRS for part in path.parts):
            yield path


class MarkdownLinkTests(unittest.TestCase):
    def test_every_relative_link_resolves(self) -> None:
        checked = 0
        for doc in _documents():
            for target in MARKDOWN_LINK.findall(doc.read_text(encoding="utf-8", errors="ignore")):
                target = target.split(" ")[0].strip()
                if target.startswith(("http://", "https://", "mailto:", "#")) or not target:
                    continue
                checked += 1
                resolved = (doc.parent / target.partition("#")[0]).resolve()
                with self.subTest(document=str(doc.relative_to(ROOT)), link=target):
                    self.assertTrue(resolved.exists(), "link points at nothing")
        self.assertGreater(checked, 20, "the link regex stopped matching - check it before trusting a pass")


class BacktickPathTests(unittest.TestCase):
    def test_every_quoted_repo_path_exists(self) -> None:
        checked = 0
        for doc in _documents():
            for ref in sorted(set(BACKTICK_PATH.findall(doc.read_text(encoding="utf-8", errors="ignore")))):
                if ref in DOCUMENTED_ABSENCES:
                    continue
                checked += 1
                with self.subTest(document=str(doc.relative_to(ROOT)), path=ref):
                    self.assertTrue((ROOT / ref).exists(), "documented path does not exist")
        self.assertGreater(checked, 50, "the path regex stopped matching - check it before trusting a pass")

    def test_the_absence_list_is_still_about_absent_files(self) -> None:
        # If one of these ever appears, the documents that explain why it is missing
        # become wrong, and the exemption would hide exactly that.
        for ref in DOCUMENTED_ABSENCES:
            with self.subTest(path=ref):
                self.assertFalse(
                    (ROOT / ref).exists(),
                    "this file now exists - the docs that call it absent need updating, "
                    "and it should come off the exemption list",
                )

    def test_every_exemption_is_actually_mentioned_somewhere(self) -> None:
        # An exemption for a path nobody mentions any more is dead weight that would
        # silently excuse a future real break.
        mentions = {ref: [] for ref in DOCUMENTED_ABSENCES}
        for doc in _documents():
            text = doc.read_text(encoding="utf-8", errors="ignore")
            for ref in DOCUMENTED_ABSENCES:
                if ref in text:
                    mentions[ref].append(doc.name)
        for ref, found in mentions.items():
            with self.subTest(path=ref):
                # Asserting on the list, not on the corpus: `assertIn(ref, whole_text)`
                # prints the entire concatenated documentation on failure - 325 KB of it,
                # measured - which buries the one line that matters.
                self.assertNotEqual(found, [], f"nothing mentions {ref}; drop the exemption")


if __name__ == "__main__":
    unittest.main()
