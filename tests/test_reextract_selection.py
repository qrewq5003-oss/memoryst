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


class EmbedPacingTests(unittest.TestCase):
    """Bulk writes pace their embeddings; the live path does not.

    A scene writes 3-5 facts back to back, and that burst hit Google's per-minute limit
    on 2026-09-21 - 429 RESOURCE_EXHAUSTED, recovered within minutes, which is what a
    per-minute limit looks like rather than a daily one. A live /memory/store embeds one
    or two facts a turn and must not be slowed: the user is waiting on that request.
    """

    def test_the_library_itself_never_sleeps(self) -> None:
        # The constant is advice for bulk callers, not behaviour of add_memory.
        import inspect

        from app.services import vector_store

        self.assertGreater(vector_store.BULK_EMBED_DELAY_SECONDS, 0)
        self.assertNotIn("sleep", inspect.getsource(vector_store.add_memory))

    def test_both_bulk_scripts_use_the_same_constant(self) -> None:
        # embed_memories.py had 0.6 hardcoded and never hit the limit; that measured
        # value is now shared rather than copied.
        root = Path(__file__).resolve().parent.parent
        for name in ("embed_memories.py", "reextract_english_memories.py"):
            source = (root / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn("BULK_EMBED_DELAY_SECONDS", source)

    def test_storing_waits_after_embedding(self) -> None:
        from unittest.mock import patch

        slept: list[float] = []
        with patch.object(reextract, "passes_memory_quality_gate", return_value=True), \
             patch.object(reextract, "find_memory_by_normalized_content", return_value=None), \
             patch.object(reextract, "create_memory") as create, \
             patch.object(reextract.vector_store, "add_memory"), \
             patch.object(reextract.time, "sleep", side_effect=slept.append):
            create.return_value = type(
                "M", (), {"id": "m1", "content": "x", "chat_id": "c", "character_id": "x"}
            )()
            reextract._store({"content": "Валерия готовит арепы"}, "c", "x", 0.6)

        self.assertEqual(slept, [0.6])

    def test_pacing_can_be_switched_off(self) -> None:
        from unittest.mock import patch

        slept: list[float] = []
        with patch.object(reextract, "passes_memory_quality_gate", return_value=True), \
             patch.object(reextract, "find_memory_by_normalized_content", return_value=None), \
             patch.object(reextract, "create_memory") as create, \
             patch.object(reextract.vector_store, "add_memory"), \
             patch.object(reextract.time, "sleep", side_effect=slept.append):
            create.return_value = type(
                "M", (), {"id": "m1", "content": "x", "chat_id": "c", "character_id": "x"}
            )()
            reextract._store({"content": "Валерия готовит арепы"}, "c", "x", 0.0)

        self.assertEqual(slept, [])
