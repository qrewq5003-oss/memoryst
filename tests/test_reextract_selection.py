"""Which memories the re-extraction pass is allowed to replace.

The two predicates pull in opposite directions on purpose, and collapsing them into one
letter-count is what made the pass select work it should not have done.

A memory is one short sentence and these characters are named in Latin, so counting
letters calls "Wanted пришёл на репетицию Paris1893 в Théâtre de l'Odéon" English. Seven
correctly-written Russian memories were selected that way on 2026-09-21. Re-extracting
them would have replaced good facts with different ones, and the pass would have had
nothing to converge on - every run producing more Latin-named Russian facts for the next
run to "fix".

A scene is long, and Latin names inside it do not make it an English scene. Requiring
zero Latin there would have excluded nearly every chat in this database.
"""

import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "reextract_english_memories",
    Path(__file__).resolve().parent.parent / "scripts" / "reextract_english_memories.py",
)
reextract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reextract)


class EnglishMemoryTests(unittest.TestCase):
    def test_a_russian_sentence_with_latin_names_is_not_english(self) -> None:
        # The exact false positive: more Latin letters than Cyrillic, still Russian.
        self.assertFalse(
            reextract._is_english_memory(
                "Wanted пришёл на репетицию Paris1893 в Théâtre de l'Odéon."
            )
        )

    def test_one_cyrillic_letter_is_enough_to_prove_it_is_not_english(self) -> None:
        self.assertFalse(reextract._is_english_memory("Valeria Mendoza приготовила arepas"))

    def test_a_genuinely_english_memory_is_selected(self) -> None:
        self.assertTrue(
            reextract._is_english_memory("Valeria is wearing dark burgundy nail polish.")
        )

    def test_a_purely_russian_memory_is_not_selected(self) -> None:
        self.assertFalse(reextract._is_english_memory("Алина работает ветеринаром в клинике"))

    def test_text_with_no_letters_at_all_is_not_selected(self) -> None:
        for text in ("", "   ", "123 — 456"):
            self.assertFalse(reextract._is_english_memory(text))


class RussianSceneTests(unittest.TestCase):
    def test_a_russian_scene_survives_its_latin_names(self) -> None:
        self.assertTrue(
            reextract._is_russian_scene(
                "Wanted обнимает Valeria, пока она жарит арепы, и она смеётся в ответ"
            )
        )

    def test_an_english_scene_is_not_called_russian(self) -> None:
        self.assertFalse(
            reextract._is_russian_scene("Valeria initiated a sexual advance, suggesting they wait")
        )

    def test_the_two_thresholds_leave_a_deliberate_gap(self) -> None:
        """Latin-heavy text that still contains Cyrillic is neither, and that is right.

        The memory test needs zero Cyrillic; the scene test needs a Cyrillic majority.
        Between them sits text like "Wanted поцеловал Valeria Mendoza" - 20 Latin
        letters against 9. As a memory it is not selected, because it is plainly a
        Russian sentence. As a scene it is not trusted as Russian either, because by
        volume it is not. Both refusals are the safe direction: nothing is replaced on
        a guess.
        """
        text = "Wanted поцеловал Valeria Mendoza."
        self.assertFalse(reextract._is_english_memory(text))
        self.assertFalse(reextract._is_russian_scene(text))


if __name__ == "__main__":
    unittest.main()
