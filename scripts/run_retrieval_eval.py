#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evals.retrieval_eval import format_retrieval_eval_report, run_retrieval_eval_cases
from app.evals.retrieval_eval_cases import DEFAULT_RETRIEVAL_EVAL_CASES


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the retrieval evaluation harness.")
    parser.add_argument(
        "--case",
        help="Optional substring filter for case names.",
    )
    args = parser.parse_args()

    cases = DEFAULT_RETRIEVAL_EVAL_CASES
    if args.case:
        needle = args.case.lower()
        cases = [case for case in cases if needle in case.name.lower()]

    if not cases:
        print("No retrieval eval cases matched the requested filter.", file=sys.stderr)
        return 1

    results = run_retrieval_eval_cases(cases)
    print(format_retrieval_eval_report(results))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
