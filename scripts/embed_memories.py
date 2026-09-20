#!/usr/bin/env python3
"""Embed existing memories into the vector store.

Embedding costs money per call, and most of a long-lived database is chats nobody is
going to open again. Default to the chats that are actually live rather than the whole
archive:

    python scripts/embed_memories.py --recent 2      # the 2 most recently touched chats
    python scripts/embed_memories.py --chat-id Rome117...
    python scripts/embed_memories.py --all           # everything, deliberately

New memories are embedded by store_service as they are written, so coverage grows on its
own; a backfill only buys semantic recall over history that already exists.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_connection
from app.services.vector_store import add_memory, is_vector_store_enabled, get_collection_count, get_key_count


def _select_rows(cursor, args) -> list:
    """The memories to embed, narrowed to what is worth paying for.

    Rows that already have a vector are excluded by the join rather than by counting:
    the old check compared two totals, so a partial backfill that had stopped early
    looked "done" as soon as the counts happened to line up.
    """
    sql = """
        SELECT m.id, m.content, m.chat_id, m.character_id
        FROM memories m
        LEFT JOIN memory_embeddings e ON e.memory_id = m.id
        WHERE m.archived = 0 AND e.memory_id IS NULL
    """
    params: list = []

    if args.chat_id:
        sql += " AND m.chat_id = ?"
        params.append(args.chat_id)
    elif not args.all:
        cursor.execute(
            """
            SELECT chat_id FROM memories WHERE archived = 0
            GROUP BY chat_id, character_id ORDER BY MAX(updated_at) DESC LIMIT ?
            """,
            (args.recent,),
        )
        chat_ids = [row["chat_id"] for row in cursor.fetchall()]
        if not chat_ids:
            return []
        sql += f" AND m.chat_id IN ({','.join('?' * len(chat_ids))})"
        params.extend(chat_ids)

    sql += " ORDER BY m.updated_at DESC"
    if args.limit:
        sql += " LIMIT ?"
        params.append(args.limit)

    cursor.execute(sql, params)
    return cursor.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recent", type=int, default=2, help="embed the N most recently touched chats")
    parser.add_argument("--chat-id", help="embed one chat only")
    parser.add_argument("--all", action="store_true", help="embed every chat")
    parser.add_argument("--limit", type=int, help="stop after N memories")
    parser.add_argument("--dry-run", action="store_true", help="report what would be embedded, call nothing")
    args = parser.parse_args()

    if not is_vector_store_enabled():
        print("Vector store not enabled (GOOGLE_API_KEYS not set).")
        return 1

    print(f"Using {get_key_count()} API key(s) with auto-rotation on 429.")

    conn = get_connection()
    cursor = conn.cursor()
    rows = _select_rows(cursor, args)

    print(f"{len(rows)} memories to embed, {get_collection_count()} already in the store.")
    if not rows:
        print("Nothing to do.")
        return 0

    characters = sum(len(row["content"] or "") for row in rows)
    print(f"~{characters // 4} tokens of input across {len(rows)} calls.")
    if args.dry_run:
        return 0

    embedded = 0
    errors = 0
    for row in rows:
        memory_id = row["id"]
        content = row["content"]
        metadata = {"chat_id": row["chat_id"], "character_id": row["character_id"]}
        try:
            add_memory(memory_id, content, metadata)
            embedded += 1
            if embedded % 50 == 0:
                print(f"  embedded {embedded}/{len(rows)}...")
            time.sleep(0.6)
        except Exception as e:
            errors += 1
            print(f"  ERROR {memory_id}: {e}", file=sys.stderr)
            if "429" in str(e):
                print("  All keys exhausted, stopping.")
                break

    conn.close()
    print(f"Done. Embedded {embedded} memories, {errors} errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
