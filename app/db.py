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


def init_schema() -> None:
    """Initialize database schema with memories table and indexes."""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        _create_memories_table(cursor)
        _create_memories_indexes(cursor)

        conn.commit()

        cursor.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'memories'
            """
        )
        row = cursor.fetchone()
        table_sql = row[0] if row is not None else ""
        if "'summary'" not in table_sql:
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
            conn.commit()
    finally:
        conn.close()
