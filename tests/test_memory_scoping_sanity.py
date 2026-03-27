import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, list_memories
from app.schemas import CreateMemoryRequest, MemoryMetadata, MessageInput, RetrieveMemoryRequest, StoreMemoryRequest
from app.services.retrieve_service import retrieve_memories
from app.services.store_service import store_memories
from app.services.summary_service import generate_rolling_summary


def _create_memory(
    *,
    chat_id: str,
    character_id: str,
    content: str,
    memory_type: str = "event",
    layer: str = "episodic",
    keywords: list[str] | None = None,
    entities: list[str] | None = None,
    importance: float = 0.7,
):
    return create_memory(
        CreateMemoryRequest(
            chat_id=chat_id,
            character_id=character_id,
            type=memory_type,
            content=content,
            source="manual",
            layer=layer,
            importance=importance,
            metadata=MemoryMetadata(
                keywords=keywords or [],
                entities=entities or [],
                is_summary=(memory_type == "summary"),
            ),
        )
    )


class MemoryScopingSanityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def test_retrieval_does_not_mix_same_character_across_different_chats(self) -> None:
        target = _create_memory(
            chat_id="chat-a",
            character_id="char-1",
            content="Алиса и Маркус договорились обсудить бюджет фильма.",
            keywords=["алиса", "маркус", "бюджет", "фильм"],
            entities=["Алиса", "Маркус"],
        )
        _create_memory(
            chat_id="chat-b",
            character_id="char-1",
            content="Алиса и Маркус поехали на пляж обсуждать отпуск.",
            keywords=["алиса", "маркус", "пляж", "отпуск"],
            entities=["Алиса", "Маркус"],
        )

        with patch("app.services.retrieve_service.increment_access_count"):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-a",
                    character_id="char-1",
                    user_input="Что они решили про бюджет фильма?",
                    limit=5,
                )
            )

        self.assertEqual([item.id for item in response.items], [target.id])

    def test_retrieval_does_not_mix_same_chat_across_different_characters(self) -> None:
        target = _create_memory(
            chat_id="chat-main",
            character_id="char-alice",
            content="Алиса хочет закончить фильм до фестиваля.",
            memory_type="profile",
            layer="stable",
            keywords=["алиса", "фильм", "фестиваль"],
            entities=["Алиса"],
        )
        _create_memory(
            chat_id="chat-main",
            character_id="char-elena",
            content="Елена хочет уехать утром к морю.",
            memory_type="profile",
            layer="stable",
            keywords=["елена", "уехать", "море"],
            entities=["Елена"],
        )

        with patch("app.services.retrieve_service.increment_access_count"):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-main",
                    character_id="char-alice",
                    user_input="Чего хочет Алиса с фильмом?",
                    limit=5,
                )
            )

        self.assertEqual([item.id for item in response.items], [target.id])

    def test_store_does_not_update_exact_duplicate_from_foreign_scope(self) -> None:
        existing = _create_memory(
            chat_id="chat-a",
            character_id="char-1",
            content="Алиса боится грозы и грома.",
            memory_type="profile",
            layer="stable",
            keywords=["алиса", "гроза"],
            entities=["Алиса"],
        )
        candidate = CreateMemoryRequest(
            chat_id="chat-b",
            character_id="char-1",
            type="profile",
            content="Алиса боится грозы и грома.",
            source="auto",
            layer="stable",
            importance=0.7,
            metadata=MemoryMetadata(keywords=["алиса", "грозa"], entities=["Алиса"]),
        )

        with patch("app.services.store_service.extract_memories", return_value=[candidate]):
            response = store_memories(
                StoreMemoryRequest(
                    chat_id="chat-b",
                    character_id="char-1",
                    messages=[MessageInput(role="user", text="irrelevant")],
                    debug=True,
                )
            )

        self.assertEqual(response.stored, 1)
        self.assertEqual(response.updated, 0)
        self.assertEqual(list_memories(chat_id="chat-a", character_id="char-1").items[0].id, existing.id)
        self.assertEqual(list_memories(chat_id="chat-b", character_id="char-1").total, 1)

    def test_rolling_summary_is_scoped_per_chat_and_character_and_updates_only_own_scope(self) -> None:
        for content in (
            "Алиса поссорилась с Маркусом из-за бюджета.",
            "Позже они решили продолжить проект без новой ссоры.",
            "Алиса хочет закончить монтаж до конца недели.",
        ):
            _create_memory(chat_id="chat-1", character_id="char-a", content=content)

        for content in (
            "Елена вернулась к разговору о поездке.",
            "Позже Елена купила новые билеты на поезд.",
            "Елена хочет уехать утром и никого не ждать.",
        ):
            _create_memory(chat_id="chat-1", character_id="char-b", content=content)

        first_a = generate_rolling_summary("chat-1", "char-a", window_size=8)
        first_b = generate_rolling_summary("chat-1", "char-b", window_size=8)

        for content in (
            "Маркус пообещал не возвращаться к старой ссоре.",
            "Они перенесли встречу команды на утро.",
            "Алиса всё ещё хочет удержать проект на плаву.",
        ):
            _create_memory(chat_id="chat-1", character_id="char-a", content=content)

        updated_a = generate_rolling_summary("chat-1", "char-a", window_size=8)
        skipped_b = generate_rolling_summary("chat-1", "char-b", window_size=8)

        summaries_a = [
            memory for memory in list_memories(chat_id="chat-1", character_id="char-a", limit=20).items
            if memory.type == "summary"
        ]
        summaries_b = [
            memory for memory in list_memories(chat_id="chat-1", character_id="char-b", limit=20).items
            if memory.type == "summary"
        ]

        self.assertEqual(first_a.summary_memory_id, updated_a.summary_memory_id)
        self.assertEqual(first_b.summary_memory_id, summaries_b[0].id)
        self.assertEqual(updated_a.action, "updated")
        self.assertEqual(skipped_b.action, "skipped_not_enough_new_inputs")
        self.assertEqual(len(summaries_a), 1)
        self.assertEqual(len(summaries_b), 1)
        self.assertNotEqual(summaries_a[0].id, summaries_b[0].id)
        self.assertNotIn("Елена", summaries_a[0].content)
        self.assertNotIn("Алиса", summaries_b[0].content)


if __name__ == "__main__":
    unittest.main()
