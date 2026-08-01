"""Entity comparison used to be exact string equality after lower().

Two consequences, both measured on the live corpus of 11793 entity occurrences:

  - role words carried weight they had not earned. `пользователь` sits in 231 stored
    memories and `user` in 377, so matching on one lifts every such row equally, and on
    the query side it inflates the denominator of entity_overlap - a query resolving to
    ["пользователь", "Валерия"] scored a genuine Валерия match 1/2 instead of 1/1.
  - the same person written two ways never matched. `валерия` (445) and `valeria` (237)
    are one character; 1527 occurrences sit in groups like that, so a Russian query
    could not see a third of that character's memories.
"""
import unittest

from app.services.text_features import (
    entity_match_keys,
    entity_overlap_ratio,
    extract_entities,
    filter_entities,
)


class StoplistTests(unittest.TestCase):
    def test_role_words_are_dropped_in_both_alphabets(self) -> None:
        kept = filter_entities(["Валерия", "пользователь", "user", "девушка", "Анри"])

        self.assertEqual(kept, ["Валерия", "Анри"])

    def test_transcript_artefacts_are_dropped(self) -> None:
        """`Time` reached 248 occurrences from the "[ 🕰️ 2:03 PM ]" headers some
        characters prefix their replies with."""
        self.assertEqual(filter_entities(["Time", "Валерия"]), ["Валерия"])

    def test_original_spelling_and_order_survive(self) -> None:
        self.assertEqual(filter_entities(["Бона Савойская", "Анри"]), ["Бона Савойская", "Анри"])

    def test_duplicates_collapse_case_insensitively(self) -> None:
        self.assertEqual(filter_entities(["Анри", "анри", "АНРИ"]), ["Анри"])

    def test_the_extractor_applies_the_stoplist(self) -> None:
        """Both sources have to be filtered; a stoplist on one side only means the noisy
        word simply stops matching from the other direction."""
        self.assertEqual(extract_entities("Пользователь выразил радость."), [])


class MatchKeyTests(unittest.TestCase):
    def test_the_same_name_in_either_alphabet_shares_a_key(self) -> None:
        self.assertEqual(entity_match_keys("Валерия"), entity_match_keys("Valeria"))
        self.assertEqual(entity_match_keys("Луна"), entity_match_keys("Luna"))

    def test_doubled_letters_do_not_split_a_name(self) -> None:
        """SillyTavern's persona is spelled "Allina volkova"; the model writes "Алина"."""
        self.assertEqual(entity_match_keys("Allina"), entity_match_keys("Алина"))

    def test_the_persona_transliteration_matches(self) -> None:
        self.assertEqual(entity_match_keys("Wanted"), entity_match_keys("Вантед"))

    def test_a_full_name_yields_a_key_per_token(self) -> None:
        self.assertEqual(entity_match_keys("Алина Волкова"), {"alina", "volkova"})

    def test_short_tokens_are_dropped(self) -> None:
        """Two characters carry no identity and would collide freely."""
        self.assertEqual(entity_match_keys("А Б"), set())


class OverlapTests(unittest.TestCase):
    def test_a_russian_query_finds_a_latin_entity(self) -> None:
        self.assertEqual(entity_overlap_ratio(["Valeria"], ["Валерия"]), 1.0)

    def test_a_short_name_matches_the_stored_full_name(self) -> None:
        """Without this the naming fix would have made matching worse than before it:
        extraction now writes "Алина Волкова" while a query says "Алина"."""
        self.assertEqual(entity_overlap_ratio(["Алина Волкова"], ["Алина"]), 1.0)

    def test_a_two_word_query_entity_does_not_double_the_denominator(self) -> None:
        """Counted per entity, not per key - a naive key-set intersection would score
        this 1/2 purely because the name has two words."""
        self.assertEqual(entity_overlap_ratio(["Бона Савойская"], ["Бона Савойская"]), 1.0)

    def test_partial_match_is_proportional(self) -> None:
        self.assertEqual(entity_overlap_ratio(["Валерия"], ["Валерия", "Анри"]), 0.5)

    def test_distinct_names_stay_distinct(self) -> None:
        """The real risk of canonicalising: merging two people. Comparison keys never
        touch the stored value, but a wrong merge would still corrupt scoring."""
        for stored, queried in [
            ("Марина", "Мария"),
            ("Анри", "Катерина"),
            ("Валерия", "Валентина"),
            ("Селим", "Зейнеп"),
            ("Луна", "Лена"),
        ]:
            with self.subTest(stored=stored, queried=queried):
                self.assertEqual(entity_overlap_ratio([stored], [queried]), 0.0)

    def test_no_query_entities_means_no_overlap(self) -> None:
        self.assertEqual(entity_overlap_ratio(["Валерия"], []), 0.0)


if __name__ == "__main__":
    unittest.main()
