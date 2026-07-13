import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.repositories.chat_message_repo import insert_chat_message
from app.schemas import ChatMessageItem
from app.services import llm_client
from app.services.chat_buffer_service import reset_all_buffers

CHAT_ID = "chat-api"
CHARACTER_ID = "20"

TIMELINE_PAYLOAD = {
    "entries": [
        {
            "date": "Thursday, February 13, 2025",
            "time": "7:45 PM",
            "location": "Milan",
            "summary": "она приехала",
            "source_message_indices": [0],
        }
    ]
}


class TrackerApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_key = config.API_KEY
        self.original_db_path = config.DATABASE_PATH
        self.original_provider_override = llm_client._active_provider_override
        config.API_KEY = ""
        llm_client._active_provider_override = None
        self.addCleanup(self._restore)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        init_schema()
        reset_all_buffers()
        self.addCleanup(reset_all_buffers)

        self.client = TestClient(app)

    def _restore(self) -> None:
        config.API_KEY = self.original_api_key
        config.DATABASE_PATH = self.original_db_path
        llm_client._active_provider_override = self.original_provider_override

    def _cool(self, *texts: str, start: int = 0) -> None:
        for offset, text in enumerate(texts):
            insert_chat_message(
                ChatMessageItem(
                    id=str(uuid.uuid4()),
                    chat_id=CHAT_ID,
                    character_id=CHARACTER_ID,
                    role="user" if offset % 2 == 0 else "assistant",
                    text=text,
                    created_at="2026-07-13T10:00:00Z",
                    sequence_index=start + offset,
                )
            )

    def _llm(self, payload: dict = None):
        return patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=lambda *a, **kw: json.dumps(payload or TIMELINE_PAYLOAD),
        )


class TrackerRouteOrderingTests(TrackerApiTestCase):
    def test_get_trackers_is_not_swallowed_by_the_catch_all_id_route(self) -> None:
        # GET /memory/{id} is declared later but matches any single segment; if
        # /memory/trackers were registered after it, this would 404 as "no memory with
        # id='trackers'" instead of returning the (empty) tracker list.
        response = self.client.get(
            "/memory/trackers", params={"chat_id": CHAT_ID, "character_id": CHARACTER_ID}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": []})

    def test_tracker_update_is_not_swallowed_by_the_id_subroutes(self) -> None:
        self._cool("первое")

        with self._llm():
            response = self.client.post(
                "/memory/tracker/update",
                json={
                    "chat_id": CHAT_ID,
                    "character_id": CHARACTER_ID,
                    "tracker_type": "timeline",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["action"], "created")


class TrackerUpdateEndpointTests(TrackerApiTestCase):
    def test_update_creates_then_rewrites_the_tracker(self) -> None:
        self._cool("первое")

        with self._llm():
            created = self.client.post(
                "/memory/tracker/update",
                json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "timeline"},
            ).json()

        self.assertEqual(created["action"], "created")
        self.assertEqual(created["entries_count"], 1)
        self.assertEqual(created["messages_consumed"], 1)
        self.assertEqual(created["extraction_method"], "llm")
        self.assertIn("Milan", created["content"])

        self._cool("второе", start=1)
        with self._llm():
            updated = self.client.post(
                "/memory/tracker/update",
                json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "timeline"},
            ).json()

        self.assertEqual(updated["action"], "updated")

    def test_update_with_no_new_messages_is_reported_as_such(self) -> None:
        with self._llm():
            response = self.client.post(
                "/memory/tracker/update",
                json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "timeline"},
            ).json()

        self.assertEqual(response["action"], "skipped_no_new_messages")
        self.assertEqual(response["messages_consumed"], 0)

    def test_missing_llm_is_reported_rather_than_producing_an_empty_tracker(self) -> None:
        self._cool("первое")

        with patch("app.services.tracker_service.is_llm_enabled", return_value=False):
            response = self.client.post(
                "/memory/tracker/update",
                json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "timeline"},
            ).json()

        self.assertEqual(response["action"], "skipped_llm_unavailable")
        self.assertEqual(response["content"], "")
        self.assertTrue(response["error_detail"])

    def test_llm_failure_reports_error_detail_for_the_ui_to_display(self) -> None:
        self._cool("первое")

        def boom(*args, **kwargs):
            raise RuntimeError("502 from provider")

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=boom,
        ):
            response = self.client.post(
                "/memory/tracker/update",
                json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "timeline"},
            ).json()

        self.assertEqual(response["action"], "skipped_llm_failed")
        self.assertTrue(response["error_detail"])

    def test_an_unknown_tracker_type_is_rejected_by_validation(self) -> None:
        response = self.client.post(
            "/memory/tracker/update",
            json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "horoscope"},
        )

        self.assertEqual(response.status_code, 422)

    def test_model_override_reaches_the_llm_call(self) -> None:
        self._cool("первое")
        seen = {}

        def capture(messages, **kwargs):
            seen["model"] = kwargs.get("model")
            return json.dumps(TIMELINE_PAYLOAD)

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=capture,
        ):
            self.client.post(
                "/memory/tracker/update",
                json={
                    "chat_id": CHAT_ID,
                    "character_id": CHARACTER_ID,
                    "tracker_type": "timeline",
                    "model": "deepseek/deepseek-v4-pro",
                },
            )

        self.assertEqual(seen["model"], "deepseek/deepseek-v4-pro")


