"""Capitalisation is not evidence of a proper noun.

extract_entities used to treat it as such, with a `(?<![.!?]\\s)` lookbehind meant to
skip sentence starts. It got both directions wrong, and the two failures compounded in
retrieval, where entity_overlap divides by the number of *query* entities:

  - the first word of a text has no preceding period, so it was always captured. Facts
    written in the third person contributed "Девушка"/"Пользователь", and every
    imperative or interrogative query contributed a phantom ("Расскажи про чай" ->
    ["Рассказать"]) that no memory could match - dropping entity_overlap to 0, or
    halving it when a real name stood alongside.
  - a name in the second sentence *did* have a preceding period and was discarded, so
    the same two facts produced different entities depending on sentence order.
"""
import unittest

from app.services.text_features import extract_entities


class SentenceInitialRussianTests(unittest.TestCase):
    def test_generic_nouns_opening_a_fact_are_not_entities(self) -> None:
        """The live corpus phrasing: extraction writes third-person facts, and 64% of a
        fresh chat's memories opened with one of these."""
        self.assertEqual(extract_entities("Девушка положила телефон экраном вниз."), [])
        self.assertEqual(extract_entities("Пользователь выразил радость от встречи."), [])
        self.assertEqual(extract_entities("Собеседник промолчал."), [])

    def test_a_query_contributes_no_phantom_entity(self) -> None:
        self.assertEqual(extract_entities("Расскажи про чай и интровертов"), [])
        self.assertEqual(extract_entities("Как ты себя чувствуешь?"), [])
        self.assertEqual(extract_entities("Напомни, что случилось вчера"), [])

    def test_a_query_keeps_only_the_real_name(self) -> None:
        """Before, this returned ['Напомнить', 'Лена'] - so a memory about Лена scored
        1/2 on entity overlap instead of 1/1, purely because of the leading verb."""
        self.assertEqual(extract_entities("Напомни, что Лена боится грозы"), ["Лена"])

    def test_a_name_opening_a_fact_is_still_an_entity(self) -> None:
        self.assertIn("Алина", extract_entities("Алина работает в кафе."))
        self.assertIn("Милан", extract_entities("Милан встретил их дождём."))


class SentenceOrderTests(unittest.TestCase):
    def test_the_same_facts_give_the_same_entities_in_either_order(self) -> None:
        """The signature of the old defect: a name after a period was discarded."""
        first = extract_entities("Она любит чай. Алина работает в кафе.")
        second = extract_entities("Алина работает в кафе. Она любит чай.")

        self.assertEqual(first, second)
        self.assertIn("Алина", first)

    def test_a_name_after_a_question_or_exclamation_survives(self) -> None:
        self.assertIn("Анри", extract_entities("Кто это был? Анри уехал в Милан."))
        self.assertIn("Анри", extract_entities("Не может быть! Анри уехал."))


class LatinTests(unittest.TestCase):
    """No morphology is available for Latin, and the two errors are not symmetric: a
    phantom only dilutes entity_overlap, while dropping a real name removes the signal.
    Queries that lead with a name are common enough that sentence-initial Latin is
    accepted."""

    def test_a_latin_name_opening_the_text_is_kept(self) -> None:
        self.assertIn("Elena", extract_entities("Elena project"))
        self.assertIn("Alice", extract_entities("Alice solved the puzzle."))

    def test_a_foreign_name_inside_russian_prose_is_kept(self) -> None:
        self.assertIn("Wanted", extract_entities("Wanted пообещал закрыть окна."))
        self.assertIn("Avril", extract_entities("Вчера Avril уехала."))


class MixedTests(unittest.TestCase):
    def test_names_and_places_mid_sentence_are_all_kept(self) -> None:
        entities = extract_entities("Вчера Анри и Катерина уехали в Милан.")

        self.assertIn("Анри", entities)
        self.assertIn("Катерина", entities)
        self.assertIn("Милан", entities)

    def test_pronouns_stay_excluded(self) -> None:
        self.assertEqual(extract_entities("Она ушла. Они остались."), [])

    def test_extraction_survives_text_with_no_capitals(self) -> None:
        self.assertEqual(extract_entities("просто строчный текст без имён"), [])


if __name__ == "__main__":
    unittest.main()
