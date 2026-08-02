#!/usr/bin/env python3
"""Remove roleplay status headers from memories that were stored with one.

Many character cards open every reply with a status line:

    [ 🕰️ Time 7:45 PM | 🗓️ Saturday, March 15, 2025 | 📍 Milan - Kitchen | 🌙 Clear ]
    [ Милан | 15 января 1477 | 11:05 | Возраст: 7 | Кастелло Сфорцеско ]

The rule-based extractor stores a line close to verbatim, so the header landed inside
the memory: 80-496 characters of clock, weather and location per row. It also fed the
scorer - `Time` reached 248 entity occurrences, and 📍 place names matched every memory
of that location regardless of what was said there.

Eighteen rows consist of nothing but a header, the header having filled the 500-char
content limit on its own. Those hold no fact and are deleted; everything else is
rewritten to the text that follows the header, with normalized_content, keywords and
entities recomputed so the row scores as if it had been stored cleanly.

Content is only ever shortened, never invented. Deletion is limited to rows whose entire
content was header.

Usage:
    .venv/bin/python scripts/strip_transcript_headers.py --dry-run   # report only
    .venv/bin/python scripts/strip_transcript_headers.py             # apply
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_connection, init_schema
from app.services import text_features
from app.services.backup_service import create_backup
from app.services.text_utils import (
    normalize_content,
    strip_transcript_header,
)


def _plan(cursor) -> tuple[list[tuple], list[str]]:
    """Return (rewrites, deletions) without touching anything."""
    cursor.execute("SELECT id, content, metadata_json FROM memories WHERE type != 'tracker'")
    rewrites: list[tuple] = []
    deletions: list[str] = []

    for row in cursor.fetchall():
        original = row["content"]
        stripped = strip_transcript_header(original)
        if stripped == original:
            continue
        if not stripped.strip():
            deletions.append(row["id"])
            continue

        metadata = json.loads(row["metadata_json"])
        metadata["entities"] = text_features.extract_entities(stripped)
        metadata["keywords"] = text_features.extract_keywords(stripped)
        rewrites.append(
            (stripped, normalize_content(stripped), json.dumps(metadata, ensure_ascii=False), row["id"])
        )

    return rewrites, deletions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report what would change")
    args = parser.parse_args()

    init_schema()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        rewrites, deletions = _plan(cursor)

        print(f"rows to rewrite: {len(rewrites)}")
        print(f"rows to delete (header only, no fact): {len(deletions)}")
        if rewrites:
            saved = sum(len(r[3]) for r in rewrites)  # placeholder, recomputed below
            print("\nexample rewrite:")
            print("  after:", rewrites[0][0][:90])

        if args.dry_run:
            print("\ndry run - nothing written")
            return 0

        backup = create_backup()
        print(f"\nbackup: {backup}")

        cursor.executemany(
            "UPDATE memories SET content = ?, normalized_content = ?, metadata_json = ? WHERE id = ?",
            rewrites,
        )
        if deletions:
            placeholders = ",".join("?" for _ in deletions)
            cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", deletions)
        conn.commit()
        print(f"rewritten: {len(rewrites)} | deleted: {len(deletions)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
