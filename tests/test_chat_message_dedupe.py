import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from app.config import config
from app.db import get_connection, init_schema
from app.repositories.chat_message_repo import (
    find_recent_chat_message_by_normalized_text,
    get_chat_message_by_id,
    list_chat_messages,
    search_chat_messages_fts,
)
from app.schemas import MessageInput
from app.services.chat_buffer_service import (
    DEDUP_LOOKBACK,
    HOT_BUFFER_SIZE,
    add_message,
    add_messages,
    get_hot_buffer,
    reset_all_buffers,
)

ROOT = Path(__file__).resolve().parent.parent

# Pre-normalized_text shape of chat_messages, to build a database that looks like one
# written before the dedup migration.
LEGACY_CHAT_MESSAGES_TABLE_SQL = """
    CREATE TABLE chat_messages (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        character_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        sequence_index INTEGER NOT NULL
    )
"""


class ChatMessageDedupeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = self.db_path
        self.addCleanup(self._restore_db_path)
        reset_all_buffers()
        self.addCleanup(reset_all_buffers)

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path


class NormalizedTextMigrationTests(ChatMessageDedupeTestCase):
    def test_fresh_database_has_the_normalized_text_column(self) -> None:
        init_schema()

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(chat_messages)")
            columns = {row[1] for row in cursor.fetchall()}
            self.assertIn("normalized_text", columns)

    def test_the_unused_normalized_text_index_is_not_created(self) -> None:
        """The column is load-bearing; the index over it never was.

        normalized_text averages 2.6KB per row, so indexing it whole stored a second
        copy of every message - 21.8MB of a 77MB database - to serve a query shape
        nothing issues. find_recent_chat_message_by_normalized_text narrows by
        sequence_index first and compares normalized_text inside the resulting
        50-row subquery.
        """
        init_schema()

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_chat_messages_normalized'"
            )
            self.assertIsNone(cursor.fetchone())

    def test_an_existing_database_has_the_index_dropped_on_startup(self) -> None:
        init_schema()
        with get_connection() as conn:
            conn.execute(
                "CREATE INDEX idx_chat_messages_normalized "
                "ON chat_messages (chat_id, character_id, normalized_text)"
            )
            conn.commit()

        init_schema()

        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_chat_messages_normalized'"
            ).fetchone()
        self.assertIsNone(row)

    def test_dedup_still_works_without_the_index(self) -> None:
        """The point of dropping it: behaviour is unchanged, only the file size."""
        init_schema()
        from app.services import chat_buffer_service

        first = chat_buffer_service.add_message("c", "7", "user", "Привет, как дела?")
        repeat = chat_buffer_service.add_message("c", "7", "user", "Привет, как дела?")

        self.assertIsNotNone(first)
        self.assertEqual(repeat.id, first.id)

    def test_legacy_database_is_migrated_and_backfilled(self) -> None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(LEGACY_CHAT_MESSAGES_TABLE_SQL)
        cursor.execute(
            "INSERT INTO chat_messages (id, chat_id, character_id, role, text, "
            "created_at, sequence_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy-1", "chat-1", "char-1", "user", "Привет, Валерия!", "2026-07-01T00:00:00Z", 0),
        )
        conn.commit()
        conn.close()

        init_schema()

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT normalized_text FROM chat_messages WHERE id = 'legacy-1'")
            self.assertEqual(cursor.fetchone()[0], "привет валерия")

        # The migration rebuilds the FTS index, so rows that predate it (or that a
        # drifted index had lost) are searchable afterwards.
        results = search_chat_messages_fts("chat-1", "char-1", "Валерия")
        self.assertEqual([r.id for r in results], ["legacy-1"])

    def test_migration_keeps_fts_index_usable(self) -> None:
        # A rebuild of chat_messages would scramble the external-content rowids the
        # FTS index is keyed on, or double every row through the insert trigger.
        init_schema()
        for i in range(HOT_BUFFER_SIZE + 1):
            add_message("chat-1", "char-1", "user", f"сообщение {i} про драконов")

        init_schema()  # idempotent: must not corrupt the index on a second run

        results = search_chat_messages_fts("chat-1", "char-1", "драконов")
        self.assertEqual(len(results), 1)
        self.assertIn("сообщение 0", results[0].text)


