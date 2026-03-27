#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.summary_service import generate_rolling_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a rolling summary memory for one chat/character.")
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--character-id", required=True)
    parser.add_argument("--window", type=int, default=8)
    args = parser.parse_args()

    result = generate_rolling_summary(
        chat_id=args.chat_id,
        character_id=args.character_id,
        window_size=args.window,
    )

    print(f"action={result.action}")
    print(f"chat_id={result.chat_id}")
    print(f"character_id={result.character_id}")
    print(f"summary_memory_id={result.summary_memory_id}")
    print(f"summarized_count={result.summarized_count}")
    print(f"source_memory_ids={result.source_memory_ids}")
    if result.summary_text:
        print("summary_text:")
        print(result.summary_text)

    return 0 if result.action in {"created", "updated"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
