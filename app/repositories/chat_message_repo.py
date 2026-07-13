from app.db import get_connection
from app.schemas import ChatMessageItem
from app.services.text_utils import normalize_for_similarity


def _row_to_chat_message(row: dict) -> ChatMessageItem:
    """Convert database row to ChatMessageItem."""
    return ChatMessageItem(
        id=row["id"],
        chat_id=row["chat_id"],
        character_id=row["character_id"],
        role=row["role"],
        text=row["text"],
        created_at=row["created_at"],
        sequence_index=row["sequence_index"],
    )


def insert_chat_message(message: ChatMessageItem) -> ChatMessageItem:
    """
    Insert a cooled chat message into the raw-history table.

    normalized_text is derived here rather than carried on ChatMessageItem: it is a
    storage-level dedup key, not part of the message as callers see it.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chat_messages (
                id, chat_id, character_id, role, text, created_at, sequence_index,
                normalized_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.chat_id,
                message.character_id,
                message.role,
                message.text,
                message.created_at,
                message.sequence_index,
                normalize_for_similarity(message.text),
            ),
        )
        conn.commit()
    return message


def find_recent_chat_message_by_normalized_text(
    chat_id: str,
    character_id: str,
    role: str,
    normalized_text: str,
    lookback: int = 50,
) -> ChatMessageItem | None:
    """
    Find an already-cooled message with the same normalized text, within the most
    recent `lookback` rows of this chat/character.

    Used to make message intake idempotent: the extension resends a window of the
    last N messages on every turn, so the same text arrives many times. The window
    is bounded because a genuine repeat far back in history ("да", "хорошо") is a
    distinct message, not a resend - only a recent match means "we already have it".

    Returns the earliest match in the window, so repeated intake keeps resolving to
    the same row (and the same id) rather than drifting forward.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM (
                SELECT * FROM chat_messages
                WHERE chat_id = ? AND character_id = ?
                ORDER BY sequence_index DESC
                LIMIT ?
            )
            WHERE role = ? AND normalized_text = ?
            ORDER BY sequence_index ASC
            LIMIT 1
            """,
            (chat_id, character_id, lookback, role, normalized_text),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_chat_message(dict(row))


def get_chat_message_by_id(message_id: str) -> ChatMessageItem | None:
    """Get a raw chat message by its (stable, buffer-assigned) id."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_chat_message(dict(row))


def list_chat_messages(
    chat_id: str,
    character_id: str,
    limit: int = 200,
    offset: int = 0,
) -> list[ChatMessageItem]:
    """List cooled raw messages for a chat/character, oldest first."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM chat_messages
            WHERE chat_id = ? AND character_id = ?
            ORDER BY sequence_index ASC
            LIMIT ? OFFSET ?
            """,
            (chat_id, character_id, limit, offset),
        )
        rows = cursor.fetchall()
        return [_row_to_chat_message(dict(row)) for row in rows]


def get_max_sequence_index(chat_id: str, character_id: str) -> int:
    """
    Highest sequence_index already cooled into chat_messages for this chat/character.

    Returns -1 when nothing has cooled yet, so callers can do max_index + 1 to
    get the next sequence number uniformly.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MAX(sequence_index) FROM chat_messages WHERE chat_id = ? AND character_id = ?",
            (chat_id, character_id),
        )
        max_index = cursor.fetchone()[0]
        return -1 if max_index is None else int(max_index)


def search_chat_messages_fts(
    chat_id: str,
    character_id: str,
    query: str,
    limit: int = 20,
) -> list[ChatMessageItem]:
    """Full-text search over cooled raw messages, scoped to one chat/character."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT cm.* FROM chat_messages cm
            JOIN chat_messages_fts fts ON cm.rowid = fts.rowid
            WHERE chat_messages_fts MATCH ?
              AND cm.chat_id = ? AND cm.character_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, chat_id, character_id, limit),
        )
        rows = cursor.fetchall()
        return [_row_to_chat_message(dict(row)) for row in rows]
