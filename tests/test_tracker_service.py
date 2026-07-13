import json
import tempfile
import unittest
import uuid
from datetime import date, time
from pathlib import Path
from unittest.mock import patch

from app.config import config
from app.db import init_schema
from app.repositories.chat_message_repo import insert_chat_message
from app.repositories.memory_repo import get_tracker
from app.schemas import ChatMessageItem
from app.services import tracker_service
from app.services.chat_buffer_service import reset_all_buffers
from app.services.tracker_prompts import (
    find_dates_in_summary,
    parse_date,
    parse_time,
    render_tracker,
    sort_timeline_entries,
)
from app.services.tracker_service import update_tracker

CHAT_ID = "chat-b"
CHARACTER_ID = "20"


class TrackerServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = str(Path(self.temp_dir.name) / "test.db")
        self.addCleanup(self._restore_db_path)
        reset_all_buffers()
        self.addCleanup(reset_all_buffers)
        init_schema()

    def _restore_db_path(self) -> None:
        config.DATABASE_PATH = self.original_db_path

    def _cool(self, *texts: str, start: int = 0) -> list[ChatMessageItem]:
        """Write messages straight into chat_messages, as if they had cooled."""
        items = []
        for offset, text in enumerate(texts):
            item = ChatMessageItem(
                id=str(uuid.uuid4()),
                chat_id=CHAT_ID,
                character_id=CHARACTER_ID,
                role="user" if offset % 2 == 0 else "assistant",
                text=text,
                created_at="2026-07-13T10:00:00Z",
                sequence_index=start + offset,
            )
            insert_chat_message(item)
            items.append(item)
        return items

    def _llm(self, *payloads: dict):
        """Patch the tracker LLM to return these payloads, one per chunk."""
        responses = [json.dumps(payload, ensure_ascii=False) for payload in payloads]
        return patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=lambda *a, **kw: responses.pop(0),
        )

    def _update(self, tracker_type: str = "timeline", **kwargs):
        return update_tracker(
            chat_id=CHAT_ID, character_id=CHARACTER_ID, tracker_type=tracker_type, **kwargs
        )


class WatermarkTests(TrackerServiceTestCase):
    def test_watermark_advances_and_second_run_sees_nothing_new(self) -> None:
        self._cool("вечер четверга", "она приехала")

        with self._llm({"entries": [_entry("Thursday, February 13, 2025", "7:45 PM", [0, 1])]}):
            first = self._update()

        self.assertEqual(first.action, "created")
        self.assertEqual(first.messages_consumed, 2)

        tracker = get_tracker(CHAT_ID, CHARACTER_ID, "timeline")
        self.assertEqual(tracker.metadata.tracker_last_sequence_index, 1)

        second = self._update()
        self.assertEqual(second.action, "skipped_no_new_messages")
        self.assertEqual(second.messages_consumed, 0)
        # The document survives a no-op run rather than being blanked.
        self.assertEqual(second.content, tracker.content)

    def test_only_messages_past_the_watermark_are_fed_back_to_the_llm(self) -> None:
        self._cool("первое", "второе")
        with self._llm({"entries": [_entry("February 13, 2025", "7:45 PM", [0, 1])]}):
            self._update()

        self._cool("третье", start=2)

        seen = {}

        def capture(messages, **kwargs):
            seen["user"] = messages[1]["content"]
            return json.dumps({"entries": [_entry("February 13, 2025", "7:45 PM", [])]})

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=capture,
        ):
            result = self._update()

        self.assertEqual(result.action, "updated")
        self.assertEqual(result.messages_consumed, 1)
        self.assertIn("третье", seen["user"])
        self.assertNotIn("первое", seen["user"])

    def test_full_rebuild_resets_the_watermark_and_discards_the_old_document(self) -> None:
        self._cool("первое", "второе")
        with self._llm({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]}):
            self._update()

        seen = {}

        def capture(messages, **kwargs):
            seen["user"] = messages[1]["content"]
            return json.dumps({"entries": [_entry("February 14, 2025", "9:00 AM", [0, 1])]})

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=capture,
        ):
            result = self._update(full_rebuild=True)

        self.assertEqual(result.action, "updated")
        self.assertEqual(result.messages_consumed, 2)
        # Rebuilt from an empty document, and from the very first message again.
        document = seen["user"].split("Новые сообщения")[0]
        self.assertIn("[]", document)
        self.assertNotIn("February 13", document)
        self.assertIn("первое", seen["user"])
        self.assertIn("February 14", result.content)
        self.assertNotIn("February 13", result.content)

    def test_hot_buffer_messages_are_consumed_so_the_tracker_does_not_lag(self) -> None:
        from app.schemas import MessageInput
        from app.services.chat_buffer_service import add_messages

        # Two messages, both still hot (the buffer only cools at the fifth).
        add_messages(
            CHAT_ID,
            CHARACTER_ID,
            [MessageInput(role="user", text="горячее"), MessageInput(role="assistant", text="тоже")],
        )

        seen = {}

        def capture(messages, **kwargs):
            seen["user"] = messages[1]["content"]
            return json.dumps({"entries": [_entry("February 13, 2025", "7:45 PM", [0, 1])]})

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=capture,
        ):
            result = self._update()

        self.assertEqual(result.messages_consumed, 2)
        self.assertIn("горячее", seen["user"])


