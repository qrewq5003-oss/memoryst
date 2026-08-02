import gzip
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import config

BACKUP_FILENAME_PREFIX = "memory_"
BACKUP_FILENAME_SUFFIX = ".db"
# Backups are gzipped after the snapshot is taken. A SQLite file of mostly text
# compresses about 4x - measured on this store, 58MB down to 14MB in 3.4 seconds - and
# the retention policy keeps up to seventeen of them, which on a phone was 938MB of the
# 992MB the data directory had grown to. Compressing beats deleting here: every restore
# point survives.
COMPRESSED_SUFFIX = ".db.gz"


def _backup_dir() -> Path:
    return Path(config.BACKUP_DIR)


def create_backup(db_path: str | None = None, backup_dir: Path | None = None) -> Path | None:
    """Copy the live database to a timestamped file in the backup directory.

    Uses SQLite's online backup API rather than a plain file copy, since the
    app may hold an open connection at the same time - a raw copy could grab
    a half-written page mid-transaction, while the backup API produces a
    consistent snapshot regardless.

    The snapshot is gzipped once it is complete, so what lands in the backup directory
    is `memory_<timestamp>.db.gz`. Restore with:

        gunzip -c data/backups/memory_<timestamp>.db.gz > data/memory.db

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

    compressed_path = target_dir / f"{BACKUP_FILENAME_PREFIX}{timestamp}{COMPRESSED_SUFFIX}"
    with target_path.open("rb") as raw, gzip.open(compressed_path, "wb") as packed:
        shutil.copyfileobj(raw, packed)
    target_path.unlink()

    return compressed_path


def _backup_day(path: Path) -> str | None:
    """Extract the YYYYMMDD day from a backup filename, or None if it doesn't parse.

    Anything unparseable is treated as "unknown age" by the caller and kept, since
    deleting a file whose date we can't establish is the one irreversible mistake
    rotation can make.
    """
    # Parsed from the front, so it works for both `.db` and `.db.gz` - older
    # uncompressed backups stay readable by the same rotation.
    day = path.name[len(BACKUP_FILENAME_PREFIX):][:8]
    if len(day) != 8 or not day.isdigit():
        return None
    return day


def rotate_backups(
    backup_dir: Path | None = None,
    keep_days: int | None = None,
    keep_recent: int | None = None,
) -> list[Path]:
    """Prune backups generationally: newest-per-day plus the newest few overall.

    A backup survives if it is either
      - the newest backup of its day, and its day is among the `keep_days` most
        recent days that have any backup at all, or
      - among the `keep_recent` newest backups overall.

    The per-day rule is what makes restart churn harmless: backups are written on
    every server start as well as nightly from cron, so a flat "keep the N newest
    files" rule let one evening of restarts evict every older day. The recent rule
    covers the opposite case - a snapshot taken just before a risky migration must
    not be dropped by the next restart on the same day.

    Filenames embed a sortable UTC timestamp, so lexicographic order is chronological.
    Returns the paths that were deleted.
    """
    target_dir = backup_dir if backup_dir is not None else _backup_dir()
    days_to_keep = config.BACKUP_KEEP_DAYS if keep_days is None else keep_days
    recent_to_keep = config.BACKUP_KEEP_RECENT if keep_recent is None else keep_recent

    backups = sorted(
        set(target_dir.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"))
        | set(target_dir.glob(f"{BACKUP_FILENAME_PREFIX}*{COMPRESSED_SUFFIX}"))
    )
    if not backups:
        return []

    newest_per_day: dict[str, Path] = {}
    undatable: set[Path] = set()
    for path in backups:
        day = _backup_day(path)
        if day is None:
            undatable.add(path)
            continue
        # `backups` is ascending, so the last write for a day wins.
        newest_per_day[day] = path

    kept_days = sorted(newest_per_day, reverse=True)[:days_to_keep] if days_to_keep > 0 else []
    protected = {newest_per_day[day] for day in kept_days}
    protected |= undatable
    if recent_to_keep > 0:
        protected |= set(backups[-recent_to_keep:])

    to_delete = [path for path in backups if path not in protected]
    for path in to_delete:
        path.unlink()
    return to_delete


def run_backup() -> Path | None:
    """Create a new backup and prune old ones to the configured retention.

    Intended to run on every server startup (cheap - a no-op copy takes a
    fraction of a second even for a multi-MB db) and/or from cron via
    scripts/backup_db.py. Callers that shouldn't fail startup on a backup
    error are responsible for catching exceptions themselves.
    """
    backup_path = create_backup()
    if backup_path is not None:
        rotate_backups()
    return backup_path
