import uuid
from typing import Literal

from app.repositories.chat_message_repo import get_max_sequence_index, insert_chat_message
from app.schemas import ChatMessageItem, MessageInput
from app.services.text_utils import get_utc_now, is_ooc_text

HOT_BUFFER_SIZE = 4

# Per (chat_id, character_id) sliding window of the most recent chat-only messages,
# kept out of chat_messages so they can be swiped/edited without leaving stale rows
# or breaking memory->raw links. Module-level, in-memory, not persisted: a process
# restart drops whatever hasn't cooled yet (acceptable - see plan Stage 2.3).
_buffers: dict[tuple[str, str], list[ChatMessageItem]] = {}
_next_sequence: dict[tuple[str, str], int] = {}


def _buffer_key(chat_id: str, character_id: str) -> tuple[str, str]:
    return (chat_id, character_id)


def _next_sequence_index(key: tuple[str, str]) -> int:
    if key not in _next_sequence:
        chat_id, character_id = key
        _next_sequence[key] = get_max_sequence_index(chat_id, character_id) + 1
    value = _next_sequence[key]
    _next_sequence[key] += 1
    return value


def is_filtered_input(role: str, text: str) -> bool:
    """OOC and system messages are filtered on input - they never reach the buffer."""
    if role == "system":
        return True
    return is_ooc_text(text)


def add_message(
    chat_id: str,
    character_id: str,
    role: Literal["user", "assistant", "system"],
    text: str,
) -> ChatMessageItem | None:
    """
    Add one chat message to the hot buffer for (chat_id, character_id).

    System and OOC messages are filtered on input and return None without ever
    getting an id. Otherwise the message gets a stable UUID immediately (not a
    sequential index - see Stage 2 plan) and joins the buffer. Once the buffer
    holds more than HOT_BUFFER_SIZE messages, the oldest one cools into the
    chat_messages table and is removed from the buffer.

    Returns the buffered ChatMessageItem, or None if it was filtered.
    """
    if is_filtered_input(role, text):
        return None

    key = _buffer_key(chat_id, character_id)
    buffer = _buffers.setdefault(key, [])

    message = ChatMessageItem(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        character_id=character_id,
        role=role,
        text=text,
        created_at=get_utc_now(),
        sequence_index=_next_sequence_index(key),
    )
    buffer.append(message)

    while len(buffer) > HOT_BUFFER_SIZE:
        oldest = buffer.pop(0)
        insert_chat_message(oldest)

    return message


def add_messages(
    chat_id: str,
    character_id: str,
    messages: list[MessageInput],
) -> list[ChatMessageItem]:
    """Add several messages in order. Returns only the ones that weren't filtered."""
    added = []
    for msg in messages:
        result = add_message(chat_id, character_id, msg.role, msg.text)
        if result is not None:
            added.append(result)
    return added


def get_hot_buffer(chat_id: str, character_id: str) -> list[ChatMessageItem]:
    """Read-only view of the current hot buffer, oldest first."""
    return list(_buffers.get(_buffer_key(chat_id, character_id), []))


def reset_all_buffers() -> None:
    """Clear all in-memory buffer state. Intended for test isolation."""
    _buffers.clear()
    _next_sequence.clear()
