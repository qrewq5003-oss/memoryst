import json
import re

from app.services.llm_client import chat_completion, is_llm_enabled

EXTRACTION_PROMPT = """You are a memory extraction system for a roleplay conversation.

Analyze the following scene and extract structured memory data.

Respond with ONLY valid JSON, no other text:

{
  "title": "Short scene title (3-8 words)",
  "content": "Detailed summary of what happened, focusing on: key events, relationship dynamics, character emotions, important facts. 2-4 sentences.",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "type": "event",
  "mood": "overall mood/atmosphere in 1-2 words"
}

Rules:
- Write in the SAME LANGUAGE as the input (Russian if Russian, English if English)
- Focus on what CHANGED, not what stayed the same
- Capture relationship dynamics, not just actions
- Keywords should be specific nouns, names, places, concepts
- type must be one of: event, relationship, profile"""


def extract_with_llm(messages_text: str) -> dict | None:
    """
    Use LLM to extract structured memory from a scene.

    Returns dict with title, content, keywords, type, mood — or None on failure.
    """
    if not is_llm_enabled():
        return None

    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": f"Scene to analyze:\n\n{messages_text}"},
    ]

    try:
        response = chat_completion(messages, max_tokens=500, temperature=0.3)
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass

    return None


def build_scene_text(messages: list[dict[str, str]], max_chars: int = 4000) -> str:
    """Format messages into a readable scene text for LLM analysis."""
    lines = []
    total = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        text = msg.get("text", "")
        line = f"[{role}]: {text}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)
