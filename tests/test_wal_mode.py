"""A reader must not be able to block a writer.

In SQLite's default `delete` journal mode it can. Measured 2026-09-21 on the live 79 MB
database: a read transaction held past busy_timeout made a concurrent write fail with
`OperationalError: database is locked`. Real reads here are fast - 0.08 s for one chat's
memories, 0.18 s for every vector - so the window was narrow, but the file is shared by
the retrieve that blocks generation, the background summary scheduler, the web UI and the
CLI scripts, and it grew from 57 MB to 79 MB in a day.

The test reproduces the fault rather than asserting the pragma, because the pragma is the
fix and the fault is the thing that must not come back.
"""

import gzip
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.config import config
from app.db import get_connection, init_schema
from app.services.backup_service import create_backup


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.original = config.DATABASE_PATH
        self.addCleanup(setattr, config, "DATABASE_PATH", self.original)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config.DATABASE_PATH = str(Path(self.tmp.name) / "test.db")
        init_schema()


class WalModeTests(_IsolatedDatabase):
    def test_the_database_is_in_wal_mode(self) -> None:
        with get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_a_held_read_does_not_block_a_write(self) -> None:
        """The regression itself.

        The reader holds an open transaction for longer than busy_timeout, which is what
        made the writer fail before. Timing rather than mocking: the fault was a property
        of the file format, and no stub reproduces it.
        """
        failure = []
        reader_ready = threading.Event()
        writer_done = threading.Event()

        def reader():
            conn = get_connection()
            try:
                conn.execute("BEGIN")
                conn.execute("SELECT COUNT(*) FROM memories").fetchone()
                reader_ready.set()
                writer_done.wait(timeout=10)
            finally:
                conn.close()

        def writer():
            reader_ready.wait(timeout=5)
            conn = sqlite3.connect(config.DATABASE_PATH, timeout=2.0)
            try:
                conn.execute("INSERT INTO app_settings (key, value) VALUES ('w', '1')")
                conn.commit()
            except Exception as error:  # noqa: BLE001 - the failure is the point
                failure.append(f"{type(error).__name__}: {error}")
            finally:
                conn.close()
                writer_done.set()

        threads = [threading.Thread(target=reader), threading.Thread(target=writer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(failure, [], "a write failed while a read was open")

    def test_a_backup_taken_in_wal_restores_intact(self) -> None:
        # WAL splits the database across -wal and -shm, so a plain file copy would produce
        # a torn snapshot. create_backup uses SQLite's online backup API instead; this
        # pins that it keeps doing so.
        with get_connection() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('backed', 'up')")

        archive = create_backup(backup_dir=Path(self.tmp.name) / "backups")
        self.assertIsNotNone(archive)

        restored = Path(self.tmp.name) / "restored.db"
        with gzip.open(archive, "rb") as src, restored.open("wb") as dst:
            shutil.copyfileobj(src, dst)

        conn = sqlite3.connect(str(restored))
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            row = conn.execute("SELECT value FROM app_settings WHERE key = 'backed'").fetchone()
            self.assertEqual(row[0], "up", "a write made before the backup is missing from it")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
