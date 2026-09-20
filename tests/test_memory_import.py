"""Import is only worth having if export → import loses nothing.

The export was described as "what a manual restore would be rebuilt from" while carrying
twelve of a memory's twenty-odd fields. A restore from it came back looking complete and
was not: summaries lost their provenance, trackers lost their entries and watermarks,
and every row lost its `source` and `archived`. So the round trip is what these tests
pin, not the parser.
"""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.repositories.memory_repo import (
    create_memory,
    get_memory_by_id,
    insert_memory,
    list_memories,
    upsert_tracker,
)
from app.schemas import (
    ConsolidationHistoryEntry,
    CreateMemoryRequest,
    MemoryMetadata,
)
from app.services.import_service import import_memories_jsonl


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore)
        init_schema()
        self.client = TestClient(app)

    def _restore(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _export(self) -> str:
        response = self.client.get("/ui/export")
        self.assertEqual(response.status_code, 200)
        return response.text

    def _wipe(self) -> None:
        from app.repositories.memory_repo import delete_memory

        page = list_memories(limit=1000, include_trackers=True)
        for item in page.items:
            delete_memory(item.id)


class RoundTripTests(_IsolatedDatabase):
    def _seed_rich_memory(self):
        return create_memory(
            CreateMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                type="summary",
                content="Алина и Марк помирились после ссоры из-за поездки",
                source="auto",
                layer="stable",
                importance=0.8,
                pinned=True,
                metadata=MemoryMetadata(
                    entities=["Алина", "Марк"],
                    keywords=["ссора", "поездка", "помирились"],
                    is_summary=True,
                    summary_kind="rolling",
                    summary_source_memory_ids=["m-1", "m-2"],
                    summarized_memory_count=2,
                    source_message_ids=["msg-7", "msg-8"],
                    consolidation_note="слито из двух эпизодов",
                    review_status="approved",
                    consolidation_history=[
                        ConsolidationHistoryEntry(
                            action="merged",
                            timestamp="2026-08-01T10:00:00+00:00",
                            related_memory_id="m-1",
                            note="слито из двух эпизодов",
                        )
                    ],
                ),
            )
        )

    def test_a_summary_survives_the_round_trip_whole(self) -> None:
        original = self._seed_rich_memory()
        payload = self._export()
        self._wipe()
        self.assertIsNone(get_memory_by_id(original.id))

        report = import_memories_jsonl(payload)
        self.assertEqual(report.imported, 1)

        restored = get_memory_by_id(original.id)
        self.assertIsNotNone(restored)
        # The fields the old export dropped, one by one - this is the whole point.
        self.assertEqual(restored.source, original.source)
        self.assertEqual(restored.archived, original.archived)
        self.assertEqual(restored.pinned, original.pinned)
        self.assertTrue(restored.metadata.is_summary)
        self.assertEqual(restored.metadata.summary_kind, "rolling")
        self.assertEqual(restored.metadata.summary_source_memory_ids, ["m-1", "m-2"])
        self.assertEqual(restored.metadata.source_message_ids, ["msg-7", "msg-8"])
        self.assertEqual(restored.metadata.consolidation_note, "слито из двух эпизодов")
        self.assertEqual(restored.metadata.review_status, "approved")
        self.assertEqual(len(restored.metadata.consolidation_history), 1)
        self.assertEqual(restored.metadata.consolidation_history[0].action, "merged")

    def test_a_tracker_survives_with_its_entries_and_watermark(self) -> None:
        upsert_tracker(
            chat_id="chat-1",
            character_id="char-1",
            tracker_type="timeline",
            content="День 1: приехали. День 2: поссорились.",
            metadata=MemoryMetadata(
                tracker_type="timeline",
                tracker_generated_at="2026-08-01T10:00:00+00:00",
                tracker_last_sequence_index=42,
                tracker_entries=[{"day": 1, "text": "приехали"}],
            ),
        )
        payload = self._export()
        self._wipe()

        report = import_memories_jsonl(payload)
        self.assertEqual(report.trackers, 1)

        restored = list_memories(limit=50, include_trackers=True).items
        tracker = next(item for item in restored if item.type == "tracker")
        self.assertEqual(tracker.metadata.tracker_type, "timeline")
        self.assertEqual(tracker.metadata.tracker_last_sequence_index, 42)
        self.assertEqual(tracker.metadata.tracker_entries, [{"day": 1, "text": "приехали"}])

    def test_importing_a_tracker_twice_rewrites_it_rather_than_failing(self) -> None:
        # A tracker is unique per (chat, character, type) in the database, so a plain
        # insert on the second import would hit the index and lose every tracker.
        upsert_tracker(
            chat_id="chat-1",
            character_id="char-1",
            tracker_type="relationship",
            content="Тёплые отношения",
            metadata=MemoryMetadata(tracker_type="relationship"),
        )
        payload = self._export()

        first = import_memories_jsonl(payload)
        second = import_memories_jsonl(payload)

        self.assertEqual(first.trackers, 1)
        self.assertEqual(second.trackers, 1)
        trackers = [
            item
            for item in list_memories(limit=50, include_trackers=True).items
            if item.type == "tracker"
        ]
        self.assertEqual(len(trackers), 1)

    def test_the_export_declares_its_schema(self) -> None:
        self._seed_rich_memory()
        record = json.loads(self._export().splitlines()[0])
        self.assertEqual(record["schema_version"], 2)
        self.assertIn("metadata", record)