class RewriteNotAccumulateTests(TrackerServiceTestCase):
    def test_update_replaces_the_document_instead_of_appending_a_second_memory(self) -> None:
        self._cool("первое")
        with self._llm({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]}):
            self._update()

        first = get_tracker(CHAT_ID, CHARACTER_ID, "timeline")

        self._cool("второе", start=1)
        with self._llm(
            {
                "entries": [
                    _entry("February 13, 2025", "7:45 PM", []),
                    _entry("February 14, 2025", "9:00 AM", [0], summary="уехала"),
                ]
            }
        ):
            result = self._update()

        second = get_tracker(CHAT_ID, CHARACTER_ID, "timeline")

        self.assertEqual(result.action, "updated")
        self.assertEqual(second.id, first.id)
        self.assertEqual(len(second.metadata.tracker_entries), 2)
        self.assertEqual(len(tracker_service.list_tracker_items(CHAT_ID, CHARACTER_ID)), 1)

    def test_carried_over_entries_keep_their_provenance(self) -> None:
        messages = self._cool("первое")
        with self._llm({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]}):
            self._update()

        self._cool("второе", start=1)
        # The model returns the old entry with an empty index list, as the prompt asks.
        with self._llm(
            {
                "entries": [
                    _entry("February 13, 2025", "7:45 PM", []),
                    _entry("February 14, 2025", "9:00 AM", [0], summary="уехала"),
                ]
            }
        ):
            self._update()

        entries = get_tracker(CHAT_ID, CHARACTER_ID, "timeline").metadata.tracker_entries
        carried = next(e for e in entries if e["date"] == "February 13, 2025")

        self.assertEqual(carried["source_message_ids"], [messages[0].id])


