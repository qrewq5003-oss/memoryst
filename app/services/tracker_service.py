"""
Tracker updates: watermark -> chunked LLM calls -> deterministic render -> upsert.

A tracker is a document that gets *rewritten*, not appended to. Each update feeds the
current document plus the chat messages newer than the tracker's own watermark to the
LLM, and the reply replaces the document wholesale. The watermark advances after every
successfully processed chunk, so a long first run over a months-old chat that dies
halfway resumes where it stopped instead of starting over.
"""

import json
import sys
import traceback

from app.config import config
from app.repositories.chat_message_repo import (
    get_max_sequence_index,
    list_chat_messages_after,
)
from app.repositories.memory_repo import get_tracker, list_trackers, upsert_tracker
from app.schemas import (
    ChatMessageItem,
    MemoryMetadata,
    TrackerCounter,
    TrackerItem,
    TrackerUpdateResponse,
)
from app.services import chat_buffer_service
from app.services.llm_client import chat_completion, is_llm_enabled
from app.services.text_utils import get_utc_now
from app.services.tracker_prompts import (
    SCHEMAS,
    TRACKER_TYPES,
    build_prompt,
    find_dates_in_summary,
    normalize_payload,
    render_tracker,
    sort_timeline_entries,
)

# Chunk sizing mirrors build_indexed_scene_text: a window the model can actually hold in
# working memory, not a whole chat history.
MAX_MESSAGES_PER_CHUNK = 40
MAX_CHARS_PER_CHUNK = 4000

# How much history a single update run will walk. A months-old chat is processed over
# several runs rather than one enormous request.
MAX_MESSAGES_PER_RUN = 400

# metadata_json is read on every memory row load, so the document-level provenance list
# is capped rather than growing with the chat forever. Per-entry provenance on timeline
# entries is the precise record; this is the coarse one.
MAX_SOURCE_IDS = 500


def _pending_messages(
    chat_id: str, character_id: str, watermark: int
) -> list[ChatMessageItem]:
    """
    Messages newer than the watermark: cooled rows first, then whatever is still hot.

    The hot buffer is included deliberately. Reading only chat_messages would leave a
    tracker permanently four messages behind the conversation, which is most visible on
    exactly the reply the user just sent. Sequence indices are assigned when a message
    enters the buffer, not when it cools, so a message consumed while hot and cooled
    afterwards lands below the watermark and is never folded in twice.
    """
    cooled = list_chat_messages_after(
        chat_id, character_id, watermark, limit=MAX_MESSAGES_PER_RUN
    )
    seen = {message.id for message in cooled}

    hot = [
        message
        for message in chat_buffer_service.get_hot_buffer(chat_id, character_id)
        if message.sequence_index > watermark and message.id not in seen
    ]

    pending = cooled + hot
    pending.sort(key=lambda message: message.sequence_index)
    return pending[:MAX_MESSAGES_PER_RUN]


def _current_max_sequence_index(chat_id: str, character_id: str) -> int:
    """Newest sequence index in the chat, hot or cooled. -1 when the chat is empty."""
    cooled_max = get_max_sequence_index(chat_id, character_id)
    hot = chat_buffer_service.get_hot_buffer(chat_id, character_id)
    hot_max = max((message.sequence_index for message in hot), default=-1)
    return max(cooled_max, hot_max)


def _watermark_of(tracker) -> int:
    if tracker is None:
        return -1
    value = tracker.metadata.tracker_last_sequence_index
    return -1 if value is None else value


def _chunk(messages: list[ChatMessageItem]) -> list[list[ChatMessageItem]]:
    """Split into windows of at most MAX_MESSAGES_PER_CHUNK / MAX_CHARS_PER_CHUNK."""
    chunks: list[list[ChatMessageItem]] = []
    current: list[ChatMessageItem] = []
    chars = 0

    for message in messages:
        length = len(message.text)
        too_many = len(current) >= MAX_MESSAGES_PER_CHUNK
        too_long = current and chars + length > MAX_CHARS_PER_CHUNK
        if too_many or too_long:
            chunks.append(current)
            current = []
            chars = 0
        current.append(message)
        chars += length

    if current:
        chunks.append(current)
    return chunks


def _build_window_text(messages: list[ChatMessageItem]) -> str:
    return "\n\n".join(
        f"[{index}][{message.role}]: {message.text}" for index, message in enumerate(messages)
    )


def _entry_identity(entry: dict) -> tuple:
    """Key for carrying per-entry provenance across chunks when an entry is unchanged."""
    return (
        (entry.get("date") or "").strip().lower(),
        (entry.get("time") or "").strip().lower(),
        (entry.get("summary") or "").strip().lower(),
    )