class IdempotentIntakeTests(ChatMessageDedupeTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_schema()

    def test_same_message_twice_returns_the_same_buffered_item(self) -> None:
        first = add_message("chat-1", "char-1", "user", "Я вернулась домой поздно.")
        second = add_message("chat-1", "char-1", "user", "Я вернулась домой поздно.")

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.sequence_index, second.sequence_index)
        self.assertEqual(len(get_hot_buffer("chat-1", "char-1")), 1)

    def test_resent_message_matches_after_it_has_cooled(self) -> None:
        original = add_message("chat-1", "char-1", "user", "Первое сообщение сцены.")
        for i in range(HOT_BUFFER_SIZE):
            add_message("chat-1", "char-1", "assistant", f"ответ {i}")

        self.assertEqual(len(list_chat_messages("chat-1", "char-1")), 1)

        resent = add_message("chat-1", "char-1", "user", "Первое сообщение сцены.")

        self.assertEqual(resent.id, original.id)
        self.assertEqual(resent.sequence_index, original.sequence_index)
        self.assertEqual(len(list_chat_messages("chat-1", "char-1")), 1)

    def test_resending_the_extension_window_does_not_grow_history(self) -> None:
        # The extension posts its last 8 messages on every turn: one new message,
        # seven already seen. Only the new one may be added.
        window = [
            MessageInput(role="user" if i % 2 == 0 else "assistant", text=f"реплика {i}")
            for i in range(8)
        ]
        add_messages("chat-1", "char-1", window)

        cooled_before = list_chat_messages("chat-1", "char-1")
        buffer_before = get_hot_buffer("chat-1", "char-1")
        ids_before = {m.id for m in cooled_before + buffer_before}

        next_window = window[1:] + [MessageInput(role="assistant", text="реплика 8")]
        added = add_messages("chat-1", "char-1", next_window)

        cooled_after = list_chat_messages("chat-1", "char-1")
        buffer_after = get_hot_buffer("chat-1", "char-1")
        ids_after = {m.id for m in cooled_after + buffer_after}

        self.assertEqual(len(ids_after), len(ids_before) + 1)
        self.assertTrue(ids_before.issubset(ids_after))
        # The scene handed to extraction is still the full window, resolved to the
        # ids the earlier messages already have.
        self.assertEqual([m.text for m in added], [f"реплика {i}" for i in range(1, 9)])

    def test_returned_ids_stay_resolvable_for_source_message_ids(self) -> None:
        original = add_message("chat-1", "char-1", "user", "Факт, на который сошлётся память.")
        for i in range(HOT_BUFFER_SIZE):
            add_message("chat-1", "char-1", "assistant", f"ответ {i}")

        resent = add_message("chat-1", "char-1", "user", "Факт, на который сошлётся память.")

        stored = get_chat_message_by_id(resent.id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.id, original.id)

    def test_sequence_index_is_not_advanced_by_a_duplicate(self) -> None:
        add_message("chat-1", "char-1", "user", "первое")
        add_message("chat-1", "char-1", "user", "первое")
        second = add_message("chat-1", "char-1", "user", "второе")

        self.assertEqual(second.sequence_index, 1)

    def test_dedup_ignores_case_and_punctuation(self) -> None:
        first = add_message("chat-1", "char-1", "user", "Привет, как дела?")
        second = add_message("chat-1", "char-1", "user", "привет как дела")

        self.assertEqual(first.id, second.id)

    def test_different_text_is_still_added(self) -> None:
        first = add_message("chat-1", "char-1", "user", "Я пошла налево.")
        second = add_message("chat-1", "char-1", "user", "Я пошла направо.")

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(len(get_hot_buffer("chat-1", "char-1")), 2)

    def test_same_text_from_a_different_role_is_a_distinct_message(self) -> None:
        user_msg = add_message("chat-1", "char-1", "user", "Ты уверена?")
        assistant_msg = add_message("chat-1", "char-1", "assistant", "Ты уверена?")

        self.assertNotEqual(user_msg.id, assistant_msg.id)
        self.assertEqual(len(get_hot_buffer("chat-1", "char-1")), 2)

    def test_dedup_is_scoped_to_chat_and_character(self) -> None:
        in_chat_1 = add_message("chat-1", "char-1", "user", "Общая реплика.")
        in_chat_2 = add_message("chat-2", "char-1", "user", "Общая реплика.")
        in_char_2 = add_message("chat-1", "char-2", "user", "Общая реплика.")

        self.assertNotEqual(in_chat_1.id, in_chat_2.id)
        self.assertNotEqual(in_chat_1.id, in_char_2.id)

    def test_repeat_beyond_the_lookback_window_is_treated_as_a_new_message(self) -> None:
        # A short line repeated much later ("да", "хорошо") is a real message, not a
        # resend - dedup must not collapse it into the one from hours ago.
        original = add_message("chat-1", "char-1", "user", "да")
        for i in range(DEDUP_LOOKBACK + HOT_BUFFER_SIZE + 5):
            add_message("chat-1", "char-1", "assistant", f"наполнитель {i}")

        later = add_message("chat-1", "char-1", "user", "да")

        self.assertNotEqual(later.id, original.id)
        self.assertGreater(later.sequence_index, original.sequence_index)

    def test_ooc_and_system_messages_are_still_filtered_before_dedup(self) -> None:
        self.assertIsNone(add_message("chat-1", "char-1", "system", "system prompt"))
        self.assertIsNone(add_message("chat-1", "char-1", "user", "OOC: пропустим"))
        self.assertEqual(get_hot_buffer("chat-1", "char-1"), [])


