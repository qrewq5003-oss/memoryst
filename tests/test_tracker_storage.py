import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import get_connection, init_schema
from app.repositories.memory_repo import (
    create_memory,
    find_memory_by_normalized_content,
    get_memory_by_id,
    get_tracker,
    list_memories,
    list_retrieval_candidates,
    list_trackers,
    list_ui_filtered_memories,
    upsert_tracker,
)
from app.schemas import CreateMemoryRequest, MemoryMetadata, MessageInput, StoreMemoryRequest
from app.services.store_service import store_memories
from app.services.summary_service import generate_tiered_consolidation

CHAT_ID = "chat-trackers"
CHARACTER_ID = "20"

# Shape of `memories` before the tracker type existed. Used to build a database that
# looks like one written by the previous release.
LEGACY_MEMORIES_TABLE_SQL = """
    CREATE TABLE memories (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        character_id TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('profile', 'relationship', 'event', 'summary')),
        content TEXT NOT NULL,
        normalized_content TEXT NOT NULL,

        source TEXT NOT NULL CHECK (source IN ('auto', 'manual')),
        layer TEXT NOT NULL CHECK (layer IN ('episodic', 'stable')),

        importance REAL NOT NULL DEFAULT 0.5,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_accessed_at TEXT,
        access_count INTEGER NOT NULL DEFAULT 0,

        pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
        archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),

        metadata_json TEXT NOT NULL
    )
"""

# Shape from before `summary` was a type either - the oldest database still in the wild.
PRE_SUMMARY_MEMORIES_TABLE_SQL = LEGACY_MEMORIES_TABLE_SQL.replace(
    "CHECK (type IN ('profile', 'relationship', 'event', 'summary'))",
    "CHECK (type IN ('profile', 'relationship', 'event'))",
)


class TrackerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = self.db_path
        self.addCleanup(self._restore_db_path)

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _make_tracker(
        self,
        tracker_type: str = "timeline",
        content: str = "- Thursday, February 13, 2025, 7:45 PM - Milan: arrived.",
        *,
        chat_id: str = CHAT_ID,
        character_id: str = CHARACTER_ID,
        last_sequence_index: int = 12,
    ):
        item, created = upsert_tracker(
            chat_id=chat_id,
            character_id=character_id,
            tracker_type=tracker_type,
            content=content,
            metadata=MemoryMetadata(
                tracker_generated_at="2026-07-13T10:00:00Z",
                tracker_last_sequence_index=last_sequence_index,
                tracker_entries=[{"date": "Thursday, February 13, 2025", "summary": "arrived"}],
            ),
        )
        return item, created

    def _make_memory(
        self,
        content: str,
        memory_type: str = "event",
        layer: str = "episodic",
        **kwargs,
    ):
        return create_memory(
            CreateMemoryRequest(
                chat_id=CHAT_ID,
                character_id=CHARACTER_ID,
                type=memory_type,
                content=content,
                source="auto",
                layer=layer,
                metadata=MemoryMetadata(**kwargs),
            )
        )

    def _count_rows(self, where: str = "1=1") -> int:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM memories WHERE {where}")
            return cursor.fetchone()[0]