def _attach_provenance(
    tracker_type: str,
    entries: list[dict],
    previous_entries: list[dict],
    window: list[ChatMessageItem],
) -> list[dict]:
    """
    Resolve the model's window-local message indices into real message ids.

    Entries the model carried over unchanged from the previous document come back with
    an empty index list (the prompt asks for exactly that), so their provenance is
    inherited from the matching previous entry instead of being silently dropped.
    """
    if tracker_type != "timeline":
        return entries

    inherited = {_entry_identity(entry): entry for entry in previous_entries}
    resolved: list[dict] = []

    for entry in entries:
        indices = entry.get("source_message_indices") or []
        ids = [window[i].id for i in indices if isinstance(i, int) and 0 <= i < len(window)]
        sequences = [
            window[i].sequence_index
            for i in indices
            if isinstance(i, int) and 0 <= i < len(window)
        ]

        if not ids:
            previous = inherited.get(_entry_identity(entry))
            if previous is not None:
                ids = list(previous.get("source_message_ids") or [])
                sequences = list(previous.get("source_sequence_indices") or [])

        cleaned = {k: v for k, v in entry.items() if k != "source_message_indices"}
        cleaned["source_message_ids"] = ids
        cleaned["source_sequence_indices"] = sequences
        resolved.append(cleaned)

    return resolved


def _warn_on_fused_days(chat_id: str, character_id: str, entries: list[dict]) -> None:
    for entry in entries:
        dates = find_dates_in_summary(entry.get("summary") or "")
        if len(dates) > 1:
            print(
                f"[tracker_service] chat={chat_id} char={character_id}: timeline entry "
                f"mentions {len(dates)} distinct dates in one summary - the model may have "
                f"fused separate days: {entry.get('summary')!r}",
                file=sys.stderr,
                flush=True,
            )


def _call_llm(
    tracker_type: str,
    current_entries: list[dict],
    window: list[ChatMessageItem],
    model: str | None,
    character_name: str | None = None,
    user_name: str | None = None,
) -> dict | None:
    """
    One tracker update call, retried on transient failure. None when it truly failed.

    Retried because the failures seen against real providers were not the model
    rejecting the schema - they were ReadTimeouts and empty completions that succeeded
    on a second identical call. An un-retried blip costs the user a manual re-click and,
    worse, looks exactly like "this model can't do trackers".
    """
    document = json.dumps(current_entries, ensure_ascii=False, indent=2)
    user_content = (
        f"Текущий документ трекера (JSON):\n{document}\n\n"
        f"Новые сообщения чата:\n\n{_build_window_text(window)}"
    )

    for attempt in range(config.TRACKER_LLM_RETRIES + 1):
        try:
            response = chat_completion(
                [
                    {
                        "role": "system",
                        "content": build_prompt(tracker_type, character_name, user_name),
                    },
                    {"role": "user", "content": user_content},
                ],
                model=model,
                # A reasoning model spends part of this budget on hidden reasoning before
                # the schema'd JSON appears. At 6000 the largest of the four schemas
                # (relationship) came back with an empty completion - the same failure
                # that silently broke scene extraction (see CLAUDE.md).
                max_tokens=config.TRACKER_LLM_MAX_TOKENS,
                temperature=0.2,
                response_format={"type": "json_schema", "json_schema": SCHEMAS[tracker_type]},
                timeout=config.TRACKER_LLM_TIMEOUT,
            )
            if not (response or "").strip():
                raise ValueError("empty completion (model spent the budget on reasoning)")
            return json.loads(response)
        except Exception:
            last = attempt == config.TRACKER_LLM_RETRIES
            print(
                f"[tracker_service] tracker={tracker_type} LLM call/parse FAILED "
                f"(attempt {attempt + 1}/{config.TRACKER_LLM_RETRIES + 1}"
                f"{', giving up' if last else ', retrying'}), model_requested={model!r}, "
                f"window={len(window)} msgs:\n{traceback.format_exc()}",
                file=sys.stderr,
                flush=True,
            )
            if last:
                return None

    return None


