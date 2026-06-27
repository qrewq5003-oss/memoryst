#!/usr/bin/env python3
"""Batch embed all existing memories into the vector store."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_connection
from app.services.vector_store import add_memory, is_vector_store_enabled, get_collection_count


def main() -> int:
    if not is_vector_store_enabled():
        print("Vector store not enabled (GOOGLE_API_KEY not set).")
        return 1

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, content, chat_id, character_id FROM memories WHERE archived = 0")
    rows = cursor.fetchall()

    existing = get_collection_count()
    print(f"Found {len(rows)} memories in SQLite, {existing} embeddings in vector store.")

    embedded = 0
    for row in rows:
        memory_id = row["id"]
        content = row["content"]
        metadata = {"chat_id": row["chat_id"], "character_id": row["character_id"]}
        try:
            add_memory(memory_id, content, metadata)
            embedded += 1
            if embedded % 50 == 0:
                print(f"  embedded {embedded}/{len(rows)}...")
        except Exception as e:
            print(f"  ERROR embedding {memory_id}: {e}", file=sys.stderr)

    conn.close()
    print(f"Done. Embedded {embedded} memories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
