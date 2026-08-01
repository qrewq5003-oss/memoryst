import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.original_keep_days = config.BACKUP_KEEP_DAYS
        self.original_keep_recent = config.BACKUP_KEEP_RECENT
        config.DATABASE_PATH = str(self.db_path)
        config.BACKUP_DIR = str(self.backup_dir)
        self.addCleanup(self._restore_config)

    def _restore_config(self) -> None:
        config.DATABASE_PATH = self.original_database_path
        config.BACKUP_DIR = self.original_backup_dir
        config.BACKUP_KEEP_DAYS = self.original_keep_days
        config.BACKUP_KEEP_RECENT = self.original_keep_recent

    def _write_backups(self, names: list[str]) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            (self.backup_dir / name).write_bytes(b"x")

    def _remaining(self) -> list[str]:
        return sorted(p.name for p in self.backup_dir.glob("memory_*.db"))

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

    def test_rotate_backups_keeps_the_most_recent_days(self) -> None:
        names = [
            "memory_20260101_000000_000000.db",
            "memory_20260102_000000_000000.db",
            "memory_20260103_000000_000000.db",
            "memory_20260104_000000_000000.db",
        ]
        self._write_backups(names)

        deleted = rotate_backups(keep_days=2, keep_recent=0)

        self.assertEqual(self._remaining(), names[-2:])
        self.assertEqual(sorted(p.name for p in deleted), names[:2])

    def test_a_day_of_restarts_cannot_evict_the_daily_history(self) -> None:
        """The regression this rotation scheme exists for.

        Backups are written on every server start as well as nightly from cron, so
        under a flat "keep the N newest files" rule an evening of restarts filled
        every slot with same-day copies and silently destroyed the daily history -
        discovered only when a backup was actually needed. Fourteen restarts on one
        day must not cost a single earlier day.
        """
        daily = [f"memory_202601{day:02d}_010000_000000.db" for day in range(1, 15)]
        churn = [f"memory_20260115_{hour:02d}0000_000000.db" for hour in range(6, 20)]
        self._write_backups(daily + churn)

        rotate_backups(keep_days=14, keep_recent=3)

        remaining = self._remaining()
        # Every earlier day survives, represented by its own newest backup.
        for name in daily[1:]:
            self.assertIn(name, remaining)
        # The churn day collapses to its newest, plus the keep_recent tail.
        self.assertIn(churn[-1], remaining)
        self.assertNotIn(churn[0], remaining)

    def test_keep_recent_protects_a_pre_migration_snapshot_taken_today(self) -> None:
        """A snapshot taken just before a risky migration must survive the next
        restart on the same day - otherwise per-day collapse would reintroduce the
        very hole that moving run_backup() before init_schema() closes."""
        self._write_backups([
            "memory_20260114_010000_000000.db",
            "memory_20260115_090000_000000.db",  # pre-migration snapshot
            "memory_20260115_091500_000000.db",  # restart minutes later
        ])

        rotate_backups(keep_days=14, keep_recent=2)

        self.assertIn("memory_20260115_090000_000000.db", self._remaining())

    def test_rotate_backups_never_deletes_a_file_it_cannot_date(self) -> None:
        self._write_backups([
            "memory_20260101_000000_000000.db",
            "memory_20260102_000000_000000.db",
            "memory_handwritten_copy.db",
        ])

        rotate_backups(keep_days=1, keep_recent=0)

        self.assertIn("memory_handwritten_copy.db", self._remaining())

    def test_run_backup_creates_and_prunes_using_configured_retention(self) -> None:
        self._create_source_db()
        config.BACKUP_KEEP_DAYS = 1
        config.BACKUP_KEEP_RECENT = 1

        first = run_backup()
        second = run_backup()

        assert first is not None and second is not None
        self.assertNotEqual(first, second)
        remaining = list(self.backup_dir.glob("memory_*.db"))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0], second)


class StartupOrderTests(unittest.TestCase):
    """init_schema() is the only place that DROPs and rebuilds `memories`
    (_run_summary_migration / _run_tracker_migration). A backup taken after it
    snapshots the already-migrated state, so a migration that succeeds but is
    wrong would overwrite the last good copy with the damaged one."""

    def test_backup_runs_before_schema_migration_on_startup(self) -> None:
        import asyncio

        from app.main import lifespan

        calls: list[str] = []

        with (
            patch("app.main.validate_security"),
            patch("app.main.run_backup", side_effect=lambda: calls.append("backup")),
            patch("app.main.init_schema", side_effect=lambda: calls.append("init_schema")),
        ):
            async def drive() -> None:
                async with lifespan(None):
                    pass

            asyncio.run(drive())

        self.assertEqual(calls, ["backup", "init_schema"])

    def test_startup_continues_when_the_backup_fails(self) -> None:
        import asyncio

        from app.main import lifespan

        calls: list[str] = []

        with (
            patch("app.main.validate_security"),
            patch("app.main.run_backup", side_effect=OSError("disk full")),
            patch("app.main.init_schema", side_effect=lambda: calls.append("init_schema")),
        ):
            async def drive() -> None:
                async with lifespan(None):
                    pass

            asyncio.run(drive())

        self.assertEqual(calls, ["init_schema"])


if __name__ == "__main__":
    unittest.main()
