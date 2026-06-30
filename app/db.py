import sqlite3
from pathlib import Path

from app.config import config

MEMORIES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        character_id TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('profile', 'relationship', 'event', 'summary')),
        content TEXT NOT NULL,
        normalized_content TEXT NOT NULL,

        source TEXT NOT NULL CHECK (source IN ('auto', 'manual')),
        layer TEXT NOT NULL CHECK (layer IN ('episodic', 'stable')),

        importance REAL NOT NULL DEFAULT 0.5,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_accessed_at TEXT,
        access_count INTEGER NOT NULL DEFAULT 0,

        pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
        archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),

        metadata_json TEXT NOT NULL
    )
"""

MEMORIES_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memories_chat_character ON memories (chat_id, character_id)",
    "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories (type)",
    "CREATE INDEX IF NOT EXISTS idx_memories_source ON memories (source)",
    "CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories (layer)",
    "CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories (archived)",
    "CREATE INDEX IF NOT EXISTS idx_memories_pinned ON memories (pinned)",
    "CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories (updated_at)",
)

# Raw chat message storage (Stage 2). Only messages that have "cooled" out of the
# hot buffer (see chat_buffer_service) land here, with a stable UUID assigned when
# they first entered the buffer - not a sequential index, since edits/swipes in the
# middle of history must not shift other messages' ids.
CHAT_MESSAGES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        character_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        sequence_index INTEGER NOT NULL
    )
"""

CHAT_MESSAGES_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_character "
    "ON chat_messages (chat_id, character_id, sequence_index)",
)

CHAT_MESSAGES_FTS_SQL = """
    CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
        text, content='chat_messages', content_rowid='rowid'
    )
"""

# Keep the external-content FTS index in sync with chat_messages. chat_messages is
# effectively insert-only (cooled rows aren't expected to change), but the
# update/delete triggers are included so the index can't silently drift if that
# ever changes (e.g. a future cleanup-on-chat-delete path).
CHAT_MESSAGES_FTS_TRIGGERS_SQL = (
    """
    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_ai AFTER INSERT ON chat_messages BEGIN
        INSERT INTO chat_messages_fts(rowid, text) VALUES (new.rowid, new.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_ad AFTER DELETE ON chat_messages BEGIN
        INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_au AFTER UPDATE ON chat_messages BEGIN
        INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
        INSERT INTO chat_messages_fts(rowid, text) VALUES (new.rowid, new.text);
    END
    """,
)


def get_connection() -> sqlite3.Connection:
    """Get database connection."""
    db_path = Path(config.DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _create_memories_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(MEMORIES_TABLE_SQL)


def _create_memories_indexes(cursor: sqlite3.Cursor) -> None:
    for statement in MEMORIES_INDEX_SQL:
        cursor.execute(statement)


def _create_chat_messages_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(CHAT_MESSAGES_TABLE_SQL)
    for statement in CHAT_MESSAGES_INDEX_SQL:
        cursor.execute(statement)
    cursor.execute(CHAT_MESSAGES_FTS_SQL)
    for statement in CHAT_MESSAGES_FTS_TRIGGERS_SQL:
        cursor.execute(statement)


def _needs_summary_migration(cursor: sqlite3.Cursor) -> bool:
    """Check whether the type CHECK constraint includes 'summary'."""
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
    )
    row = cursor.fetchone()
    if row is None:
        return False
    table_sql = row[0]
    return "'summary'" not in table_sql


def _run_summary_migration(conn: sqlite3.Connection) -> None:
    """Migrate the type CHECK constraint to include 'summary', wrapped in a savepoint."""
    cursor = conn.cursor()
    cursor.execute("SAVEPOINT migrate_summary")
    try:
        cursor.execute("ALTER TABLE memories RENAME TO memories_old")
        _create_memories_table(cursor)
        cursor.execute("""
            INSERT INTO memories (
                id, chat_id, character_id, type, content, normalized_content,
                source, layer, importance, created_at, updated_at,
                last_accessed_at, access_count, pinned, archived, metadata_json
            )
            SELECT
                id, chat_id, character_id, type, content, normalized_content,
                source, layer, importance, created_at, updated_at,
                last_accessed_at, access_count, pinned, archived, metadata_json
            FROM memories_old
        """)
        cursor.execute("DROP TABLE memories_old")
        _create_memories_indexes(cursor)
        cursor.execute("RELEASE migrate_summary")
    except Exception:
        cursor.execute("ROLLBACK TO migrate_summary")
        raise


def init_schema() -> None:
    """Initialize database schema with memories table and indexes."""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        _create_memories_table(cursor)
        _create_memories_indexes(cursor)
        _create_chat_messages_table(cursor)

        conn.commit()

        if _needs_summary_migration(cursor):
            _run_summary_migration(conn)
            conn.commit()
    finally:
        conn.close()