class ChunkingTests(TrackerServiceTestCase):
    def test_long_history_is_processed_in_windows_with_a_watermark_per_chunk(self) -> None:
        self._cool(*[f"сообщение {i}" for i in range(95)])

        calls = []

        def capture(messages, **kwargs):
            calls.append(messages[1]["content"])
            return json.dumps({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]})

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=capture,
        ):
            result = self._update()

        # 95 messages at 40 per window.
        self.assertEqual(len(calls), 3)
        self.assertEqual(result.messages_consumed, 95)
        self.assertEqual(get_tracker(CHAT_ID, CHARACTER_ID, "timeline").metadata.tracker_last_sequence_index, 94)

    def test_an_interrupted_run_keeps_the_chunks_it_already_committed(self) -> None:
        self._cool(*[f"сообщение {i}" for i in range(95)])

        # Keyed on the window's content, not on a call counter: the retry would otherwise
        # rescue the second window and the run would never actually be interrupted.
        def dies_on_the_second_window(messages, **kwargs):
            if "сообщение 40" in messages[1]["content"]:
                raise RuntimeError("provider exploded mid-run")
            return json.dumps({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]})

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=dies_on_the_second_window,
        ):
            result = self._update()

        # First window committed; the run stopped at the second rather than losing it.
        self.assertEqual(result.action, "created")
        self.assertEqual(result.messages_consumed, 40)

        tracker = get_tracker(CHAT_ID, CHARACTER_ID, "timeline")
        self.assertEqual(tracker.metadata.tracker_last_sequence_index, 39)

        # The next run resumes from message 40, not from zero.
        with self._llm({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]}, {"entries": []}):
            resumed = self._update()
        self.assertEqual(resumed.messages_consumed, 55)


class LlmUnavailableTests(TrackerServiceTestCase):
    def test_no_llm_is_reported_as_unavailable_not_as_an_empty_tracker(self) -> None:
        self._cool("первое")

        with patch("app.services.tracker_service.is_llm_enabled", return_value=False):
            result = self._update()

        self.assertEqual(result.action, "skipped_llm_unavailable")
        self.assertIsNone(get_tracker(CHAT_ID, CHARACTER_ID, "timeline"))

    def test_a_failing_llm_is_distinguishable_from_a_missing_one(self) -> None:
        self._cool("первое")

        def boom(*args, **kwargs):
            raise RuntimeError("502 from provider")

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=boom,
        ):
            result = self._update()

        self.assertEqual(result.action, "skipped_llm_failed")
        self.assertIsNone(get_tracker(CHAT_ID, CHARACTER_ID, "timeline"))

    def test_a_failed_update_does_not_damage_an_existing_document(self) -> None:
        self._cool("первое")
        with self._llm({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]}):
            self._update()
        before = get_tracker(CHAT_ID, CHARACTER_ID, "timeline")

        self._cool("второе", start=1)

        def boom(*args, **kwargs):
            raise RuntimeError("502 from provider")

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=boom,
        ):
            result = self._update()

        after = get_tracker(CHAT_ID, CHARACTER_ID, "timeline")
        self.assertEqual(result.action, "skipped_llm_failed")
        self.assertEqual(after.content, before.content)
        self.assertEqual(
            after.metadata.tracker_last_sequence_index,
            before.metadata.tracker_last_sequence_index,
        )


