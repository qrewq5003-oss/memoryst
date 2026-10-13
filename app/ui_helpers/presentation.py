import re
from typing import Any
from urllib.parse import parse_qsl, urlencode

from app.schemas import RetrieveMemoryResponse, StoreMemoryResponse
from app.ui_helpers.classifiers import days_since


def parse_list(value: str) -> list[str]:
    """Parse comma-separated string into list."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_redirect_query(value: Any) -> str:
    return value if isinstance(value, str) else ""


def build_query_string(params: dict[str, Any]) -> str:
    """Build query string from params, excluding empty values."""
    return urlencode({k: v for k, v in params.items() if v not in (None, "")})


def normalize_scope_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def shorten_display_text(value: str, max_length: int = 36) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3].rstrip()}..."


def build_friendly_scope_label(value: str) -> str:
    compact = " ".join(value.split()).strip()
    if not compact:
        return "Unnamed chat"

    friendly = re.sub(r"[-_/]+", " ", compact)
    friendly = " ".join(friendly.split())
    if friendly != compact and not any(char.isupper() for char in friendly):
        friendly = friendly.title()

    if len(friendly) > 36:
        return shorten_display_text(friendly)
    if friendly != compact:
        return friendly
    return shorten_display_text(compact)


# SillyTavern names a chat file "<character name> - <timestamp>", so the name is in the
# chat id even when nothing else holds it. The alternative, the avatar file, is a
# worse label ("Alina1.png" - ST appends a digit when two cards share a name), and the
# ids from before 2026-09-20 are not labels at all: they are the card's position in
# the array that day.
_ST_CHAT_ID_RE = re.compile(r"^(?P<name>.+?) - \d{4}-\d{2}-\d{2}@\d{2}h\d{2}m\d{2}s\d{3}ms$")
_AVATAR_SUFFIX_RE = re.compile(r"\.(png|webp|jpe?g|gif)$", re.IGNORECASE)


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_ST_CHAT_ID_FULL_RE = re.compile(
    r"^(?P<name>.+?) - (?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})@(?P<hh>\d{2})h(?P<mm>\d{2})m\d{2}s\d{3}ms$"
)


def build_chat_title(chat_id: str) -> str:
    """
    The sidebar title for a chat: "Apex · 12 Oct 2026, 17:45" for a SillyTavern id.

    The generic label used to strip the hyphens out of the id - "Apex 2026 10 12@17h45m11s969ms" -
    which made the date harder to read than the raw id and kept the milliseconds that
    distinguish nothing a person would look at. Anything that is not an ST id keeps the
    generic treatment.
    """
    compact = " ".join((chat_id or "").split())
    match = _ST_CHAT_ID_FULL_RE.match(compact)
    if not match:
        return build_friendly_scope_label(chat_id)
    month = int(match.group("m"))
    month_label = _MONTHS[month - 1] if 1 <= month <= 12 else match.group("m")
    return (
        f"{match.group('name')} · {int(match.group('d'))} {month_label} {match.group('y')}, "
        f"{match.group('hh')}:{match.group('mm')}"
    )


def character_display_name(chat_id: str, character_id: str | None) -> str | None:
    """
    A name a person can read, or None when there is nothing worth showing.

    Order: the character name inside a SillyTavern chat id; then an avatar filename
    without its extension; then nothing. Nothing, rather than the bare id, because
    the ids that remain are card indexes - "Character: 20" told the reader as much as
    an empty line and looked like a value.
    """
    match = _ST_CHAT_ID_RE.match(" ".join((chat_id or "").split()))
    if match:
        return match.group("name")
    cleaned = " ".join((character_id or "").split())
    if cleaned and _AVATAR_SUFFIX_RE.search(cleaned):
        return _AVATAR_SUFFIX_RE.sub("", cleaned)
    return None


def sorted_breakdown(items: dict[str, int]) -> list[dict[str, Any]]:
    """Convert a counter dict into a stable list for template rendering."""
    return [
        {"label": label, "count": count}
        for label, count in sorted(items.items())
    ]


def build_scope_query(
    *,
    view: str,
    selected_chat_id: str | None,
    selected_character_id: str | None,
) -> str:
    return build_query_string(
        {
            "view": view if view == "all" else None,
            "selected_chat_id": selected_chat_id if view != "all" else None,
            "selected_character_id": selected_character_id if view != "all" else None,
        }
    )


def redirect_query_to_render_args(redirect_query: str) -> dict[str, Any]:
    redirect_query = normalize_redirect_query(redirect_query)
    if not redirect_query:
        return {}

    params = dict(parse_qsl(redirect_query, keep_blank_values=False))
    render_args: dict[str, Any] = {}
    string_keys = {
        "selected_chat_id",
        "selected_character_id",
        "view",
        "type",
        "source",
        "layer",
        "search",
        "freshness",
        "activity",
        "consolidation",
        "sort",
        "archived",
        "pinned",
        "show_consolidated",
    }
    int_keys = {"limit", "offset"}

    for key in string_keys:
        if key in params:
            render_args[key] = params[key]
    for key in int_keys:
        if key in params:
            try:
                render_args[key] = int(params[key])
            except ValueError:
                continue

    return render_args


def build_chat_groups(group_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build sidebar chat groups from pre-aggregated SQL summaries."""
    groups = []
    for row in group_summaries:
        character_ids = list(row.get("character_ids") or [row["character_id"]])
        title = build_chat_title(row["chat_id"])
        character_label = character_display_name(row["chat_id"], row["character_id"])
        if character_label and title.startswith(character_label):
            # The title already opens with the name; a second line repeating it is noise.
            character_label = None
        group = {
            "chat_id": row["chat_id"],
            "character_id": row["character_id"],
            # Every id the chat has carried; the primary one first. The template shows
            # the list only for the selected chat, where ids are of any use.
            "character_ids": [row["character_id"]] + [c for c in character_ids if c != row["character_id"]],
            "total_count": row["total_count"],
            "summary_count": row["summary_count"],
            "stable_count": row["stable_count"],
            "episodic_count": row["episodic_count"],
            "last_updated": row["last_updated"],
            "last_updated_days": days_since(row["last_updated"]),
            "display_label": title,
            "display_character_label": character_label,
            "has_friendly_label": title != row["chat_id"],
        }
        groups.append(group)
    return groups


