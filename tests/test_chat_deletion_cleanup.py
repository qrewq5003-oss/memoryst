"""Deleting a chat must remove everything that chat owns.

It used to remove only what list_memories could see. Trackers are hidden from that
view, so they outlived their chat and a chat re-created under the same id inherited
them. chat_messages was not touched at all - the repository had no delete function -
so the raw text of a "deleted" chat stayed in the database and stayed reachable
through retrieval's FTS fallback.

These run against a real SQLite database. A mocked repository would have reproduced
the original hole exactly and passed.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.chat_message_repo import list_chat_messages, search_chat_messages_fts
from app.repositories.memory_repo import (
    create_memory,
    list_memories,
    list_trackers,
    upsert_tracker,
)
from app.schemas import CreateMemoryRequest, MemoryMetadata, MessageInput
from app.services import chat_buffer_service
from app.services.chat_cleanup_service import delete_chat_data

CHAT_ID = "chat-doomed"
OTHER_CHAT_ID = "chat-innocent"
CHARACTER_ID = "20"
OTHER_CHARACTER_ID = "21"


class ChatDeletionCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _make_memory(self, content: str, *, chat_id: str = CHAT_ID, character_id: str = CHARACTER_ID):
        return create_memory(
            CreateMemoryRequest(
                chat_id=chat_id,
                character_id=character_id,
                type="event",
                content=content,
                source="manual",
                layer="episodic",
                importance=0.7,
                metadata=MemoryMetadata(),
            )
        )

    def _make_tracker(self, *, chat_id: str = CHAT_ID, character_id: str = CHARACTER_ID, tracker_type: str = "timeline"):
        item, _ = upsert_tracker(
            chat_id=chat_id,
            character_id=character_id,
            tracker_type=tracker_type,
            content="- Thursday, February 13, 2025 - Milan: arrived.",
            metadata=MemoryMetadata(),
        )
        return item

    def _cool_messages(self, count: int, *, chat_id: str = CHAT_ID, character_id: str = CHARACTER_ID) -> None:
        """Push enough messages that some cool out of the hot buffer into the table."""
        chat_buffer_service.add_messages(
            chat_id,
            character_id,
            [
                MessageInput(role="user" if i % 2 == 0 else "assistant", text=f"Реплика {i} про Милан.")
                for i in range(count)
            ],
        )

    def test_deleting_a_chat_removes_its_trackers(self) -> None:
        self._make_tracker()
        self._make_tracker(tracker_type="relationship")
        self._make_memory("She likes espresso.")

        result = delete_chat_data(CHAT_ID, CHARACTER_ID)

        self.assertEqual(list_trackers(CHAT_ID, CHARACTER_ID), [])
        self.assertEqual(result.deleted_trackers, 2)
        self.assertEqual(result.deleted_memories, 1)

    def test_deleting_a_chat_removes_its_raw_messages(self) -> None:
        self._cool_messages(10)
        self.assertTrue(list_chat_messages(CHAT_ID, CHARACTER_ID))

        result = delete_chat_data(CHAT_ID, CHARACTER_ID)

        self.assertEqual(list_chat_messages(CHAT_ID, CHARACTER_ID), [])
        self.assertGreater(result.deleted_raw_messages, 0)

    def test_deleted_raw_messages_are_gone_from_the_fts_index_too(self) -> None:
        """The FTS index is what made this a real leak rather than a tidiness issue:
        raw text of a deleted chat stayed searchable by retrieval's fallback."""
        self._cool_messages(10)
        self.assertTrue(search_chat_messages_fts(CHAT_ID, CHARACTER_ID, "Милан", limit=5))

        delete_chat_data(CHAT_ID, CHARACTER_ID)

        self.assertEqual(search_chat_messages_fts(CHAT_ID, CHARACTER_ID, "Милан", limit=5), [])

    def test_hot_buffer_does_not_refill_the_table_after_deletion(self) -> None:
        """Up to HOT_BUFFER_SIZE messages live only in memory. Without dropping the
        buffer they cool straight back into the table the delete just emptied."""
        self._cool_messages(10)

        delete_chat_data(CHAT_ID, CHARACTER_ID)

        self.assertEqual(chat_buffer_service.get_hot_buffer(CHAT_ID, CHARACTER_ID), [])

        # A fresh message after deletion must not drag the old ones back in.
        self._cool_messages(6)
        remaining = list_chat_messages(CHAT_ID, CHARACTER_ID)
        self.assertTrue(all("Реплика" in m.text for m in remaining))
        self.assertEqual([m.sequence_index for m in remaining], sorted(m.sequence_index for m in remaining))
        self.assertEqual(min(m.sequence_index for m in remaining), 0)

    def test_deletion_is_scoped_and_spares_other_chats_and_characters(self) -> None:
        self._make_tracker()
        self._make_memory("Doomed fact.")
        self._cool_messages(10)

        self._make_tracker(chat_id=OTHER_CHAT_ID)
        self._make_memory("Innocent fact.", chat_id=OTHER_CHAT_ID)
        self._cool_messages(10, chat_id=OTHER_CHAT_ID)

        self._make_tracker(character_id=OTHER_CHARACTER_ID)
        self._make_memory("Other character fact.", character_id=OTHER_CHARACTER_ID)

        delete_chat_data(CHAT_ID, CHARACTER_ID)

        self.assertEqual(list_trackers(OTHER_CHAT_ID, CHARACTER_ID) != [], True)
        self.assertTrue(list_chat_messages(OTHER_CHAT_ID, CHARACTER_ID))
        self.assertEqual(
            [m.content for m in list_memories(chat_id=OTHER_CHAT_ID).items],
            ["Innocent fact."],
        )
        self.assertTrue(list_trackers(CHAT_ID, OTHER_CHARACTER_ID))

    def test_omitting_character_id_clears_every_character_in_the_chat(self) -> None:
        self._make_tracker()
        self._make_memory("A.")
        self._make_tracker(character_id=OTHER_CHARACTER_ID)
        self._make_memory("B.", character_id=OTHER_CHARACTER_ID)
        self._cool_messages(10)
        self._cool_messages(10, character_id=OTHER_CHARACTER_ID)

        delete_chat_data(CHAT_ID)

        self.assertEqual(list_trackers(CHAT_ID, CHARACTER_ID), [])
        self.assertEqual(list_trackers(CHAT_ID, OTHER_CHARACTER_ID), [])
        self.assertEqual(list_chat_messages(CHAT_ID, CHARACTER_ID), [])
        self.assertEqual(list_chat_messages(CHAT_ID, OTHER_CHARACTER_ID), [])
        self.assertEqual(list_memories(chat_id=CHAT_ID, include_trackers=True).total, 0)

    def test_vectors_are_dropped_for_trackers_and_memories_alike(self) -> None:
        self._make_tracker()
        memory = self._make_memory("She likes espresso.")

        with patch("app.services.chat_cleanup_service.vector_store.delete_memory") as delete_vector:
            delete_chat_data(CHAT_ID, CHARACTER_ID)

        deleted_ids = {call.args[0] for call in delete_vector.call_args_list}
        self.assertIn(memory.id, deleted_ids)

    def test_a_chat_recreated_under_the_same_id_starts_clean(self) -> None:
        """The user-visible symptom: a new chat inheriting the previous one's
        timeline and relationship documents."""
        self._make_tracker()
        self._make_memory("Old life.")
        delete_chat_data(CHAT_ID, CHARACTER_ID)

        self._make_memory("New life.")

        self.assertEqual(list_trackers(CHAT_ID, CHARACTER_ID), [])
        self.assertEqual(
            [m.content for m in list_memories(chat_id=CHAT_ID, character_id=CHARACTER_ID).items],
            ["New life."],
        )


if __name__ == "__main__":
    unittest.main()