class ChronologyTests(unittest.TestCase):
    def test_entries_are_sorted_by_date_not_by_the_order_the_model_returned_them(self) -> None:
        entries = [
            {"date": "Friday, February 14, 2025", "time": "9:00 AM", "summary": "утро пятницы"},
            {"date": "Thursday, February 13, 2025", "time": "7:45 PM", "summary": "вечер четверга"},
            {"date": "Thursday, February 13, 2025", "time": "8:30 AM", "summary": "утро четверга"},
        ]

        ordered = [e["summary"] for e in sort_timeline_entries(entries)]

        self.assertEqual(ordered, ["утро четверга", "вечер четверга", "утро пятницы"])

    def test_unparseable_dates_sort_last_and_keep_their_relative_order(self) -> None:
        entries = [
            {"date": "позже", "time": "", "summary": "без даты A", "source_sequence_indices": [9]},
            {"date": "February 13, 2025", "time": "7:45 PM", "summary": "с датой"},
            {"date": "когда-то", "time": "", "summary": "без даты B", "source_sequence_indices": [3]},
        ]

        ordered = [e["summary"] for e in sort_timeline_entries(entries)]

        self.assertEqual(ordered[0], "с датой")
        # Ordered among themselves by earliest source message, not by model order.
        self.assertEqual(ordered[1:], ["без даты B", "без даты A"])

    def test_same_moment_ties_break_on_the_earliest_source_message(self) -> None:
        entries = [
            {"date": "February 13, 2025", "time": "7:45 PM", "summary": "второе", "source_sequence_indices": [7]},
            {"date": "February 13, 2025", "time": "7:45 PM", "summary": "первое", "source_sequence_indices": [2]},
        ]

        ordered = [e["summary"] for e in sort_timeline_entries(entries)]

        self.assertEqual(ordered, ["первое", "второе"])

    def test_dates_parse_across_formats_and_languages(self) -> None:
        self.assertEqual(parse_date("Thursday, February 13, 2025"), date(2025, 2, 13))
        self.assertEqual(parse_date("13 февраля 2025"), date(2025, 2, 13))
        self.assertEqual(parse_date("2025-02-13"), date(2025, 2, 13))
        self.assertEqual(parse_date("13.02.2025"), date(2025, 2, 13))
        self.assertIsNone(parse_date("на следующий день"))
        self.assertIsNone(parse_date(""))

    def test_times_parse_in_both_12h_and_24h(self) -> None:
        self.assertEqual(parse_time("7:45 PM"), time(19, 45))
        self.assertEqual(parse_time("12:30 AM"), time(0, 30))
        self.assertEqual(parse_time("19:45"), time(19, 45))
        self.assertIsNone(parse_time("вечером"))


class FusedDayDetectionTests(unittest.TestCase):
    def test_two_days_fused_into_one_summary_is_detected(self) -> None:
        # This is verbatim the shape of the bug that motivated trackers: Thursday evening
        # and Friday morning welded into a single entry.
        summary = (
            "Вечером 13 February они поговорили, а утром 14 February она уехала, "
            "так и не дождавшись ответа."
        )

        found = find_dates_in_summary(summary)

        self.assertEqual(len(found), 2)

    def test_a_single_day_summary_is_not_flagged(self) -> None:
        found = find_dates_in_summary("Вечером 13 February они наконец поговорили.")
        self.assertEqual(len(found), 1)

    def test_the_service_warns_but_still_keeps_the_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = config.DATABASE_PATH
            config.DATABASE_PATH = str(Path(tmp) / "t.db")
            try:
                init_schema()
                reset_all_buffers()
                insert_chat_message(
                    ChatMessageItem(
                        id=str(uuid.uuid4()), chat_id=CHAT_ID, character_id=CHARACTER_ID,
                        role="user", text="что-то", created_at="2026-07-13T10:00:00Z",
                        sequence_index=0,
                    )
                )
                fused = _entry(
                    "February 13, 2025", "7:45 PM", [0],
                    summary="Вечером 13 February поговорили, утром 14 February она уехала.",
                )
                with patch.multiple(
                    "app.services.tracker_service",
                    is_llm_enabled=lambda: True,
                    chat_completion=lambda *a, **kw: json.dumps({"entries": [fused]}),
                ), patch("app.services.tracker_service.print") as warn:
                    result = update_tracker(CHAT_ID, CHARACTER_ID, "timeline")

                warnings = [c for c in warn.call_args_list if "distinct dates" in str(c)]
                self.assertEqual(len(warnings), 1)
                # Warned, not dropped - losing the content would be worse than logging it.
                self.assertEqual(result.entries_count, 1)
            finally:
                config.DATABASE_PATH = original
                reset_all_buffers()


