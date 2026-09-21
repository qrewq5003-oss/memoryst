"""Every document says what it is, and the index lists every document.

`docs/` had grown to 22 files in three different header styles: audits opened with
"Дата аудита / Коммит / Модель", the entity-extraction finding with "**Дата:** /
**Статус:**", and the March measurement reports with a bare "Date: 2026-03-27" - while
eleven had no status line at all until 2026-09-21. The information was mostly there; the
form was not, so telling a policy that still applies from a report of one afternoon's
experiment meant opening the file and reading it.

That distinction is the one that has actually cost time here: `CLAUDE.md` spent six weeks
pointing at a closed audit as though it were a list of open work, and every session that
started from it was told to fix what was already fixed.
"""

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
KINDS = {"Аудит", "План работ", "Политика", "Находка", "Отчёт о замере", "Указатель"}
HEADER = re.compile(r"^> \*\*(?P<kind>[^*]+)\*\* · (?P<date>[^·]+) · (?P<status>.+)$")


def _documents():
    return sorted(p for p in DOCS.glob("*.md") if p.name != "README.md")


class DocumentHeaderTests(unittest.TestCase):
    def test_every_document_declares_its_kind_date_and_status(self) -> None:
        for path in _documents():
            with self.subTest(document=path.name):
                lines = path.read_text(encoding="utf-8").split("\n")
                self.assertTrue(lines[0].startswith("# "), "no title")
                match = HEADER.match(lines[2])
                self.assertIsNotNone(
                    match, f"line 3 is not a `> **Тип** · дата · статус` header: {lines[2]!r}"
                )
                self.assertIn(match.group("kind"), KINDS)
                self.assertRegex(match.group("date"), r"20\d\d-\d\d-\d\d")

    def test_a_measurement_report_says_it_is_history(self) -> None:
        # The failure this whole structure exists to prevent: reading a March experiment
        # as a description of how the system behaves now.
        for path in _documents():
            header = "\n".join(path.read_text(encoding="utf-8").split("\n")[2:6])
            if "**Отчёт о замере**" not in header:
                continue
            with self.subTest(document=path.name):
                self.assertIn("история", header)
                self.assertIn("не описание текущего", header)


class DocumentIndexTests(unittest.TestCase):
    def test_the_index_lists_every_document(self) -> None:
        index = (DOCS / "README.md").read_text(encoding="utf-8")
        linked = set(re.findall(r"\]\(([a-z0-9_.-]+\.md)\)", index))
        missing = {p.name for p in _documents()} - linked
        self.assertEqual(missing, set(), "a document exists that the index never mentions")

    def test_the_index_has_no_dead_links(self) -> None:
        index = (DOCS / "README.md").read_text(encoding="utf-8")
        linked = set(re.findall(r"\]\(([a-z0-9_.-]+\.md)\)", index))
        dead = {name for name in linked if not (DOCS / name).exists()}
        self.assertEqual(dead, set())

    def test_the_index_points_at_the_current_state_rather_than_holding_it(self) -> None:
        # The index is navigation. Architecture lives in CLAUDE.md, and saying so here
        # is what stops this file from slowly becoming a second, stale copy of it.
        index = (DOCS / "README.md").read_text(encoding="utf-8")
        self.assertIn("CLAUDE.md", index)


if __name__ == "__main__":
    unittest.main()