def update_tracker(
    chat_id: str,
    character_id: str,
    tracker_type: str,
    model: str | None = None,
    full_rebuild: bool = False,
    character_name: str | None = None,
    user_name: str | None = None,
) -> TrackerUpdateResponse:
    """Fold every chat message newer than this tracker's watermark into its document."""
    if tracker_type not in TRACKER_TYPES:
        raise ValueError(f"unknown tracker_type: {tracker_type}")

    existing = get_tracker(chat_id, character_id, tracker_type)
    watermark = -1 if full_rebuild else _watermark_of(existing)

    pending = _pending_messages(chat_id, character_id, watermark)
    if not pending:
        return TrackerUpdateResponse(
            action="skipped_no_new_messages",
            tracker_type=tracker_type,
            content=existing.content if existing else "",
            entries_count=len(existing.metadata.tracker_entries or []) if existing else 0,
        )

    if not is_llm_enabled():
        # No rule-based fallback on purpose. A regex cannot produce a chronology, and a
        # half-built tracker is worse than an absent one - the extension would inject it
        # into the prompt as though it were authoritative.
        return TrackerUpdateResponse(
            action="skipped_llm_unavailable",
            tracker_type=tracker_type,
            content=existing.content if existing else "",
            entries_count=len(existing.metadata.tracker_entries or []) if existing else 0,
        )

    # full_rebuild throws the old document away rather than feeding it back in - that is
    # the whole point of a rebuild (e.g. after a prompt fix, or a document that drifted).
    entries: list[dict] = (
        [] if full_rebuild or existing is None else list(existing.metadata.tracker_entries or [])
    )
    source_ids: list[str] = (
        [] if full_rebuild or existing is None else list(existing.metadata.source_message_ids or [])
    )

    created = existing is None
    consumed = 0
    committed_any = False

    for window in _chunk(pending):
        payload = _call_llm(
            tracker_type, entries, window, model, character_name, user_name
        )
        if payload is None:
            break

        previous_entries = entries
        entries = normalize_payload(
            tracker_type, payload, exclude_names=[character_name, user_name]
        )
        entries = _attach_provenance(tracker_type, entries, previous_entries, window)

        if tracker_type == "timeline":
            _warn_on_fused_days(chat_id, character_id, entries)
            entries = sort_timeline_entries(entries)

        for message in window:
            if message.id not in source_ids:
                source_ids.append(message.id)
        source_ids = source_ids[-MAX_SOURCE_IDS:]

        content = render_tracker(tracker_type, entries)
        # Committed per chunk, so an interrupted run resumes from the last good window
        # instead of replaying the whole chat.
        _, was_created = upsert_tracker(
            chat_id=chat_id,
            character_id=character_id,
            tracker_type=tracker_type,
            content=content,
            metadata=MemoryMetadata(
                tracker_generated_at=get_utc_now(),
                tracker_last_sequence_index=window[-1].sequence_index,
                tracker_entries=entries,
                source_message_ids=source_ids,
            ),
        )
        created = created and was_created
        consumed += len(window)
        committed_any = True

    if not committed_any:
        return TrackerUpdateResponse(
            action="skipped_llm_failed",
            tracker_type=tracker_type,
            content=existing.content if existing else "",
            entries_count=len(existing.metadata.tracker_entries or []) if existing else 0,
        )

    tracker = get_tracker(chat_id, character_id, tracker_type)
    print(
        f"[tracker_service] chat={chat_id} char={character_id} tracker={tracker_type}: "
        f"consumed={consumed} msgs -> {len(entries)} entries, watermark={_watermark_of(tracker)}",
        file=sys.stderr,
        flush=True,
    )

    return TrackerUpdateResponse(
        action="created" if created else "updated",
        tracker_type=tracker_type,
        content=tracker.content if tracker else "",
        entries_count=len(entries),
        messages_consumed=consumed,
        extraction_method="llm",
    )


def list_tracker_items(chat_id: str, character_id: str) -> list[TrackerItem]:
    """Every tracker of this chat/character, with its own staleness counter."""
    current_max = _current_max_sequence_index(chat_id, character_id)
    items: list[TrackerItem] = []

    for tracker in list_trackers(chat_id, character_id):
        tracker_type = tracker.metadata.tracker_type
        if tracker_type not in TRACKER_TYPES:
            continue
        watermark = _watermark_of(tracker)
        items.append(
            TrackerItem(
                tracker_type=tracker_type,
                memory_id=tracker.id,
                content=tracker.content,
                entries=tracker.metadata.tracker_entries or [],
                updated_at=tracker.updated_at,
                last_sequence_index=tracker.metadata.tracker_last_sequence_index,
                # Clamped: a tracker that consumed still-hot messages can hold a
                # watermark above anything that has cooled into chat_messages yet.
                messages_since_update=max(0, current_max - watermark),
            )
        )
    return items


def list_tracker_counters(chat_id: str, character_id: str) -> list[TrackerCounter]:
    """Staleness counters only - what /memory/store piggybacks on its response."""
    return [
        TrackerCounter(
            tracker_type=item.tracker_type,
            messages_since_update=item.messages_since_update,
        )
        for item in list_tracker_items(chat_id, character_id)
    ]