def resolve_selected_group(
    chat_groups: list[dict[str, Any]],
    *,
    requested_chat_id: str | None,
    requested_character_id: str | None,
    view: str,
) -> dict[str, Any] | None:
    if view == "all":
        return None

    # The chat alone decides. requested_character_id is accepted for the links and
    # redirects that still carry one - including an old index that no longer heads
    # the group - and ignored: matching on it is how a chat split across ids used to
    # resolve to one of its halves.
    del requested_character_id
    if requested_chat_id:
        for group in chat_groups:
            if group["chat_id"] == requested_chat_id:
                return group

    return chat_groups[0] if chat_groups else None


def build_store_summary(store_result: StoreMemoryResponse | None) -> dict[str, Any] | None:
    """Build compact aggregate summary for a store run."""
    if store_result is None:
        return None

    summary = {
        "stored": store_result.stored,
        "updated": store_result.updated,
        "skipped": store_result.skipped,
        "debug_breakdown": None,
    }

    if store_result.debug is None:
        return summary

    decision_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    branch_counts: dict[str, int] = {}

    for candidate in store_result.debug.candidates:
        decision_counts[candidate.decision] = decision_counts.get(candidate.decision, 0) + 1
        reason_counts[candidate.reason] = reason_counts.get(candidate.reason, 0) + 1
        branch_counts[candidate.branch] = branch_counts.get(candidate.branch, 0) + 1

    summary["debug_breakdown"] = {
        "decisions": sorted_breakdown(decision_counts),
        "reasons": sorted_breakdown(reason_counts),
        "branches": sorted_breakdown(branch_counts),
    }
    return summary


def build_retrieve_summary(retrieve_result: RetrieveMemoryResponse | None) -> dict[str, Any] | None:
    """Build compact aggregate summary for a retrieval run."""
    if retrieve_result is None:
        return None

    summary = {
        "total_candidates": retrieve_result.total_candidates,
        "selected_count": len(retrieve_result.items),
        "top_score": None,
        "avg_selected_score": None,
        "debug_breakdown": None,
    }

    if retrieve_result.debug is None:
        return summary

    reason_counts: dict[str, int] = {}
    below_threshold = 0
    filtered_by_diversity = 0
    selected_top = 0
    selected_scores: list[float] = []

    for candidate in retrieve_result.debug.candidates:
        reason_counts[candidate.reason] = reason_counts.get(candidate.reason, 0) + 1
        if not candidate.passed_threshold:
            below_threshold += 1
        if candidate.filtered_by_diversity:
            filtered_by_diversity += 1
        if candidate.selected:
            selected_top += 1
            selected_scores.append(candidate.score)

    if selected_scores:
        summary["top_score"] = max(selected_scores)
        summary["avg_selected_score"] = sum(selected_scores) / len(selected_scores)

    summary["debug_breakdown"] = {
        "below_threshold": below_threshold,
        "filtered_by_diversity": filtered_by_diversity,
        "selected_top": selected_top,
        "reasons": sorted_breakdown(reason_counts),
    }
    return summary
