"""
Re-apply the current size limits to trackers already stored in the database.

normalize_payload clamps what the LLM returns, so a tracker only picks up a changed limit
the next time it regenerates - and a tracker that has caught up with its chat never
regenerates at all ("skipped_no_new_messages"). That left the NPC document sitting on
350-char descriptions and the relationship document on nine custom dimensions long after
both were capped. This script closes that gap: it feeds each stored document back through
the same normalize_payload + render_tracker path a fresh LLM reply would take, so the
result is exactly what the model would have produced under today's rules.

Watermarks are untouched: nothing new is consumed, the document is only re-shaped.

    python -m scripts.reclamp_trackers            # report what would change
    python -m scripts.reclamp_trackers --apply    # write it
"""

import argparse
import json
import sqlite3

from app.config import config
from app.services.tracker_prompts import normalize_payload, render_tracker


def entries_to_payload(tracker_type: str, entries: list[dict]) -> dict:
    """Rebuild the LLM-payload shape that normalize_payload expects from stored entries."""
    if tracker_type == "timeline":
        return {"entries": entries}
    if tracker_type == "npc_whoswho":
        return {"npcs": entries}
    if tracker_type == "character_pov_notes":
        return {"notes": [e.get("note", "") for e in entries]}
    if tracker_type == "relationship":
        return entries[0] if entries else {}
    raise ValueError(f"unknown tracker type: {tracker_type}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    connection = sqlite3.connect(config.DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, chat_id, character_id, content, metadata_json FROM memories WHERE type = 'tracker'"
    ).fetchall()

    changed = 0
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        tracker_type = metadata.get("tracker_type")
        entries = metadata.get("tracker_entries") or []
        if not tracker_type or not entries:
            continue

        reclamped = normalize_payload(tracker_type, entries_to_payload(tracker_type, entries))
        content = render_tracker(tracker_type, reclamped)

        if content == row["content"]:
            print(f"  {tracker_type} ({row['chat_id']}): already within limits")
            continue

        changed += 1
        print(
            f"  {tracker_type} ({row['chat_id']}): "
            f"{len(row['content'])} -> {len(content)} chars, "
            f"{len(entries)} -> {len(reclamped)} entries"
        )

        if args.apply:
            metadata["tracker_entries"] = reclamped
            connection.execute(
                "UPDATE memories SET content = ?, metadata_json = ? WHERE id = ?",
                (content, json.dumps(metadata, ensure_ascii=False), row["id"]),
            )

    if args.apply:
        connection.commit()
        print(f"\napplied to {changed} tracker(s)")
    else:
        print(f"\n{changed} tracker(s) would change; re-run with --apply")

    connection.close()


if __name__ == "__main__":
    main()
