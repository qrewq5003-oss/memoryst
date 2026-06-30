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
        group = {
            "chat_id": row["chat_id"],
            "character_id": row["character_id"],
            "total_count": row["total_count"],
            "summary_count": row["summary_count"],
            "stable_count": row["stable_count"],
            "episodic_count": row["episodic_count"],
            "last_updated": row["last_updated"],
            "last_updated_days": days_since(row["last_updated"]),
            "display_label": build_friendly_scope_label(row["chat_id"]),
            "display_character_label": build_friendly_scope_label(row["character_id"]),
            "has_friendly_label": build_friendly_scope_label(row["chat_id"]) != row["chat_id"],
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

    if requested_chat_id and requested_character_id:
        for group in chat_groups:
            if (
                group["chat_id"] == requested_chat_id
                and group["character_id"] == requested_character_id
            ):
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
