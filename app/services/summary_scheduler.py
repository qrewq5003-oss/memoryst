"""Run the rolling summary automatically, after the turn it belongs to.

The summary layer was implemented and then never ran. `generate_rolling_summary` was
reachable from the web UI's button, a CLI script and the Telegram bot - all of them
things a person has to remember to do - so a database of 4639 memories held two
summaries, both pressed by hand. The layer was not broken; nothing ever called it.

Two things decide where the call goes.

It cannot run inline in /memory/store. That request already waits on scene extraction,
which app/config.py sizes at 90 seconds, and the extension's own deadline for it is 120.
Adding a second LLM call to the same request would push a normal turn past both.

It should not depend on cron either, at least not here. README documents why: Termux's
crond does not survive a reboot without the separate Termux:Boot app, so a scheduled
sweep is exactly the kind of thing that stops running months before anyone notices - the
same failure the summary layer already had.

So it runs as a background task after the store response has been sent. The turn is
already over; nothing is waiting on it.
"""

import logging
import threading

from app.config import config
from app.services.summary_service import generate_rolling_summary

logger = logging.getLogger(__name__)

# (chat_id, character_id) pairs a summary is currently being built for.
#
# Without this, a fast exchange stacks LLM calls: each store schedules a refresh, and a
# refresh outlives several turns, so five quick messages would have five summarizations
# of overlapping windows running at once - paying five times to race each other for the
# same row.
_in_flight: set[tuple[str, str]] = set()
_lock = threading.Lock()


def _claim(scope: tuple[str, str]) -> bool:
    with _lock:
        if scope in _in_flight:
            return False
        _in_flight.add(scope)
        return True


def _release(scope: tuple[str, str]) -> None:
    with _lock:
        _in_flight.discard(scope)


def is_auto_summary_enabled() -> bool:
    return bool(config.ROLLING_SUMMARY_AUTO)


def refresh_rolling_summary(chat_id: str, character_id: str) -> str | None:
    """Update this scope's rolling summary if it is due. Returns the action, or None.

    Cheap when nothing is due: generate_rolling_summary decides that from two queries
    and returns `skipped_not_enough_new_inputs` before reaching the model, so calling
    this on every store costs an LLM request only on the turns that actually earn one.

    Never raises. A summary is an improvement to memory, not part of storing it, and the
    store it follows has already been reported as successful - failing loudly here would
    turn a missing summary into an error the user cannot act on.
    """
    if not chat_id or not character_id or not is_auto_summary_enabled():
        return None

    scope = (chat_id, character_id)
    if not _claim(scope):
        logger.debug("Rolling summary already running for %s", scope)
        return None

    try:
        result = generate_rolling_summary(
            chat_id,
            character_id,
            window_size=config.ROLLING_SUMMARY_WINDOW,
            min_new_memories_for_refresh=config.ROLLING_SUMMARY_MIN_NEW,
            # Never write the rule-based fallback from here. See generate_rolling_summary:
            # that text ends up in every prompt as [SUMMARY], so an unreachable model
            # should leave the layer empty rather than fill it with filler that looks like
            # it is working.
            require_llm=True,
        )
    except Exception:
        logger.exception("Rolling summary failed for %s", scope)
        return None
    finally:
        _release(scope)

    if result.action in ("created", "updated"):
        logger.info(
            "Rolling summary %s for %s from %d memories",
            result.action,
            scope,
            result.summarized_count,
        )
    return result.action


def reset_in_flight() -> None:
    """Clear the in-flight set. For tests, which must not leak state between cases."""
    with _lock:
        _in_flight.clear()
