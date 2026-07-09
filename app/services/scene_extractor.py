import sys
from typing import Literal

from app.schemas import ChatMessageItem, CreateMemoryRequest, MemoryMetadata, MessageInput
from app.services import llm_extractor
from app.services.extractor import (
    extract_memories,
    get_importance_for_type,
    get_layer_for_type,
    has_regex_signal,
    is_meaningful_text,
)
from app.services.llm_client import is_llm_enabled
from app.services.text_utils import truncate_content

VALID_TYPES = ("profile", "relationship", "event")
VALID_LAYERS = ("episodic", "stable")

ExtractionMethod = Literal["llm", "regex_fallback"]


def _scene_has_signal(messages: list[ChatMessageItem]) -> bool:
    """Cheap regex pre-filter: is this batch worth paying for an LLM call at all?"""
    return any(is_meaningful_text(message.text) and has_regex_signal(message.text) for message in messages)


def _rule_based_fallback(
    chat_id: str, character_id: str, messages: list[ChatMessageItem]
) -> list[CreateMemoryRequest]:
    return extract_memories(
        chat_id=chat_id,
        character_id=character_id,
        messages=[MessageInput(role=message.role, text=message.text) for message in messages],
        mode="live",
    )


def extract_scene_memories(
    chat_id: str,
    character_id: str,
    messages: list[ChatMessageItem],
    model: str | None = None,
) -> tuple[list[CreateMemoryRequest], ExtractionMethod | None]:
    """
    Stage 3 entry point: extract memory candidates from a whole scene at once.

    Regex markers (extractor.has_regex_signal) are no longer the classifier - they're
    a cheap pre-filter deciding whether this batch is worth an LLM call at all. When
    nothing in the scene trips a marker, this returns [] without touching the LLM.
    Otherwise the LLM (structured output, see llm_extractor.extract_scene_facts)
    classifies and extracts facts over the whole scene, and each fact carries
    source_message_ids pointing back to the chat_messages (Stage 2) it came from.
    `model`, when given, overrides the active provider's default model for this
    LLM call only (see sillytavern-extension's "Scene Extraction Model" setting) -
    it lets extraction use a non-reasoning model independently of whatever model
    is active for consolidation/manual tools.

    Falls back to the rule-based line-by-line extractor (extract_memories, mode="live")
    when the LLM isn't configured or the call/parse fails, so live extraction keeps
    working without an LLM (e.g. in dev/test) - just without source_message_ids attached.

    Returns (candidates, extraction_method). extraction_method is "llm" only when the
    LLM call actually ran and parsed - including when it legitimately found nothing
    worth remembering (candidates == []). It's "regex_fallback" when the LLM was
    skipped/disabled or the call/parse failed and extract_memories ran instead - this
    also covers the pre-filter-rejected case (candidates == [] before either extractor
    would have found anything, since the pre-filter and extract_memories share the
    same marker set). It's None only when there were no messages to extract from at
    all - nothing ran. Surfaced on the /memory/store response (see StoreMemoryResponse)
    so "success" is no longer indistinguishable between the LLM path and the
    (intentionally cruder) fallback path - see CLAUDE.md's scene-extraction-llm-failing
    investigation for why this mattered.
    """
    if not messages:
        return [], None

    if not _scene_has_signal(messages):
        # TEMP DEBUG (see CLAUDE.md investigation): regex pre-filter rejected the
        # whole batch, so the LLM is never even called - this looks identical to
        # "LLM said nothing memorable" from the /memory/store response alone.
        print(
            f"[scene_extractor] chat={chat_id} char={character_id}: "
            f"{len(messages)} msgs, regex pre-filter found NO signal -> "
            f"skipping LLM call, 0 candidates",
            file=sys.stderr,
            flush=True,
        )
        return [], "regex_fallback"

    if not is_llm_enabled():
        candidates = _rule_based_fallback(chat_id, character_id, messages)
        print(
            f"[scene_extractor] chat={chat_id} char={character_id}: "
            f"LLM disabled, rule-based fallback -> {len(candidates)} candidates",
            file=sys.stderr,
            flush=True,
        )
        return candidates, "regex_fallback"

    facts = llm_extractor.extract_scene_facts(messages, model=model)
    if facts is None:
        candidates = _rule_based_fallback(chat_id, character_id, messages)
        print(
            f"[scene_extractor] chat={chat_id} char={character_id}: "
            f"LLM extraction failed (see llm_extractor error above), rule-based "
            f"fallback -> {len(candidates)} candidates",
            file=sys.stderr,
            flush=True,
        )
        return candidates, "regex_fallback"

    candidates: list[CreateMemoryRequest] = []
    for fact in facts:
        content = (fact.get("content") or "").strip()
        if not content or not is_meaningful_text(content):
            continue

        memory_type = fact.get("type")
        if memory_type not in VALID_TYPES:
            continue

        layer = fact.get("layer")
        if layer not in VALID_LAYERS:
            layer = get_layer_for_type(memory_type, content)

        candidates.append(
            CreateMemoryRequest(
                chat_id=chat_id,
                character_id=character_id,
                type=memory_type,
                content=truncate_content(content),
                source="auto",
                layer=layer,
                importance=get_importance_for_type(memory_type),
                pinned=False,
                archived=False,
                metadata=MemoryMetadata(
                    entities=fact.get("entities", []),
                    keywords=fact.get("keywords", []),
                    source_message_ids=fact.get("source_message_ids", []),
                ),
            )
        )

    print(
        f"[scene_extractor] chat={chat_id} char={character_id}: "
        f"LLM returned {len(facts)} facts -> {len(candidates)} valid candidates "
        f"after type/content filtering",
        file=sys.stderr,
        flush=True,
    )
    return candidates, "llm"
