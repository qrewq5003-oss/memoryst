"""Scene extraction has to be told who it is writing about.

The LLM supplies `entities` directly (see SCENE_FACTS_SCHEMA), and with only roles to
go on it writes "Девушка положила телефон" / "Пользователь выразил радость", putting
`девушка` and `пользователь` into entities too. Those phrasings are permanent once
stored, and no query contains them, so the entity signal is spent on words that match
nothing. Measured on a fresh chat: 64% of memories opened with a generic noun and the
character's name appeared in entities zero times.
"""
import unittest
from unittest.mock import patch

from app.schemas import ChatMessageItem, MessageInput, StoreMemoryRequest
from app.services.llm_extractor import (
    SCENE_FACTS_PROMPT,
    build_scene_facts_prompt,
)


def _messages(count: int = 4) -> list[ChatMessageItem]:
    return [
        ChatMessageItem(
            id=f"m{i}",
            chat_id="c",
            character_id="4",
            role="user" if i % 2 == 0 else "assistant",
            text=f"Реплика {i} про чай.",
            created_at="2026-08-02T00:00:00+00:00",
            sequence_index=i,
        )
        for i in range(count)
    ]


class ScenePromptTests(unittest.TestCase):
    def test_names_are_injected_when_known(self) -> None:
        prompt = build_scene_facts_prompt("Аллина Волкова", "Wanted")

        self.assertIn("Аллина Волкова", prompt)
        self.assertIn("Wanted", prompt)
        self.assertIn("девушка", prompt)  # the forbidden role words are named explicitly

    def test_no_participants_are_invented_when_no_names_are_known(self) -> None:
        """An older extension sends no names; the model must not be told the
        participants are called "unknown"."""
        for prompt in (build_scene_facts_prompt(), build_scene_facts_prompt(None, None)):
            self.assertNotIn("The participants are", prompt)
            self.assertNotIn("unknown", prompt)

    def test_the_language_block_is_present_without_names_too(self) -> None:
        """It used to live only in the names block, so a scene with no names ended on
        "return an empty facts list" and the language rule stayed at the top of the
        list. 170 memories were written in English from Russian scenes that way."""
        prompt = build_scene_facts_prompt(language="Russian")
        self.assertIn("LANGUAGE,", prompt)
        self.assertGreater(prompt.index("LANGUAGE,"), prompt.index("return an empty"))

    def test_the_language_rule_still_has_the_last_word(self) -> None:
        """Regression from the first live run.

        The names went in as a separate emphatic block after the Rules list, and the
        model - reading English instructions last - wrote all eight facts of a Russian
        scene in English, entities included. Facts stored in the wrong language cannot
        match a query's keywords, which is worse than the role words this was fixing.
        The names now sit inside the Rules and the language requirement closes the
        prompt.
        """
        prompt = build_scene_facts_prompt("Аллина Волкова", "Wanteda")

        names_at = prompt.index("Аллина Волкова")
        language_at = prompt.rindex("LANGUAGE,")
        self.assertGreater(
            language_at,
            names_at,
            "the language requirement must come after the names, or the model follows "
            "the English instruction it read last",
        )
        # The prompt now closes on the names exception to the language rule, which is
        # itself the last thing after the language rule - see VerbatimNameTests.
        self.assertTrue(prompt.rstrip().endswith("a fact nobody can find again."))

    def test_the_names_are_rules_not_a_trailing_block(self) -> None:
        prompt = build_scene_facts_prompt("Аллина Волкова", "Wanteda")
        line = next(l for l in prompt.splitlines() if "The participants are" in l)

        self.assertTrue(line.lstrip().startswith("- "), "must read as one more rule")

    def test_one_known_name_is_still_worth_sending(self) -> None:
        prompt = build_scene_facts_prompt("Аллина Волкова", None)

        self.assertIn("Аллина Волкова", prompt)
        self.assertIn("unknown", prompt)


class ScenePassThroughTests(unittest.TestCase):
    """The names have to survive the whole chain: request -> store_service ->
    scene_extractor -> llm_extractor -> the actual system prompt."""

    def test_names_reach_the_system_prompt_from_the_store_request(self) -> None:
        from app.services import scene_extractor

        captured = {}

        def fake_completion(messages, **kwargs):
            captured["system"] = messages[0]["content"]
            return '{"facts": []}'

        # is_llm_enabled is imported by value into both modules, so both copies have to
        # be patched - scene_extractor gates on its own before ever calling the extractor.
        with (
            patch("app.services.scene_extractor.is_llm_enabled", return_value=True),
            patch("app.services.llm_extractor.is_llm_enabled", return_value=True),
            patch("app.services.llm_extractor.chat_completion", side_effect=fake_completion),
            patch("app.services.scene_extractor.has_regex_signal", return_value=True),
        ):
            scene_extractor.extract_scene_memories(
                chat_id="c",
                character_id="4",
                messages=_messages(),
                character_name="Аллина Волкова",
                user_name="Wanted",
            )

        self.assertIn("Аллина Волкова", captured.get("system", ""))
        self.assertIn("Wanted", captured.get("system", ""))

    def test_the_request_carries_the_names_and_they_stay_optional(self) -> None:
        with_names = StoreMemoryRequest(
            chat_id="c",
            character_id="4",
            messages=[MessageInput(role="user", text="привет")],
            character_name="Аллина Волкова",
            user_name="Wanted",
        )
        without = StoreMemoryRequest(
            chat_id="c",
            character_id="4",
            messages=[MessageInput(role="user", text="привет")],
        )

        self.assertEqual(with_names.character_name, "Аллина Волкова")
        self.assertIsNone(without.character_name)
        self.assertIsNone(without.user_name)


if __name__ == "__main__":
    unittest.main()


class VerbatimNameTests(unittest.TestCase):
    """Participant names are copied, not adapted.

    The rule used to end "use the form of the name that fits the language of the fact",
    which for a Latin persona name in a Russian fact means transliterating it. A trial
    re-extraction of 10 historical scenes on 2026-09-21 produced "Вантед" beside
    "Wanted" and "Мендоза" beside "Мендоса" - and a stored name that does not match how
    it is written everywhere else is a fact no query can reach. Fixing the language
    without this would have traded one unreachable-memory bug for another.

    The exception is repeated inside the LANGUAGE block because that block is last and
    outranks everything above it: an exception stated earlier loses to it.
    """

    def test_the_rule_demands_the_exact_spelling(self) -> None:
        prompt = build_scene_facts_prompt("Beatriz", "Wanted", language="Russian")
        self.assertIn("EXACTLY as spelled here", prompt)
        self.assertIn("never transliterated, translated or", prompt)

    def test_the_language_block_carves_the_names_out(self) -> None:
        prompt = build_scene_facts_prompt("Beatriz", "Wanted", language="Russian")
        language_at = prompt.index("LANGUAGE,")
        exception_at = prompt.index("names keep their own spelling")
        self.assertGreater(
            exception_at,
            language_at,
            "the exception must come after the rule it excepts, or the model applies the "
            "language rule to the names too",
        )

    def test_the_names_are_interpolated_not_described(self) -> None:
        prompt = build_scene_facts_prompt("Beatriz", "Wanted", language="Russian")
        self.assertIn("the character is Beatriz, the user is Wanted", prompt)

    def test_role_words_are_still_forbidden(self) -> None:
        prompt = build_scene_facts_prompt("Beatriz", "Wanted", language="Russian")
        for role_word in ("девушка", "пользователь", "the girl", "the user"):
            self.assertIn(role_word, prompt)
