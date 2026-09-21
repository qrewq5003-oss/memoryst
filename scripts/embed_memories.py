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
from app.config import config
from app.services.vector_store import (
    BULK_EMBED_DELAY_SECONDS,
    add_memories_batch,
    add_memory,
    get_collection_count,
    get_key_count,
    is_vector_store_enabled,
)


def _select_rows(cursor, args) -> list:
    """The memories to embed, narrowed to what is worth paying for.

    Rows that already have a vector are excluded by the join rather than by counting:
    the old check compared two totals, so a partial backfill that had stopped early
    looked "done" as soon as the counts happened to line up.
    """
    # The join is on (memory_id, model), not memory_id alone. A row carrying a vector
    # from a previous model is not "already embedded" - it is unusable, because the
    # query filters by model - and matching on the id alone would report the backfill
    # as done while leaving those rows permanently invisible to retrieval.
    sql = """
        SELECT m.id, m.content, m.chat_id, m.character_id
        FROM memories m
        LEFT JOIN memory_embeddings e
               ON e.memory_id = m.id AND e.model = ?
        WHERE m.archived = 0 AND e.memory_id IS NULL
    """
    params: list = [config.active_embedding_model()]

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
    parser.add_argument("--batch", type=int, default=50, help="texts per request on batching providers")
    parser.add_argument("--dry-run", action="store_true", help="report what would be embedded, call nothing")
    args = parser.parse_args()

    if not is_vector_store_enabled():
        print(f"Vector store not enabled for provider {config.EMBEDDING_PROVIDER!r}.")
        return 1

    provider = (config.EMBEDDING_PROVIDER or "google").lower()
    print(f"Provider {provider}, model {config.active_embedding_model()}, "
          f"{config.active_embedding_dim()} dimensions.")

    conn = get_connection()
    cursor = conn.cursor()
    rows = _select_rows(cursor, args)

    print(f"{len(rows)} memories to embed, {get_collection_count()} already in the store.")
    if not rows:
        print("Nothing to do.")
        return 0

    characters = sum(len(row["content"] or "") for row in rows)
    batched = provider in ("nanogpt", "cohere")
    calls = -(-len(rows) // args.batch) if batched else len(rows)
    print(f"~{characters // 4} tokens of input across {calls} request(s).")
    if args.dry_run:
        return 0

    embedded = 0
    errors = 0

    if batched:
        # Batched providers get the whole list per request. The pacing sleep stays, but
        # per request rather than per memory - it exists for a per-minute rate limit,
        # and a batch is one unit of that.
        for start in range(0, len(rows), args.batch):
            chunk = rows[start : start + args.batch]
            items = [
                (r["id"], r["content"], {"chat_id": r["chat_id"], "character_id": r["character_id"]})
                for r in chunk
            ]
            try:
                embedded += add_memories_batch(items)
                print(f"  embedded {embedded}/{len(rows)}...")
                time.sleep(BULK_EMBED_DELAY_SECONDS)
            except Exception as e:
                errors += len(chunk)
                print(f"  ERROR batch at {start}: {e}", file=sys.stderr)
                if "429" in str(e) or "insufficient" in str(e).lower():
                    print("  Rate limited or out of balance, stopping.")
                    break
    else:
        for row in rows:
            memory_id = row["id"]
            content = row["content"]
            metadata = {"chat_id": row["chat_id"], "character_id": row["character_id"]}
            try:
                add_memory(memory_id, content, metadata)
                embedded += 1
                if embedded % 50 == 0:
                    print(f"  embedded {embedded}/{len(rows)}...")
                time.sleep(BULK_EMBED_DELAY_SECONDS)
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
