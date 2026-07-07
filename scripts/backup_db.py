#!/usr/bin/env python3
"""Stand-alone DB backup entrypoint, for use outside the running server
(e.g. from cron/Termux job scheduler) so backups keep happening even on
days the server never restarts.

Usage:
    .venv/bin/python scripts/backup_db.py

Example crontab entry (pkg install cronie; crontab -e), daily at 04:00:
    0 4 * * * cd ~/memoryst && .venv/bin/python scripts/backup_db.py >> data/backup.log 2>&1
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.backup_service import run_backup


def main() -> int:
    backup_path = run_backup()
    if backup_path is None:
        print("No database found to back up yet - nothing to do.")
        return 0
    print(f"Backup created: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
