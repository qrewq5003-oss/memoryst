"""Roleplay status headers are scaffolding, not content.

Many character cards open every reply with one:

    [ 🕰️ Time 7:45 PM | 🗓️ Saturday, March 15, 2025 | 📍 Milan - Kitchen | 🌙 Clear ]
    [ Милан | 15 января 1477 | 11:05 | Возраст: 7 | Кастелло Сфорцеско ]

The rule-based extractor stores a line close to verbatim, so the header ended up inside
the memory - 315 rows carrying 80-496 characters of clock and weather, 18 of them
nothing else because the header alone filled the 500-char content limit. It also fed the
scorer: `Time` reached 248 entity occurrences and 📍 place names matched every memory of
that location regardless of what was said there.
"""
import unittest

from app.services.text_utils import (
    clean_memory_text,
    strip_scene_scaffolding,
    strip_transcript_header,
)


class MarkedHeaderTests(unittest.TestCase):
    def test_an_emoji_header_is_removed_and_the_line_survives(self) -> None:
        text = (
            "[ 🕰️ Time 7:45 PM | 🗓️ Saturday, March 15, 2025 AD | 📍 Milan - Kitchen | "
            "🌙 Clear, 54°F ] *Валерия принимает поцелуй.*"
        )

        self.assertEqual(strip_transcript_header(text), "*Валерия принимает поцелуй.*")

    def test_a_russian_header_without_emoji_is_removed(self) -> None:
        text = "[ Время 14:44 | Среда, 12 апреля, 1721 | Хамам | Свет мягкий ] Она вошла."

        self.assertEqual(strip_transcript_header(text), "Она вошла.")

    def test_a_bare_field_header_with_no_marker_word_is_removed(self) -> None:
        """Recognised by structure alone - place, date, time, age, location."""
        text = "[ Милан | 15 января 1477 | 11:05 | Возраст: 7 | Кастелло Сфорцеско ] Тишина."

        self.assertEqual(strip_transcript_header(text), "Тишина.")


class HeaderOnlyTests(unittest.TestCase):
    def test_a_truncated_header_leaves_nothing(self) -> None:
        """The 18 rows that held no fact at all: the header ran past the content limit,
        so the memory recorded only a clock reading. An empty result tells the caller
        there is nothing worth storing."""
        text = "[ 🕰️ Time 17:18 | 🗓️ Jeudi, Avril 27, 1775 | 📍 Versailles — Petit Trianon"

        self.assertEqual(strip_transcript_header(text), "")

    def test_a_closed_header_with_nothing_after_it_leaves_nothing(self) -> None:
        text = "[ 🕰️ Time 10:00 | 🗓️ Monday | 📍 Rome ]"

        self.assertEqual(strip_transcript_header(text), "")


class OrdinaryTextTests(unittest.TestCase):
    """Prose does open with a bracket sometimes; requiring pipe-separated fields is what
    keeps those untouched."""

    def test_plain_text_is_returned_unchanged(self) -> None:
        self.assertEqual(strip_transcript_header("Алина любит чай."), "Алина любит чай.")

    def test_a_bracketed_stage_direction_is_kept(self) -> None:
        self.assertEqual(strip_transcript_header("[смеётся] она кивнула"), "[смеётся] она кивнула")
        self.assertEqual(strip_transcript_header("[OOC] проверка"), "[OOC] проверка")

    def test_a_bracket_later_in_the_line_is_kept(self) -> None:
        text = "Он сказал: [пауза] и ушёл"

        self.assertEqual(strip_transcript_header(text), text)

    def test_a_single_bracketed_field_without_a_marker_is_kept(self) -> None:
        """One pipe and no time/date/place word is not enough evidence."""
        text = "[Глава 1 | продолжение] Она вошла."

        self.assertEqual(strip_transcript_header(text), text)

    def test_empty_input_is_returned_as_is(self) -> None:
        self.assertEqual(strip_transcript_header(""), "")


