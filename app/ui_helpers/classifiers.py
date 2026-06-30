from datetime import datetime, timezone
from typing import Any

from app.schemas import MemoryItem


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value: str | None) -> int | None:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    return max((utc_now() - parsed).days, 0)


def get_freshness_bucket(memory: MemoryItem) -> str:
    updated_days = days_since(memory.updated_at)
    if updated_days is None or updated_days <= 7:
        return "fresh"
    if updated_days <= 30:
        return "warm"
    return "stale"


def get_activity_bucket(memory: MemoryItem) -> str:
    accessed_days = days_since(memory.last_accessed_at)
    if memory.access_count <= 0 or memory.last_accessed_at is None:
        return "never_used"
    if memory.access_count >= 5 or (accessed_days is not None and accessed_days <= 14):
        return "active"
    return "low_use"


def get_touch_state(memory: MemoryItem) -> str:
    updated_days = days_since(memory.updated_at)
    accessed_days = days_since(memory.last_accessed_at)
    if accessed_days is not None and accessed_days <= 14:
        return "recently_accessed"
    if updated_days is not None and updated_days <= 14:
        return "recently_updated"
    if memory.access_count <= 0 and updated_days is not None and updated_days > 30:
        return "stale_unused"
    return "quiet"


def build_memory_card(memory: MemoryItem) -> dict[str, Any]:
    updated_days = days_since(memory.updated_at)
    accessed_days = days_since(memory.last_accessed_at)
    return {
        **memory.model_dump(),
        "freshness": get_freshness_bucket(memory),
        "activity": get_activity_bucket(memory),
        "touch_state": get_touch_state(memory),
        "updated_days": updated_days,
        "accessed_days": accessed_days,
    }