class RenderingTests(unittest.TestCase):
    def test_timeline_renders_one_line_per_moment(self) -> None:
        text = render_tracker(
            "timeline",
            [
                {
                    "date": "Thursday, February 13, 2025",
                    "time": "7:45 PM",
                    "location": "Milan, apartment",
                    "summary": "она приехала",
                }
            ],
        )
        self.assertEqual(
            text, "- Thursday, February 13, 2025, 7:45 PM — Milan, apartment: она приехала"
        )

    def test_relationship_renders_score_dimensions_and_lists(self) -> None:
        text = render_tracker(
            "relationship",
            [
                {
                    "affinity_score": 72,
                    "affinity_evidence": "делится личным",
                    "custom_dimensions": [{"name": "влюблённость", "value": 65}],
                    "status": "сближаются",
                    "trust": "растёт",
                    "tension": "невысказанное",
                    "key_facts": ["живёт в Милане"],
                    "goals": ["уехать вместе"],
                    "open_threads": ["не ответила про отца"],
                }
            ],
        )
        self.assertIn("Affinity: 72/100 — делится личным", text)
        self.assertIn("влюблённость: 65/100", text)
        self.assertIn("Key facts:\n- живёт в Милане", text)

    def test_npcs_render_in_importance_order(self) -> None:
        from app.services.tracker_prompts import normalize_payload

        entries = normalize_payload(
            "npc_whoswho",
            {
                "npcs": [
                    {"name": "Марко", "description": "сосед", "importance_rank": 2},
                    {"name": "Отец", "description": "давит на неё", "importance_rank": 1},
                ]
            },
        )
        text = render_tracker("npc_whoswho", entries)

        # Sorted by plot importance, so the extension's budget can truncate from the tail
        # and lose the least significant NPCs first.
        self.assertEqual(text, "1. Отец — давит на неё\n2. Марко — сосед")

    def test_pov_notes_render_as_a_bullet_list(self) -> None:
        from app.services.tracker_prompts import normalize_payload

        entries = normalize_payload("character_pov_notes", {"notes": ["Он не любит вопросов."]})
        self.assertEqual(render_tracker("character_pov_notes", entries), "- Он не любит вопросов.")


class AllTrackerTypesTests(TrackerServiceTestCase):
    def test_every_tracker_type_can_be_built_and_they_coexist(self) -> None:
        self._cool("первое", "второе")

        payloads = {
            "timeline": {"entries": [_entry("February 13, 2025", "7:45 PM", [0])]},
            "relationship": {
                "affinity_score": 72, "affinity_evidence": "доверилась",
                "custom_dimensions": [], "status": "сближаются", "trust": "растёт",
                "tension": "есть", "key_facts": ["живёт в Милане"], "goals": [],
                "open_threads": [],
            },
            "npc_whoswho": {"npcs": [{"name": "Марко", "description": "сосед", "importance_rank": 1}]},
            "character_pov_notes": {"notes": ["Он молчит, когда врёт."]},
        }

        for tracker_type, payload in payloads.items():
            with self._llm(payload):
                result = self._update(tracker_type)
            self.assertEqual(result.action, "created", tracker_type)
            self.assertTrue(result.content.strip(), tracker_type)

        items = tracker_service.list_tracker_items(CHAT_ID, CHARACTER_ID)
        self.assertEqual({i.tracker_type for i in items}, set(payloads))
        self.assertEqual(len(items), 4)

    def test_counters_report_how_far_each_tracker_has_fallen_behind(self) -> None:
        self._cool("первое", "второе")
        with self._llm({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]}):
            self._update("timeline")

        counters = tracker_service.list_tracker_counters(CHAT_ID, CHARACTER_ID)
        self.assertEqual(counters[0].messages_since_update, 0)

        self._cool("третье", "четвёртое", "пятое", start=2)

        counters = tracker_service.list_tracker_counters(CHAT_ID, CHARACTER_ID)
        self.assertEqual(counters[0].tracker_type, "timeline")
        self.assertEqual(counters[0].messages_since_update, 3)

    def test_unknown_tracker_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._update("horoscope")