class IdempotenceTests(_IsolatedDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.memory = create_memory(
            CreateMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                type="event",
                content="Марк починил мотоцикл брата в субботу",
                source="auto",
                layer="episodic",
                importance=0.5,
                metadata=MemoryMetadata(entities=["Марк"], keywords=["мотоцикл", "суббота"]),
            )
        )
        self.payload = self._export()

    def test_importing_over_an_existing_memory_skips_it_by_default(self) -> None:
        report = import_memories_jsonl(self.payload)
        self.assertEqual(report.skipped_existing, 1)
        self.assertEqual(report.imported, 0)
        self.assertEqual(list_memories(limit=50).total, 1)

    def test_overwrite_replaces_the_existing_row(self) -> None:
        edited = self.payload.replace("починил мотоцикл брата", "продал мотоцикл брата")
        report = import_memories_jsonl(edited, overwrite=True)
        self.assertEqual(report.overwritten, 1)
        self.assertIn("продал", get_memory_by_id(self.memory.id).content)

    def test_importing_twice_never_duplicates(self) -> None:
        import_memories_jsonl(self.payload)
        import_memories_jsonl(self.payload)
        self.assertEqual(list_memories(limit=50).total, 1)


class MalformedInputTests(_IsolatedDatabase):
    def test_one_bad_line_does_not_abort_the_restore(self) -> None:
        # A 4000-line backup must not be lost to one bad character.
        good = json.dumps(
            {
                "id": "m-good",
                "chat_id": "c",
                "character_id": "x",
                "type": "event",
                "layer": "episodic",
                "content": "Алина уехала в Казань",
                "importance": 0.5,
            },
            ensure_ascii=False,
        )
        payload = "\n".join([good, "{not json", "[]", json.dumps({"id": "m-2"})])

        report = import_memories_jsonl(payload)

        self.assertEqual(report.imported, 1)
        self.assertEqual(report.invalid, 3)
        self.assertIsNotNone(get_memory_by_id("m-good"))
        self.assertTrue(any("not valid JSON" in error for error in report.errors))

    def test_blank_lines_are_not_counted_as_failures(self) -> None:
        report = import_memories_jsonl("\n\n   \n")
        self.assertEqual(report.processed, 0)
        self.assertEqual(report.invalid, 0)

    def test_a_tracker_without_its_type_is_rejected_not_guessed(self) -> None:
        payload = json.dumps(
            {
                "id": "t-1",
                "chat_id": "c",
                "character_id": "x",
                "type": "tracker",
                "layer": "stable",
                "content": "что-то",
                "importance": 0.5,
            }
        )
        report = import_memories_jsonl(payload)
        self.assertEqual(report.invalid, 1)
        self.assertEqual(report.trackers, 0)


class LegacyExportTests(_IsolatedDatabase):
    def test_a_schema_1_file_still_restores(self) -> None:
        legacy = json.dumps(
            {
                "id": "old-1",
                "chat_id": "c",
                "character_id": "x",
                "type": "event",
                "layer": "episodic",
                "content": "Алина работает ветеринаром",
                "importance": 0.7,
                "created_at": "2026-05-01T10:00:00+00:00",
                "updated_at": "2026-05-01T10:00:00+00:00",
                "pinned": False,
                "entities": ["Алина"],
                "keywords": ["ветеринар", "работа"],
            },
            ensure_ascii=False,
        )
        report = import_memories_jsonl(legacy)

        self.assertEqual(report.imported, 1)
        # Reported, not hidden: the file genuinely could not carry the rest.
        self.assertEqual(report.lossy_lines, 1)

        restored = get_memory_by_id("old-1")
        self.assertEqual(restored.metadata.entities, ["Алина"])
        # No source in the file. "manual" keeps automatic merging from rewriting a row
        # a person restored on purpose.
        self.assertEqual(restored.source, "manual")

    def test_a_schema_2_file_is_not_reported_as_lossy(self) -> None:
        create_memory(
            CreateMemoryRequest(
                chat_id="c",
                character_id="x",
                type="event",
                content="Марк живёт в Казани",
                source="auto",
                layer="episodic",
                importance=0.5,
                metadata=MemoryMetadata(),
            )
        )
        payload = self._export()
        self._wipe()
        self.assertEqual(import_memories_jsonl(payload).lossy_lines, 0)


class ImportEndpointTests(_IsolatedDatabase):
    def test_the_endpoint_restores_an_uploaded_file(self) -> None:
        create_memory(
            CreateMemoryRequest(
                chat_id="c",
                character_id="x",
                type="event",
                content="Алина уехала в Казань на неделю",
                source="auto",
                layer="episodic",
                importance=0.5,
                metadata=MemoryMetadata(),
            )
        )
        payload = self._export()
        self._wipe()

        response = self.client.post(
            "/ui/import",
            files={"file": ("memories.jsonl", payload, "application/jsonl")},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("import_imported=1", response.headers["location"])
        self.assertEqual(list_memories(limit=50).total, 1)

    def test_the_endpoint_is_behind_the_same_origin_guard(self) -> None:
        # It writes in bulk from an uploaded file; a cross-site form post must not reach it.
        response = self.client.post(
            "/ui/import",
            files={"file": ("x.jsonl", "", "application/jsonl")},
            headers={"Origin": "https://evil.example"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
