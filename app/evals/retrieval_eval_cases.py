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
        name="ru_relationship_status_wording_surfaces_summary_and_stable",
        query="Как он теперь к ней относится?",
        recent_messages=[
            MessageInput(role="user", text="После ссоры Маркус и Алиса снова работают вместе, но между ними ещё осталось напряжение."),
        ],
        fixture_memories=[
            _memory(
                "relationship-summary",
                "Краткая сводка: после ссоры Маркус и Алиса частично помирились, но между ними всё ещё есть напряжение.",
                memory_type="summary",
                layer="stable",
                importance=0.95,
            ),
            _memory(
                "relationship-stable",
                "Маркус снова доверяет Алисе в работе, хотя держит осторожную дистанцию.",
                memory_type="relationship",
                layer="stable",
                importance=0.84,
            ),
            _memory(
                "old-fight",
                "Раньше Маркус сорвался на Алису после провала на съёмке.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-10T00:00:00+00:00",
            ),
        ],
        expected_contains_ids=["relationship-summary", "relationship-stable"],
        forbidden_top_ids=["old-fight"],
        limit=2,
        notes="Broad Russian attitude phrasing should still surface relationship summary and stable context.",
    ),
    RetrievalEvalCase(
        name="ru_relationship_working_together_phrase_keeps_summary_context",
        query="Они снова нормально работают вместе?",
        recent_messages=[
            MessageInput(role="assistant", text="После конфликта Алина и Маркус всё-таки вернулись к общему проекту."),
        ],
        fixture_memories=[
            _memory(
                "working-summary",
                "Краткая сводка: после ссоры Алина и Маркус снова работают вместе над фильмом, но осторожничают.",
                memory_type="summary",
                layer="stable",
                importance=0.95,
            ),
            _memory(
                "working-stable",
                "Маркус снова помогает Алине с фильмом и постепенно возвращает доверие.",
                memory_type="relationship",
                layer="stable",
                importance=0.82,
            ),
            _memory(
                "old-conflict",
                "Неделю назад Алина и Маркус громко спорили в мастерской.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-10T00:00:00+00:00",
            ),
        ],
        expected_contains_ids=["working-summary", "working-stable"],
        forbidden_top_ids=["old-conflict"],
        limit=2,
        notes="Working-together phrasing should not miss relationship summary and stable repair context.",
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
                updated_at="2026-03-21T00:00:00+00:00",
            ),
            _memory(
                "tea-dup",
                "По утрам Алина очень любит зелёный чай.",
                memory_type="profile",
                layer="stable",
                updated_at="2026-03-19T00:00:00+00:00",
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
    RetrievalEvalCase(
        name="ru_layered_general_query_returns_summary_and_stable_context",
        query="Что сейчас важно помнить про Алису и Маркуса по фильму?",
        fixture_memories=[
            _memory(
                "rolling-summary",
                "Краткая сводка: Алиса и Маркус снова сотрудничают над фильмом и держат общий план поездки.",
                memory_type="summary",
                layer="stable",
                importance=0.95,
            ),
            _memory(
                "trust-stable",
                "Маркус снова доверяет Алисе в рабочих вопросах.",
                memory_type="relationship",
                layer="stable",
            ),
            _memory(
                "meeting-episode",
                "Вчера Алиса и Маркус согласовали новое время встречи по фильму.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-26T00:00:00+00:00",
            ),
        ],
        expected_contains_ids=["rolling-summary", "trust-stable", "meeting-episode"],
        limit=5,
        notes="Layered retrieval should keep summary, stable context, and a relevant local episode together.",
    ),
    RetrievalEvalCase(
        name="ru_layered_local_query_keeps_recent_episode_without_losing_summary",
        query="Что они решили вчера по встрече?",
        recent_messages=[
            MessageInput(role="user", text="Напомни общий статус проекта Алисы и Маркуса.")
        ],
        fixture_memories=[
            _memory(
                "rolling-summary",
                "Краткая сводка: Алиса и Маркус пытаются удержать проект и не сорвать поездку.",
                memory_type="summary",
                layer="stable",
                importance=0.9,
            ),
            _memory(
                "project-trust",
                "Маркус доверяет Алисе и полагается на неё в проекте.",
                memory_type="relationship",
                layer="stable",
            ),
            _memory(
                "meeting-episode",
                "Вчера Алиса и Маркус решили перенести встречу на утро.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-26T00:00:00+00:00",
            ),
        ],
        expected_top_ids=["meeting-episode"],
        expected_contains_ids=["rolling-summary"],
        limit=3,
        notes="A local fresh query should still surface the recent episodic memory first while retaining summary context.",
    ),
    RetrievalEvalCase(
        name="ru_layered_preference_coexists_with_summary",
        query="Что важно помнить про Алису сейчас и что она любит?",
        fixture_memories=[
            _memory(
                "rolling-summary",
                "Краткая сводка: Алиса держит проект под контролем и старается не ссориться с Маркусом.",
                memory_type="summary",
                layer="stable",
                importance=0.9,
            ),
            _memory(
                "jazz-preference",
                "Алиса любит джазовую музыку и тихие бары.",
                memory_type="profile",
                layer="stable",
            ),
            _memory(
                "ticket-noise",
                "Вчера Алиса спорила о билетах на концерт.",
                memory_type="event",
                layer="episodic",
            ),
        ],
        expected_contains_ids=["rolling-summary", "jazz-preference"],
        forbidden_top_ids=["ticket-noise"],
        limit=2,
        notes="Stable preference should coexist with the rolling summary instead of disappearing behind episodic noise.",
    ),
]


