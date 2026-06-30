import tempfile
import unittest
from pathlib import Path

from app.config import config
from app.db import init_schema
from app.repositories.chat_message_repo import (
    get_chat_message_by_id,
    get_max_sequence_index,
    list_chat_messages,
    search_chat_messages_fts,
)
from app.services.chat_buffer_service import (
    HOT_BUFFER_SIZE,
    add_message,
    add_messages,
    get_hot_buffer,
    is_filtered_input,
    reset_all_buffers,
)
from app.schemas import MessageInput


class ChatMessageRawStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()
        reset_all_buffers()
        self.addCleanup(reset_all_buffers)

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    # -- Schema / FTS --------------------------------------------------

    def test_chat_messages_table_and_fts_index_exist(self) -> None:
        from app.db import get_connection

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'chat_messages'"
            )
            self.assertIsNotNone(cursor.fetchone())

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE name = 'chat_messages_fts'"
            )
            self.assertIsNotNone(cursor.fetchone())

    def test_fts_search_finds_cooled_message_by_content(self) -> None:
        # Push 5 messages so the first one cools into chat_messages.
        for i in range(5):
            add_message("chat-1", "char-1", "user", f"сообщение номер {i} про драконов")

        results = search_chat_messages_fts("chat-1", "char-1", "драконов")
        self.assertTrue(any("номер 0" in r.text for r in results))

    def test_fts_search_is_scoped_to_chat_and_character(self) -> None:
        for i in range(5):
            add_message("chat-1", "char-1", "user", f"уникальныйтермин{i} в чате один")
        for i in range(5):
            add_message("chat-2", "char-1", "user", f"уникальныйтермин{i} в чате два")

        results = search_chat_messages_fts("chat-1", "char-1", "уникальныйтермин0")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chat_id, "chat-1")

    # -- Hot buffer ------------------------------------------------------

    def test_buffer_holds_up_to_four_messages_without_persisting(self) -> None:
        for i in range(HOT_BUFFER_SIZE):
            add_message("chat-1", "char-1", "user", f"message {i}")

        self.assertEqual(len(get_hot_buffer("chat-1", "char-1")), HOT_BUFFER_SIZE)
        self.assertEqual(list_chat_messages("chat-1", "char-1"), [])

    def test_fifth_message_cools_the_oldest_into_chat_messages(self) -> None:
        added = [
            add_message("chat-1", "char-1", "user", f"message {i}")
            for i in range(HOT_BUFFER_SIZE + 1)
        ]

        cooled = list_chat_messages("chat-1", "char-1")
        self.assertEqual(len(cooled), 1)
        self.assertEqual(cooled[0].id, added[0].id)
        self.assertEqual(cooled[0].text, "message 0")

        buffer = get_hot_buffer("chat-1", "char-1")
        self.assertEqual(len(buffer), HOT_BUFFER_SIZE)
        self.assertEqual([m.text for m in buffer], [f"message {i}" for i in range(1, HOT_BUFFER_SIZE + 1)])

    def test_buffer_is_per_chat_and_character(self) -> None:
        add_message("chat-1", "char-1", "user", "hello from chat 1")
        add_message("chat-2", "char-1", "user", "hello from chat 2")
        add_message("chat-1", "char-2", "user", "hello from char 2")

        self.assertEqual(len(get_hot_buffer("chat-1", "char-1")), 1)
        self.assertEqual(len(get_hot_buffer("chat-2", "char-1")), 1)
        self.assertEqual(len(get_hot_buffer("chat-1", "char-2")), 1)

    def test_sequence_index_is_monotonic_across_buffer_and_cooled(self) -> None:
        for i in range(10):
            add_message("chat-1", "char-1", "user", f"message {i}")

        cooled = list_chat_messages("chat-1", "char-1")
        buffer = get_hot_buffer("chat-1", "char-1")
        all_indices = [m.sequence_index for m in cooled] + [m.sequence_index for m in buffer]
        self.assertEqual(all_indices, sorted(all_indices))
        self.assertEqual(len(set(all_indices)), len(all_indices))

    def test_sequence_continues_correctly_after_reloading_buffer_state(self) -> None:
        # Cool some messages, then simulate a process restart by clearing only
        # the in-memory buffer (chat_messages on disk is untouched).
        for i in range(6):
            add_message("chat-1", "char-1", "user", f"message {i}")
        reset_all_buffers()

        self.assertEqual(get_max_sequence_index("chat-1", "char-1"), 1)

        add_message("chat-1", "char-1", "user", "message after restart")
        cooled_after = list_chat_messages("chat-1", "char-1")
        self.assertEqual(get_max_sequence_index("chat-1", "char-1"), 1)
        # The new message is still in the buffer (only 1 message so far), but its
        # sequence index must not collide with anything already cooled.
        buffer = get_hot_buffer("chat-1", "char-1")
        self.assertEqual(len(buffer), 1)
        self.assertGreater(buffer[0].sequence_index, max(m.sequence_index for m in cooled_after))

    # -- UUID stability ----------------------------------------------------

    def test_message_id_is_uuid_assigned_on_buffer_entry_and_stable_through_cooling(self) -> None:
        message = add_message("chat-1", "char-1", "user", "first message")
        self.assertIsNotNone(message)
        original_id = message.id

        for i in range(HOT_BUFFER_SIZE):
            add_message("chat-1", "char-1", "user", f"filler {i}")

        cooled = get_chat_message_by_id(original_id)
        self.assertIsNotNone(cooled)
        self.assertEqual(cooled.id, original_id)
        self.assertEqual(cooled.text, "first message")

    def test_ids_are_not_sequential_indices(self) -> None:
        import uuid

        m1 = add_message("chat-1", "char-1", "user", "a")
        m2 = add_message("chat-1", "char-1", "user", "b")

        # Should parse as UUIDs, and not simply be "0"/"1" or similar.
        uuid.UUID(m1.id)
        uuid.UUID(m2.id)
        self.assertNotEqual(m1.id, m2.id)

    # -- OOC / system filtering on input ------------------------------------

    def test_system_messages_are_filtered_and_never_enter_buffer(self) -> None:
        result = add_message("chat-1", "char-1", "system", "You are a helpful assistant.")
        self.assertIsNone(result)
        self.assertEqual(get_hot_buffer("chat-1", "char-1"), [])

    def test_ooc_prefixed_messages_are_filtered(self) -> None:
        for text in ["OOC: let's skip ahead", "(OOC need a sec)", "ooc: brb", "Ooc(quick note)"]:
            with self.subTest(text=text):
                reset_all_buffers()
                result = add_message("chat-1", "char-1", "user", text)
                self.assertIsNone(result)
                self.assertEqual(get_hot_buffer("chat-1", "char-1"), [])

    def test_is_filtered_input_helper_matches_add_message_behavior(self) -> None:
        self.assertTrue(is_filtered_input("system", "anything"))
        self.assertTrue(is_filtered_input("user", "OOC: hi"))
        self.assertFalse(is_filtered_input("user", "regular in-character line"))
        self.assertFalse(is_filtered_input("assistant", "another in-character line"))

    def test_add_messages_batch_skips_filtered_and_keeps_order(self) -> None:
        messages = [
            MessageInput(role="system", text="system prompt"),
            MessageInput(role="user", text="OOC: meta comment"),
            MessageInput(role="user", text="Hello there"),
            MessageInput(role="assistant", text="General Kenobi"),
        ]
        added = add_messages("chat-1", "char-1", messages)

        self.assertEqual([m.text for m in added], ["Hello there", "General Kenobi"])
        self.assertEqual(len(get_hot_buffer("chat-1", "char-1")), 2)


if __name__ == "__main__":
    unittest.main()
