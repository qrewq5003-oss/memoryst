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

    def test_prompt_is_untouched_when_no_names_are_known(self) -> None:
        """An older extension sends no names; extraction must behave exactly as before
        rather than telling the model the participants are called "unknown"."""
        self.assertEqual(build_scene_facts_prompt(), SCENE_FACTS_PROMPT)
        self.assertEqual(build_scene_facts_prompt(None, None), SCENE_FACTS_PROMPT)

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
