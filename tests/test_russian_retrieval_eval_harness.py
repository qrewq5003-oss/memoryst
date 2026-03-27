import unittest

from app.evals.retrieval_eval import (
    RetrievalEvalCase,
    format_retrieval_eval_report,
    run_retrieval_eval_case,
    run_retrieval_eval_cases,
    summarize_retrieval_eval,
)
from app.evals.retrieval_eval_cases import DEFAULT_RETRIEVAL_EVAL_CASES, LONG_CHAT_RUSSIAN_RP_EVAL_CASES
from app.schemas import MemoryItem, MemoryMetadata


def _memory(memory_id: str, content: str) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        chat_id="chat-1",
        character_id="char-1",
        type="profile",
        content=content,
        normalized_content=content.lower(),
        source="manual",
        layer="stable",
        importance=0.7,
        created_at="2026-03-01T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        last_accessed_at=None,
        access_count=0,
        pinned=False,
        archived=False,
        metadata=MemoryMetadata(keywords=["алиса", "джаз"], entities=["Алиса"]),
    )


class RussianRetrievalEvalHarnessTests(unittest.TestCase):
    def test_default_eval_cases_pass(self) -> None:
        results = run_retrieval_eval_cases(DEFAULT_RETRIEVAL_EVAL_CASES)
        summary = summarize_retrieval_eval(results)

        self.assertEqual(summary["total"], len(DEFAULT_RETRIEVAL_EVAL_CASES))
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(all(result.passed for result in results))

    def test_harness_is_deterministic_for_same_cases(self) -> None:
        first_run = run_retrieval_eval_cases(DEFAULT_RETRIEVAL_EVAL_CASES)
        second_run = run_retrieval_eval_cases(DEFAULT_RETRIEVAL_EVAL_CASES)

        self.assertEqual(
            [
                (result.case_name, result.retrieved_ids, result.retrieved_layer_counts, result.passed)
                for result in first_run
            ],
            [
                (result.case_name, result.retrieved_ids, result.retrieved_layer_counts, result.passed)
                for result in second_run
            ],
        )

    def test_long_chat_eval_cases_preserve_expected_layer_composition(self) -> None:
        results = run_retrieval_eval_cases(LONG_CHAT_RUSSIAN_RP_EVAL_CASES)

        self.assertTrue(all(result.passed for result in results))
        self.assertTrue(all(sum(result.retrieved_layer_counts.values()) >= 2 for result in results))
        self.assertTrue(any(result.retrieved_layer_counts["summary"] == 1 for result in results))

    def test_failure_result_reports_missing_and_forbidden_ids(self) -> None:
        failing_case = RetrievalEvalCase(
            name="failing_case",
            query="Что любит Алиса?",
            fixture_memories=[_memory("wrong", "Алиса любит кофе.")],
            expected_top_ids=["expected"],
            forbidden_top_ids=["wrong"],
            expected_layer_counts={"summary": 1},
        )

        result = run_retrieval_eval_case(failing_case)
        report = format_retrieval_eval_report([result])

        self.assertFalse(result.passed)
        self.assertEqual(result.missing_expected_top_ids, ["expected"])
        self.assertEqual(result.forbidden_present_ids, ["wrong"])
        self.assertEqual(result.mismatched_layer_counts["summary"]["expected"], 1)
        self.assertIn("missing_expected_top", report)
        self.assertIn("forbidden_present", report)
        self.assertIn("mismatched_layer_counts", report)


if __name__ == "__main__":
    unittest.main()
