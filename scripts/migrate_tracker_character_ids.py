#!/usr/bin/env python3
"""Re-key trackers from SillyTavern's character index to the character's avatar.

Memories did not need this. Retrieval scopes by chat (see text_utils.scope_character_id),
so a memory stored under a stale index is still reachable. A tracker is different: it is
unique per (chat_id, character_id, tracker_type) at the database level and is fetched by
that exact triple, so when the extension started sending avatars instead of indexes its
trackers stopped resolving - four documents per scope, silently absent from the prompt.

The mapping is not guessed. SillyTavern stores a chat at
`data/<user>/chats/<character directory>/<chat id>.jsonl`, and that directory name is the
character's card name, which is also its avatar's filename. So a chat's character is read
off the filesystem, and a scope whose chat file is gone is reported and left alone rather
than migrated on a hunch.

    python scripts/migrate_tracker_character_ids.py --dry-run   # reads only
    python scripts/migrate_tracker_character_ids.py             # migrates, backs up first
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_connection

DEFAULT_ST_ROOT = Path.home() / "SillyTavern" / "data" / "default-user"


def _chat_directories(st_root: Path) -> dict[str, str]:
    """chat id -> the character directory holding it."""
    chats_root = st_root / "chats"
    mapping: dict[str, str] = {}
    if not chats_root.is_dir():
        return mapping
    for chat_file in chats_root.glob("*/*.jsonl"):
        mapping[chat_file.stem] = chat_file.parent.name
    return mapping


def _avatar_for(character_name: str, st_root: Path) -> str | None:
    """The avatar filename for a character directory name, if the card still exists."""
    characters_root = st_root / "characters"
    for suffix in (".png", ".webp", ".card.png"):
        candidate = characters_root / f"{character_name}{suffix}"
        if candidate.exists():
            return candidate.name
    return None


def _tracker_scopes() -> list[tuple[str, str, int]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT chat_id, character_id, COUNT(*) AS n FROM memories
            WHERE type = 'tracker' GROUP BY chat_id, character_id ORDER BY chat_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [(row["chat_id"], row["character_id"], row["n"]) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report the mapping, change nothing")
    parser.add_argument("--st-root", type=Path, default=DEFAULT_ST_ROOT)
    args = parser.parse_args()

    chat_dirs = _chat_directories(args.st_root)
    if not chat_dirs:
        print(f"No SillyTavern chats found under {args.st_root} - nothing to map against.")
        return 1

    planned: list[tuple[str, str, str, int]] = []
    for chat_id, character_id, count in _tracker_scopes():
        directory = chat_dirs.get(chat_id)
        if directory is None:
            print(f"  SKIP {chat_id[:46]!r}: its chat file is gone, so its character is unknown")
            continue
        avatar = _avatar_for(directory, args.st_root)
        if avatar is None:
            print(f"  SKIP {chat_id[:46]!r}: no card found for {directory!r}")
            continue
        if avatar == character_id:
            print(f"  OK   {chat_id[:46]!r}: already {avatar!r}")
            continue
        planned.append((chat_id, character_id, avatar, count))
        print(f"  MOVE {chat_id[:46]!r}: {character_id!r} -> {avatar!r} ({count} trackers)")

    if not planned:
        print("Nothing to migrate.")
        return 0

    if args.dry_run:
        print(f"\n--dry-run: {len(planned)} scopes would move. Nothing was written.")
        return 0

    from app.services.backup_service import create_backup

    backup = create_backup()
    print(f"\nBackup: {backup}")

    conn = get_connection()
    try:
        for chat_id, old_id, avatar, _count in planned:
            # The unique index is (chat_id, character_id, tracker_type) for trackers only,
            # so moving a whole scope at once cannot collide with itself - but it can
            # collide with a scope already sitting at the destination, which is why that
            # case is checked rather than left to the index to reject.
            existing = conn.execute(
                """
                SELECT COUNT(*) FROM memories
                WHERE type = 'tracker' AND chat_id = ? AND character_id = ?
                """,
                (chat_id, avatar),
            ).fetchone()[0]
            if existing:
                print(f"  REFUSED {chat_id[:40]!r}: {avatar!r} already holds {existing} trackers")
                continue
            conn.execute(
                """
                UPDATE memories SET character_id = ?
                WHERE type = 'tracker' AND chat_id = ? AND character_id = ?
                """,
                (avatar, chat_id, old_id),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"Migrated {len(planned)} scopes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
