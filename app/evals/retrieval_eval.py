from dataclasses import dataclass, field
from typing import Iterable
from unittest.mock import patch

from app.schemas import MemoryItem, MessageInput, RetrieveMemoryRequest
from app.services.retrieve_service import retrieve_memories


@dataclass(frozen=True)
class RetrievalEvalCase:
    name: str
    query: str
    fixture_memories: list[MemoryItem]
    recent_messages: list[MessageInput] = field(default_factory=list)
    expected_top_ids: list[str] = field(default_factory=list)
    expected_contains_ids: list[str] = field(default_factory=list)
    forbidden_top_ids: list[str] = field(default_factory=list)
    limit: int = 5
    notes: str = ""


@dataclass(frozen=True)
class RetrievalEvalResult:
    case_name: str
    passed: bool
    retrieved_ids: list[str]
    missing_expected_top_ids: list[str]
    missing_expected_contains_ids: list[str]
    forbidden_present_ids: list[str]
    debug_snapshot: list[str]
    notes: str = ""


def run_retrieval_eval_case(case: RetrievalEvalCase) -> RetrievalEvalResult:
    """Run one retrieval eval case against in-memory fixtures."""
    with (
        patch("app.services.retrieve_service.list_retrieval_candidates", return_value=case.fixture_memories),
        patch("app.services.retrieve_service.increment_access_count"),
    ):
        response = retrieve_memories(
            RetrieveMemoryRequest(
                chat_id="chat-1",
                character_id="char-1",
                user_input=case.query,
                recent_messages=case.recent_messages,
                limit=case.limit,
                debug=True,
            )
        )

    retrieved_ids = [item.id for item in response.items]
    missing_expected_top_ids = [
        memory_id
        for index, memory_id in enumerate(case.expected_top_ids)
        if index >= len(retrieved_ids) or retrieved_ids[index] != memory_id
    ]
    missing_expected_contains_ids = [
        memory_id for memory_id in case.expected_contains_ids if memory_id not in retrieved_ids
    ]
    forbidden_present_ids = [
        memory_id for memory_id in case.forbidden_top_ids if memory_id in retrieved_ids
    ]

    debug_snapshot: list[str] = []
    expected_ids = set(case.expected_top_ids + case.expected_contains_ids + case.forbidden_top_ids)
    if response.debug is not None:
        for candidate in response.debug.candidates:
            if candidate.memory_id in expected_ids:
                debug_snapshot.append(
                    (
                        f"{candidate.memory_id}:layer={candidate.layer}:"
                        f"score={candidate.score:.3f}:reason={candidate.reason}"
                    )
                )

    passed = not (
        missing_expected_top_ids
        or missing_expected_contains_ids
        or forbidden_present_ids
    )

    return RetrievalEvalResult(
        case_name=case.name,
        passed=passed,
        retrieved_ids=retrieved_ids,
        missing_expected_top_ids=missing_expected_top_ids,
        missing_expected_contains_ids=missing_expected_contains_ids,
        forbidden_present_ids=forbidden_present_ids,
        debug_snapshot=debug_snapshot,
        notes=case.notes,
    )


def run_retrieval_eval_cases(cases: Iterable[RetrievalEvalCase]) -> list[RetrievalEvalResult]:
    """Run a deterministic batch of retrieval eval cases."""
    return [run_retrieval_eval_case(case) for case in cases]


def summarize_retrieval_eval(results: Iterable[RetrievalEvalResult]) -> dict[str, int]:
    results_list = list(results)
    passed = sum(1 for result in results_list if result.passed)
    return {
        "total": len(results_list),
        "passed": passed,
        "failed": len(results_list) - passed,
    }


def format_retrieval_eval_report(results: Iterable[RetrievalEvalResult]) -> str:
    """Render a compact text report suitable for local CLI use."""
    results_list = list(results)
    summary = summarize_retrieval_eval(results_list)
    lines = [
        f"retrieval-eval total={summary['total']} passed={summary['passed']} failed={summary['failed']}",
    ]

    for result in results_list:
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"[{status}] {result.case_name}")
        lines.append(f"  retrieved={result.retrieved_ids}")
        if result.missing_expected_top_ids:
            lines.append(f"  missing_expected_top={result.missing_expected_top_ids}")
        if result.missing_expected_contains_ids:
            lines.append(f"  missing_expected_contains={result.missing_expected_contains_ids}")
        if result.forbidden_present_ids:
            lines.append(f"  forbidden_present={result.forbidden_present_ids}")
        if result.debug_snapshot:
            lines.append(f"  debug={result.debug_snapshot}")
        if result.notes:
            lines.append(f"  notes={result.notes}")

    return "\n".join(lines)
