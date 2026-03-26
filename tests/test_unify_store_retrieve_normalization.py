import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.memory_repo import create_memory
from app.schemas import (
    CreateMemoryRequest,
    ListMemoriesResponse,
    MemoryItem,
    MemoryMetadata,
    MessageInput,
    RetrieveMemoryRequest,
)
from app.services import text_features
from app.services.extractor import extract_memories
from app.services.retrieve_service import retrieve_memories


class UnifyStoreRetrieveNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def test_store_path_uses_shared_text_features_module(self) -> None:
        with (
            patch("app.services.text_features.extract_keywords", return_value=["shared-keyword"]) as keywords_mock,
            patch("app.services.text_features.extract_entities", return_value=["SharedEntity"]) as entities_mock,
        ):
            candidates = extract_memories(
                chat_id="chat-1",
                character_id="char-1",
                messages=[MessageInput(role="user", text="I love Rome with Elena.")],
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].metadata.keywords, ["shared-keyword"])
        self.assertEqual(candidates[0].metadata.entities, ["SharedEntity"])
        keywords_mock.assert_called_once_with("I love Rome with Elena.")
        entities_mock.assert_called_once_with("I love Rome with Elena.")

    def test_retrieve_path_uses_shared_text_features_module_for_input_and_recent_messages(self) -> None:
        memory = MemoryItem(
            id="memory-1",
            chat_id="chat-1",
            character_id="char-1",
            type="event",
            content="Elena visited Rome.",
            normalized_content="elena visited rome",
            source="manual",
            layer="episodic",
            importance=0.7,
            created_at="2026-03-26T00:00:00+00:00",
            updated_at="2026-03-26T00:00:00+00:00",
            last_accessed_at=None,
            access_count=0,
            pinned=False,
            archived=False,
            metadata=MemoryMetadata(entities=["Elena"], keywords=["rome"]),
        )

        with (
            patch(
                "app.services.retrieve_service.list_retrieval_candidates",
                return_value=[memory],
            ),
            patch("app.services.retrieve_service.increment_access_count"),
            patch("app.services.retrieve_service.format_memory_block", return_value="formatted"),
            patch(
                "app.services.text_features.extract_keywords",
                side_effect=[["rome"], ["trip"]],
            ) as keywords_mock,
            patch(
                "app.services.text_features.extract_entities",
                side_effect=[["Elena"], ["Rome"]],
            ) as entities_mock,
        ):
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input="Tell me about Elena in Rome",
                    recent_messages=[MessageInput(role="user", text="We discussed the trip yesterday")],
                    limit=5,
                )
            )

        self.assertEqual([item.id for item in response.items], ["memory-1"])
        self.assertEqual(keywords_mock.call_args_list[0].args[0], "Tell me about Elena in Rome")
        self.assertEqual(keywords_mock.call_args_list[1].args[0], "We discussed the trip yesterday")
        self.assertEqual(entities_mock.call_args_list[0].args[0], "Tell me about Elena in Rome")
        self.assertEqual(entities_mock.call_args_list[1].args[0], "We discussed the trip yesterday")

    def test_russian_word_forms_match_between_store_metadata_and_retrieve_query(self) -> None:
        target_metadata = MemoryMetadata(
            entities=text_features.extract_entities("Анна любит кошек."),
            keywords=text_features.extract_keywords("Анна любит кошек."),
        )
        distractor_metadata = MemoryMetadata(
            entities=text_features.extract_entities("Анна любит собак."),
            keywords=text_features.extract_keywords("Анна любит собак."),
        )

        create_memory(
            CreateMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                type="profile",
                content="Анна любит кошек.",
                source="manual",
                layer="stable",
                importance=0.7,
                metadata=target_metadata,
            )
        )
        create_memory(
            CreateMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                type="profile",
                content="Анна любит собак.",
                source="manual",
                layer="stable",
                importance=0.7,
                metadata=distractor_metadata,
            )
        )

        response = retrieve_memories(
            RetrieveMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                user_input="Расскажи мне про кошку",
                limit=2,
            )
        )

        self.assertGreaterEqual(len(response.items), 1)
        self.assertEqual(response.items[0].content, "Анна любит кошек.")
        self.assertIn("кошка", target_metadata.keywords)
        self.assertIn("кошка", text_features.extract_keywords("Расскажи мне про кошку"))

    def test_same_text_produces_compatible_store_and_retrieve_features(self) -> None:
        text = "Elena discussed films in Rome with Marcus."
        candidates = extract_memories(
            chat_id="chat-1",
            character_id="char-1",
            messages=[MessageInput(role="user", text=text)],
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].metadata.keywords, text_features.extract_keywords(text))
        self.assertEqual(candidates[0].metadata.entities, text_features.extract_entities(text))


if __name__ == "__main__":
    unittest.main()
