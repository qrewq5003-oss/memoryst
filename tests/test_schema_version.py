"""Schema migrations are numbered and stamped, not rediscovered by probing every start.

Before 2026-09-21 each migration carried its own structural detector and nothing recorded
the order they must run in - that the summary rebuild has to precede the tracker one, for
instance, lived in a comment. `PRAGMA user_version` was 0 on every database including the
live one, which had all three applied.

So the stamp cannot be trusted on its own for an existing database, and the detectors stay
authoritative: a database below SCHEMA_VERSION is probed, one already at it is not.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.config import config
from app.db import MIGRATIONS, SCHEMA_VERSION, get_connection, init_schema


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.original = config.DATABASE_PATH
        self.addCleanup(setattr, config, "DATABASE_PATH", self.original)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config.DATABASE_PATH = str(Path(self.tmp.name) / "test.db")

    def _version(self) -> int:
        with get_connection() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]


class SchemaVersionTests(_IsolatedDatabase):
    def test_a_fresh_database_is_stamped(self) -> None:
        init_schema()
        self.assertEqual(self._version(), SCHEMA_VERSION)

    def test_the_numbers_are_a_gapless_sequence_ending_at_the_version(self) -> None:
        # A gap or a repeat means one migration silently never runs.
        numbers = [number for number, *_ in MIGRATIONS]
        self.assertEqual(numbers, list(range(1, len(MIGRATIONS) + 1)))
        self.assertEqual(SCHEMA_VERSION, len(MIGRATIONS),
                         "bump SCHEMA_VERSION in the same commit that appends to MIGRATIONS")

    def _probe_migration(self, calls: list):
        """A stand-in migration that records whether its check was consulted.

        MIGRATIONS binds the real functions at import, so patching them on the module has
        no effect on the tuple - the list has to be replaced instead. Worth knowing before
        the next person tries `patch.object(db, "_needs_...")` and watches it do nothing,
        as I did.
        """
        def needs(_cursor):
            calls.append("probed")
            return False
        return ((1, "probe", needs, lambda _conn: None),)

    def test_an_up_to_date_database_is_not_probed_again(self) -> None:
        init_schema()
        calls: list = []
        with patch.object(db, "MIGRATIONS", self._probe_migration(calls)), \
             patch.object(db, "SCHEMA_VERSION", 1):
            with get_connection() as conn:
                conn.execute("PRAGMA user_version = 1")
            init_schema()
        self.assertEqual(calls, [], "an up-to-date database was probed anyway")

    def test_an_older_database_is_probed(self) -> None:
        # The live database reads 0 while already carrying every migration, so the
        # detectors have to run and find nothing rather than the version deciding alone.
        init_schema()
        calls: list = []
        with patch.object(db, "MIGRATIONS", self._probe_migration(calls)), \
             patch.object(db, "SCHEMA_VERSION", 1):
            with get_connection() as conn:
                conn.execute("PRAGMA user_version = 0")
            init_schema()
        self.assertEqual(calls, ["probed"])

    def test_a_newer_database_is_left_alone_with_a_warning(self) -> None:
        # Refusing to start would lock someone out of their own memory over a rolled-back
        # commit, and every migration here is additive, so reading it is safe.
        init_schema()
        with get_connection() as conn:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")

        with self.assertLogs("app.db", level="WARNING") as logs:
            init_schema()

        self.assertTrue(any("newer" in line for line in logs.output), logs.output)
        self.assertEqual(self._version(), SCHEMA_VERSION + 5, "the newer stamp was overwritten")


class SchemaAssertionsRunEveryStartTests(_IsolatedDatabase):
    """The trap that caught this change while it was being written.

    `_drop_unused_chat_messages_index` was put in MIGRATIONS at first. Version-gated, it
    ran once and never again, so a database that regained the index kept it - which the
    existing regression test in test_chat_message_dedupe recreates exactly. It is not an
    upgrade, it is the mirror of a CREATE INDEX IF NOT EXISTS, and it belongs with them.
    """

    def test_the_unused_index_is_dropped_even_at_the_current_version(self) -> None:
        init_schema()
        self.assertEqual(self._version(), SCHEMA_VERSION)

        with get_connection() as conn:
            conn.execute(
                "CREATE INDEX idx_chat_messages_normalized "
                "ON chat_messages (chat_id, character_id, normalized_text)"
            )

        init_schema()

        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_chat_messages_normalized'"
            ).fetchone()
        self.assertIsNone(row, "a schema assertion was version-gated into running once")


if __name__ == "__main__":
    unittest.main()
