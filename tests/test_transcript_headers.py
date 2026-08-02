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

from app.services.text_utils import strip_transcript_header


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


if __name__ == "__main__":
    unittest.main()
