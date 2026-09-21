"""A connection must not outlive its `with` block.

`sqlite3.Connection.__exit__` is a *transaction* context manager: it commits or rolls
back and leaves the handle open. Every `with get_connection() as conn:` here therefore
depended on refcounting, and the handles sit in reference cycles, so only the cyclic
collector freed them.

Measured 2026-09-21 on the running server: 40 requests to /memory/list left 40 new file
descriptors and 47 open handles on memory.db, still there after 20 seconds idle. It was a
delay rather than an unbounded leak - gc.collect() dropped 34 back to 4 - but a delay that
tracked request rate against a 32768 limit, and whose eventual failure would have read as
"the service stopped responding" with nothing pointing at the database layer.

These tests count descriptors rather than inspect the class, because the defect was
invisible to every test that only checked behaviour: the queries were always correct.
"""

import gc
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.config import config
from app.db import get_connection, init_schema
from app.repositories.memory_repo import list_memories


def _open_descriptors() -> int:
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.original = config.DATABASE_PATH
        self.addCleanup(setattr, config, "DATABASE_PATH", self.original)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config.DATABASE_PATH = str(Path(self.tmp.name) / "test.db")
        init_schema()


class ConnectionClosesTests(_IsolatedDatabase):
    def test_the_with_block_closes_the_connection(self) -> None:
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_a_commit_still_happens_before_the_close(self) -> None:
        # Closing must not cost the write the context manager exists to commit.
        with get_connection() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('k', 'v')")
        with get_connection() as check:
            row = check.execute("SELECT value FROM app_settings WHERE key = 'k'").fetchone()
        self.assertEqual(row["value"], "v")

    def test_an_exception_still_rolls_back_before_the_close(self) -> None:
        with self.assertRaises(ValueError):
            with get_connection() as conn:
                conn.execute("INSERT INTO app_settings (key, value) VALUES ('k2', 'v')")
                raise ValueError("boom")
        with get_connection() as check:
            self.assertIsNone(
                check.execute("SELECT value FROM app_settings WHERE key = 'k2'").fetchone()
            )

    def test_closing_twice_is_harmless(self) -> None:
        # 14 call sites keep the connection in a variable and close it in a `finally`.
        # Those must keep working, and a double close must not raise.
        conn = get_connection()
        conn.close()
        conn.close()

    def test_repeated_repository_calls_do_not_accumulate_descriptors(self) -> None:
        # The regression itself, measured the way it was found. gc is disabled so a
        # collection cannot hide the leak the way it hid it in production.
        list_memories(limit=1)  # warm any lazy import that opens its own handles
        gc.disable()
        self.addCleanup(gc.enable)
        before = _open_descriptors()
        for _ in range(50):
            list_memories(limit=1)
        self.assertLessEqual(
            _open_descriptors() - before, 2,
            "descriptors grew across 50 calls - a connection is outliving its with block",
        )


if __name__ == "__main__":
    unittest.main()
