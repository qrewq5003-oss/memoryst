#!/usr/bin/env python3
"""Re-extract the memories that were written in English from Russian scenes.

The extraction prompt used to leave its language rule at the top of the rules list
whenever the caller sent no participant names, five rules and a schema away from the
generation. Measured over data/memory.db on 2026-09-21: 170 of the 2603 memories whose
source messages are still on disk came out in English from scenes that are Russian. A
fact in the wrong language cannot match a Russian query's keywords, so those memories
are stored and unreachable.

This is a re-extraction, not a translation. The model reads the scene again and writes
whatever facts it finds now: some of the old ones will not come back, some new ones will
appear, and the wording will differ throughout. That is the honest cost of the repair and
the reason for --dry-run.

Only memories that are all of: English, from a scene whose source messages are Russian,
auto-sourced, unpinned and unarchived are touched. On this database that was all 170 of
them, but the filter is applied rather than assumed - a memory somebody pinned or edited
is theirs, not the extractor's.

    python scripts/reextract_english_memories.py --dry-run      # reads only
    python scripts/reextract_english_memories.py --limit 5      # a few, for real
    python scripts/reextract_english_memories.py                # all of them
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_connection
from app.repositories.memory_repo import (
    create_memory,
    delete_memory,
    find_memory_by_normalized_content,
)
from app.schemas import ChatMessageItem, CreateMemoryRequest, MemoryMetadata
from app.services import vector_store
from app.services.llm_extractor import extract_scene_facts
from app.services.store_service import passes_memory_quality_gate
from app.services.text_utils import normalize_content, scope_character_id

CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
LATIN_RE = re.compile(r"[a-zA-Z]")

# The user's persona name. Extraction needs both participants to avoid writing role words
# ("пользователь", "the user") into content and entities, and the chat rows do not carry
# it - so it is named here rather than guessed per chat.
USER_NAME = "Wanted"


def _is_english_memory(text: str) -> bool:
    """A memory written in English - meaning no Cyrillic in it at all.

    Deliberately stricter than "more Latin than Cyrillic". A fact is one short sentence,
    and these characters are named in Latin, so counting letters calls "Wanted пришёл на
    репетицию Paris1893 в Théâtre de l'Odéon" English. Seven correctly-written Russian
    memories were selected that way on 2026-09-21; re-extracting them would have replaced
    good facts with different ones and left the run with nothing to converge on.

    A Russian fact contains Russian words. One Cyrillic letter is enough to prove it.
    """
    return bool(LATIN_RE.search(text)) and not CYRILLIC_RE.search(text)


def _is_russian_scene(text: str) -> bool:
    """A scene is Russian when most of its letters are.

    The looser test on purpose, and for the same reason the other one is strict: a scene
    is long, and Latin names inside it do not make it an English scene. Requiring zero
    Latin here would exclude nearly every chat in this database.
    """
    return len(CYRILLIC_RE.findall(text)) > len(LATIN_RE.findall(text))


def _collect_scenes() -> dict[tuple, dict]:
    """Group the affected memories by the scene they were extracted from.

    A scene is one LLM call, so this is also what the run costs: the 170 memories come
    from 77 distinct sets of source messages.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, chat_id, character_id, content, metadata_json
            FROM memories
            WHERE type NOT IN ('summary', 'tracker')
              AND source = 'auto' AND pinned = 0 AND archived = 0
            """
        ).fetchall()

        scenes: dict[tuple, dict] = {}
        for row in rows:
            if not _is_english_memory(row["content"]):
                continue
            source_ids = json.loads(row["metadata_json"]).get("source_message_ids") or []
            if not source_ids:
                continue

            placeholders = ",".join("?" * len(source_ids))
            messages = conn.execute(
                f"SELECT * FROM chat_messages WHERE id IN ({placeholders}) ORDER BY sequence_index",
                source_ids,
            ).fetchall()
            if not messages:
                continue
            if not _is_russian_scene(" ".join(m["text"] for m in messages)):
                continue

            key = (row["chat_id"], tuple(m["id"] for m in messages))
            scene = scenes.setdefault(
                key,
                {
                    "chat_id": row["chat_id"],
                    "character_id": row["character_id"],
                    "messages": [ChatMessageItem(**dict(m)) for m in messages],
                    "old_ids": [],
                    "old_contents": [],
                },
            )
            scene["old_ids"].append(row["id"])
            scene["old_contents"].append(row["content"])
        return scenes
    finally:
        conn.close()


def _store(fact: dict, chat_id: str, character_id: str, embed_delay: float) -> bool:
    """Store one extracted fact, through the same gates the live path uses."""
    content = (fact.get("content") or "").strip()
    if not content:
        return False

    candidate = CreateMemoryRequest(
        chat_id=chat_id,
        character_id=character_id,
        type=fact.get("type") or "event",
        content=content,
        source="auto",
        layer=fact.get("layer") or "episodic",
        importance=0.6,
        metadata=MemoryMetadata(
            entities=list(fact.get("entities") or []),
            keywords=list(fact.get("keywords") or []),
            source_message_ids=list(fact.get("source_message_ids") or []),
        ),
    )
    if not passes_memory_quality_gate(candidate):
        return False

    # Chat-scoped, like every other duplicate check since 9763f71.
    existing = find_memory_by_normalized_content(
        chat_id=chat_id,
        character_id=scope_character_id(character_id),
        normalized_content=normalize_content(content),
    )
    if existing is not None:
        return False

    created = create_memory(candidate)
    # Best-effort since vector_store guards it now - a missing vector costs the memory
    # its semantic boost, nothing else. It was not guarded when this run first hit
    # Google's quota at scene 37 of 73, and the exception ended the pass there.
    vector_store.add_memory(
        created.id,
        created.content,
        {"chat_id": created.chat_id, "character_id": created.character_id},
    )
    # Paced because a scene writes 3-5 facts back to back, and that burst is what hit
    # Google's per-minute limit on the first run. The sleep is per embedding rather than
    # per scene: the gap between scenes is already an LLM call several seconds long, so
    # it is the burst inside one that needs flattening, not the interval between them.
    if embed_delay > 0:
        time.sleep(embed_delay)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report the plan, call nothing")
    parser.add_argument("--limit", type=int, help="process only the first N scenes")
    parser.add_argument(
        "--embed-delay",
        type=float,
        default=vector_store.BULK_EMBED_DELAY_SECONDS,
        help="seconds to wait after each embedding; 0 disables the pacing",
    )
    args = parser.parse_args()

    scenes = _collect_scenes()
    keys = sorted(scenes, key=str)
    if args.limit:
        keys = keys[: args.limit]

    memories = sum(len(scenes[k]["old_ids"]) for k in keys)
    characters = sum(len(m.text) for k in keys for m in scenes[k]["messages"])
    print(f"{len(keys)} scenes, {memories} memories, ~{characters // 4} tokens of scene text.")

    if args.dry_run:
        for key in keys[:5]:
            scene = scenes[key]
            print(f"  {scene['chat_id'][:44]}: {len(scene['old_ids'])} memories, "
                  f"{len(scene['messages'])} messages")
        print("--dry-run: nothing was written.")
        return 0

    from app.services.backup_service import create_backup

    print(f"Backup: {create_backup()}\n")

    replaced = removed = created_count = failed = 0
    for index, key in enumerate(keys, start=1):
        scene = scenes[key]
        character = scene["chat_id"].split(" - ")[0]

        facts = extract_scene_facts(
            scene["messages"], character_name=character, user_name=USER_NAME
        )
        if not facts:
            # Deliberately keeps the old memories: an English fact is worse than a
            # Russian one and better than none, and a failed call is not a reason to
            # delete something that is at least there.
            failed += 1
            print(f"  [{index}/{len(keys)}] {scene['chat_id'][:36]}: extraction returned nothing, kept the old")
            continue

        for memory_id in scene["old_ids"]:
            if delete_memory(memory_id):
                removed += 1

        stored = sum(
            _store(f, scene["chat_id"], scene["character_id"], args.embed_delay)
            for f in facts
        )
        created_count += stored
        replaced += 1
        print(f"  [{index}/{len(keys)}] {scene['chat_id'][:36]}: -{len(scene['old_ids'])} +{stored}")

    print(f"\nScenes replaced: {replaced}, failed: {failed}. "
          f"Memories removed: {removed}, created: {created_count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
