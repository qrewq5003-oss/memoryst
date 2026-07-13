#!/usr/bin/env python3
"""
One-off cleanup for chat_messages rows duplicated before intake became idempotent.

The extension posts its whole recent-message window (8 messages by default) on every
turn, and chat_buffer_service used to append all of them unconditionally, so a single
message could land dozens of times. Reading such a chat back by sequence_index gives a
stuttering transcript, and it pollutes the FTS raw-history fallback.

Collapses each (chat_id, character_id, role, normalized_text) group down to its earliest
row, rewrites any memory whose metadata.source_message_ids referenced a removed row so it
points at the surviving one instead, and rebuilds the FTS index.

Dry run by default; pass --apply to actually write.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import config
from app.db import get_connection, init_schema


def _find_duplicate_groups(cursor) -> dict[str, list[str]]:
    """Map surviving message id -> ids of the duplicates it replaces."""
    cursor.execute(
        """
        SELECT id, chat_id, character_id, role, normalized_text
        FROM chat_messages
        ORDER BY chat_id, character_id, sequence_index ASC
        """
    )
    survivor_by_key: dict[tuple[str, str, str, str], str] = {}
    duplicates: dict[str, list[str]] = {}

    for row in cursor.fetchall():
        key = (row["chat_id"], row["character_id"], row["role"], row["normalized_text"])
        survivor = survivor_by_key.get(key)
        if survivor is None:
            survivor_by_key[key] = row["id"]
        else:
            duplicates.setdefault(survivor, []).append(row["id"])

    return duplicates


def _remap_source_message_ids(cursor, id_remap: dict[str, str], apply: bool) -> int:
    """Repoint metadata.source_message_ids from removed duplicates to survivors."""
    cursor.execute("SELECT id, metadata_json FROM memories")
    rows = cursor.fetchall()

    updates: list[tuple[str, str]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, ValueError):
            continue

        source_ids = metadata.get("source_message_ids")
        if not source_ids:
            continue

        remapped = list(dict.fromkeys(id_remap.get(mid, mid) for mid in source_ids))
        if remapped == list(source_ids):
            continue

        metadata["source_message_ids"] = remapped
        updates.append((json.dumps(metadata, ensure_ascii=False), row["id"]))

    if apply and updates:
        cursor.executemany(
            "UPDATE memories SET metadata_json = ? WHERE id = ?", updates
        )
    return len(updates)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collapse duplicate rows in chat_messages (dry run unless --apply)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete duplicates. Without this the script only reports.",
    )
    args = parser.parse_args()

    # Ensures normalized_text exists and is backfilled before we group on it.
    init_schema()

    conn = get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM chat_messages")
        total_before = cursor.fetchone()[0]

        duplicates = _find_duplicate_groups(cursor)
        duplicate_ids = [dup_id for dups in duplicates.values() for dup_id in dups]
        id_remap = {
            dup_id: survivor
            for survivor, dups in duplicates.items()
            for dup_id in dups
        }

        print(f"database:            {config.DATABASE_PATH}")
        print(f"rows before:         {total_before}")
        print(f"duplicate rows:      {len(duplicate_ids)}")
        print(f"unique messages:     {total_before - len(duplicate_ids)}")

        if duplicates:
            worst_survivor, worst_dups = max(duplicates.items(), key=lambda kv: len(kv[1]))
            cursor.execute("SELECT text FROM chat_messages WHERE id = ?", (worst_survivor,))
            worst_row = cursor.fetchone()
            worst_text = (worst_row["text"] if worst_row else "")[:60].replace("\n", " ")
            print(f"worst offender:      {len(worst_dups) + 1} copies of {worst_text!r}")

        remapped_memories = _remap_source_message_ids(cursor, id_remap, apply=args.apply)
        print(f"memories to repoint: {remapped_memories}")

        if not args.apply:
            print("\nDry run - nothing written. Re-run with --apply to delete.")
            return 0

        if not duplicate_ids:
            print("\nNothing to delete.")
            return 0

        # Deleting fires the FTS delete trigger row by row; the explicit rebuild
        # afterwards is belt-and-braces in case the index had already drifted.
        cursor.executemany(
            "DELETE FROM chat_messages WHERE id = ?", [(mid,) for mid in duplicate_ids]
        )
        cursor.execute(
            "INSERT INTO chat_messages_fts(chat_messages_fts) VALUES ('rebuild')"
        )
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM chat_messages")
        total_after = cursor.fetchone()[0]
        print(f"\ndeleted:             {len(duplicate_ids)} rows")
        print(f"rows after:          {total_after}")
        print("FTS index rebuilt.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
