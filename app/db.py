import sqlite3
from pathlib import Path

from app.config import config


def get_connection() -> sqlite3.Connection:
    """Get database connection."""
    db_path = Path(config.DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    """Initialize database schema with memories table and indexes."""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Create memories table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('profile', 'relationship', 'event')),
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
        """)

        # Create indexes
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_chat_character ON memories (chat_id, character_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories (type)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_source ON memories (source)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories (layer)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories (archived)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_pinned ON memories (pinned)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories (created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories (updated_at)"
        )

        conn.commit()
    finally:
        conn.close()
