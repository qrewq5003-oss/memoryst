"""Trackers live in `memories` for storage reasons but are not memories.

Every read of that table has to say so explicitly, which means the default for any
new query is *wrong* until its author remembers the filter. That is how
list_chat_group_summaries ended up as the one query without it - counting trackers
into the sidebar's total_count and, since trackers carry layer='stable', into
stable_count as well.

Two kinds of test here:

  - behavioural, against a real SQLite database, for the queries that back the UI.
    The existing UI tests mock these out and rebuild the aggregation in Python,
    where no tracker exists, so they could never have caught this.
  - a reflective invariant over memory_repo itself, so the next query added to the
    module cannot quietly reopen the hole.
"""
import ast
import inspect
import tempfile
import unittest
from pathlib import Path

from app.config import config
from app.db import init_schema
from app.repositories import memory_repo
from app.repositories.memory_repo import (
    create_memory,
    list_chat_group_summaries,
    list_retrieval_candidates,
    upsert_tracker,
)
from app.schemas import CreateMemoryRequest, MemoryMetadata

CHAT_ID = "chat-invariant"
CHARACTER_ID = "20"

# Functions that read `memories` and must exclude trackers, with the exceptions
# stated rather than assumed. Adding a reader to memory_repo without listing it here
# fails test_every_memories_reader_is_accounted_for.
TRACKER_EXCLUDING_READERS = {
    "list_memories",
    "list_retrieval_candidates",
    "list_chat_group_summaries",
    "list_ui_filtered_memories",
    "find_memory_by_normalized_content",
}
TRACKER_READERS_BY_DESIGN = {
    "get_tracker",
    "list_trackers",
}
# Addressed by primary key, so they hold no opinion about type: the caller already
# knows which id it asked for. Callers that must not act on a tracker check the type
# themselves (summary_service._resolve_explicit_sources), and one deliberately does
# act on them - chat_cleanup_service deletes a chat's trackers by id.
BY_ID_OPERATIONS = {"get_memory_by_id", "delete_memory"}


class TrackerSidebarCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _make_memory(self, content: str, *, layer: str = "episodic", memory_type: str = "event"):
        return create_memory(
            CreateMemoryRequest(
                chat_id=CHAT_ID,
                character_id=CHARACTER_ID,
                type=memory_type,
                content=content,
                source="manual",
                layer=layer,
                importance=0.7,
                metadata=MemoryMetadata(),
            )
        )

    def _make_tracker(self, tracker_type: str = "timeline"):
        item, _ = upsert_tracker(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tracker_type=tracker_type,
            content="- Thursday, February 13, 2025 - Milan: arrived.",
            metadata=MemoryMetadata(),
        )
        return item

    def test_trackers_are_absent_from_the_sidebar_counts(self) -> None:
        self._make_tracker("timeline")
        self._make_tracker("relationship")
        self._make_memory("She likes espresso.", layer="episodic")
        self._make_memory("She is a violinist.", layer="stable")

        groups = list_chat_group_summaries()

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["total_count"], 2)
        # Trackers carry layer='stable', so this is where they used to hide.
        self.assertEqual(group["stable_count"], 1)
        self.assertEqual(group["episodic_count"], 1)

    def test_a_chat_holding_only_trackers_still_lists_with_zero_counts(self) -> None:
        """Excluding trackers from the counts is not the same as excluding them from
        the grouping. Dropping the rows entirely made a tracker-only chat vanish from
        the sidebar, and its trackers unreachable - the UI renders that section only
        for a selected chat. Counted as zero, listed all the same."""
        self._make_tracker("timeline")

        groups = list_chat_group_summaries()

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total_count"], 0)
        self.assertEqual(groups[0]["stable_count"], 0)
        self.assertEqual(groups[0]["tracker_count"], 1)

    def test_the_counts_always_add_up_to_the_total(self) -> None:
        """Guards the json_extract NULL semantics: a row whose metadata lacks
        is_summary would be counted in total_count while `NULL != 1` kept it out of
        both stable_count and episodic_count."""
        self._make_tracker("timeline")
        self._make_memory("Episodic one.", layer="episodic")
        self._make_memory("Stable one.", layer="stable")
        self._make_memory("A summary.", layer="stable", memory_type="summary")
        self._strip_is_summary_from_metadata()

        group = list_chat_group_summaries()[0]

        self.assertEqual(
            group["summary_count"] + group["stable_count"] + group["episodic_count"],
            group["total_count"],
        )

    def _strip_is_summary_from_metadata(self) -> None:
        """Rewrite metadata_json without the is_summary key, as a row written outside
        the pydantic path would look."""
        from app.db import get_connection

        with get_connection() as conn:
            conn.execute(
                "UPDATE memories SET metadata_json = json_remove(metadata_json, '$.is_summary') "
                "WHERE type != 'summary'"
            )
            conn.commit()

    def test_retrieval_candidates_exclude_trackers_against_a_real_database(self) -> None:
        """The retrieval eval harness patches list_retrieval_candidates out entirely,
        so candidate selection is never exercised there - only scoring is."""
        self._make_tracker("timeline")
        self._make_memory("She likes espresso.")

        candidates = list_retrieval_candidates(CHAT_ID, CHARACTER_ID)

        self.assertEqual([c.content for c in candidates], ["She likes espresso."])

    def test_archived_memories_are_excluded_from_candidates(self) -> None:
        """Also only ever covered through the mock until now."""
        from app.repositories.memory_repo import set_archived

        kept = self._make_memory("Visible fact.")
        hidden = self._make_memory("Archived fact.")
        set_archived(hidden.id, True)

        candidates = list_retrieval_candidates(CHAT_ID, CHARACTER_ID)

        self.assertEqual([c.id for c in candidates], [kept.id])


