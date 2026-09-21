import sqlite3
from pathlib import Path

from app.config import config
from app.services.text_utils import normalize_for_similarity

MEMORIES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        character_id TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('profile', 'relationship', 'event', 'summary', 'tracker')),
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
    # A tracker is a single document per (chat, character, tracker_type) that gets
    # rewritten in place, not an accumulating log. Enforced by the database rather
    # than by upsert_tracker alone, so a second writer can't fork a tracker in two.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_tracker_unique "
    "ON memories (chat_id, character_id, json_extract(metadata_json, '$.tracker_type')) "
    "WHERE type = 'tracker'",
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
        sequence_index INTEGER NOT NULL,
        normalized_text TEXT NOT NULL DEFAULT ''
    )
"""

CHAT_MESSAGES_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_character "
    "ON chat_messages (chat_id, character_id, sequence_index)",
)

# idx_chat_messages_normalized (chat_id, character_id, normalized_text) used to be
# created here and is now dropped instead. It covered a query shape nothing issues:
# the dedup lookup in find_recent_chat_message_by_normalized_text narrows to the last
# `lookback` rows by sequence_index first and only then compares role and
# normalized_text, so EXPLAIN QUERY PLAN shows it using idx_chat_messages_chat_character
# and scanning the 50-row subquery. scripts/dedupe_chat_messages.py reads the whole
# table and groups in Python, so it doesn't use it either.
#
# The cost was not theoretical: normalized_text averages 2.6KB per row, so the index
# held a second full copy of every message - 21.8MB of a 77MB database, 28% of the
# file, for nothing. The column stays; only the index goes.
#
# Dropping an index never loses data, so no backup gate is needed beyond the startup
# snapshot. Space returns to the freelist immediately and to the filesystem after
# scripts/vacuum_db.py.
CHAT_MESSAGES_DROP_UNUSED_INDEX_SQL = "DROP INDEX IF EXISTS idx_chat_messages_normalized"

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


# Small persistent key-value store for process-spanning app settings (e.g. the
# active LLM provider) that shouldn't live in .env because they're changed at
# runtime, not at deploy time.
# Embeddings live beside the memories rather than in a JSON file. The file backend this
# replaced rewrote itself on every insert, so backfilling this database would have written
# hundreds of gigabytes to flash to produce a file too large to parse on the query path.
MEMORY_EMBEDDINGS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS memory_embeddings (
        memory_id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        character_id TEXT NOT NULL,
        dimensions INTEGER NOT NULL,
        model TEXT NOT NULL,
        vector BLOB NOT NULL
    )
"""

# A retrieve only ever compares within one (chat, character), so that is the only access
# path this needs - without it every query would deserialize every vector in the database.
MEMORY_EMBEDDINGS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memory_embeddings_scope "
    "ON memory_embeddings (chat_id, character_id)",
)

