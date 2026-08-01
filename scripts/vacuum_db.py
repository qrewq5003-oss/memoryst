#!/usr/bin/env python3
"""Compact the database file after space has been freed inside it.

Dropping an index or deleting rows returns pages to SQLite's freelist, but the file
on disk stays the same size until it is rebuilt. VACUUM does that rebuild.

Worth running once after the release that dropped idx_chat_messages_normalized:
that index held a second full copy of every message's normalized text - 21.8MB of a
77MB database - and the space only returns to the filesystem here.

VACUUM rewrites the whole file, so it needs room for a second copy while it runs and
must not race the server. Stop the server first; this script refuses to run if the
database is locked rather than blocking on it.

Usage:
    .venv/bin/python scripts/vacuum_db.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import config
from app.services.backup_service import run_backup


def _size_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024


def main() -> int:
    db_path = Path(config.DATABASE_PATH)
    if not db_path.exists():
        print(f"No database at {db_path} - nothing to do.")
        return 0

    before = _size_mb(db_path)
    free_pages, page_size = _freelist(db_path)
    print(f"Before: {before:.1f} MB, {free_pages} free pages ({free_pages * page_size / 1024 / 1024:.1f} MB reclaimable)")

    # A rebuild of the whole file deserves a snapshot in front of it, for the same
    # reason the server takes one before migrating.
    backup_path = run_backup()
    if backup_path is not None:
        print(f"Backup: {backup_path}")

    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        conn.execute("VACUUM")
    except sqlite3.OperationalError as exc:
        print(f"VACUUM failed: {exc}")
        print("Is the server still running? Stop it and try again.")
        return 1
    finally:
        conn.close()

    after = _size_mb(db_path)
    print(f"After:  {after:.1f} MB (freed {before - after:.1f} MB)")
    return 0


def _freelist(db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        return free_pages, page_size
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
