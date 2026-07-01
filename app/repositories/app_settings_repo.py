from app.db import get_connection


def get_setting(key: str) -> str | None:
    """Read a single key-value app setting, or None if it's never been set."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row is not None else None


def set_setting(key: str, value: str) -> None:
    """Upsert a single key-value app setting."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()
