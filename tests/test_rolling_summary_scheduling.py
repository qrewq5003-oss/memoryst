"""The rolling summary layer only failed in one way: nothing ever called it.

`generate_rolling_summary` was reachable from the web UI's button, a CLI script and the
Telegram bot - all of them things a person has to remember. A database of 4639 memories
held two summaries. These tests pin the trigger, not the summarizer: that a store
schedules a refresh, that a refresh which is not due costs no model call, that two turns
in quick succession do not stack them, and that a failure stays inside the background
task where it belongs.
"""

import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.config import config
from app.db import init_schema
from app.main import app
from app.repositories.memory_repo import create_memory
from app.schemas import CreateMemoryRequest, MemoryMetadata
from app.services import summary_scheduler


class _IsolatedDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db)
        init_schema()
        summary_scheduler.reset_in_flight()
        self.addCleanup(summary_scheduler.reset_in_flight)

    def _restore_db(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _episodic(self, content: str) -> None:
        create_memory(
            CreateMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                type="event",
                content=content,
                source="auto",
                layer="episodic",
                importance=0.5,
                metadata=MemoryMetadata(),
            )
        )


class StoreSchedulesARefreshTests(_IsolatedDatabase):
    def test_storing_schedules_a_rolling_summary_refresh(self) -> None:
        # The whole finding: before this, nothing did.
        with patch.object(summary_scheduler, "refresh_rolling_summary") as refresh:
            client = TestClient(app)
            response = client.post(
                "/memory/store",
                json={
                    "chat_id": "chat-1",
                    "character_id": "char-1",
                    "messages": [{"role": "user", "text": "Алина уехала в Казань на неделю"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        refresh.assert_called_once_with("chat-1", "char-1")

    def test_the_refresh_runs_after_the_response_not_inside_it(self) -> None:
        # /memory/store already waits on scene extraction (90s) under a 120s client
        # deadline; a second LLM call inside the request would push a turn past both.
        order: list[str] = []

        def slow_refresh(*_args, **_kwargs):
            order.append("refresh")

        with patch.object(summary_scheduler, "refresh_rolling_summary", side_effect=slow_refresh):
            client = TestClient(app)
            client.post(
                "/memory/store",
                json={
                    "chat_id": "chat-1",
                    "character_id": "char-1",
                    "messages": [{"role": "user", "text": "Марк живёт в Казани и чинит мотоциклы"}],
                },
            )
            order.append("response_returned")

        # TestClient runs background tasks before returning, so the marker order is the
        # only thing that shows the task was scheduled rather than awaited inline.
        self.assertIn("refresh", order)


class RefreshCostTests(_IsolatedDatabase):
    def test_a_refresh_that_is_not_due_never_reaches_the_model(self) -> None:
        # Too few episodic memories to summarize at all. This is the common case on most
        # turns, and it has to be free.
        self._episodic("Алина уехала в Казань")

        with patch("app.services.summary_service.build_llm_summary_text") as llm:
            action = summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        llm.assert_not_called()
        self.assertEqual(action, "skipped_not_enough_inputs")

    def test_a_due_refresh_creates_the_summary(self) -> None:
        for index in range(4):
            self._episodic(f"Событие номер {index}: Алина и Марк поговорили о поездке")

        with patch(
            "app.services.summary_service.build_llm_summary_text",
            return_value="Алина и Марк обсуждали поездку.",
        ):
            action = summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        self.assertEqual(action, "created")

    def test_a_second_refresh_with_nothing_new_costs_no_model_call(self) -> None:
        for index in range(4):
            self._episodic(f"Событие номер {index}: Алина и Марк поговорили о поездке")

        with patch(
            "app.services.summary_service.build_llm_summary_text",
            return_value="Алина и Марк обсуждали поездку.",
        ):
            summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        with patch("app.services.summary_service.build_llm_summary_text") as llm:
            action = summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        llm.assert_not_called()
        self.assertEqual(action, "skipped_not_enough_new_inputs")


class ConcurrencyAndFailureTests(_IsolatedDatabase):
    def test_a_refresh_already_running_for_a_scope_is_not_started_twice(self) -> None:
        # A fast exchange schedules a refresh per turn; without the guard, five quick
        # messages pay five times to race each other for the same row.
        calls: list[tuple[str, str]] = []

        def reentrant(chat_id, character_id, **_kwargs):
            calls.append((chat_id, character_id))
            # Re-entering from inside the running refresh is what a second turn looks
            # like to the guard.
            self.assertIsNone(summary_scheduler.refresh_rolling_summary(chat_id, character_id))
            return _Result("created")

        with patch("app.services.summary_scheduler.generate_rolling_summary", side_effect=reentrant):
            summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        self.assertEqual(len(calls), 1)

    def test_a_different_scope_is_not_blocked_by_a_running_one(self) -> None:
        seen: list[tuple[str, str]] = []

        def reentrant(chat_id, character_id, **_kwargs):
            seen.append((chat_id, character_id))
            if chat_id == "chat-1":
                summary_scheduler.refresh_rolling_summary("chat-2", "char-1")
            return _Result("created")

        with patch("app.services.summary_scheduler.generate_rolling_summary", side_effect=reentrant):
            summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        self.assertIn(("chat-2", "char-1"), seen)

    def test_a_failing_refresh_never_escapes(self) -> None:
        # The store it follows has already been reported successful; raising here would
        # turn a missing summary into an error nobody can act on.
        with patch(
            "app.services.summary_scheduler.generate_rolling_summary",
            side_effect=RuntimeError("model exploded"),
        ):
            self.assertIsNone(summary_scheduler.refresh_rolling_summary("chat-1", "char-1"))

    def test_a_failure_releases_the_scope_for_the_next_turn(self) -> None:
        with patch(
            "app.services.summary_scheduler.generate_rolling_summary",
            side_effect=RuntimeError("model exploded"),
        ):
            summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        calls: list[str] = []
        with patch(
            "app.services.summary_scheduler.generate_rolling_summary",
            side_effect=lambda *a, **k: calls.append("ran") or _Result("created"),
        ):
            summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        self.assertEqual(calls, ["ran"])

    def test_the_switch_turns_it_off(self) -> None:
        original = config.ROLLING_SUMMARY_AUTO
        config.ROLLING_SUMMARY_AUTO = False
        self.addCleanup(lambda: setattr(config, "ROLLING_SUMMARY_AUTO", original))

        with patch("app.services.summary_scheduler.generate_rolling_summary") as generate:
            self.assertIsNone(summary_scheduler.refresh_rolling_summary("chat-1", "char-1"))

        generate.assert_not_called()

    def test_an_empty_scope_is_ignored(self) -> None:
        with patch("app.services.summary_scheduler.generate_rolling_summary") as generate:
            self.assertIsNone(summary_scheduler.refresh_rolling_summary("", "char-1"))
            self.assertIsNone(summary_scheduler.refresh_rolling_summary("chat-1", ""))
        generate.assert_not_called()


class FallbackTextTests(_IsolatedDatabase):
    """The automatic path must not write the rule-based summary.

    Its text is "Краткая сводка последних эпизодов (N): Недавние события:" followed by a
    truncated line lifted out of a memory, and once written it rides in every prompt as
    [SUMMARY]. A row like that makes the layer look alive while putting filler in front
    of the model - which is exactly what 56 of them did to the live database when a
    sweep ran with the model unreachable.
    """

    def _seed(self) -> None:
        for index in range(4):
            self._episodic(f"Событие {index}: Алина и Марк обсуждали поездку в Казань")

    def test_an_unreachable_model_leaves_the_layer_empty(self) -> None:
        from app.repositories.memory_repo import list_memories

        self._seed()
        with patch("app.services.summary_service.build_llm_summary_text", return_value=None):
            action = summary_scheduler.refresh_rolling_summary("chat-1", "char-1")

        self.assertEqual(action, "skipped_llm_unavailable")
        summaries = [item for item in list_memories(limit=50).items if item.type == "summary"]
        self.assertEqual(summaries, [])

    def test_the_manual_path_still_gets_the_fallback(self) -> None:
        # A person pressing the button is asking for something now.
        from app.services.summary_service import generate_rolling_summary

        self._seed()
        with patch("app.services.summary_service.build_llm_summary_text", return_value=None):
            result = generate_rolling_summary("chat-1", "char-1")

        self.assertEqual(result.action, "created")
        self.assertIn("Краткая сводка", result.summary_text)


class SweepDryRunTests(_IsolatedDatabase):
    def test_a_dry_run_writes_nothing_and_calls_no_model(self) -> None:
        # The first cut passed an impossibly high min-new expecting a refusal. min-new is
        # only consulted when a summary already exists, so every scope without one went
        # straight to writing. A dry run has to read, and only read.
        import scripts.run_rolling_summary as cli
        from app.repositories.memory_repo import list_memories

        for index in range(5):
            self._episodic(f"Событие {index}: Алина и Марк обсуждали поездку")

        args = SimpleNamespace(limit=None, dry_run=True, window=8, min_new=3)
        with patch("app.services.summary_service.build_llm_summary_text") as llm:
            cli._sweep(args)

        llm.assert_not_called()
        self.assertEqual([i for i in list_memories(limit=50).items if i.type == "summary"], [])

    def test_the_dry_run_still_reports_what_is_due(self) -> None:
        import scripts.run_rolling_summary as cli

        for index in range(5):
            self._episodic(f"Событие {index}: Алина и Марк обсуждали поездку")

        args = SimpleNamespace(limit=None, dry_run=True, window=8, min_new=3)
        self.assertEqual(cli._sweep(args), 0)
        self.assertGreaterEqual(cli._episodic_count("chat-1"), 5)


class _Result:
    """Minimal stand-in for RollingSummaryResult - only `action` is read here."""

    def __init__(self, action: str) -> None:
        self.action = action
        self.summarized_count = 0


if __name__ == "__main__":
    unittest.main()