class ListTrackersEndpointTests(TrackerApiTestCase):
    def test_listing_returns_content_entries_and_staleness(self) -> None:
        self._cool("первое")
        with self._llm():
            self.client.post(
                "/memory/tracker/update",
                json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "timeline"},
            )

        self._cool("второе", "третье", start=1)

        items = self.client.get(
            "/memory/trackers", params={"chat_id": CHAT_ID, "character_id": CHARACTER_ID}
        ).json()["items"]

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["tracker_type"], "timeline")
        self.assertEqual(item["last_sequence_index"], 0)
        self.assertEqual(item["messages_since_update"], 2)
        self.assertEqual(len(item["entries"]), 1)
        self.assertIn("Milan", item["content"])
        self.assertTrue(item["memory_id"])

    def test_listing_is_scoped_to_the_chat_and_character(self) -> None:
        self._cool("первое")
        with self._llm():
            self.client.post(
                "/memory/tracker/update",
                json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "timeline"},
            )

        other = self.client.get(
            "/memory/trackers", params={"chat_id": "other-chat", "character_id": CHARACTER_ID}
        ).json()["items"]

        self.assertEqual(other, [])


class StoreResponseCounterTests(TrackerApiTestCase):
    def test_store_reports_tracker_staleness_without_an_extra_request(self) -> None:
        self._cool("первое")
        with self._llm():
            self.client.post(
                "/memory/tracker/update",
                json={"chat_id": CHAT_ID, "character_id": CHARACTER_ID, "tracker_type": "timeline"},
            )

        response = self.client.post(
            "/memory/store",
            json={
                "chat_id": CHAT_ID,
                "character_id": CHARACTER_ID,
                "messages": [{"role": "user", "text": "ещё одна реплика"}],
            },
        ).json()

        self.assertEqual(
            response["trackers"], [{"tracker_type": "timeline", "messages_since_update": 1}]
        )

    def test_store_contract_is_unchanged_when_no_tracker_exists(self) -> None:
        response = self.client.post(
            "/memory/store",
            json={
                "chat_id": CHAT_ID,
                "character_id": CHARACTER_ID,
                "messages": [{"role": "user", "text": "привет"}],
            },
        )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        # Additive field only: everything the extension already relies on is still there.
        self.assertEqual(body["trackers"], [])
        for field in ("stored", "updated", "skipped", "items"):
            self.assertIn(field, body)


if __name__ == "__main__":
    unittest.main()