class TrackerMigrationTests(TrackerTestCase):
    def test_fresh_database_accepts_tracker_type_and_has_unique_index(self) -> None:
        init_schema()

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
            )
            self.assertIn("'tracker'", cursor.fetchone()[0])

            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name = 'idx_memories_tracker_unique'"
            )
            self.assertIsNotNone(cursor.fetchone())

    def test_legacy_database_migrates_and_keeps_existing_rows(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(LEGACY_MEMORIES_TABLE_SQL)
        conn.execute(
            """
            INSERT INTO memories (
                id, chat_id, character_id, type, content, normalized_content,
                source, layer, importance, created_at, updated_at,
                last_accessed_at, access_count, pinned, archived, metadata_json
            ) VALUES (?, ?, ?, 'event', 'she arrived in Milan', 'she arrived in milan',
                      'auto', 'episodic', 0.5, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z',
                      NULL, 0, 0, 0, '{}')
            """,
            ("legacy-1", CHAT_ID, CHARACTER_ID),
        )
        conn.commit()
        conn.close()

        init_schema()

        surviving = get_memory_by_id("legacy-1")
        self.assertIsNotNone(surviving)
        self.assertEqual(surviving.content, "she arrived in Milan")

        item, created = self._make_tracker()
        self.assertTrue(created)
        self.assertEqual(item.type, "tracker")

    def test_pre_summary_database_gets_both_types_in_one_pass(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(PRE_SUMMARY_MEMORIES_TABLE_SQL)
        conn.commit()
        conn.close()

        init_schema()

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
            )
            table_sql = cursor.fetchone()[0]
        self.assertIn("'summary'", table_sql)
        self.assertIn("'tracker'", table_sql)

    def test_migration_is_idempotent(self) -> None:
        init_schema()
        self._make_tracker()
        init_schema()

        self.assertEqual(self._count_rows("type = 'tracker'"), 1)


class TrackerUniquenessTests(TrackerTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_schema()

    def test_second_tracker_of_same_type_is_rejected_by_the_database(self) -> None:
        self._make_tracker()

        with self.assertRaises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO memories (
                        id, chat_id, character_id, type, content, normalized_content,
                        source, layer, importance, created_at, updated_at,
                        last_accessed_at, access_count, pinned, archived, metadata_json
                    ) VALUES (?, ?, ?, 'tracker', 'rival timeline', 'rival timeline',
                              'auto', 'stable', 0.5, '2026-07-13T00:00:00Z',
                              '2026-07-13T00:00:00Z', NULL, 0, 0, 0,
                              '{"tracker_type": "timeline"}')
                    """,
                    (str(uuid.uuid4()), CHAT_ID, CHARACTER_ID),
                )
                conn.commit()

    def test_different_tracker_types_coexist(self) -> None:
        self._make_tracker("timeline")
        self._make_tracker("relationship")
        self._make_tracker("npc_whoswho")
        self._make_tracker("character_pov_notes")

        self.assertEqual(self._count_rows("type = 'tracker'"), 4)
        self.assertEqual(len(list_trackers(CHAT_ID, CHARACTER_ID)), 4)

    def test_same_tracker_type_coexists_across_scopes(self) -> None:
        self._make_tracker("timeline")
        self._make_tracker("timeline", chat_id="other-chat")
        self._make_tracker("timeline", character_id="18")

        self.assertEqual(self._count_rows("type = 'tracker'"), 3)
        self.assertEqual(len(list_trackers(CHAT_ID, CHARACTER_ID)), 1)


class UpsertTrackerTests(TrackerTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_schema()

    def test_update_rewrites_in_place_rather_than_accumulating(self) -> None:
        first, created = self._make_tracker(content="- Feb 13: arrived.")
        self.assertTrue(created)

        second, created_again = self._make_tracker(
            content="- Feb 13: arrived.\n- Feb 14: left.",
            last_sequence_index=40,
        )

        self.assertFalse(created_again)
        self.assertEqual(second.id, first.id)
        self.assertEqual(self._count_rows("type = 'tracker'"), 1)
        self.assertEqual(second.content, "- Feb 13: arrived.\n- Feb 14: left.")
        self.assertEqual(second.metadata.tracker_last_sequence_index, 40)

    def test_long_document_survives_the_update_memory_content_cap(self) -> None:
        # UpdateMemoryRequest caps content at 5000 chars; a timeline outgrows that, which
        # is exactly why upsert_tracker does not route through update_memory().
        self._make_tracker(content="- Feb 13: arrived.")
        long_content = "- Feb 13: arrived. " * 400
        self.assertGreater(len(long_content), 5000)

        item, created = self._make_tracker(content=long_content)

        self.assertFalse(created)
        self.assertEqual(item.content, long_content)
        self.assertEqual(get_tracker(CHAT_ID, CHARACTER_ID, "timeline").content, long_content)

    def test_metadata_tracker_type_is_forced_to_match_the_argument(self) -> None:
        item, _ = upsert_tracker(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tracker_type="relationship",
            content="Affinity 72.",
            metadata=MemoryMetadata(tracker_type="timeline"),
        )

        self.assertEqual(item.metadata.tracker_type, "relationship")
        self.assertIsNotNone(get_tracker(CHAT_ID, CHARACTER_ID, "relationship"))
        self.assertIsNone(get_tracker(CHAT_ID, CHARACTER_ID, "timeline"))

    def test_tracker_rows_carry_the_stable_layer(self) -> None:
        item, _ = self._make_tracker()
        self.assertEqual(item.layer, "stable")
        self.assertEqual(item.source, "auto")

    def test_get_tracker_is_scoped(self) -> None:
        self._make_tracker("timeline")

        self.assertIsNotNone(get_tracker(CHAT_ID, CHARACTER_ID, "timeline"))
        self.assertIsNone(get_tracker(CHAT_ID, CHARACTER_ID, "npc_whoswho"))
        self.assertIsNone(get_tracker("other-chat", CHARACTER_ID, "timeline"))
        self.assertIsNone(get_tracker(CHAT_ID, "18", "timeline"))

    def test_upsert_does_not_write_to_the_vector_store(self) -> None:
        with patch("app.services.vector_store.add_memory") as add_memory:
            self._make_tracker()
            self._make_tracker(content="updated")

        add_memory.assert_not_called()


class TrackerIsolationTests(TrackerTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_schema()

    def test_tracker_is_not_a_retrieval_candidate(self) -> None:
        self._make_tracker()
        self._make_memory("She likes espresso.")

        candidates = list_retrieval_candidates(CHAT_ID, CHARACTER_ID)

        self.assertEqual([c.content for c in candidates], ["She likes espresso."])
        self.assertNotIn("tracker", {c.type for c in candidates})

    def test_list_memories_hides_trackers_unless_explicitly_asked(self) -> None:
        self._make_tracker()
        self._make_memory("She likes espresso.")

        default = list_memories(chat_id=CHAT_ID, character_id=CHARACTER_ID)
        self.assertEqual(default.total, 1)
        self.assertNotIn("tracker", {i.type for i in default.items})

        including = list_memories(
            chat_id=CHAT_ID, character_id=CHARACTER_ID, include_trackers=True
        )
        self.assertEqual(including.total, 2)
        self.assertIn("tracker", {i.type for i in including.items})

    def test_tracker_is_not_a_ui_card_or_consolidation_source(self) -> None:
        self._make_tracker()
        self._make_memory("She likes espresso.")

        listing = list_ui_filtered_memories(chat_id=CHAT_ID, character_id=CHARACTER_ID)

        self.assertEqual(listing.total, 1)
        self.assertNotIn("tracker", {i.type for i in listing.items})

    def test_exact_content_match_never_resolves_to_a_tracker(self) -> None:
        # The real hole this closes: find_memory_by_normalized_content did not filter by
        # type, so an extracted fact whose normalized text happened to equal a tracker's
        # would resolve to the tracker row and overwrite the document.
        shared = "She arrived in Milan."
        self._make_tracker(content=shared)

        found = find_memory_by_normalized_content(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            normalized_content="she arrived in milan.",
        )

        self.assertIsNone(found)

    def test_store_does_not_overwrite_a_tracker_with_an_extracted_fact(self) -> None:
        # The candidate is injected rather than extracted from text: the regex pre-filter
        # finds no signal in a bare sentence, so a "realistic" message would extract
        # nothing and the test would pass without ever reaching the dedup path it guards.
        collision = "She likes espresso."
        tracker, _ = self._make_tracker(content=collision)

        candidate = CreateMemoryRequest(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            type="profile",
            content=collision,
            source="auto",
            layer="episodic",
            metadata=MemoryMetadata(entities=["espresso"], keywords=["likes", "espresso"]),
        )

        with patch(
            "app.services.store_service.extract_scene_memories",
            return_value=([candidate], "regex_fallback"),
        ):
            result = store_memories(
                StoreMemoryRequest(
                    chat_id=CHAT_ID,
                    character_id=CHARACTER_ID,
                    messages=[MessageInput(role="user", text=collision)],
                )
            )

        # The fact lands as its own new memory instead of resolving onto the tracker.
        self.assertEqual(result.stored, 1)
        self.assertEqual(result.updated, 0)

        after = get_memory_by_id(tracker.id)
        self.assertEqual(after.content, collision)
        self.assertEqual(after.type, "tracker")
        self.assertEqual(after.metadata.tracker_type, "timeline")
        self.assertEqual(after.metadata.tracker_last_sequence_index, 12)
        self.assertEqual(after.metadata.tracker_entries, [
            {"date": "Thursday, February 13, 2025", "summary": "arrived"}
        ])
        self.assertEqual(self._count_rows("type = 'tracker'"), 1)

    def test_consolidation_ignores_trackers_even_when_forced_as_a_source(self) -> None:
        tracker, _ = self._make_tracker()
        episodic = self._make_memory("She arrived in Milan.")

        result = generate_tiered_consolidation(
            chat_id=CHAT_ID,
            character_id=CHARACTER_ID,
            tier="arc",
            source_ids=[tracker.id, episodic.id],
        )

        self.assertNotIn(tracker.id, result.source_memory_ids)

        after = get_memory_by_id(tracker.id)
        self.assertEqual(after.type, "tracker")
        self.assertIsNone(after.metadata.review_status)
        self.assertFalse(after.archived)


class TrackerTypeValidationTests(TrackerTestCase):
    """An unknown tracker_type used to be written and only fail on the way back out.

    upsert_tracker forces metadata.tracker_type through model_copy(), which does not
    validate, so the bad value reached SQLite intact. MemoryMetadata then rejected it on
    read, and since _row_to_memory_item validates every row, one poisoned write broke
    every subsequent read of that whole chat - list_memories included - with a pydantic
    error naming the field but neither the row nor the writer.
    """

    def setUp(self) -> None:
        super().setUp()
        init_schema()

    def test_unknown_tracker_type_is_rejected_at_the_write(self) -> None:
        with self.assertRaises(ValueError) as caught:
            upsert_tracker(
                chat_id=CHAT_ID,
                character_id=CHARACTER_ID,
                tracker_type="relationships",  # plural: the real typo that found this
                content="- something",
                metadata=MemoryMetadata(),
            )

        message = str(caught.exception)
        self.assertIn("relationships", message)
        self.assertIn("relationship", message)

    def test_a_rejected_write_leaves_the_chat_readable(self) -> None:
        self._make_memory("She likes espresso.")
        with self.assertRaises(ValueError):
            upsert_tracker(
                chat_id=CHAT_ID,
                character_id=CHARACTER_ID,
                tracker_type="not_a_tracker",
                content="- something",
                metadata=MemoryMetadata(),
            )

        # The whole point: the failed write must not have landed, so reads still work.
        listing = list_memories(chat_id=CHAT_ID, character_id=CHARACTER_ID, include_trackers=True)
        self.assertEqual([i.content for i in listing.items], ["She likes espresso."])
        self.assertEqual(list_trackers(CHAT_ID, CHARACTER_ID), [])

    def test_every_declared_tracker_type_is_accepted(self) -> None:
        from app.services.tracker_prompts import TRACKER_TYPES

        for tracker_type in TRACKER_TYPES:
            with self.subTest(tracker_type=tracker_type):
                item, created = upsert_tracker(
                    chat_id=CHAT_ID,
                    character_id=CHARACTER_ID,
                    tracker_type=tracker_type,
                    content=f"- {tracker_type} content",
                    metadata=MemoryMetadata(),
                )
                self.assertTrue(created)
                self.assertEqual(item.metadata.tracker_type, tracker_type)

        self.assertEqual(len(list_trackers(CHAT_ID, CHARACTER_ID)), len(TRACKER_TYPES))

    def test_tracker_type_is_still_the_only_constrained_field_inside_metadata(self) -> None:
        """A tripwire, not a proof.

        Constrained values stored as *columns* - type, source, layer, pinned, archived -
        are covered by SQLite CHECK constraints, so a bad one fails loudly at the write.
        Nothing guards the inside of metadata_json, which is why tracker_type was the one
        field that could be written happily and then break every read of the chat.

        It is currently the only Literal-typed field in MemoryMetadata. A new one needs
        its own write-side validation, and this test is where that gets noticed.
        """
        constrained = {
            name
            for name, field in MemoryMetadata.model_fields.items()
            if "Literal" in str(field.annotation)
        }

        self.assertEqual(
            constrained,
            {"tracker_type"},
            "new constrained field in MemoryMetadata - json_set and model_copy(update=) "
            "both skip validation, so give it a guard at the write like upsert_tracker's",
        )

    def test_the_runtime_list_cannot_drift_from_the_schema(self) -> None:
        """TRACKER_TYPES and TrackerType were two independent literals saying the same
        thing. A value added to one and not the other would pass the service layer and
        then fail every read of the chat it was written to."""
        from typing import get_args

        from app.schemas import TrackerType
        from app.services.tracker_prompts import TRACKER_TYPES

        self.assertEqual(tuple(TRACKER_TYPES), get_args(TrackerType))


if __name__ == "__main__":
    unittest.main()
