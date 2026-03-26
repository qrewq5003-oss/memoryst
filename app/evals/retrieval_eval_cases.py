from app.evals.retrieval_eval import RetrievalEvalCase
from app.schemas import MemoryItem, MemoryMetadata, MessageInput
from app.services import text_features


def _memory(
    memory_id: str,
    content: str,
    *,
    memory_type: str,
    layer: str,
    importance: float = 0.7,
    updated_at: str = "2026-03-20T00:00:00+00:00",
    pinned: bool = False,
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
        pinned=pinned,
        archived=False,
        metadata=MemoryMetadata(
            entities=text_features.extract_entities(content),
            keywords=text_features.extract_keywords(content),
        ),
    )


RUSSIAN_RETRIEVAL_EVAL_CASES = [
    RetrievalEvalCase(
        name="ru_preference_stable_beats_irrelevant_noise",
        query="Какую музыку любит Алиса?",
        fixture_memories=[
            _memory(
                "pref-jazz",
                "Алиса любит джазовую музыку и тихие бары.",
                memory_type="profile",
                layer="stable",
            ),
            _memory(
                "event-concert",
                "Вчера Алиса спорила о билетах на концерт.",
                memory_type="event",
                layer="episodic",
            ),
            _memory(
                "pref-dogs",
                "Алиса любит собак и долгие прогулки.",
                memory_type="profile",
                layer="stable",
            ),
        ],
        expected_top_ids=["pref-jazz"],
        forbidden_top_ids=["event-concert"],
        limit=1,
        notes="Русский preference case: stable preference should beat irrelevant episodic noise.",
    ),
    RetrievalEvalCase(
        name="ru_relationship_fact_beats_old_conflict_episode",
        query="Доверяет ли Маркус Алисе?",
        fixture_memories=[
            _memory(
                "trust-stable",
                "Маркус доверяет Алисе и обычно советуется с ней.",
                memory_type="relationship",
                layer="stable",
            ),
            _memory(
                "old-argument",
                "Вчера Маркус поссорился с Алисой из-за денег.",
                memory_type="event",
                layer="episodic",
            ),
            _memory(
                "coffee",
                "Маркус любит крепкий кофе по утрам.",
                memory_type="profile",
                layer="stable",
            ),
        ],
        expected_top_ids=["trust-stable"],
        forbidden_top_ids=["old-argument"],
        limit=1,
        notes="Relationship-like stable memory should beat a conflict episode for a trust query.",
    ),
    RetrievalEvalCase(
        name="ru_profile_summary_beats_episode_for_general_question",
        query="Напомни, Алиса врач и из Рима?",
        fixture_memories=[
            _memory(
                "profile-rome",
                "Алиса врач из Рима.",
                memory_type="profile",
                layer="stable",
            ),
            _memory(
                "museum-episode",
                "Вчера Алиса встретила Маркуса в римском музее.",
                memory_type="event",
                layer="episodic",
            ),
            _memory(
                "tea-pref",
                "Алиса любит зелёный чай.",
                memory_type="profile",
                layer="stable",
            ),
        ],
        expected_top_ids=["profile-rome"],
        limit=1,
        notes="General identity query should surface the stable profile fact before episodic detail.",
    ),
    RetrievalEvalCase(
        name="ru_old_important_fact_beats_fresh_noise",
        query="Можно ли Алисе клубнику?",
        fixture_memories=[
            _memory(
                "allergy",
                "У Алисы аллергия на клубнику.",
                memory_type="profile",
                layer="stable",
                importance=0.9,
                updated_at="2026-03-05T00:00:00+00:00",
            ),
            _memory(
                "fresh-noise",
                "Сегодня Алиса долго выбирала новые шторы.",
                memory_type="event",
                layer="episodic",
                importance=0.4,
                updated_at="2026-03-26T00:00:00+00:00",
            ),
            _memory(
                "tea",
                "Алиса любит пить чай вечером.",
                memory_type="profile",
                layer="stable",
                importance=0.6,
            ),
        ],
        expected_top_ids=["allergy"],
        forbidden_top_ids=["fresh-noise"],
        limit=1,
        notes="Long-chat style case: old important fact should beat fresh but irrelevant noise.",
    ),
    RetrievalEvalCase(
        name="ru_recent_messages_recover_context_for_vague_query",
        query="А что насчёт этого?",
        recent_messages=[
            MessageInput(role="user", text="Мы обсуждали, что Лена боится грозы и плохо переносит гром.")
        ],
        fixture_memories=[
            _memory(
                "storm-fear",
                "Лена боится грозы.",
                memory_type="profile",
                layer="stable",
            ),
            _memory(
                "argument",
                "Вчера Лена спорила с Маркусом.",
                memory_type="event",
                layer="episodic",
            ),
        ],
        expected_top_ids=["storm-fear"],
        limit=1,
        notes="Recent message context should recover a relevant stable fact for a vague follow-up query.",
    ),
    RetrievalEvalCase(
        name="ru_diversity_keeps_distinct_memory_and_drops_duplicate",
        query="Что Алина делает по утрам?",
        fixture_memories=[
            _memory(
                "tea-base",
                "Алина любит зелёный чай по утрам.",
                memory_type="profile",
                layer="stable",
            ),
            _memory(
                "tea-dup",
                "По утрам Алина очень любит зелёный чай.",
                memory_type="profile",
                layer="stable",
            ),
            _memory(
                "news",
                "По утрам Алина читает новости за кофе.",
                memory_type="event",
                layer="episodic",
            ),
        ],
        expected_contains_ids=["tea-base", "news"],
        forbidden_top_ids=["tea-dup"],
        limit=2,
        notes="Near-duplicate stable memories should not consume extra top slots when a distinct memory exists.",
    ),
    RetrievalEvalCase(
        name="ru_word_form_matching_for_preferences",
        query="Напомни, как Аня относится к кошке",
        fixture_memories=[
            _memory(
                "cats",
                "Аня обожает кошек.",
                memory_type="profile",
                layer="stable",
            ),
            _memory(
                "dogs",
                "Аня обожает собак.",
                memory_type="profile",
                layer="stable",
            ),
        ],
        expected_top_ids=["cats"],
        limit=1,
        notes="Russian normalization should connect word forms like кошке and кошек.",
    ),
]


SANITY_RETRIEVAL_EVAL_CASES = [
    RetrievalEvalCase(
        name="en_profile_fact_retrieval_sanity",
        query="What does Alice do in Rome?",
        fixture_memories=[
            _memory(
                "alice-doctor",
                "Alice is a doctor from Rome.",
                memory_type="profile",
                layer="stable",
            ),
            _memory(
                "alice-museum",
                "Alice visited a museum in Rome yesterday.",
                memory_type="event",
                layer="episodic",
            ),
        ],
        expected_top_ids=["alice-doctor"],
        limit=1,
        notes="English sanity check for stable profile retrieval.",
    ),
    RetrievalEvalCase(
        name="en_relationship_fact_retrieval_sanity",
        query="Who is Marcus to Alice?",
        fixture_memories=[
            _memory(
                "brother",
                "Marcus is Alice's brother.",
                memory_type="relationship",
                layer="stable",
            ),
            _memory(
                "meeting",
                "Alice met Marcus in Rome yesterday.",
                memory_type="event",
                layer="episodic",
            ),
        ],
        expected_top_ids=["brother"],
        limit=1,
        notes="English sanity check for durable relationship retrieval.",
    ),
]


DEFAULT_RETRIEVAL_EVAL_CASES = [
    *RUSSIAN_RETRIEVAL_EVAL_CASES,
    *SANITY_RETRIEVAL_EVAL_CASES,
]
