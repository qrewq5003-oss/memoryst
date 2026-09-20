#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_connection
from app.services.summary_service import MIN_SUMMARY_INPUTS, generate_rolling_summary


def _episodic_count(chat_id: str, character_id: str) -> int:
    """How many episodic memories a scope has. Reads only."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM memories
            WHERE chat_id = ? AND character_id = ? AND archived = 0
              AND layer = 'episodic' AND type != 'summary'
            """,
            (chat_id, character_id),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0])


def _all_scopes() -> list[tuple[str, str]]:
    """Every (chat, character) that has episodic memories, newest activity first.

    The catch-up pass. Automatic refreshes only cover chats that are still being written
    to, so history that predates the trigger would otherwise never get a summary at all.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT chat_id, character_id FROM memories
            WHERE archived = 0 AND layer = 'episodic' AND type != 'summary'
            GROUP BY chat_id, character_id
            ORDER BY MAX(updated_at) DESC
            """
        ).fetchall()
    finally:
        conn.close()
    return [(row["chat_id"], row["character_id"]) for row in rows]


def _sweep(args) -> int:
    scopes = _all_scopes()
    if args.limit:
        scopes = scopes[: args.limit]

    print(f"{len(scopes)} scopes to consider.")
    actions: dict[str, int] = {}
    for chat_id, character_id in scopes:
        if args.dry_run:
            # Counted, never generated. The first cut of this called
            # generate_rolling_summary with an impossibly high min-new, on the assumption
            # that would make it decline - it does not. min-new is only consulted when a
            # summary already exists, so for the 56 scopes that had none it went straight
            # to writing one. A dry run has to read, and only read.
            due = _episodic_count(chat_id, character_id) >= MIN_SUMMARY_INPUTS
            key = "would_summarize" if due else "too_few_inputs"
            actions[key] = actions.get(key, 0) + 1
            continue

        result = generate_rolling_summary(
            chat_id, character_id, window_size=args.window,
            min_new_memories_for_refresh=args.min_new,
        )
        actions[result.action] = actions.get(result.action, 0) + 1
        if result.action in ("created", "updated"):
            print(f"  {result.action}: {chat_id[:44]}")

    for action, count in sorted(actions.items()):
        print(f"{action}: {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate rolling summary memories.")
    parser.add_argument("--chat-id")
    parser.add_argument("--character-id")
    parser.add_argument("--all", action="store_true", help="sweep every chat that has episodic memories")
    parser.add_argument("--limit", type=int, help="with --all, stop after N scopes")
    parser.add_argument("--dry-run", action="store_true", help="with --all, report what would be summarized without calling a model")
    parser.add_argument("--window", type=int, default=8)
    parser.add_argument("--min-new", type=int, default=3, help="Minimum new episodic memories required to refresh an existing summary.")
    parser.add_argument("--no-llm", action="store_true", help="Force rule-based summary (skip LLM).")
    args = parser.parse_args()

    if args.no_llm:
        os.environ["LLM_API_BASE"] = ""

    if args.all:
        return _sweep(args)

    if not args.chat_id or not args.character_id:
        parser.error("--chat-id and --character-id are required unless --all is given")

    result = generate_rolling_summary(
        chat_id=args.chat_id,
        character_id=args.character_id,
        window_size=args.window,
        min_new_memories_for_refresh=args.min_new,
    )

    print(f"action={result.action}")
    print(f"chat_id={result.chat_id}")
    print(f"character_id={result.character_id}")
    print(f"summary_memory_id={result.summary_memory_id}")
    print(f"summarized_count={result.summarized_count}")
    print(f"new_input_count={result.new_input_count}")
    print(f"refresh_threshold_used={result.refresh_threshold_used}")
    print(f"source_memory_ids={result.source_memory_ids}")
    if result.summary_text:
        print("summary_text:")
        print(result.summary_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
