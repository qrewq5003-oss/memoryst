"""The audit needs a home that can be read without a browser.

It lived only in SillyTavern's settings.json, and records went missing: a turn would
store memories, settings.json would be rewritten by an unrelated save, and no audit row
appeared. Reading the in-memory copy needs a browser console, which does not exist on
the phone this runs on - so the diagnostic that had caught every regression in this
codebase was unreadable exactly when it mattered.
"""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.services import audit_sink


class AuditSinkTests(unittest.TestCase):
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

    def test_a_record_survives_a_round_trip(self) -> None:
        record = {"interaction_id": "chat:1", "retrieve_stage": "user_message_sent"}

        response = self.client.post("/memory/audit", json=record)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["stored"])
        self.assertEqual(audit_sink.read_audit_records()[0], record)

    def test_records_come_back_newest_first(self) -> None:
        for index in range(3):
            self.client.post("/memory/audit", json={"n": index})

        self.assertEqual([r["n"] for r in audit_sink.read_audit_records()], [2, 1, 0])

    def test_the_file_is_bounded(self) -> None:
        """It lives on a phone; an unbounded log is its own problem."""
        original = audit_sink.MAX_RECORDS
        audit_sink.MAX_RECORDS = 5
        self.addCleanup(setattr, audit_sink, "MAX_RECORDS", original)

        for index in range(12):
            audit_sink.append_audit_record({"n": index})

        records = audit_sink.read_audit_records(limit=50)
        self.assertEqual(len(records), 5)
        self.assertEqual(records[0]["n"], 11)

    def test_an_unserializable_record_is_kept_rather_than_dropped(self) -> None:
        audit_sink.append_audit_record({"bad": {1, 2, 3}})

        stored = audit_sink.read_audit_records()[0]
        self.assertIn("unserializable_record", stored)

    def test_reading_before_anything_was_written_is_empty(self) -> None:
        self.assertEqual(audit_sink.read_audit_records(), [])

    def test_the_endpoint_accepts_a_full_shaped_record(self) -> None:
        record = {
            "interaction_id": "chat-1:123",
            "extension_build": "3f93dcb",
            "retrieve_stage": "user_message_sent",
            "retrieve": {"user_input_preview": "Что Аллина рассказывала", "returned_item_count": 4},
            "store": {"extraction_method": "llm", "stored": 5},
            "notes": [],
        }

        response = self.client.post("/memory/audit", json=record)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(audit_sink.read_audit_records()[0]["retrieve"]["returned_item_count"], 4)


if __name__ == "__main__":
    unittest.main()