LONG_CHAT_RUSSIAN_RP_EVAL_CASES = [
    RetrievalEvalCase(
        name="ru_long_chat_relationship_arc_prefers_summary_and_repair_context",
        query="Он всё ещё злится на неё или они уже помирились?",
        recent_messages=[
            MessageInput(role="user", text="Напомни, что сейчас между Алисой и Маркусом после всех сцен.")
        ],
        fixture_memories=[
            _memory(
                "relationship-summary",
                "Краткая сводка: после ссоры Алиса и Маркус частично помирились, но между ними всё ещё остаётся напряжение.",
                memory_type="summary",
                layer="stable",
                importance=0.95,
            ),
            _memory(
                "trust-repair",
                "Маркус снова начал доверять Алисе в рабочих вопросах, хотя полностью не расслабился.",
                memory_type="relationship",
                layer="stable",
                importance=0.85,
            ),
            _memory(
                "old-fight",
                "Неделю назад Маркус сорвался на Алису из-за провала на съёмке.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-10T00:00:00+00:00",
            ),
            _memory(
                "recent-truce",
                "Вчера Алиса и Маркус договорились не возвращаться к старой ссоре до конца поездки.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-26T00:00:00+00:00",
            ),
        ],
        expected_contains_ids=["relationship-summary", "trust-repair", "recent-truce"],
        forbidden_top_ids=["old-fight"],
        expected_layer_counts={"summary": 1, "stable": 1, "episodic": 1},
        limit=3,
        notes="Long relationship arc should keep the rolling summary, durable repair context, and one fresh episodic scene without letting an old conflict fragment dominate.",
    ),
    RetrievalEvalCase(
        name="ru_long_chat_goal_survives_noise_via_summary_and_stable",
        query="Чего Алиса сейчас пытается добиться с фильмом?",
        recent_messages=[
            MessageInput(role="user", text="В последних сценах было много шума про поездку и усталость команды.")
        ],
        fixture_memories=[
            _memory(
                "goal-summary",
                "Краткая сводка: Алиса пытается закончить фильм вовремя, удержать команду вместе и не сорвать монтаж.",
                memory_type="summary",
                layer="stable",
                importance=0.95,
            ),
            _memory(
                "goal-stable",
                "Алисе важно закончить монтаж до фестиваля и не потерять финансирование.",
                memory_type="profile",
                layer="stable",
                importance=0.88,
            ),
            _memory(
                "noise-market",
                "Вчера Алиса долго спорила с продавцом реквизита на рынке.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-25T00:00:00+00:00",
            ),
            _memory(
                "noise-train",
                "Позже команда застряла на вокзале и потеряла час.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-26T00:00:00+00:00",
            ),
        ],
        expected_contains_ids=["goal-summary", "goal-stable"],
        forbidden_top_ids=["noise-market", "noise-train"],
        expected_layer_counts={"summary": 1, "stable": 1},
        limit=2,
        notes="Ongoing long-arc goal should survive scene noise through summary plus stable goal context.",
    ),
    RetrievalEvalCase(
        name="ru_long_chat_local_scene_survives_summary_presence",
        query="Что Алиса и Маркус решили про встречу по проекту?",
        recent_messages=[
            MessageInput(role="user", text="Напомни их общий статус по проекту.")
        ],
        fixture_memories=[
            _memory(
                "project-summary",
                "Краткая сводка: Алиса и Маркус удерживают проект, не хотят срывать поездку и стараются держать общий рабочий ритм без новой ссоры.",
                memory_type="summary",
                layer="stable",
                importance=0.9,
            ),
            _memory(
                "project-stable",
                "Маркус доверяет Алисе в вопросах проекта и рабочих решений.",
                memory_type="relationship",
                layer="stable",
                importance=0.8,
            ),
            _memory(
                "fresh-meeting",
                "Только что Алиса и Маркус решили перенести встречу по проекту на утро и позвать Лену позже.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-26T00:00:00+00:00",
            ),
        ],
        expected_top_ids=["fresh-meeting"],
        expected_contains_ids=["project-summary"],
        expected_layer_counts={"summary": 1, "stable": 1, "episodic": 1},
        limit=3,
        notes="Fresh local scene should still rank first while summary and stable context remain present.",
    ),
    RetrievalEvalCase(
        name="ru_long_chat_vague_follow_up_uses_recent_messages_and_layers",
        query="А что у них сейчас вообще?",
        recent_messages=[
            MessageInput(role="user", text="Мы обсуждали, что после ссоры они всё-таки снова работают вместе над фильмом."),
            MessageInput(role="assistant", text="И Маркус пока ещё не до конца расслабился, хотя конфликт уже не острый."),
        ],
        fixture_memories=[
            _memory(
                "status-summary",
                "Краткая сводка: после тяжёлой ссоры Алиса и Маркус снова сотрудничают, но между ними держится осторожное напряжение.",
                memory_type="summary",
                layer="stable",
                importance=0.95,
            ),
            _memory(
                "status-stable",
                "Маркус снова помогает Алисе с фильмом, хотя всё ещё держит дистанцию.",
                memory_type="relationship",
                layer="stable",
                importance=0.83,
            ),
            _memory(
                "old-scene",
                "Раньше Маркус хлопнул дверью и ушёл после ссоры в мастерской.",
                memory_type="event",
                layer="episodic",
                updated_at="2026-03-08T00:00:00+00:00",
            ),
        ],
        expected_contains_ids=["status-summary", "status-stable"],
        forbidden_top_ids=["old-scene"],
        expected_layer_counts={"summary": 1, "stable": 1},
        limit=2,
        notes="Vague Russian follow-up should use recent_messages plus summary/stable context instead of collapsing into an old literal-matching episode.",
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
    *LONG_CHAT_RUSSIAN_RP_EVAL_CASES,
    *SANITY_RETRIEVAL_EVAL_CASES,
]
