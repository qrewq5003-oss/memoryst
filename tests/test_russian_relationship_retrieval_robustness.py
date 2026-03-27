import unittest
from unittest.mock import patch

from app.schemas import MemoryItem, MemoryMetadata, MessageInput, RetrieveMemoryRequest
from app.services import text_features
from app.services.retrieve_service import retrieve_memories


def _memory(
    memory_id: str,
    content: str,
    *,
    memory_type: str,
    layer: str,
    importance: float = 0.8,
    updated_at: str = "2026-03-20T00:00:00+00:00",
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
            summary_kind="rolling_v1" if memory_type == "summary" else None,
        ),
    )


class RussianRelationshipRetrievalRobustnessTests(unittest.TestCase):
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

    def test_relationship_status_query_keeps_summary_stable_and_fresh_repair_scene(self) -> None:
        summary = _memory(
            "summary",
            "Краткая сводка: после ссоры Алиса и Маркус частично помирились, но между ними всё ещё есть напряжение.",
            memory_type="summary",
            layer="stable",
            importance=0.95,
        )
        stable = _memory(
            "stable",
            "Маркус снова доверяет Алисе в работе, хотя держит осторожную дистанцию.",
            memory_type="relationship",
            layer="stable",
            importance=0.84,
        )
        recent_truce = _memory(
            "recent-truce",
            "Вчера Алиса и Маркус договорились не возвращаться к старой ссоре до конца поездки.",
            memory_type="event",
            layer="episodic",
            importance=0.78,
            updated_at="2026-03-26T00:00:00+00:00",
        )
        old_fight = _memory(
            "old-fight",
            "Неделю назад Маркус сорвался на Алису после провала на съёмке.",
            memory_type="event",
            layer="episodic",
            importance=0.52,
            updated_at="2026-03-10T00:00:00+00:00",
        )

        response = self._retrieve(
            [summary, stable, recent_truce, old_fight],
            user_input="Он всё ещё на неё злится или они уже помирились?",
            limit=3,
            recent_messages=[
                MessageInput(role="user", text="Напомни, что сейчас между Алисой и Маркусом после всех сцен."),
            ],
        )

        self.assertEqual({item.id for item in response.items}, {"summary", "stable", "recent-truce"})
        self.assertNotIn("old-fight", [item.id for item in response.items])
        assert response.debug is not None
        self.assertTrue(response.debug.relationship_query_like)
        self.assertIn("conflict", response.debug.input_relationship_cues)
        self.assertIn("repair", response.debug.input_relationship_cues)

    def test_local_scene_query_stays_episodic_first(self) -> None:
        summary = _memory(
            "summary",
            "Краткая сводка: Алина и Маркус снова работают вместе над фильмом, но осторожничают после ссоры.",
            memory_type="summary",
            layer="stable",
            importance=0.95,
        )
        stable = _memory(
            "stable",
            "Маркус снова помогает Алине с фильмом и постепенно возвращает доверие.",
            memory_type="relationship",
            layer="stable",
            importance=0.82,
        )
        episodic = _memory(
            "episodic",
            "Только что Алина и Маркус решили перенести встречу по проекту на утро.",
            memory_type="event",
            layer="episodic",
            importance=0.9,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        response = self._retrieve(
            [summary, stable, episodic],
            user_input="Что они решили про встречу по проекту?",
            limit=3,
            recent_messages=[
                MessageInput(role="user", text="Мы говорили об их общем статусе по проекту."),
            ],
        )

        self.assertEqual(response.items[0].id, "episodic")
        assert response.debug is not None
        self.assertFalse(response.debug.relationship_query_like)

    def test_non_relationship_query_does_not_activate_relationship_mode(self) -> None:
        profile = _memory(
            "profile",
            "Алиса работает хирургом и живёт в Риме.",
            memory_type="profile",
            layer="stable",
            importance=0.82,
        )
        relationship = _memory(
            "relationship",
            "Маркус всё ещё осторожно относится к Алисе после ссоры.",
            memory_type="relationship",
            layer="stable",
            importance=0.82,
        )

        response = self._retrieve(
            [profile, relationship],
            user_input="Кем работает Алиса?",
            limit=2,
        )

        assert response.debug is not None
        self.assertFalse(response.debug.relationship_query_like)
        self.assertEqual(response.items[0].id, "profile")
        debug_by_id = {item.memory_id: item for item in response.debug.candidates}
        self.assertEqual(debug_by_id["profile"].relationship_support_bonus, 0.0)
        self.assertEqual(debug_by_id["relationship"].relationship_support_bonus, 0.0)

    def test_unrelated_stable_memory_does_not_get_relationship_bonus(self) -> None:
        related = _memory(
            "related",
            "Маркус теперь относится к Алисе осторожно, но снова доверяет ей в работе.",
            memory_type="relationship",
            layer="stable",
            importance=0.84,
        )
        unrelated = _memory(
            "unrelated",
            "Алиса любит джаз и тихие бары.",
            memory_type="profile",
            layer="stable",
            importance=0.9,
        )

        response = self._retrieve(
            [related, unrelated],
            user_input="Как он теперь к ней относится?",
            limit=2,
            recent_messages=[
                MessageInput(role="user", text="После конфликта они всё-таки снова работают вместе."),
            ],
        )

        assert response.debug is not None
        debug_by_id = {item.memory_id: item for item in response.debug.candidates}
        self.assertEqual(response.items[0].id, "related")
        self.assertGreater(debug_by_id["related"].relationship_cue_overlap, 0.0)
        self.assertGreater(debug_by_id["related"].relationship_support_bonus, 0.0)
        self.assertEqual(debug_by_id["unrelated"].relationship_cue_overlap, 0.0)
        self.assertEqual(debug_by_id["unrelated"].relationship_support_bonus, 0.0)

    def test_relationship_cue_support_does_not_override_large_raw_score_gap(self) -> None:
        strong_local = _memory(
            "strong-local",
            "Алина и Маркус решили перенести встречу по проекту на утро и позвать Лену позже.",
            memory_type="event",
            layer="episodic",
            importance=0.9,
            updated_at="2026-03-26T00:00:00+00:00",
        )
        weak_relationship = _memory(
            "weak-relationship",
            "После ссоры между Алиной и Маркусом осталось напряжение.",
            memory_type="relationship",
            layer="stable",
            importance=0.7,
            updated_at="2026-03-20T00:00:00+00:00",
        )

        response = self._retrieve(
            [strong_local, weak_relationship],
            user_input="Что они решили про встречу по проекту?",
            limit=2,
            recent_messages=[
                MessageInput(role="user", text="У них после конфликта всё стало спокойнее."),
            ],
        )

        assert response.debug is not None
        self.assertFalse(response.debug.relationship_query_like)
        self.assertEqual(response.items[0].id, "strong-local")
        debug_by_id = {item.memory_id: item for item in response.debug.candidates}
        self.assertEqual(debug_by_id["weak-relationship"].relationship_support_bonus, 0.0)


if __name__ == "__main__":
    unittest.main()
