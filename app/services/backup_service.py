import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import config

BACKUP_FILENAME_PREFIX = "memory_"
BACKUP_FILENAME_SUFFIX = ".db"


def _backup_dir() -> Path:
    return Path(config.BACKUP_DIR)


def create_backup(db_path: str | None = None, backup_dir: Path | None = None) -> Path | None:
    """Copy the live database to a timestamped file in the backup directory.

    Uses SQLite's online backup API rather than a plain file copy, since the
    app may hold an open connection at the same time - a raw copy could grab
    a half-written page mid-transaction, while the backup API produces a
    consistent snapshot regardless.

    Returns the new backup path, or None if there is no database yet
    (e.g. first run before init_schema has ever created one).
    """
    source_path = Path(db_path if db_path is not None else config.DATABASE_PATH)
    if not source_path.exists():
        return None

    target_dir = backup_dir if backup_dir is not None else _backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    target_path = target_dir / f"{BACKUP_FILENAME_PREFIX}{timestamp}{BACKUP_FILENAME_SUFFIX}"

    source_conn = sqlite3.connect(str(source_path))
    try:
        dest_conn = sqlite3.connect(str(target_path))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()

    return target_path


def rotate_backups(backup_dir: Path | None = None, keep: int | None = None) -> list[Path]:
    """Delete all but the `keep` most recent backups in backup_dir.

    Backup filenames embed a sortable UTC timestamp, so lexicographic sort
    order matches chronological order. Returns the paths that were deleted.
    """
    target_dir = backup_dir if backup_dir is not None else _backup_dir()
    keep_count = config.BACKUP_KEEP if keep is None else keep

    backups = sorted(target_dir.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"))
    to_delete = backups[:-keep_count] if keep_count > 0 else backups

    for path in to_delete:
        path.unlink()
    return to_delete


def run_backup() -> Path | None:
    """Create a new backup and rotate old ones down to the configured limit.

    Intended to run on every server startup (cheap - a no-op copy takes a
    fraction of a second even for a multi-MB db) and/or from cron via
    scripts/backup_db.py. Callers that shouldn't fail startup on a backup
    error are responsible for catching exceptions themselves.
    """
    backup_path = create_backup()
    if backup_path is not None:
        rotate_backups()
    return backup_path
