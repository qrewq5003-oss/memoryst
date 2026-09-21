"""Re-keying trackers reads the mapping off SillyTavern's filesystem rather than guessing.

Memories survived the switch to stable character ids without migration - retrieval scopes
by chat. Trackers did not: they are unique per (chat_id, character_id, tracker_type) and
fetched by that exact triple, so the moment the extension started sending avatars its four
tracker documents per scope stopped resolving.

The mapping is derivable, not guessable: SillyTavern stores a chat at
`chats/<character directory>/<chat id>.jsonl`, and that directory is the card's name,
which is its avatar's filename. These tests pin that a scope whose chat or card is gone is
left alone instead of being migrated on a hunch.
"""

import tempfile
import unittest
from pathlib import Path

from scripts.migrate_tracker_character_ids import _avatar_for, _chat_directories


class _FakeSillyTavern(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        (self.root / "chats" / "Valeria Mendoza1").mkdir(parents=True)
        (self.root / "chats" / "Valeria Mendoza1" / "Valeria Mendoza - 2026-07-09@22h06m04s351ms.jsonl").touch()
        (self.root / "characters").mkdir()
        (self.root / "characters" / "Valeria Mendoza1.png").touch()


class ChatToCharacterTests(_FakeSillyTavern):
    def test_a_chat_maps_to_the_directory_that_holds_it(self) -> None:
        mapping = _chat_directories(self.root)
        self.assertEqual(
            mapping["Valeria Mendoza - 2026-07-09@22h06m04s351ms"],
            "Valeria Mendoza1",
        )

    def test_a_missing_chats_directory_is_empty_not_an_error(self) -> None:
        self.assertEqual(_chat_directories(self.root / "nope"), {})

    def test_the_card_name_resolves_to_its_avatar_file(self) -> None:
        self.assertEqual(_avatar_for("Valeria Mendoza1", self.root), "Valeria Mendoza1.png")

    def test_a_webp_card_is_found_too(self) -> None:
        (self.root / "characters" / "Марк.webp").touch()
        self.assertEqual(_avatar_for("Марк", self.root), "Марк.webp")

    def test_a_deleted_card_resolves_to_nothing_rather_than_a_guess(self) -> None:
        # The scope is then skipped and reported. A tracker moved to an invented id is
        # worse than one left where it is: the old id at least still says something true.
        self.assertIsNone(_avatar_for("Никого", self.root))

    def test_the_directory_name_is_not_assumed_to_be_the_chat_name(self) -> None:
        # SillyTavern appends a digit when two cards share a name, so the chat id prefix
        # and the directory differ - which is exactly why the directory is read rather
        # than derived from the chat id.
        mapping = _chat_directories(self.root)
        chat_id = "Valeria Mendoza - 2026-07-09@22h06m04s351ms"
        self.assertNotEqual(mapping[chat_id], chat_id.split(" - ")[0])


if __name__ == "__main__":
    unittest.main()