class FindRecentByNormalizedTextTests(ChatMessageDedupeTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_schema()

    def _cool_everything(self) -> None:
        for i in range(HOT_BUFFER_SIZE):
            add_message("chat-1", "char-1", "assistant", f"вытесняющий {i}")

    def test_returns_none_when_nothing_matches(self) -> None:
        add_message("chat-1", "char-1", "user", "что-то одно")
        self._cool_everything()

        self.assertIsNone(
            find_recent_chat_message_by_normalized_text(
                "chat-1", "char-1", "user", "совсем другое"
            )
        )

    def test_finds_the_earliest_match_in_the_window(self) -> None:
        conn = get_connection()
        cursor = conn.cursor()
        for index in (0, 1):
            cursor.execute(
                "INSERT INTO chat_messages (id, chat_id, character_id, role, text, "
                "created_at, sequence_index, normalized_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"dup-{index}",
                    "chat-1",
                    "char-1",
                    "user",
                    "повтор",
                    "2026-07-01T00:00:00Z",
                    index,
                    "повтор",
                ),
            )
        conn.commit()
        conn.close()

        found = find_recent_chat_message_by_normalized_text(
            "chat-1", "char-1", "user", "повтор"
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.id, "dup-0")


class DedupeScriptTests(ChatMessageDedupeTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_schema()

    def _insert_raw(self, message_id: str, text: str, sequence_index: int) -> None:
        from app.services.text_utils import normalize_for_similarity

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO chat_messages (id, chat_id, character_id, role, text, "
                "created_at, sequence_index, normalized_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    "chat-1",
                    "char-1",
                    "user",
                    text,
                    "2026-07-01T00:00:00Z",
                    sequence_index,
                    normalize_for_similarity(text),
                ),
            )
            conn.commit()

    def _insert_memory_referencing(self, message_ids: list[str]) -> str:
        import json

        memory_id = str(uuid.uuid4())
        metadata = {"source_message_ids": message_ids}
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO memories (id, chat_id, character_id, type, content, "
                "normalized_content, source, layer, importance, created_at, updated_at, "
                "access_count, pinned, archived, metadata_json) "
                "VALUES (?, ?, ?, 'event', 'факт', 'факт', 'auto', 'episodic', 0.5, "
                "?, ?, 0, 0, 0, ?)",
                (
                    memory_id,
                    "chat-1",
                    "char-1",
                    "2026-07-01T00:00:00Z",
                    "2026-07-01T00:00:00Z",
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            conn.commit()
        return memory_id

    def _read_source_message_ids(self, memory_id: str) -> list[str]:
        import json

        with get_connection() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return json.loads(row["metadata_json"])["source_message_ids"]

    def _run_script(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "DATABASE_PATH": self.db_path}
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dedupe_chat_messages.py"), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
            check=True,
        )

    def _seed_duplicates(self) -> None:
        self._insert_raw("keep-1", "Валерия закрыла дверь.", 0)
        self._insert_raw("dup-1a", "Валерия закрыла дверь.", 1)
        self._insert_raw("dup-1b", "валерия закрыла дверь", 2)
        self._insert_raw("keep-2", "Дождь не прекращался.", 3)

    def test_dry_run_reports_but_deletes_nothing(self) -> None:
        self._seed_duplicates()

        result = self._run_script()

        self.assertIn("duplicate rows:      2", result.stdout)
        self.assertIn("Dry run", result.stdout)
        with get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        self.assertEqual(count, 4)

    def test_apply_keeps_the_earliest_row_of_each_group(self) -> None:
        self._seed_duplicates()

        self._run_script("--apply")

        remaining = list_chat_messages("chat-1", "char-1")
        self.assertEqual([m.id for m in remaining], ["keep-1", "keep-2"])

    def test_apply_repoints_source_message_ids_at_the_surviving_row(self) -> None:
        self._seed_duplicates()
        memory_id = self._insert_memory_referencing(["dup-1b", "keep-2"])

        self._run_script("--apply")

        self.assertEqual(self._read_source_message_ids(memory_id), ["keep-1", "keep-2"])

    def test_apply_leaves_the_fts_index_searchable(self) -> None:
        self._seed_duplicates()

        self._run_script("--apply")

        results = search_chat_messages_fts("chat-1", "char-1", "дверь")
        self.assertEqual([r.id for r in results], ["keep-1"])

    def test_apply_is_a_no_op_on_an_already_clean_chat(self) -> None:
        self._insert_raw("keep-1", "Валерия закрыла дверь.", 0)
        self._insert_raw("keep-2", "Дождь не прекращался.", 1)

        result = self._run_script("--apply")

        self.assertIn("Nothing to delete.", result.stdout)
        self.assertEqual(len(list_chat_messages("chat-1", "char-1")), 2)


if __name__ == "__main__":
    unittest.main()
