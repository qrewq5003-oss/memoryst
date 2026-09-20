"""`created_ids` is what makes undoing a rejected swipe safe.

The extension deletes what a superseded reply created. `items` cannot be used for that:
a candidate that soft-matches an existing memory is merged into it, so `items` also
carries rows that predate the turn, and deleting them would be data loss. These tests
pin the distinction.
"""

import tempfile
import unittest
from pathlib import Path

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import delete_memory, get_memory_by_id
from app.schemas import MessageInput, StoreMemoryRequest, StoreMemoryResponse
from app.services import chat_buffer_service
from app.services.store_service import store_memories


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()
        chat_buffer_service.reset_all_buffers()
        self.addCleanup(chat_buffer_service.reset_all_buffers)

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path


class CreatedIdsTests(_IsolatedDatabase):

    def _store(self, text: str, *, chat_id: str = "chat-1") -> object:
        return store_memories(
            StoreMemoryRequest(
                chat_id=chat_id,
                character_id="char-1",
                messages=[MessageInput(role="user", text=text)],
            )
        )

    def test_created_ids_name_exactly_the_new_memories(self) -> None:
        response = self._store("Алина работает ветеринаром в клинике на Садовой")

        self.assertEqual(len(response.created_ids), response.stored)
        self.assertEqual(set(response.created_ids), {item.id for item in response.items})

    def test_a_merged_candidate_is_not_reported_as_created(self) -> None:
        # The second store must not offer the first store's memory up for deletion:
        # undoing a swipe would then remove a memory the swipe did not create.
        first = self._store("Алина работает ветеринаром в клинике на Садовой")
        self.assertTrue(first.created_ids)
        original_id = first.created_ids[0]

        second = self._store("Алина работает ветеринаром в клинике на Садовой уже пять лет")

        self.assertNotIn(original_id, second.created_ids)
        if second.updated:
            # It was a merge, so the pre-existing row is in items but not in created_ids.
            self.assertIn(original_id, {item.id for item in second.items})

    def test_created_ids_is_empty_when_nothing_is_stored(self) -> None:
        response = self._store("да")
        self.assertEqual(response.created_ids, [])

    def test_every_created_id_resolves_to_a_real_memory(self) -> None:
        response = self._store("Марк боится воды после того случая на озере")
        for memory_id in response.created_ids:
            with self.subTest(memory_id=memory_id):
                self.assertIsNotNone(get_memory_by_id(memory_id))

    def test_deleting_the_created_ids_leaves_earlier_memories_alone(self) -> None:
        # The whole point, end to end: undo the second store and the first survives.
        first = self._store("Алина работает ветеринаром")
        kept = first.created_ids[0]

        second = self._store("Марк живёт в Казани и чинит мотоциклы")
        for memory_id in second.created_ids:
            delete_memory(memory_id)

        self.assertIsNotNone(get_memory_by_id(kept))
        for memory_id in second.created_ids:
            self.assertIsNone(get_memory_by_id(memory_id))

    def test_the_field_is_additive_and_defaults_empty(self) -> None:
        # An older extension ignores it; a newer extension against an older backend sees
        # it missing and offers no undo. Neither is a protocol break.
        self.assertEqual(StoreMemoryResponse(stored=0, updated=0, skipped=0, items=[]).created_ids, [])


class StoreEndpointCreatedIdsTests(_IsolatedDatabase):
    def test_the_endpoint_serializes_created_ids(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        response = client.post(
            "/memory/store",
            json={
                "chat_id": "chat-http",
                "character_id": "char-http",
                "messages": [
                    {"role": "user", "text": "Алина работает ветеринаром в клинике на Садовой"}
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("created_ids", body)
        self.assertEqual(len(body["created_ids"]), body["stored"])


if __name__ == "__main__":
    unittest.main()