class ExtractionIntegrationTests(unittest.TestCase):
    def test_the_rule_based_extractor_stores_the_line_without_its_header(self) -> None:
        from app.schemas import MessageInput
        from app.services.extractor import extract_memories

        header = "[ 🕰️ Time 7:45 PM | 🗓️ Saturday | 📍 Milan - Kitchen | 🌙 Clear ]"
        candidates = extract_memories(
            chat_id="c",
            character_id="7",
            messages=[MessageInput(role="assistant", text=f"{header} Вчера они обсуждали переезд в Милан.")],
            mode="backfill",
        )

        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertNotIn("🕰️", candidate.content)
            self.assertNotIn("Time", candidate.metadata.entities)



class SceneScaffoldingTests(unittest.TestCase):
    """Model scaffolding leaks into replies in three shapes, and they need different
    treatment - which is why stripping tags alone does not work.

    Ten stored memories were nothing but these. Several had the real prose sitting
    *after* the block, so dropping such a row wholesale would have thrown the fact away
    along with the scaffolding.
    """

    def test_a_rendered_widget_goes_contents_and_all(self) -> None:
        """The markup AND its text are presentation - "> ACCESS GRANTED" is not a fact."""
        text = (
            '<!-- GFX_START --> <div style="background: #1a1a1a;"> > ACCESS GRANTED </div> '
            "<!-- GFX_END --> Твоя ладонь ложится на её макушку."
        )

        self.assertEqual(strip_scene_scaffolding(text), "Твоя ладонь ложится на её макушку.")

    def test_a_widget_truncated_before_its_end_marker_takes_the_rest(self) -> None:
        text = '<!-- GFX_START --> <div style="x"> > SUBJECT: TIFFANY (PLIANT'

        self.assertEqual(strip_scene_scaffolding(text), "")

    def test_the_models_own_thinking_goes_contents_and_all(self) -> None:
        text = "<reasoning> Task 1: check banned constructs </reasoning> Она вошла."

        self.assertEqual(strip_scene_scaffolding(text), "Она вошла.")

    def test_an_unclosed_thinking_block_takes_the_rest(self) -> None:
        """Truncation cuts a block before its closing tag often enough to matter: two of
        the ten rows were an unclosed <reasoning> and <aside>, and matching only balanced
        blocks left their contents behind as if they were prose."""
        self.assertEqual(strip_scene_scaffolding("<reasoning> Task 1: check banned"), "")
        self.assertEqual(strip_scene_scaffolding("<aside> <summary>Момент</summary> - Pressure: high"), "")

    def test_a_wrapper_around_the_reply_keeps_its_contents(self) -> None:
        """<output> is not meta - it wraps what the character actually said."""
        text = "<output> **СЕЛИМ** — Мне не жалко масла. </output>"

        self.assertEqual(strip_scene_scaffolding(text), "**СЕЛИМ** — Мне не жалко масла.")

    def test_a_stray_closing_tag_left_by_truncation_is_dropped(self) -> None:
        text = "</think> Слова достигли её сознания."

        self.assertEqual(strip_scene_scaffolding(text), "Слова достигли её сознания.")

    def test_prose_containing_angle_brackets_is_left_alone(self) -> None:
        for text in ["Он сказал <и осёкся>", "*Валерия улыбается*", "Алина любит чай."]:
            with self.subTest(text=text):
                self.assertEqual(strip_scene_scaffolding(text), text)


class CombinedCleaningTests(unittest.TestCase):
    def test_widget_then_header_then_prose_leaves_the_prose(self) -> None:
        """The real shape in the corpus: the header sits between the block and the
        prose, and only becomes leading once the block is gone - which is why
        scaffolding is stripped first."""
        text = (
            "<!-- GFX_START --> <div> > STATUS </div> <!-- GFX_END --> "
            "[ 🕰️ Время 20:30 | 🗓️ Вторник | 📍 Милан ] Тиффани издаёт тихий вздох."
        )

        self.assertEqual(clean_memory_text(text), "Тиффани издаёт тихий вздох.")

    def test_nothing_but_furniture_leaves_nothing(self) -> None:
        text = '<!-- GFX_START --> <div style="x">📱 INSTAGRAM — 23m ago</div> <!-- GFX_END -->'

        self.assertEqual(clean_memory_text(text), "")

    def test_ordinary_text_passes_through_untouched(self) -> None:
        self.assertEqual(clean_memory_text("Алина любит чай."), "Алина любит чай.")


if __name__ == "__main__":
    unittest.main()
