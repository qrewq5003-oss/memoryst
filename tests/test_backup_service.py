import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.config import config
from app.services.backup_service import create_backup, rotate_backups, run_backup


class BackupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.db_path = Path(self.temp_dir.name) / "memory.db"
        self.backup_dir = Path(self.temp_dir.name) / "backups"

        self.original_database_path = config.DATABASE_PATH
        self.original_backup_dir = config.BACKUP_DIR
        self.original_backup_keep = config.BACKUP_KEEP
        config.DATABASE_PATH = str(self.db_path)
        config.BACKUP_DIR = str(self.backup_dir)
        self.addCleanup(self._restore_config)

    def _restore_config(self) -> None:
        config.DATABASE_PATH = self.original_database_path
        config.BACKUP_DIR = self.original_backup_dir
        config.BACKUP_KEEP = self.original_backup_keep

    def _create_source_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO t (v) VALUES ('hello')")
            conn.commit()
        finally:
            conn.close()

    def _create_populated_source_db(self, rows: list[str]) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.executemany("INSERT INTO t (v) VALUES (?)", [(v,) for v in rows])
            conn.commit()
        finally:
            conn.close()

    def test_create_backup_returns_none_when_no_database_exists(self) -> None:
        result = create_backup()
        self.assertIsNone(result)

    def test_create_backup_copies_data_into_new_timestamped_file(self) -> None:
        self._create_source_db()

        backup_path = create_backup()

        assert backup_path is not None
        self.assertTrue(backup_path.exists())
        self.assertEqual(backup_path.parent, self.backup_dir)

        conn = sqlite3.connect(str(backup_path))
        try:
            row = conn.execute("SELECT v FROM t").fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "hello")

    def test_backup_integrity_row_count_and_content_match_source(self) -> None:
        """A backup that merely exists as a file isn't proof it's usable -
        open it as a real SQLite db, via its own connection, and check row
        count plus a specific record's content against the source."""
        rows = [f"record-{i}" for i in range(37)]
        self._create_populated_source_db(rows)

        source_conn = sqlite3.connect(str(self.db_path))
        try:
            source_count = source_conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            source_row_20 = source_conn.execute(
                "SELECT v FROM t WHERE id = ?", (20,)
            ).fetchone()[0]
        finally:
            source_conn.close()

        backup_path = create_backup()
        assert backup_path is not None

        backup_conn = sqlite3.connect(str(backup_path))
        try:
            backup_count = backup_conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            backup_row_20 = backup_conn.execute(
                "SELECT v FROM t WHERE id = ?", (20,)
            ).fetchone()[0]
        finally:
            backup_conn.close()

        self.assertEqual(source_count, len(rows))
        self.assertEqual(backup_count, source_count)
        self.assertEqual(backup_row_20, source_row_20)
        self.assertEqual(backup_row_20, "record-19")

    def test_rotate_backups_keeps_only_most_recent_n(self) -> None:
        self.backup_dir.mkdir(parents=True)
        names = [
            "memory_20260101_000000.db",
            "memory_20260102_000000.db",
            "memory_20260103_000000.db",
            "memory_20260104_000000.db",
        ]
        for name in names:
            (self.backup_dir / name).write_bytes(b"x")

        deleted = rotate_backups(keep=2)

        remaining = sorted(p.name for p in self.backup_dir.glob("memory_*.db"))
        self.assertEqual(remaining, names[-2:])
        self.assertEqual(sorted(p.name for p in deleted), names[:2])

    def test_run_backup_creates_and_rotates_using_configured_keep(self) -> None:
        self._create_source_db()
        config.BACKUP_KEEP = 1

        first = run_backup()
        second = run_backup()

        assert first is not None and second is not None
        self.assertNotEqual(first, second)
        remaining = list(self.backup_dir.glob("memory_*.db"))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0], second)


if __name__ == "__main__":
    unittest.main()