APP_SETTINGS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
"""


class _ClosingConnection(sqlite3.Connection):
    """A connection that closes itself when its `with` block ends.

    `sqlite3.Connection.__exit__` commits or rolls back and then leaves the connection
    open - it is a transaction context manager, not a resource one. Every
    `with get_connection() as conn:` in this codebase therefore relied on refcounting to
    close the handle, and the handles are held in reference cycles, so only the cyclic
    collector freed them.

    Measured 2026-09-21 on the running server: 40 requests to /memory/list left 40 new
    descriptors and 47 open handles on memory.db, still there after 20 seconds idle.
    Reproduced outside the web layer, where an explicit gc.collect() dropped 34 back to
    4 - so it was a delay, not an unbounded leak, but a delay that tracked request rate
    against a 32768 limit, and whose eventual failure would read as "the service stopped
    responding" with nothing pointing here.

    Subclassing rather than handing out a @contextmanager keeps both call styles working:
    the 26 `with get_connection()` sites now close, and the 14 that keep a connection in
    a variable and close it in a `finally` are untouched, since `close()` twice is a
    no-op.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_connection() -> sqlite3.Connection:
    """Get database connection."""
    db_path = Path(config.DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    # WAL so a reader cannot block a writer. In the default `delete` mode it can, and
    # did: measured 2026-09-21 on the live 79 MB database, a read transaction held past
    # busy_timeout made a concurrent write fail with "database is locked". Real reads are
    # fast (0.08 s for a chat, 0.18 s for every vector), so the window was narrow - but
    # the file is shared by the retrieve that blocks generation, the background summary
    # scheduler, the web UI and the CLI scripts, and it grew from 57 MB to 79 MB in a day.
    #
    # Set per connection rather than once at init: the mode is persistent in the file, so
    # this is a 6 microsecond no-op afterwards, and it also covers a database that never
    # went through init_schema - a restored backup, say.
    #
    # busy_timeout is deliberately left at Python's 5 s default. The measured fault was
    # the journal mode; changing both at once would leave neither attributable.
    #
    # Safe for the backup path: create_backup uses SQLite's online backup API, not a file
    # copy, and that is WAL-aware. Nothing else copies the database file - the Downloads
    # mirror excludes data/ entirely.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _create_memories_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(MEMORIES_TABLE_SQL)


def _create_memories_indexes(cursor: sqlite3.Cursor) -> None:
    for statement in MEMORIES_INDEX_SQL:
        cursor.execute(statement)


def _create_app_settings_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(APP_SETTINGS_TABLE_SQL)


def _create_memory_embeddings_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(MEMORY_EMBEDDINGS_TABLE_SQL)
    for statement in MEMORY_EMBEDDINGS_INDEX_SQL:
        cursor.execute(statement)


def _create_chat_messages_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(CHAT_MESSAGES_TABLE_SQL)
    for statement in CHAT_MESSAGES_INDEX_SQL:
        cursor.execute(statement)
    cursor.execute(CHAT_MESSAGES_FTS_SQL)
    for statement in CHAT_MESSAGES_FTS_TRIGGERS_SQL:
        cursor.execute(statement)


def _drop_unused_chat_messages_index(cursor: sqlite3.Cursor) -> None:
    """Remove idx_chat_messages_normalized, which no query in the codebase uses.

    Idempotent by construction (DROP INDEX IF EXISTS): a no-op on a database that
    never had it, and on every start after the first.
    """
    cursor.execute(CHAT_MESSAGES_DROP_UNUSED_INDEX_SQL)


def _needs_chat_messages_normalized_text_migration(cursor: sqlite3.Cursor) -> bool:
    """Check whether chat_messages still lacks the normalized_text dedup column."""
    cursor.execute("PRAGMA table_info(chat_messages)")
    columns = {row[1] for row in cursor.fetchall()}
    if not columns:
        return False
    return "normalized_text" not in columns


def _run_chat_messages_normalized_text_migration(conn: sqlite3.Connection) -> None:
    """
    Add normalized_text to chat_messages and backfill it, wrapped in a savepoint.

    ALTER TABLE ADD COLUMN rather than the rename/create/copy/drop dance used by
    _run_summary_migration: chat_messages_fts is an external-content FTS5 index
    keyed on chat_messages.rowid, and rebuilding the table would both scramble
    those rowids and re-fire the insert trigger, doubling every row in the index.
    Adding a column leaves rowids alone.

    The FTS sync triggers come off for the duration. Left in place they fire once per
    backfilled row for a text that hasn't changed - pointless churn, and on a database
    whose index had already drifted out of sync the delete half of the update trigger
    aborts the whole migration with "database disk image is malformed". Rebuilding the
    index at the end is both cheaper and self-healing.
    """
    cursor = conn.cursor()
    cursor.execute("SAVEPOINT migrate_chat_messages_normalized_text")
    try:
        for trigger in ("chat_messages_fts_ai", "chat_messages_fts_ad", "chat_messages_fts_au"):
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")

        cursor.execute(
            "ALTER TABLE chat_messages ADD COLUMN normalized_text TEXT NOT NULL DEFAULT ''"
        )
        cursor.execute("SELECT id, text FROM chat_messages")
        rows = cursor.fetchall()
        cursor.executemany(
            "UPDATE chat_messages SET normalized_text = ? WHERE id = ?",
            [(normalize_for_similarity(row[1]), row[0]) for row in rows],
        )

        for statement in CHAT_MESSAGES_FTS_TRIGGERS_SQL:
            cursor.execute(statement)
        cursor.execute("INSERT INTO chat_messages_fts(chat_messages_fts) VALUES ('rebuild')")

        cursor.execute("RELEASE migrate_chat_messages_normalized_text")
    except Exception:
        cursor.execute("ROLLBACK TO migrate_chat_messages_normalized_text")
        raise


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


def _needs_tracker_migration(cursor: sqlite3.Cursor) -> bool:
    """Check whether the type CHECK constraint includes 'tracker'."""
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
    )
    row = cursor.fetchone()
    if row is None:
        return False
    table_sql = row[0]
    return "'tracker'" not in table_sql


def _run_tracker_migration(conn: sqlite3.Connection) -> None:
    """Migrate the type CHECK constraint to include 'tracker', wrapped in a savepoint."""
    cursor = conn.cursor()
    cursor.execute("SAVEPOINT migrate_tracker")
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
        cursor.execute("RELEASE migrate_tracker")
    except Exception:
        cursor.execute("ROLLBACK TO migrate_tracker")
        raise


def init_schema() -> None:
    """Initialize database schema with memories table and indexes."""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        _create_memories_table(cursor)
        _create_memories_indexes(cursor)
        _create_chat_messages_table(cursor)
        _create_app_settings_table(cursor)
        _create_memory_embeddings_table(cursor)

        conn.commit()

        if _needs_summary_migration(cursor):
            _run_summary_migration(conn)
            conn.commit()

        # No-op on a database the summary migration just rebuilt: it recreates the
        # table from MEMORIES_TABLE_SQL, which already carries 'tracker'.
        if _needs_tracker_migration(cursor):
            _run_tracker_migration(conn)
            conn.commit()

        if _needs_chat_messages_normalized_text_migration(cursor):
            _run_chat_messages_normalized_text_migration(conn)
            conn.commit()

        _drop_unused_chat_messages_index(cursor)
        conn.commit()
    finally:
        conn.close()
