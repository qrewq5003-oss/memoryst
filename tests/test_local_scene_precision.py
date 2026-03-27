import unittest
from unittest.mock import patch

from app.schemas import MemoryItem, MemoryMetadata, MessageInput, RetrieveMemoryRequest
from app.services import text_features
from app.services.retrieve_service import _compute_score_details, retrieve_memories


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

    def test_generic_profile_query_does_not_activate_local_scene_mode(self) -> None:
        profile = _memory(
            "profile",
            "Алина любит джаз и часто слушает его ночью.",
            memory_type="profile",
            layer="stable",
        )
        episodic = _memory(
            "episodic",
            "Вчера Алина говорила о джазе после встречи.",
        )

        response = self._retrieve(
            [profile, episodic],
            user_input="Что любит Алина?",
            limit=2,
        )

        assert response.debug is not None
        self.assertFalse(response.debug.local_scene_query_like)
        debug_by_id = {item.memory_id: item for item in response.debug.candidates}
        self.assertEqual(debug_by_id["episodic"].episodic_specificity_bonus, 0.0)
        self.assertEqual(debug_by_id["episodic"].episodic_low_value_penalty, 0.0)

    def test_local_scene_bonus_does_not_override_large_raw_score_gap(self) -> None:
        strong = _memory(
            "strong",
            "На встрече с Леной они договорились перенести общий созвон на утро.",
        )
        weak = _memory(
            "weak",
            "Вчера они решили кое-что.",
        )

        strong_details = _compute_score_details(
            strong,
            text_features.extract_keywords("Что произошло на встрече с Леной?"),
            text_features.extract_entities("Что произошло на встрече с Леной?"),
            user_input_text="Что произошло на встрече с Леной?",
            local_scene_query_like=True,
        )
        weak_details = _compute_score_details(
            weak,
            text_features.extract_keywords("Что произошло на встрече с Леной?"),
            text_features.extract_entities("Что произошло на встрече с Леной?"),
            user_input_text="Что произошло на встрече с Леной?",
            local_scene_query_like=True,
        )

        self.assertGreater(strong_details["score"], weak_details["score"])
        self.assertGreater(strong_details["keyword_overlap"], weak_details["keyword_overlap"])

    def test_local_scene_policy_keeps_summary_context_in_layered_mix(self) -> None:
        summary = _memory(
            "summary",
            "Краткая сводка: Алина и Маркус стараются удержать проект и не сорвать общий график.",
            memory_type="summary",
            layer="stable",
            importance=0.9,
        )
        stable = _memory(
            "stable",
            "Маркус снова доверяет Алине в вопросах проекта.",
            memory_type="relationship",
            layer="stable",
            importance=0.82,
        )
        episodic = _memory(
            "episodic",
            "Только что они решили перенести встречу по проекту на утро и позвать Лену позже.",
        )
        query_echo = _memory(
            "query-echo",
            "Что они решили про встречу по проекту?",
        )

        response = self._retrieve(
            [summary, stable, episodic, query_echo],
            user_input="Что они решили про встречу по проекту?",
            limit=3,
        )

        self.assertIn("summary", {item.id for item in response.items})
        self.assertIn("stable", {item.id for item in response.items})
        assert response.debug is not None
        debug_by_id = {item.memory_id: item for item in response.debug.candidates}
        self.assertGreater(debug_by_id["query-echo"].episodic_low_value_penalty, 0.0)
        self.assertGreater(debug_by_id["episodic"].episodic_specificity_bonus, 0.0)
