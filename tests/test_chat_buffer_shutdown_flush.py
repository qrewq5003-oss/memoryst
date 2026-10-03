"""The hot buffer is written out on shutdown, not lost.

Measured 2026-10-03 on the live database: 60 of 587 memories pointed at source
messages that never reached chat_messages, and two short chats had facts but no raw
history at all. Every ordinary restart dropped the newest HOT_BUFFER_SIZE messages of
every chat - exactly the ones raw-history fallback most needs.
"""

import unittest

from fastapi.testclient import TestClient

from app.repositories.chat_message_repo import list_chat_messages
from app.schemas import MessageInput
from app.services import chat_buffer_service
from app.services.chat_buffer_service import (
    HOT_BUFFER_SIZE,
    add_messages,
    flush_all_buffers,
    get_hot_buffer,
)
from tests.test_chat_message_dedupe import ChatMessageDedupeTestCase

CHAT = "short chat"
CHARACTER = "Mai.png"


def _messages(count: int) -> list[MessageInput]:
    roles = ("user", "assistant")
    return [MessageInput(role=roles[i % 2], text=f"реплика номер {i}") for i in range(count)]


def _stored(chat_id: str = CHAT) -> list:
    return list_chat_messages(chat_id=chat_id, character_id=CHARACTER, limit=100)


class ShutdownFlushTests(ChatMessageDedupeTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.db import init_schema

        init_schema()

    def test_a_chat_that_never_overflowed_the_buffer_reaches_the_table(self) -> None:
        added = add_messages(CHAT, CHARACTER, _messages(HOT_BUFFER_SIZE))
        self.assertEqual(_stored(), [], "precondition: nothing cooled yet")

        self.assertEqual(flush_all_buffers(), HOT_BUFFER_SIZE)

        stored = _stored()
        # Same ids, so memories' source_message_ids stay valid.
        self.assertEqual({m.id for m in stored}, {m.id for m in added})
        self.assertEqual(get_hot_buffer(CHAT, CHARACTER), [])

    def test_the_resent_window_after_a_restart_adds_no_duplicates(self) -> None:
        window = _messages(HOT_BUFFER_SIZE + 3)
        add_messages(CHAT, CHARACTER, window)
        flush_all_buffers()
        before = _stored()

        # A restart: in-memory state is gone; the extension resends the same window.
        chat_buffer_service.reset_all_buffers()
        resent = add_messages(CHAT, CHARACTER, window)

        self.assertEqual(len(_stored()), len(before))
        self.assertEqual({m.id for m in resent}, {m.id for m in before})

    def test_sequence_continues_after_the_flush(self) -> None:
        add_messages(CHAT, CHARACTER, _messages(2))
        flush_all_buffers()
        chat_buffer_service.reset_all_buffers()

        new = add_messages(CHAT, CHARACTER, [MessageInput(role="user", text="совсем новая реплика")])

        self.assertEqual(new[0].sequence_index, max(m.sequence_index for m in _stored()) + 1)

    def test_the_server_flushes_on_shutdown(self) -> None:
        from app.main import app

        with TestClient(app):
            add_messages(CHAT, CHARACTER, _messages(3))
        self.assertEqual(len(_stored()), 3)


if __name__ == "__main__":
    unittest.main()
