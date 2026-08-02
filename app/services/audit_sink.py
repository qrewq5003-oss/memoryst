"""A second home for integration audit records, readable without a browser.

The audit lived only in SillyTavern's settings.json. That is a fragile single home:
records went missing repeatedly - a turn would store memories, settings.json would be
rewritten by an unrelated save, and no audit row appeared for it. Reading the in-memory
copy needs a browser console, and this runs on a phone where there is no console, so the
one tool that had caught every regression in this codebase became unreadable exactly
when it was needed.

Records are appended here as JSON lines. `tail data/audit.jsonl` is the whole interface.
The file is bounded so it cannot grow without limit on a phone.
"""
import json
from pathlib import Path

from app.config import config

AUDIT_FILENAME = "audit.jsonl"
MAX_RECORDS = 500


def _audit_path() -> Path:
    return Path(config.DATABASE_PATH).parent / AUDIT_FILENAME


def _trim(path: Path) -> int:
    """Keep the newest MAX_RECORDS lines. Returns how many remain."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_RECORDS:
        lines = lines[-MAX_RECORDS:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def append_audit_record(record: dict) -> int:
    """Append one record. Returns the number of records now held.

    Never raises for a malformed record: the caller is a fire-and-forget client, and an
    audit problem must not become a turn problem. Anything unserialisable is stored as a
    repr so the record is still visible rather than silently dropped.
    """
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        line = json.dumps(record, ensure_ascii=False)
    except (TypeError, ValueError):
        line = json.dumps({"unserializable_record": repr(record)[:2000]}, ensure_ascii=False)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return _trim(path)


def read_audit_records(limit: int = 20) -> list[dict]:
    """Newest records first. Used by tooling and tests, not by the hot path."""
    path = _audit_path()
    if not path.exists():
        return []
    records = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(records) >= limit:
            break
    return records
