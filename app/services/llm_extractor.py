import json
import sys
import traceback

from app.schemas import ChatMessageItem
from app.services.llm_client import chat_completion, is_llm_enabled

EXTRACTION_PROMPT = """You are a memory extraction system for a roleplay conversation.

Analyze the following scene and extract structured memory data.

Rules:
- Write in the SAME LANGUAGE as the input (Russian if Russian, English if English)
- Focus on what CHANGED, not what stayed the same
- Capture relationship dynamics, not just actions
- Keywords should be specific nouns, names, places, concepts
- type must be one of: event, relationship, profile"""

EXTRACTION_SCHEMA = {
    "name": "scene_memory_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short scene title (3-8 words)"},
            "content": {
                "type": "string",
                "description": (
                    "Detailed summary of what happened, focusing on: key events, "
                    "relationship dynamics, character emotions, important facts. "
                    "2-4 sentences."
                ),
            },
            "keywords": {"type": "array", "items": {"type": "string"}},
            "type": {"type": "string", "enum": ["event", "relationship", "profile"]},
            "mood": {"type": "string", "description": "Overall mood/atmosphere in 1-2 words"},
        },
        "required": ["title", "content", "keywords", "type", "mood"],
        "additionalProperties": False,
    },
}


def extract_with_llm(messages_text: str, *, model: str | None = None) -> dict | None:
    """
    Use LLM to extract a single structured memory from a scene (manual /memory/scene tool).

    Returns dict with title, content, keywords, type, mood — or None on failure.
    """
    if not is_llm_enabled():
        return None

    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": f"Scene to analyze:\n\n{messages_text}"},
    ]

    try:
        response = chat_completion(
            messages,
            model=model,
            max_tokens=500,
            temperature=0.3,
            response_format={"type": "json_schema", "json_schema": EXTRACTION_SCHEMA},
        )
        return json.loads(response)
    except Exception:
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


SCENE_FACTS_PROMPT = """You are a memory extraction system for a roleplay conversation.

You will be shown a scene as a numbered list of chat messages: "[N][role]: text".
Read the WHOLE scene together (not message by message) and extract the distinct
facts that are worth remembering long-term: profile facts, relationship dynamics,
and notable events. Do not restate the raw dialogue - extract what CHANGED or what
is now true.

For each fact, list the indices (the N in "[N]") of every message that fact was
drawn from, in "source_message_indices".

Rules:
- Write each fact's content in the SAME LANGUAGE as the input (Russian if Russian,
  English if English).
- Skip small talk, greetings, and questions that weren't answered in the scene.
- "type" must be one of: profile, relationship, event.
- "layer" must be "stable" for durable facts (who someone is, an ongoing
  relationship state) and "episodic" for one-off scenes/events.
- keywords: specific nouns, names, places, concepts. entities: character/person names.
- If nothing in the scene is worth remembering, return an empty "facts" list."""

SCENE_FACTS_SCHEMA = {
    "name": "scene_fact_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "type": {"type": "string", "enum": ["profile", "relationship", "event"]},
                        "layer": {"type": "string", "enum": ["episodic", "stable"]},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "entities": {"type": "array", "items": {"type": "string"}},
                        "source_message_indices": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": [
                        "content",
                        "type",
                        "layer",
                        "keywords",
                        "entities",
                        "source_message_indices",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["facts"],
        "additionalProperties": False,
    },
}


def build_indexed_scene_text(
    messages: list[ChatMessageItem], max_chars: int = 4000
) -> tuple[str, list[str]]:
    """
    Format buffered/cooled chat messages into an indexed scene text for LLM analysis.

    Returns (scene_text, id_by_index) where id_by_index[i] is the chat_messages id
    of the message labeled "[i]" in scene_text - this lets the LLM reference messages
    by a small integer instead of repeating UUIDs, while still letting callers map
    "source_message_indices" back to real message ids afterwards.
    """
    lines = []
    id_by_index: list[str] = []
    total = 0
    for message in messages:
        index = len(id_by_index)
        line = f"[{index}][{message.role}]: {message.text}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        id_by_index.append(message.id)
        total += len(line)
    return "\n\n".join(lines), id_by_index


def extract_scene_facts(
    messages: list[ChatMessageItem], *, model: str | None = None
) -> list[dict] | None:
    """
    Use the LLM to extract structured facts from a whole scene at once (Stage 3).

    Unlike extract_with_llm (single fact, used by the manual /memory/scene tool),
    this returns a list of facts, each tagged with source_message_ids pointing back
    to the chat_messages (Stage 2) it was drawn from. Returns None when the LLM is
    disabled, the scene is empty, or the call/parse fails - callers should fall back
    to the rule-based extractor in that case.
    """
    if not is_llm_enabled() or not messages:
        return None

    scene_text, id_by_index = build_indexed_scene_text(messages)
    if not scene_text:
        return None

    llm_messages = [
        {"role": "system", "content": SCENE_FACTS_PROMPT},
        {"role": "user", "content": f"Scene to analyze:\n\n{scene_text}"},
    ]

    try:
        response = chat_completion(
            llm_messages,
            model=model,
            # Reasoning models (e.g. zai-org/glm-4.7, the former default) spend part of this
            # budget on hidden reasoning tokens before ever emitting the schema'd
            # JSON - 1500 was observed (see CLAUDE.md investigation) to leave no room
            # for actual content, producing an empty message.content that then fails
            # json.loads() on every call. Raised to give reasoning headroom.
            max_tokens=6000,
            temperature=0.2,
            response_format={"type": "json_schema", "json_schema": SCENE_FACTS_SCHEMA},
        )
        parsed = json.loads(response)
    except Exception:
        # TEMP DEBUG (see CLAUDE.md investigation): this except used to swallow
        # everything silently, so a failed/malformed LLM call was indistinguishable
        # from "the model decided nothing was worth remembering". Print to stderr
        # (not logging - see llm_client.py's _list_models_via_get for why) so it
        # shows up no matter how uvicorn configures logging.
        print(
            f"[llm_extractor] extract_scene_facts LLM call/parse FAILED, "
            f"model_requested={model!r}, messages={len(messages)}:\n{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )
        return None

    facts = parsed.get("facts")
    if not isinstance(facts, list):
        print(
            f"[llm_extractor] extract_scene_facts got malformed response "
            f"(no 'facts' list): {parsed!r}",
            file=sys.stderr,
            flush=True,
        )
        return None

    results = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        indices = fact.get("source_message_indices") or []
        source_ids = [
            id_by_index[i] for i in indices if isinstance(i, int) and 0 <= i < len(id_by_index)
        ]
        results.append(
            {
                "content": fact.get("content", ""),
                "type": fact.get("type"),
                "layer": fact.get("layer"),
                "keywords": fact.get("keywords") or [],
                "entities": fact.get("entities") or [],
                "source_message_ids": source_ids,
            }
        )
    return results