class NpcParticipantExclusionTests(TrackerServiceTestCase):
    NPCS = {
        "npcs": [
            {"name": "Валерия", "description": "главная героиня", "importance_rank": 1},
            {"name": "Отец Валерии", "description": "умер", "importance_rank": 2},
            {"name": "Марко", "description": "пользователь", "importance_rank": 3},
        ]
    }

    def test_main_participants_are_stripped_even_when_the_model_lists_them(self) -> None:
        # Observed against a real provider: the model lists the main character as the top
        # NPC no matter how emphatically the prompt forbids it, because a roleplay
        # assistant narrates itself in the third person. So Python removes them.
        self._cool("первое")

        with self._llm(self.NPCS):
            result = self._update(
                "npc_whoswho", character_name="Валерия", user_name="Марко"
            )

        self.assertEqual(result.entries_count, 1)
        self.assertIn("Отец Валерии", result.content)
        self.assertNotIn("1. Валерия —", result.content)
        self.assertNotIn("Марко", result.content)

    def test_exclusion_is_exact_and_does_not_eat_npcs_named_after_a_participant(self) -> None:
        # "Отец Валерии" is an NPC *because* he is not Валерия - a substring filter
        # would silently delete him.
        self._cool("первое")

        with self._llm(self.NPCS):
            result = self._update("npc_whoswho", character_name="Валерия")

        self.assertIn("Отец Валерии", result.content)

    def test_without_names_nothing_is_stripped(self) -> None:
        self._cool("первое")

        with self._llm(self.NPCS):
            result = self._update("npc_whoswho")

        self.assertEqual(result.entries_count, 3)

    def test_participant_names_reach_the_prompt(self) -> None:
        self._cool("первое")
        seen = {}

        def capture(messages, **kwargs):
            seen["system"] = messages[0]["content"]
            return json.dumps(self.NPCS)

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=capture,
        ):
            self._update("npc_whoswho", character_name="Валерия", user_name="Марко")

        self.assertIn("Валерия", seen["system"])
        self.assertIn("Марко", seen["system"])


class TransientFailureTests(TrackerServiceTestCase):
    def test_a_transient_failure_is_retried_rather_than_surfaced(self) -> None:
        # The real-provider failures were ReadTimeouts and empty completions that
        # succeeded on an identical second call - not the model rejecting the schema.
        self._cool("первое")
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("read timed out")
            return json.dumps({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]})

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=flaky,
        ):
            result = self._update()

        self.assertEqual(calls["n"], 2)
        self.assertEqual(result.action, "created")

    def test_an_empty_completion_counts_as_a_failure_not_an_empty_document(self) -> None:
        # A reasoning model that spends its whole budget on hidden reasoning returns "".
        # json.loads("") would raise anyway, but treating it as a distinct failure keeps
        # the log honest about what happened - the lesson from the scene-extraction bug.
        self._cool("первое")

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=lambda *a, **kw: "   ",
        ):
            result = self._update()

        self.assertEqual(result.action, "skipped_llm_failed")
        self.assertIsNone(get_tracker(CHAT_ID, CHARACTER_ID, "timeline"))

    def test_tracker_calls_get_their_own_generous_timeout(self) -> None:
        # 30s (the default) is where the real ReadTimeouts happened: a reasoning model
        # rewriting a whole document runs 15-35s.
        self._cool("первое")
        seen = {}

        def capture(messages, **kwargs):
            seen.update(kwargs)
            return json.dumps({"entries": [_entry("February 13, 2025", "7:45 PM", [0])]})

        with patch.multiple(
            "app.services.tracker_service",
            is_llm_enabled=lambda: True,
            chat_completion=capture,
        ):
            self._update()

        self.assertEqual(seen["timeout"], config.TRACKER_LLM_TIMEOUT)
        self.assertGreaterEqual(seen["timeout"], 60)
        self.assertEqual(seen["max_tokens"], config.TRACKER_LLM_MAX_TOKENS)


def _entry(date_str: str, time_str: str, indices: list[int], summary: str = "она приехала") -> dict:
    return {
        "date": date_str,
        "time": time_str,
        "location": "Milan",
        "summary": summary,
        "source_message_indices": indices,
    }


if __name__ == "__main__":
    unittest.main()
