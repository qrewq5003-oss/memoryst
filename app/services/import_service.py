"""Restore memories from a `.jsonl` export.

The export existed without this for a long time and was described as "what a manual
restore would be rebuilt from". It could not have been. It carried twelve fields, and a
memory has more than twenty: `source`, `archived`, the whole of `metadata` beyond
entities and keywords - summary provenance, consolidation history, tracker entries and
watermarks, the `source_message_ids` that link a memory back to the raw chat. A restore
from that file would have come back looking complete while having quietly flattened
every summary into an ordinary row and emptied every tracker.

So the export now writes the full record (schema 2) and this reads both shapes: an old
file still restores, it just cannot conjure back what was never in it, and the result
says so rather than pretending.

Nothing here is clever about merging. An import either adds a memory or leaves the
existing one alone, unless overwrite is asked for explicitly - a restore that silently
reconciles two versions of the same id is not a restore.
"""

import json
from dataclasses import dataclass, field

from app.repositories.memory_repo import (
    get_memory_by_id,
    insert_memory,
    update_memory,
    upsert_tracker,
)
from app.schemas import MemoryItem, MemoryMetadata, UpdateMemoryRequest
from app.services.text_utils import get_utc_now, normalize_content

# Fields an export has always carried. A file with nothing beyond these is schema 1 and
# is restored as best it can be.
_SCHEMA_1_FIELDS = {
    "id", "chat_id", "character_id", "type", "layer", "content",
    "importance", "created_at", "updated_at", "pinned", "entities", "keywords",
    "tracker_type",
}


@dataclass
class ImportReport:
    imported: int = 0
    overwritten: int = 0
    skipped_existing: int = 0
    trackers: int = 0
    invalid: int = 0
    errors: list[str] = field(default_factory=list)
    lossy_lines: int = 0

    @property
    def processed(self) -> int:
        return self.imported + self.overwritten + self.skipped_existing + self.invalid

    def as_dict(self) -> dict:
        return {
            "imported": self.imported,
            "overwritten": self.overwritten,
            "skipped_existing": self.skipped_existing,
            "trackers": self.trackers,
            "invalid": self.invalid,
            "lossy_lines": self.lossy_lines,
            "processed": self.processed,
            # Capped: a corrupt file should explain itself, not fill the page.
            "errors": self.errors[:20],
        }


def _metadata_from_record(record: dict) -> MemoryMetadata:
    """Rebuild metadata from either export shape.

    Schema 2 carries the object whole. Schema 1 carried two of its lists and, for
    trackers, the type - so everything else is genuinely absent and is left at its
    default rather than invented.
    """
    raw = record.get("metadata")
    if isinstance(raw, dict):
        return MemoryMetadata.model_validate(raw)

    return MemoryMetadata(
        entities=list(record.get("entities") or []),
        keywords=list(record.get("keywords") or []),
        tracker_type=record.get("tracker_type"),
    )


def _is_lossy(record: dict) -> bool:
    return not isinstance(record.get("metadata"), dict)


def _memory_from_record(record: dict) -> MemoryItem:
    content = str(record["content"]).strip()
    now = get_utc_now()
    metadata = _metadata_from_record(record)

    return MemoryItem(
        id=str(record["id"]),
        chat_id=str(record["chat_id"]),
        character_id=str(record["character_id"]),
        type=record["type"],
        content=content,
        # Recomputed rather than trusted: it is derived from content, and a file edited
        # by hand (which is half the point of a text export) would otherwise carry a
        # normalized form that no longer matches, breaking every dedup lookup.
        normalized_content=normalize_content(content),
        # Schema 1 had no source. "manual" is the safe reading: it excludes the row from
        # automatic merging and rewriting, which is the right default for something a
        # person restored on purpose.
        source=record.get("source") or "manual",
        layer=record["layer"],
        importance=float(record.get("importance", 0.5)),
        created_at=record.get("created_at") or now,
        updated_at=record.get("updated_at") or now,
        last_accessed_at=record.get("last_accessed_at"),
        access_count=int(record.get("access_count") or 0),
        pinned=bool(record.get("pinned", False)),
        archived=bool(record.get("archived", False)),
        metadata=metadata,
    )


def _import_tracker(memory: MemoryItem) -> None:
    """Trackers go through upsert_tracker, not insert_memory.

    A tracker is unique per (chat, character, tracker_type) at the database level, so
    inserting one whose triple already exists fails on the index rather than replacing
    it - and a restore whose trackers all fail is not a restore.
    """
    upsert_tracker(
        chat_id=memory.chat_id,
        character_id=memory.character_id,
        tracker_type=memory.metadata.tracker_type,
        content=memory.content,
        metadata=memory.metadata,
        importance=memory.importance,
    )


def import_memories_jsonl(payload: str, *, overwrite: bool = False) -> ImportReport:
    """Restore every memory in a `.jsonl` export.

    One malformed line does not abort the import: a truncated or hand-edited file should
    restore what it can and report the rest, because the alternative on a 4000-line
    backup is an all-or-nothing failure over one bad character.
    """
    report = ImportReport()

    for line_number, line in enumerate(payload.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            report.invalid += 1
            report.errors.append(f"line {line_number}: not valid JSON ({exc.msg})")
            continue

        if not isinstance(record, dict):
            report.invalid += 1
            report.errors.append(f"line {line_number}: expected an object")
            continue

        try:
            memory = _memory_from_record(record)
        except (KeyError, ValueError, TypeError) as exc:
            report.invalid += 1
            report.errors.append(f"line {line_number}: {exc}")
            continue

        if _is_lossy(record):
            report.lossy_lines += 1

        if memory.type == "tracker":
            if not memory.metadata.tracker_type:
                report.invalid += 1
                report.errors.append(f"line {line_number}: tracker without tracker_type")
                continue
            _import_tracker(memory)
            report.trackers += 1
            continue

        existing = get_memory_by_id(memory.id)
        if existing is not None:
            if not overwrite:
                report.skipped_existing += 1
                continue
            update_memory(
                memory.id,
                UpdateMemoryRequest(
                    content=memory.content,
                    importance=memory.importance,
                    metadata=memory.metadata,
                ),
            )
            report.overwritten += 1
            continue

        insert_memory(memory)
        report.imported += 1

    return report
