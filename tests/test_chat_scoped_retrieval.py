"""The retrieval scope is the chat, not the character.

`character_id` arrives from SillyTavern's `getContext().characterId`, which is the
character's position in the characters array. Adding, deleting or reordering a card
shifts every index above it and the scope moves with it, silently. Measured over
data/memory.db on 2026-09-20: `character_id=20` appears in 15 unrelated chats, and 9 of
54 chats had their own memories split across two or three ids - the worst holding 360
memories in three unreachable pieces. A live turn in one of them scored exactly one
candidate and injected nothing.

These tests pin that a chat can see all of its own memories, and that what used to be
enforced by filtering - not answering an Alice question with Elena's memory - is still
true, because relevance was doing that work all along.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory, find_memory_by_normalized_content
from app.schemas import CreateMemoryRequest, MemoryMetadata, MessageInput, RetrieveMemoryRequest, StoreMemoryRequest
from app.services.retrieve_service import retrieve_memories
from app.services.store_service import store_memories
from app.services.text_utils import normalize_content, scope_character_id


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore)
        init_schema()

    def _restore(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _memory(self, *, character_id: str, content: str, keywords, entities):
        return create_memory(
            CreateMemoryRequest(
                chat_id="chat-1",
                character_id=character_id,
                type="profile",
                content=content,
                source="auto",
                layer="stable",
                importance=0.7,
                metadata=MemoryMetadata(keywords=keywords, entities=entities),
            )
        )

    def _retrieve(self, user_input: str, character_id: str):
        with patch("app.services.retrieve_service.increment_access_count"):
            return retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id=character_id,
                    user_input=user_input,
                    limit=5,
                )
            )


class ShiftedIndexTests(_IsolatedDatabase):
    def test_a_chat_sees_memories_stored_under_an_older_index(self) -> None:
        # The exact live failure: the chat's history sat under index 20, the turn ran as
        # 25, and retrieval found one candidate out of 218.
        target = self._memory(
            character_id="20",
            content="Алина Волкова работает ветеринаром в клинике на Садовой",
            keywords=["алина", "волкова", "ветеринар", "клиника"],
            entities=["Алина Волкова"],
        )

        response = self._retrieve("где работает Алина", character_id="25")

        self.assertIn(target.id, [item.id for item in response.items])

    def test_memories_split_across_three_indexes_are_all_reachable(self) -> None:
        # Deliberately distinct facts rather than three phrasings of one: near-identical
        # memories are collapsed by the near-duplicate filter, and that is correct
        # behaviour this test has no business fighting.
        ids = {
            self._memory(
                character_id="20",
                content="Алина работает ветеринаром в клинике на Садовой",
                keywords=["алина", "ветеринар", "клиника"],
                entities=["Алина"],
            ).id,
            self._memory(
                character_id="21",
                content="Алина боится летать после случая в детстве",
                keywords=["алина", "летать", "детство"],
                entities=["Алина"],
            ).id,
            self._memory(
                character_id="25",
                content="Алина держит дома трёхлапого кота по кличке Профессор",
                keywords=["алина", "кот", "профессор"],
                entities=["Алина"],
            ).id,
        }

        # Each fact is asked for separately, from the newest index - the point is that
        # every one of them is reachable at all, not that they arrive together.
        found = set()
        for question in ("где работает Алина", "чего боится Алина", "какой у Алины кот"):
            response = self._retrieve(question, character_id="25")
            found.update(item.id for item in response.items)

        self.assertEqual(found & ids, ids)

    def test_another_chat_is_still_out_of_reach(self) -> None:
        # Widening the scope to the chat must not widen it past the chat.
        create_memory(
            CreateMemoryRequest(
                chat_id="chat-OTHER",
                character_id="20",
                type="profile",
                content="Алина Волкова работает ветеринаром в клинике на Садовой",
                source="auto",
                layer="stable",
                importance=0.9,
                metadata=MemoryMetadata(keywords=["алина", "ветеринар"], entities=["Алина"]),
            )
        )

        response = self._retrieve("где работает Алина", character_id="20")

        self.assertEqual(response.items, [])


class RelevanceStillSeparatesTests(_IsolatedDatabase):
    def test_an_alice_question_is_not_answered_with_elenas_memory(self) -> None:
        # This used to be enforced by the scope filter. It holds without one, because
        # ranking was doing the work: the two memories share no keywords or entities.
        alice = self._memory(
            character_id="char-alice",
            content="Алиса хочет закончить фильм до фестиваля",
            keywords=["алиса", "фильм", "фестиваль"],
            entities=["Алиса"],
        )
        self._memory(
            character_id="char-elena",
            content="Елена хочет уехать утром к морю",
            keywords=["елена", "уехать", "море"],
            entities=["Елена"],
        )

        response = self._retrieve("Чего хочет Алиса с фильмом?", character_id="char-alice")

        self.assertEqual([item.id for item in response.items], [alice.id])


class DedupAcrossIndexesTests(_IsolatedDatabase):
    def _store(self, character_id: str, text: str):
        return store_memories(
            StoreMemoryRequest(
                chat_id="chat-1",
                character_id=character_id,
                messages=[MessageInput(role="user", text=text)],
            )
        )

    def test_the_same_fact_under_a_new_index_is_not_stored_twice(self) -> None:
        # A shifted index hid a chat's own memories from the dedup pass too, so every
        # fact was re-stored the first time the index moved.
        text = "Алина Волкова работает ветеринаром в клинике на Садовой"
        first = self._store("20", text)
        self.assertEqual(first.stored, 1)

        second = self._store("25", text)

        self.assertEqual(second.stored, 0)

    def test_the_exact_duplicate_lookup_is_chat_scoped(self) -> None:
        stored = self._memory(
            character_id="20",
            content="Марк чинит мотоциклы в гараже у брата",
            keywords=["марк", "мотоцикл", "гараж"],
            entities=["Марк"],
        )
        found = find_memory_by_normalized_content(
            chat_id="chat-1",
            character_id=scope_character_id("25"),
            normalized_content=normalize_content(stored.content),
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.id, stored.id)

    def test_passing_a_character_id_still_narrows_the_lookup(self) -> None:
        # The parameter is optional, not ignored - the UI and API still scope by it.
        stored = self._memory(
            character_id="20",
            content="Марк чинит мотоциклы в гараже у брата",
            keywords=["марк", "мотоцикл"],
            entities=["Марк"],
        )
        self.assertIsNone(
            find_memory_by_normalized_content(
                chat_id="chat-1",
                character_id="25",
                normalized_content=normalize_content(stored.content),
            )
        )


if __name__ == "__main__":
    unittest.main()
