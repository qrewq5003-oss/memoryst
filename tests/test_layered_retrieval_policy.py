import unittest
from unittest.mock import patch

from app.schemas import MemoryItem, MemoryMetadata, MessageInput, RetrieveMemoryRequest
from app.services.retrieve_service import retrieve_memories


def _memory(
    memory_id: str,
    content: str,
    *,
    memory_type: str,
    layer: str,
    keywords: list[str],
    entities: list[str],
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
            keywords=keywords,
            entities=entities,
            is_summary=(memory_type == "summary"),
        ),
    )


class LayeredRetrievalPolicyTests(unittest.TestCase):
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
            response = retrieve_memories(
                RetrieveMemoryRequest(
                    chat_id="chat-1",
                    character_id="char-1",
                    user_input=user_input,
                    recent_messages=recent_messages or [],
                    limit=limit,
                    debug=True,
                )
            )
        return response

    def test_summary_stable_and_episodic_layers_all_contribute(self) -> None:
        summary = _memory(
            "summary",
            "Краткая сводка: Алиса и Маркус снова сотрудничают над фильмом и держат общий план поездки.",
            memory_type="summary",
            layer="stable",
            keywords=["алиса", "маркус", "фильм", "план", "поездка"],
            entities=["Алиса", "Маркус"],
            importance=0.95,
        )
        stable = _memory(
            "stable",
            "Маркус снова доверяет Алисе в рабочих вопросах.",
            memory_type="relationship",
            layer="stable",
            keywords=["маркус", "алиса", "доверяет", "работа"],
            entities=["Маркус", "Алиса"],
            importance=0.8,
        )
        episodic = _memory(
            "episodic",
            "Вчера Алиса и Маркус согласовали новое время встречи по фильму.",
            memory_type="event",
            layer="episodic",
            keywords=["алиса", "маркус", "встреча", "фильм", "вчера"],
            entities=["Алиса", "Маркус"],
            importance=0.75,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        response = self._retrieve(
            [summary, stable, episodic],
            user_input="Что сейчас важно помнить про Алису и Маркуса по фильму?",
            limit=5,
        )

        self.assertEqual({item.id for item in response.items}, {"summary", "stable", "episodic"})
        assert response.debug is not None
        self.assertEqual(response.debug.summary_candidates, 1)
        self.assertEqual(response.debug.stable_candidates, 1)
        self.assertEqual(response.debug.episodic_candidates, 1)
        self.assertEqual(response.debug.selected_summary, 1)
        self.assertEqual(response.debug.selected_stable, 1)
        self.assertEqual(response.debug.selected_episodic, 1)

    def test_local_scene_query_keeps_episodic_top_while_summary_still_can_join(self) -> None:
        summary = _memory(
            "summary",
            "Краткая сводка: Алиса и Маркус пытаются удержать проект и не сорвать поездку.",
            memory_type="summary",
            layer="stable",
            keywords=["алиса", "маркус", "проект", "поездка"],
            entities=["Алиса", "Маркус"],
            importance=0.9,
        )
        stable = _memory(
            "stable",
            "Маркус доверяет Алисе и полагается на неё в проекте.",
            memory_type="relationship",
            layer="stable",
            keywords=["маркус", "алиса", "доверяет", "проект"],
            entities=["Алиса", "Маркус"],
            importance=0.75,
        )
        episodic = _memory(
            "episodic",
            "Вчера Алиса и Маркус решили перенести встречу на утро.",
            memory_type="event",
            layer="episodic",
            keywords=["алиса", "маркус", "решили", "перенести", "встречу", "вчера", "утро"],
            entities=["Алиса", "Маркус"],
            importance=0.78,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        response = self._retrieve(
            [summary, stable, episodic],
            user_input="Что они решили вчера про перенос встречи на утро?",
            limit=3,
            recent_messages=[
                MessageInput(role="user", text="Напомни общий статус проекта Алисы и Маркуса.")
            ],
        )

        self.assertEqual(response.items[0].id, "episodic")
        self.assertIn("summary", [item.id for item in response.items])
        assert response.debug is not None
        decisions = {item.memory_id: item for item in response.debug.candidates}
        self.assertEqual(decisions["episodic"].selected_from_layer, "episodic")
        self.assertEqual(decisions["summary"].selected_from_layer, "summary")

    def test_close_score_general_query_prefers_durable_layer_without_bonus_magic(self) -> None:
        stable = _memory(
            "stable",
            "Alice is a doctor from Rome.",
            memory_type="profile",
            layer="stable",
            keywords=["alice", "doctor", "rome"],
            entities=["Alice"],
            importance=0.72,
        )
        episodic = _memory(
            "episodic",
            "Alice visited a museum in Rome yesterday.",
            memory_type="event",
            layer="episodic",
            keywords=["alice", "rome", "museum", "yesterday"],
            entities=["Alice"],
            importance=0.72,
            updated_at="2026-03-26T00:00:00+00:00",
        )

        response = self._retrieve(
            [stable, episodic],
            user_input="What does Alice do in Rome?",
            limit=1,
        )

        self.assertEqual([item.id for item in response.items], ["stable"])


if __name__ == "__main__":
    unittest.main()