class TrackerIsolationInvariantTests(unittest.TestCase):
    """Reflective guard over memory_repo, so a newly added query can't reopen this."""

    def _reader_names(self) -> set[str]:
        """Public functions in memory_repo whose body selects from `memories`."""
        source = Path(inspect.getfile(memory_repo)).read_text()
        tree = ast.parse(source)
        readers = set()
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            body = ast.get_source_segment(source, node) or ""
            if "FROM memories" in body:
                readers.add(node.name)
        return readers

    def _function_body(self, name: str) -> str:
        source = Path(inspect.getfile(memory_repo)).read_text()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(source, node) or ""
        raise AssertionError(f"{name} not found in memory_repo")

    def test_every_memories_reader_is_accounted_for(self) -> None:
        known = TRACKER_EXCLUDING_READERS | TRACKER_READERS_BY_DESIGN | BY_ID_OPERATIONS
        unaccounted = self._reader_names() - known

        self.assertEqual(
            unaccounted,
            set(),
            "new query against `memories` - decide whether it must exclude "
            "type='tracker' and add it to the matching set in this test",
        )

    def test_the_invariant_list_has_not_gone_stale(self) -> None:
        """A name removed from memory_repo must not linger here, or the sets stop
        describing the module and the guard rots into decoration."""
        declared = TRACKER_EXCLUDING_READERS | TRACKER_READERS_BY_DESIGN | BY_ID_OPERATIONS
        self.assertEqual(declared - self._reader_names(), set())

    def test_readers_that_must_exclude_trackers_actually_do(self) -> None:
        for name in sorted(TRACKER_EXCLUDING_READERS):
            with self.subTest(function=name):
                self.assertIn(
                    "type != 'tracker'",
                    self._function_body(name),
                    f"{name} reads `memories` without excluding trackers",
                )

    def test_the_deliberate_tracker_readers_select_trackers_only(self) -> None:
        for name in sorted(TRACKER_READERS_BY_DESIGN):
            with self.subTest(function=name):
                self.assertIn("type = 'tracker'", self._function_body(name))


if __name__ == "__main__":
    unittest.main()
