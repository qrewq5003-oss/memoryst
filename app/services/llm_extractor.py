import json
import sys
import traceback

from app.config import config
from app.schemas import ChatMessageItem
from app.services.llm_client import chat_completion, is_llm_enabled
from app.services.text_utils import clean_memory_text, dominant_language

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
            # Smaller budget, but still a whole scene in the prompt - same class of
            # call, same timeout.
            timeout=config.SCENE_LLM_TIMEOUT,
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
- Write each fact's content in {language}.
- Skip small talk, greetings, and questions that weren't answered in the scene.
- "type" must be one of: profile, relationship, event.
- "layer" must be "stable" for durable facts (who someone is, an ongoing
  relationship state) and "episodic" for one-off scenes/events.
- keywords: specific nouns, names, places, concepts. entities: character/person names.
- If nothing in the scene is worth remembering, return an empty "facts" list."""

# Extra rules appended to the Rules list when the caller knows who the participants are.
#
# Without them the model has only roles to go on and writes "Девушка положила телефон" /
# "Пользователь выразил радость", putting `девушка` and `пользователь` into `entities`
# as well. Those phrasings are permanent once stored, and a query never contains them,
# so the entity signal is spent on words that match nothing. Measured on a fresh chat:
# 64% of memories opened with a generic noun and the character's name appeared in
# entities zero times, against 0-10% generic in chats where the name had come up in
# dialogue early.
#
# The language rule is restated here on purpose. The first live run put these lines in a
# separate emphatic block after the Rules, and the model - reading English instructions
# last - wrote all eight facts of a Russian scene in English, entities included. Facts
# stored in the wrong language cannot match a query's keywords, which is worse than the
# role words this was meant to fix. Keeping the block inside the Rules list and ending on
# the language requirement is what stopped it.
SCENE_FACTS_NAMES_TEMPLATE = """
- The participants are: the character is {character_name}, the user is {user_name}.
  Name them in "content", "keywords" and "entities" instead of writing a role word
  ("девушка", "пользователь", "the girl", "the user"), even when the scene does not
  say the name aloud. Use the form of the name that fits the language of the fact.
- Repeating the first rule because it outranks the one above: each fact's "content"
  MUST be in {language}, whatever language these instructions are written in."""

# Always the last thing the model reads, names known or not.
#
# The restatement used to live only in the names block, which is appended only when the
# caller knows who the participants are - so a scene without them ended on "return an
# empty facts list" and the language rule was left sitting at the top of the list, five
# rules and a schema away from the generation. Measured over data/memory.db on
# 2026-09-21: 170 of 2603 memories whose source messages are still on disk were written
# in English from Russian scenes. A fact in the wrong language cannot match a query's
# keywords, so those 170 are stored and unreachable.
SCENE_FACTS_LANGUAGE_TEMPLATE = """
- LANGUAGE, last because it outranks everything above: every fact's "content",
  "keywords" and "entities" must be written in {language}. The scene mixes alphabets -
  names and quoted lines may be in another language - and that does not change the
  language you write in."""


def build_scene_facts_prompt(
    character_name: str | None = None,
    user_name: str | None = None,
    language: str = "the same language as the scene",
) -> str:
    """Scene extraction prompt, naming the participants and the output language.

    `language` is decided by the caller from the scene text rather than inferred by the
    model: these scenes mix alphabets, so "the same language as the input" is not a
    question with one answer. The default keeps the old wording for callers that have no
    scene to measure.
    """
    prompt = SCENE_FACTS_PROMPT.format(language=language)
    if character_name or user_name:
        prompt += SCENE_FACTS_NAMES_TEMPLATE.format(
            character_name=character_name or "unknown",
            user_name=user_name or "unknown",
            language=language,
        )
    return prompt + SCENE_FACTS_LANGUAGE_TEMPLATE.format(language=language)

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


ELISION_MARKER = " [...] "


def _fit_message_text(text: str, max_chars: int) -> str:
    """
    Shorten one message to `max_chars`, keeping its head and its tail.

    Head-only truncation is the wrong cut for a roleplay message: these open with
    scene-setting description and develop the dialogue, decisions and outcomes -
    the parts actually worth remembering - further down. Dropping the middle keeps
    both ends of that arc.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    budget = max_chars - len(ELISION_MARKER)
    if budget <= 0:
        return text[:max_chars]
    head = budget * 3 // 5
    tail = budget - head
    return f"{text[:head].rstrip()}{ELISION_MARKER}{text[-tail:].lstrip()}" if tail else text[:head]


def build_indexed_scene_text(
    messages: list[ChatMessageItem],
    max_chars: int | None = None,
    *,
    max_message_chars: int | None = None,
) -> tuple[str, list[str]]:
    """
    Format buffered/cooled chat messages into an indexed scene text for LLM analysis.

    Returns (scene_text, id_by_index) where id_by_index[i] is the chat_messages id
    of the message labeled "[i]" in scene_text - this lets the LLM reference messages
    by a small integer instead of repeating UUIDs, while still letting callers map
    "source_message_indices" back to real message ids afterwards.

    Each message is first shortened to `max_message_chars` on its own, then messages
    are added until the whole scene reaches `max_chars`. The per-message cap is what
    makes the scene budget survive a long message: this used to `break` on the first
    message that did not fit the total, which on a chat whose assistant messages run
    4000+ characters (41.3% of them do here) either returned an empty scene - silently
    degrading the whole batch to the rule-based extractor - or handed the LLM one
    message out of eight and called it a scene. See config.SCENE_TEXT_MAX_CHARS.

    At least one message is always included, however long it is, so this never
    returns an empty scene for a non-empty batch.
    """
    total_budget = config.SCENE_TEXT_MAX_CHARS if max_chars is None else max_chars
    message_budget = (
        config.SCENE_MESSAGE_MAX_CHARS if max_message_chars is None else max_message_chars
    )

    lines = []
    id_by_index: list[str] = []
    total = 0
    for message in messages:
        index = len(id_by_index)
        # The status header is scaffolding the model does not need, and feeding it in
        # put `Time` and 📍 place names into the entities it returned - 248 occurrences
        # of `Time` alone. Dropping it also buys back scene budget.
        text = clean_memory_text(message.text) or message.text
        line = f"[{index}][{message.role}]: {_fit_message_text(text, message_budget)}"
        if lines and total + len(line) > total_budget:
            break
        lines.append(line)
        id_by_index.append(message.id)
        total += len(line)
    return "\n\n".join(lines), id_by_index


def extract_scene_facts(
    messages: list[ChatMessageItem],
    *,
    model: str | None = None,
    character_name: str | None = None,
    user_name: str | None = None,
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
        # Was a bare `return None`, and it accounted for 536 of the 627 rule-based
        # fallbacks in data/server.log without leaving a single line behind - the
        # scene budget dropped every message and this looked exactly like "the LLM
        # decided nothing was memorable". build_indexed_scene_text now always keeps
        # at least one message, so reaching this means the batch cleaned down to
        # nothing; log it rather than let it go quiet again.
        print(
            f"[llm_extractor] extract_scene_facts got an EMPTY scene text from "
            f"{len(messages)} messages -> falling back to the rule-based extractor",
            file=sys.stderr,
            flush=True,
        )
        return None

    llm_messages = [
        {
            "role": "system",
            "content": build_scene_facts_prompt(
                character_name,
                user_name,
                language=dominant_language(scene_text),
            ),
        },
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
            # A 6000-token request was running on the 30s default meant for short
            # calls, which is what most of the ReadTimeouts behind the 26.4%
            # fallback rate were.
            timeout=config.SCENE_LLM_TIMEOUT,
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
