import unittest

from app.schemas import MessageInput
from app.services.extractor import extract_memories


def _extract(text: str):
    candidates = extract_memories(
        chat_id="chat-1",
        character_id="char-1",
        messages=[MessageInput(role="user", text=text)],
    )
    return candidates[0] if candidates else None


class MemoryTypeLayerHeuristicsTests(unittest.TestCase):
    def test_preference_statement_is_profile_and_stable(self) -> None:
        candidate = _extract("Alice loves jazz and prefers quiet bars.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "profile")
        self.assertEqual(candidate.layer, "stable")

    def test_profile_fact_is_profile_and_stable(self) -> None:
        candidate = _extract("Alice is a doctor from Rome.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "profile")
        self.assertEqual(candidate.layer, "stable")

    def test_concrete_event_is_event_and_episodic(self) -> None:
        candidate = _extract("Alice met Marcus in Rome yesterday.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "event")
        self.assertEqual(candidate.layer, "episodic")

    def test_durable_relationship_is_not_misclassified_as_event(self) -> None:
        candidate = _extract("Marcus is Alice's brother.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "relationship")
        self.assertEqual(candidate.layer, "stable")

    def test_russian_preference_statement_is_profile_and_stable(self) -> None:
        candidate = _extract("Алиса любит джаз и предпочитает тихие кафе.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "profile")
        self.assertEqual(candidate.layer, "stable")

    def test_russian_profile_fact_is_profile_and_stable(self) -> None:
        candidate = _extract("Алиса работает врачом и живет в Риме.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "profile")
        self.assertEqual(candidate.layer, "stable")

    def test_russian_event_is_event_and_episodic(self) -> None:
        candidate = _extract("Алиса встретила Маркуса вчера в Риме.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "event")
        self.assertEqual(candidate.layer, "episodic")

    def test_russian_durable_relationship_state_becomes_stable_relationship(self) -> None:
        candidate = _extract("Маркус снова доверяет Алисе в работе, хотя между ними всё ещё остаётся осторожность.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "relationship")
        self.assertEqual(candidate.layer, "stable")

    def test_russian_supportive_relationship_carry_over_becomes_stable_relationship(self) -> None:
        candidate = _extract("Маркус при всей команде поддержал Алису и не собирается снова оставлять её одну с фильмом.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "relationship")
        self.assertEqual(candidate.layer, "stable")

    def test_one_off_conflict_scene_remains_episodic_event(self) -> None:
        candidate = _extract("После провала на площадке Маркус сорвался на Алису, и между ними началась тяжёлая ссора.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "event")
        self.assertEqual(candidate.layer, "episodic")

    def test_durable_ownership_like_fact_stays_stable(self) -> None:
        candidate = _extract("Alice owns a small bakery in Rome.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "profile")
        self.assertEqual(candidate.layer, "stable")

    def test_profession_word_without_profile_context_does_not_force_profile(self) -> None:
        candidate = _extract("We met the doctor yesterday in Rome.")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.type, "event")
        self.assertEqual(candidate.layer, "episodic")

    def test_likes_noun_does_not_trigger_preference_classification(self) -> None:
        candidate = _extract("The post has many likes.")
        self.assertIsNone(candidate)


if __name__ == "__main__":
    unittest.main()
