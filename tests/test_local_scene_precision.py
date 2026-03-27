import unittest
from unittest.mock import patch

from app.schemas import MemoryItem, MemoryMetadata, MessageInput, RetrieveMemoryRequest
from app.services import text_features
from app.services.retrieve_service import retrieve_memories


def _memory(
    memory_id: str,
    content: str,
    *,
    memory_type: str = "event",
    layer: str = "episodic",
    importance: float = 0.8,
    updated_at: str = "2026-03-26T00:00:00+00:00",
) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id="chat-1",
        character_id="char-1",
        type=memory_type,
        content=content,
        normalized_content=content.lower(),
        source="manual",
        layer=layer,
        importance=importance,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at=updated_at,
        last_accessed_at=None,
        access_count=0,
        pinned=False,
        archived=False,
        metadata=MemoryMetadata(
            entities=text_features.extract_entities(content),
            keywords=text_features.extract_keywords(content),
            is_summary=(memory_type == "summary"),
        ),
    )


class LocalScenePrecisionTests(unittest.TestCase):
    def _retrieve(
        self,
        memories: list[MemoryItem],
        *,
        user_input: str,
        limit: int = 5,
        recent_messages: list[MessageInput] | None = None,
    ):
        with (
            patch("app.services.retrieve_service.list_retrieval_candidates", return_value=memories),
            patch("app.services.retrieve_service.increment_access_count"),
            patch("app.services.retrieve_service.format_memory_block", return_value="formatted"),
        ):
            return retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input=user_input,
                    recent_messages=recent_messages or [],
                    limit=limit,
                    debug=True,
                )
            )

    def test_query_echo_episodic_is_penalized_for_local_scene_query(self) -> None:
        query_echo = _memory("query-echo", "Что они решили про встречу по проекту?")
        concrete = _memory(
            "concrete",
            "Только что Алина и Маркус решили перенести встречу по проекту на утро и позвать Лену позже.",
        )

        response = self._retrieve(
            [query_echo, concrete],
            user_input="Что они решили про встречу по проекту?",
            limit=2,
        )

        self.assertEqual(response.items[0].id, "concrete")
        assert response.debug is not None
        debug_by_id = {item.memory_id: item for item in response.debug.candidates}
        self.assertTrue(response.debug.local_scene_query_like)
        self.assertGreater(debug_by_id["concrete"].episodic_specificity_bonus, 0.0)
        self.assertGreater(debug_by_id["query-echo"].episodic_low_value_penalty, 0.0)

    def test_concrete_meeting_outcome_beats_generic_meeting_episode(self) -> None:
        generic = _memory("generic", "У них была встреча с Леной.")
        detailed = _memory(
            "detailed",
            "На встрече с Леной они договорились пересобрать монтажный план и перенести общий созвон на утро.",
        )

        response = self._retrieve(
            [generic, detailed],
            user_input="Что произошло на встрече с Леной?",
            limit=1,
        )

        self.assertEqual([item.id for item in response.items], ["detailed"])

    def test_broad_relationship_query_does_not_trigger_local_scene_penalties(self) -> None:
        summary = _memory(
            "summary",
            "Краткая сводка: после ссоры они снова работают вместе, но между ними ещё есть напряжение.",
            memory_type="summary",
            layer="stable",
            importance=0.95,
        )
        relationship = _memory(
            "relationship",
            "Маркус снова доверяет Алине, хотя всё ещё держит дистанцию.",
            memory_type="relationship",
            layer="stable",
            importance=0.84,
        )
        episodic = _memory(
            "episodic",
            "Вчера они договорились не возвращаться к старой ссоре до конца поездки.",
            importance=0.78,
        )

        response = self._retrieve(
            [summary, relationship, episodic],
            user_input="Как он теперь к ней относится?",
            limit=3,
            recent_messages=[
                MessageInput(role="user", text="После ссоры они всё-таки снова работают вместе."),
            ],
        )

        assert response.debug is not None
        self.assertFalse(response.debug.local_scene_query_like)
        debug_by_id = {item.memory_id: item for item in response.debug.candidates}
        self.assertEqual(debug_by_id["episodic"].episodic_low_value_penalty, 0.0)
        self.assertIn("summary", {item.id for item in response.items})
