import tempfile
import unittest
from pathlib import Path

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import list_memories
from app.schemas import MessageInput, StoreMemoryRequest
from app.services.store_service import store_memories


class DurableRelationshipFormationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def test_relationship_arc_state_creates_stable_relationship_memory(self) -> None:
        response = store_memories(
            StoreMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                messages=[
                    MessageInput(
                        role="assistant",
                        text="После тяжёлого разговора Маркус снова доверяет Алисе в работе, хотя между ними всё ещё остаётся осторожность.",
                    )
                ],
            )
        )

        self.assertEqual(response.stored, 1)
        created = list_memories(chat_id="chat-1", character_id="char-1").items[0]
        self.assertEqual(created.type, "relationship")
        self.assertEqual(created.layer, "stable")

    def test_question_form_user_prompt_does_not_become_stable_relationship(self) -> None:
        response = store_memories(
            StoreMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                messages=[
                    MessageInput(
                        role="user",
                        text="Они хоть немного помирились после разговора?",
                    )
                ],
            )
        )

        self.assertEqual(response.stored, 0)
        self.assertEqual(response.updated, 0)
        self.assertEqual(response.skipped, 0)
        self.assertEqual(list_memories(chat_id="chat-1", character_id="char-1").total, 0)

    def test_one_off_meeting_episode_does_not_turn_into_stable_relationship(self) -> None:
        response = store_memories(
            StoreMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                messages=[
                    MessageInput(
                        role="assistant",
                        text="Вчера Маркус и Алиса встретились у вокзала и спорили о времени встречи.",
                    )
                ],
            )
        )

        self.assertEqual(response.stored, 1)
        created = list_memories(chat_id="chat-1", character_id="char-1").items[0]
        self.assertEqual(created.type, "event")
        self.assertEqual(created.layer, "episodic")

    def test_one_off_support_scene_without_carry_over_wording_stays_episodic(self) -> None:
        response = store_memories(
            StoreMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                messages=[
                    MessageInput(
                        role="assistant",
                        text="Вчера Маркус помог Алисе донести коробки до студии и сразу уехал.",
                    )
                ],
            )
        )

        self.assertEqual(response.stored, 1)
        created = list_memories(chat_id="chat-1", character_id="char-1").items[0]
        self.assertEqual(created.type, "event")
        self.assertEqual(created.layer, "episodic")

    def test_profile_statement_does_not_spuriously_become_relationship_memory(self) -> None:
        response = store_memories(
            StoreMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                messages=[
                    MessageInput(
                        role="assistant",
                        text="Алиса работает врачом и живёт в Риме.",
                    )
                ],
            )
        )

        self.assertEqual(response.stored, 1)
        created = list_memories(chat_id="chat-1", character_id="char-1").items[0]
        self.assertEqual(created.type, "profile")
        self.assertEqual(created.layer, "stable")

    def test_repeated_relationship_arc_can_leave_summary_and_stable_relation_together(self) -> None:
        store_memories(
            StoreMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                messages=[
                    MessageInput(role="assistant", text="Маркус снова доверяет Алисе в рабочих вопросах."),
                    MessageInput(role="assistant", text="После ссоры между ними всё ещё остаётся осторожность."),
                    MessageInput(role="assistant", text="Они снова работают вместе над фильмом и держат друг друга в курсе."),
                ],
            )
        )

        items = list_memories(chat_id="chat-1", character_id="char-1").items
        relationship_items = [item for item in items if item.type == "relationship" and item.layer == "stable"]
        self.assertGreaterEqual(len(relationship_items), 1)

    def test_summary_and_stable_relation_do_not_appear_from_single_scene_without_carry_over(self) -> None:
        store_memories(
            StoreMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                messages=[
                    MessageInput(
                        role="assistant",
                        text="Вчера на встрече Маркус резко спорил с Алисой о бюджете фильма.",
                    )
                ],
            )
        )

        items = list_memories(chat_id="chat-1", character_id="char-1").items
        stable_relationship_items = [item for item in items if item.type == "relationship" and item.layer == "stable"]
        self.assertEqual(stable_relationship_items, [])


if __name__ == "__main__":
    unittest.main()
