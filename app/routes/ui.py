import json
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.repositories.memory_repo import (
    create_memory,
    delete_memory,
    get_memory_by_id,
    list_chat_group_summaries,
    list_ui_filtered_memories,
    set_archived,
    set_pinned,
    update_memory,
)
from app.schemas import (
    CreateMemoryRequest,
    ListMemoriesResponse,
    MemoryMetadata,
    MessageInput,
    RetrieveMemoryRequest,
    StoreMemoryRequest,
    UpdateMemoryRequest,
)
from app.services.conflict_resolver import SUPERSEDED_REVIEW_STATUS
from app.version import get_asset_version
from app.services.retrieve_service import retrieve_memories
from app.services.store_service import store_memories
from app.services.summary_service import CONSOLIDATED_REVIEW_STATUS
from app.services.tracker_service import TRACKER_TYPES, list_tracker_items
from app.ui_helpers.classifiers import build_memory_card
from app.ui_helpers.consolidation import (
    append_consolidation_history,
    build_consolidation_data,
    build_consolidation_result,
)
from app.ui_helpers.presentation import (
    build_chat_groups,
    build_query_string,
    build_retrieve_summary,
    build_scope_query,
    build_store_summary,
    normalize_redirect_query,
    normalize_scope_value,
    parse_list,
    redirect_query_to_render_args,
    resolve_selected_group,
)

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["ui"])

UI_SEARCH_SCAN_LIMIT = 2000
# Fifty cards is roughly 9500px of scroll on a phone - about twelve screens before
# the pagination controls. Twenty is two or three screens, and paging is a tap on a
# server running on the same device.
DEFAULT_PAGE_SIZE = 20
# Export pages through the whole result set rather than capping it, so a large chat
# can't be truncated without anyone noticing.
EXPORT_PAGE_SIZE = 1000

TRACKER_LABELS = {
    "timeline": "Timeline",
    "relationship": "Relationship",
    "npc_whoswho": "NPC Who's Who",
    "character_pov_notes": "Character POV Notes",
}


def _parse_messages(value: str) -> list[MessageInput]:
    """Parse textarea input into user messages, one non-empty line per message."""
    messages = []
    for line in value.splitlines():
        text = line.strip()
        if not text:
            continue
        messages.append(MessageInput(role="user", text=text))
    return messages


def _render_memories_page(
    request: Request,
    *,
    selected_chat_id: str | None = None,
    selected_character_id: str | None = None,
    view: str | None = None,
    chat_id: str | None = None,
    character_id: str | None = None,
    type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    search: str | None = None,
    freshness: str | None = None,
    activity: str | None = None,
    consolidation: str | None = None,
    sort: str = "updated_desc",
    archived: str | None = None,
    pinned: str | None = None,
    show_consolidated: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    store_result=None,
    retrieve_result=None,
    consolidation_result: dict[str, Any] | None = None,
    store_form: dict[str, Any] | None = None,
    retrieve_form: dict[str, Any] | None = None,
    template: str = "memories.html",
) -> Any:
    """Render the memories page with optional store/retrieve diagnostics sections."""
    legacy_chat_id = normalize_scope_value(chat_id)
    legacy_character_id = normalize_scope_value(character_id)
    requested_chat_id = normalize_scope_value(selected_chat_id) or legacy_chat_id
    requested_character_id = normalize_scope_value(selected_character_id) or legacy_character_id
    view_mode = "all" if view == "all" else "chat"
    type = type or None
    source = source or None
    layer = layer or None
    search = search or None
    if search and len(search) > 200:
        search = search[:200]
    freshness = freshness or None
    activity = activity or None
    consolidation = consolidation or None

    if archived == "true":
        archived_bool = True
    elif archived == "false":
        archived_bool = False
    else:
        archived_bool = None

    if pinned == "true":
        pinned_bool = True
    elif pinned == "false":
        pinned_bool = False
    else:
        pinned_bool = None

    show_consolidated_bool = show_consolidated == "true"
    hide_consolidated = not show_consolidated_bool

    group_summaries = list_chat_group_summaries(
        memory_type=type,
        source=source,
        layer=layer,
        archived=archived_bool,
        pinned=pinned_bool,
    )
    chat_groups = build_chat_groups(group_summaries)
    selected_group = resolve_selected_group(
        chat_groups,
        requested_chat_id=requested_chat_id,
        requested_character_id=requested_character_id,
        view=view_mode,
    )
    active_chat_id = selected_group["chat_id"] if selected_group else None
    active_character_id = selected_group["character_id"] if selected_group else None

    # SQL-level filtered + sorted + paginated results for display
    if view_mode == "all":
        memories = list_ui_filtered_memories(
            memory_type=type,
            source=source,
            layer=layer,
            archived=archived_bool,
            pinned=pinned_bool,
            hide_consolidated=hide_consolidated,
            search=search,
            freshness=freshness,
            activity=activity,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    elif active_chat_id and active_character_id:
        memories = list_ui_filtered_memories(
            chat_id=active_chat_id,
            character_id=active_character_id,
            memory_type=type,
            source=source,
            layer=layer,
            archived=archived_bool,
            pinned=pinned_bool,
            hide_consolidated=hide_consolidated,
            search=search,
            freshness=freshness,
            activity=activity,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    else:
        memories = ListMemoriesResponse(items=[], total=0, limit=limit, offset=offset)

    # Load items in scope for consolidation analysis (SQL-filtered by scope)
    if view_mode == "all":
        consolidation_items = list_ui_filtered_memories(
            memory_type=type,
            source=source,
            layer=layer,
            archived=archived_bool,
            pinned=pinned_bool,
            limit=UI_SEARCH_SCAN_LIMIT,
            offset=0,
        ).items
    elif active_chat_id and active_character_id:
        consolidation_items = list_ui_filtered_memories(
            chat_id=active_chat_id,
            character_id=active_character_id,
            memory_type=type,
            source=source,
            layer=layer,
            archived=archived_bool,
            pinned=pinned_bool,
            limit=UI_SEARCH_SCAN_LIMIT,
            offset=0,
        ).items
    else:
        consolidation_items = []

    candidate_map, consolidation_summary = build_consolidation_data(consolidation_items)

    # Trackers only make sense pinned to one chat/character - "All Chats" has no single
    # scope for a document that gets rewritten in place, so the section stays hidden there.
    trackers_display = None
    if view_mode != "all" and active_chat_id and active_character_id:
        items_by_type = {
            item.tracker_type: item
            for item in list_tracker_items(active_chat_id, active_character_id)
        }
        trackers_display = [
            {
                "tracker_type": tracker_type,
                "label": TRACKER_LABELS[tracker_type],
                "item": items_by_type.get(tracker_type),
            }
            for tracker_type in TRACKER_TYPES
        ]

    # Full pool of memories eligible as consolidation sources, independent of the
    # page-size-limited `memories` list above - the consolidate-checkbox picker
    # in the UI must be able to select an older memory that isn't on the current
    # page, otherwise it silently gets excluded from source_ids with no sign
    # anything was missed (see: episodic memories from earlier in a long chat
    # never being selectable once the chat exceeds one page of results).
    consolidation_all_candidate_ids = [
        item.id
        for item in consolidation_items
        if not item.archived
        and item.metadata.review_status not in (CONSOLIDATED_REVIEW_STATUS, SUPERSEDED_REVIEW_STATUS)
    ]
    consolidation_pool_truncated = len(consolidation_items) >= UI_SEARCH_SCAN_LIMIT

    # Apply consolidation filter in Python if needed (overrides SQL pagination)
    if consolidation and consolidation_items:
        consolidation_ids = set()
        for item in consolidation_items:
            candidates = candidate_map.get(item.id, [])
            if consolidation == "candidates_only" and candidates:
                consolidation_ids.add(item.id)
            elif consolidation != "candidates_only" and any(c["type"] == consolidation for c in candidates):
                consolidation_ids.add(item.id)
        if consolidation_ids:
            filtered = [item for item in memories.items if item.id in consolidation_ids]
            memories = ListMemoriesResponse(items=filtered, total=len(filtered), limit=limit, offset=offset)
    memory_cards = [
        {
            **build_memory_card(item),
            "consolidation_candidates": candidate_map.get(item.id, []),
            "is_consolidation_candidate": bool(candidate_map.get(item.id)),
        }
        for item in memories.items
    ]

    redirect_query = build_query_string(
        {
            "view": view_mode if view_mode == "all" else None,
            "selected_chat_id": active_chat_id if view_mode != "all" else None,
            "selected_character_id": active_character_id if view_mode != "all" else None,
            "type": type,
            "source": source,
            "layer": layer,
            "search": search,
            "freshness": freshness,
            "activity": activity,
            "consolidation": consolidation,
            "sort": sort,
            "archived": archived,
            "pinned": pinned,
            "show_consolidated": show_consolidated,
            "limit": limit,
            "offset": offset,
        }
    )
    clear_filters_query = build_scope_query(
        view=view_mode,
        selected_chat_id=active_chat_id,
        selected_character_id=active_character_id,
    )
    clear_filters_url = "/ui"
    if clear_filters_query:
        clear_filters_url = f"/ui?{clear_filters_query}"

    all_chats_query = build_query_string(
        {
            "view": "all",
            "type": type,
            "source": source,
            "layer": layer,
            "search": search,
            "freshness": freshness,
            "activity": activity,
            "consolidation": consolidation,
            "sort": sort,
            "archived": archived,
            "pinned": pinned,
            "show_consolidated": show_consolidated,
            "limit": limit,
        }
    )
    all_chats_url = f"/ui?{all_chats_query}" if all_chats_query else "/ui"

    for group in chat_groups:
        group_query = build_query_string(
            {
                "selected_chat_id": group["chat_id"],
                "selected_character_id": group["character_id"],
                "type": type,
                "source": source,
                "layer": layer,
                "search": search,
                "freshness": freshness,
                "activity": activity,
                "consolidation": consolidation,
                "sort": sort,
                "archived": archived,
                "pinned": pinned,
                "show_consolidated": show_consolidated,
                "limit": limit,
            }
        )
        group["url"] = f"/ui?{group_query}" if group_query else "/ui"
        group["is_selected"] = (
            view_mode != "all"
            and active_chat_id == group["chat_id"]
            and active_character_id == group["character_id"]
        )

    scope_title = "All Chats"
    scope_subtitle = "Global view across the current filtered dataset"
    scope_meta: list[dict[str, str]] = []
    if view_mode != "all":
        scope_title = selected_group["display_label"] if selected_group else (active_chat_id or "Select a chat")
        scope_subtitle = (
            f"Character: {selected_group['display_character_label']}"
            if selected_group
            else (f"Character: {active_character_id}" if active_character_id else None)
        )
        if active_chat_id:
            scope_meta.append({"label": "Chat ID", "value": active_chat_id})
        if active_character_id:
            scope_meta.append({"label": "Character ID", "value": active_character_id})

    filters = {
        "chat_id": active_chat_id,
        "character_id": active_character_id,
        "selected_chat_id": active_chat_id,
        "selected_character_id": active_character_id,
        "view": view_mode,
        "type": type,
        "source": source,
        "layer": layer,
        "search": search,
        "freshness": freshness,
        "activity": activity,
        "consolidation": consolidation,
        "sort": sort,
        "archived": archived,
        "pinned": pinned,
        "show_consolidated": show_consolidated,
        "limit": limit,
        "offset": offset,
        "query_string": build_query_string(
            {
                "view": view_mode if view_mode == "all" else None,
                "selected_chat_id": active_chat_id if view_mode != "all" else None,
                "selected_character_id": active_character_id if view_mode != "all" else None,
                "type": type,
                "source": source,
                "layer": layer,
                "search": search,
                "freshness": freshness,
                "activity": activity,
                "consolidation": consolidation,
                "sort": sort,
                "archived": archived,
                "pinned": pinned,
                "show_consolidated": show_consolidated,
                "limit": limit,
            }
        ),
        "redirect_query": redirect_query,
        "clear_filters_url": clear_filters_url,
        # Base query for the filter chips: they have to stay inside the current
        # scope, or tapping "Stable" would silently jump to every chat.
        "scope_query": clear_filters_query,
    }

    response = templates.TemplateResponse(
        request,
        template,
        {
            "asset_version": get_asset_version(),
            "memories": memories.model_dump(),
            "memory_cards": memory_cards,
            "chat_groups": chat_groups,
            "scope_title": scope_title,
            "scope_subtitle": scope_subtitle,
            "scope_meta": scope_meta,
            "scope_is_all_chats": view_mode == "all",
            "all_chats_url": all_chats_url,
            "all_chats_selected": view_mode == "all",
            "has_chat_groups": bool(chat_groups),
            "consolidation_summary": consolidation_summary,
            "consolidation_all_candidate_ids": consolidation_all_candidate_ids,
            "consolidation_pool_truncated": consolidation_pool_truncated,
            "trackers_display": trackers_display,
            "filters": filters,
            "store_result": store_result.model_dump() if store_result else None,
            "retrieve_result": retrieve_result.model_dump() if retrieve_result else None,
            "consolidation_result": consolidation_result,
            "store_summary": build_store_summary(store_result),
            "retrieve_summary": build_retrieve_summary(retrieve_result),
            "store_form": store_form or {
                "chat_id": active_chat_id or "",
                "character_id": active_character_id or "",
                "messages": "",
                "debug": False,
            },
            "retrieve_form": retrieve_form or {
                "chat_id": active_chat_id or "",
                "character_id": active_character_id or "",
                "user_input": "",
                "recent_messages": "",
                "limit": 5,
                "include_archived": False,
                "debug": False,
            },
        },
    )

    # The page carries a fingerprinted stylesheet URL, so it must never itself be
    # served from a stale copy - a cached page would keep pointing at the old
    # fingerprint and the new stylesheet would never be requested. `no-cache`
    # rather than `no-store`: revalidation is enough, and it keeps the service
    # worker's offline fallback usable.
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


RESET_CACHE_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>memoryst — сброс кэша</title>
<style>
 body{font:16px/1.5 system-ui,-apple-system,Roboto,sans-serif;margin:0;padding:24px;
      background:#EEF2F5;color:#0F1922}
 main{max-width:34rem;margin:0 auto}
 h1{font-size:21px;margin:0 0 12px}
 p{color:#47596A}
 #log{font-family:ui-monospace,monospace;font-size:13px;background:#fff;
      border:1px solid #D6DEE5;border-radius:10px;padding:12px;margin-top:16px;
      white-space:pre-wrap}
 a.button{display:block;min-height:44px;line-height:44px;text-align:center;
      margin-top:16px;background:#0B6E75;color:#fff;text-decoration:none;border-radius:8px}
</style></head><body><main>
<h1>Сброс кэша интерфейса</h1>
<p>Снимает service worker и очищает его хранилище. Нужно один раз: дальше
адрес стилей содержит отпечаток содержимого, и кэш обновляется сам.</p>
<div id="log">работаю…</div>
<a class="button" href="/ui">Открыть интерфейс</a>
</main><script>
(async () => {
  const log = document.getElementById('log');
  const lines = [];
  const say = (t) => { lines.push(t); log.textContent = lines.join('\n'); };
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    say('service worker: найдено ' + regs.length);
    for (const r of regs) { await r.unregister(); }
    if (regs.length) say('service worker: снят');
  } catch (e) { say('service worker: недоступен (' + e.message + ')'); }
  try {
    const keys = await caches.keys();
    say('хранилищ кэша: ' + keys.length + (keys.length ? ' — ' + keys.join(', ') : ''));
    for (const k of keys) { await caches.delete(k); }
    if (keys.length) say('кэш очищен');
  } catch (e) { say('кэш: недоступен (' + e.message + ')'); }
  say('готово — нажмите кнопку ниже');
})();
</script></body></html>"""


DIAG_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>memoryst — диагностика стилей</title>
<link rel="stylesheet" href="/static/styles.css?v=__V__">
<style>
 body{font:16px/1.5 system-ui,-apple-system,Roboto,sans-serif;margin:0;padding:20px;background:#fff}
 table{border-collapse:collapse;width:100%;font-size:14px;margin-top:12px}
 td{border-bottom:1px solid #ddd;padding:8px 6px;vertical-align:top}
 td:first-child{color:#555;width:46%}
 b.ok{color:#0B6E75} b.bad{color:#B3261E}
 a.button{display:block;min-height:44px;line-height:44px;text-align:center;
   margin-top:20px;background:#0B6E75;color:#fff;text-decoration:none;border-radius:8px}
</style></head><body>
<h1 style="font-size:20px;margin:0">Что дошло до браузера</h1>
<table id="t"><tbody></tbody></table>
<a class="button" href="/ui">К интерфейсу</a>
<div id="probe" class="memory-card layer-stable" style="position:absolute;left:-9999px">
  <div class="actions-cell"><form><button type="button">x</button></form></div>
</div>
<script>
(async () => {
  const tb = document.querySelector('#t tbody');
  const row = (k, v, ok) => {
    const tr = document.createElement('tr');
    const a = document.createElement('td'); a.textContent = k;
    const b = document.createElement('td');
    const strong = document.createElement('b');
    if (ok !== undefined) strong.className = ok ? 'ok' : 'bad';
    strong.textContent = v; b.appendChild(strong); tr.append(a, b); tb.appendChild(tr);
  };

  const link = document.querySelector('link[rel=stylesheet]');
  row('адрес стилей в DOM', link ? link.getAttribute('href') : 'нет');
  row('страницу отдаёт service worker', navigator.serviceWorker && navigator.serviceWorker.controller ? 'да' : 'нет',
      !(navigator.serviceWorker && navigator.serviceWorker.controller));

  try {
    const r = await fetch(link.getAttribute('href'), { cache: 'no-store' });
    const text = await r.text();
    row('CSS: код ответа', String(r.status), r.status === 200);
    row('CSS: размер', text.length + ' символов');
    row('CSS: есть новый акцент', text.includes('#0B6E75') ? 'да' : 'НЕТ', text.includes('#0B6E75'));
    row('CSS: есть 44px', text.includes('min-height: 44px') ? 'да' : 'НЕТ', text.includes('min-height: 44px'));
  } catch (e) { row('CSS: загрузка', 'ошибка ' + e.message, false); }

  const probe = document.getElementById('probe');
  const btn = probe.querySelector('button');
  const h = getComputedStyle(btn).minHeight;
  row('применилось: высота кнопки', h, h === '44px');
  const stripe = getComputedStyle(probe, '::before').backgroundColor;
  row('применилось: полоса слоя', stripe || '(нет)', /rgb/.test(stripe || ''));
  const body = getComputedStyle(document.body).fontFamily;
  row('шрифт body', body.slice(0, 40));
})();
</script></body></html>"""


@router.get("/ui/diag")
def ui_diag() -> Any:
    """Report what stylesheet the device actually received and applied.

    Reachability and correctness on the server were both established, and the
    page still looked unchanged - which leaves only what happens between the two,
    and a phone has no console to inspect it with. This fetches the stylesheet
    the page is really linking, checks it for markers of the current build, and
    reads getComputedStyle on a probe element so "delivered" and "applied" are
    answered separately.
    """
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        DIAG_PAGE.replace("__V__", get_asset_version()),
        headers={"Cache-Control": "no-store"},
    )


def _memory_card_context(memory_id: str) -> dict[str, Any] | None:
    """Everything a details fragment needs for one memory, or None if it is gone."""
    memory = get_memory_by_id(memory_id)
    if memory is None:
        return None
    card = build_memory_card(memory)
    candidate_map, _ = build_consolidation_data([memory])
    card["consolidation_candidates"] = candidate_map.get(memory.id, [])
    card["is_consolidation_candidate"] = bool(card["consolidation_candidates"])
    return card


@router.get("/ui/memory/{memory_id}/fragment")
def ui_memory_details_fragment(request: Request, memory_id: str, part: str = "inspect") -> Any:
    """The Inspect Details body for one memory, fetched when a card is expanded.

    A collapsed <details> still ships everything inside it, and across the list
    that was 202KB of a 366KB page - 55% of the bytes, invisible until opened.
    """
    card = _memory_card_context(memory_id)
    if card is None:
        from fastapi.responses import HTMLResponse

        return HTMLResponse('<p class="empty-value">Memory not found.</p>', status_code=404)

    template = "_memory_consolidation.html" if part == "consolidation" else "_memory_details.html"
    return templates.TemplateResponse(
        request,
        template,
        {"item": card, "filters": {"redirect_query": ""}},
    )


@router.get("/ui/memory/{memory_id}")
def ui_memory_details_page(request: Request, memory_id: str) -> Any:
    """Standalone page for one memory's details.

    The fallback the disclosure links to, so expanding a card still works with
    JavaScript off - the rest of this UI does.
    """
    card = _memory_card_context(memory_id)
    if card is None:
        return RedirectResponse(url="/ui", status_code=303)

    return templates.TemplateResponse(
        request,
        "memory_detail.html",
        {
            "asset_version": get_asset_version(),
            "item": card,
            "filters": {"redirect_query": ""},
        },
    )


@router.get("/ui/tools")
def ui_tools_page(
    request: Request,
    selected_chat_id: str | None = None,
    selected_character_id: str | None = None,
    view: str | None = None,
) -> Any:
    """Tools on their own page.

    They used to sit at the bottom of the memory list, where reaching them meant
    scrolling past every card - they began at 94% of the page. They are a
    different job from browsing memories and now have their own address.

    Built from the same context as the list, because the tools are scoped: the
    tracker panel and the consolidation pool both depend on which chat is
    selected.
    """
    return _render_memories_page(
        request,
        selected_chat_id=selected_chat_id,
        selected_character_id=selected_character_id,
        view=view,
        template="tools.html",
    )


@router.get("/ui/reset-cache")
def ui_reset_cache() -> Any:
    """One-tap escape hatch for a stale service worker.

    The previous worker served /static/styles.css cache-first with no
    revalidation under a cache name that never changed, so a device that had
    once cached a stylesheet kept it for good. Fixing the worker does not help a
    device already holding the old one - and a phone has no developer console to
    unregister it by hand. This page does it in one visit.
    """
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        RESET_CACHE_PAGE,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/ui")
def ui_memories_page(
    request: Request,
    selected_chat_id: str | None = None,
    selected_character_id: str | None = None,
    view: str | None = None,
    chat_id: str | None = None,
    character_id: str | None = None,
    type: str | None = None,
    source: str | None = None,
    layer: str | None = None,
    search: str | None = None,
    freshness: str | None = None,
    activity: str | None = None,
    consolidation: str | None = None,
    sort: str = "updated_desc",
    archived: str | None = None,
    pinned: str | None = None,
    show_consolidated: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> Any:
    """Render memories page with filters."""
    return _render_memories_page(
        request,
        selected_chat_id=selected_chat_id,
        selected_character_id=selected_character_id,
        view=view,
        chat_id=chat_id,
        character_id=character_id,
        type=type,
        source=source,
        layer=layer,
        search=search,
        freshness=freshness,
        activity=activity,
        consolidation=consolidation,
        sort=sort,
        archived=archived,
        pinned=pinned,
        show_consolidated=show_consolidated,
        limit=limit,
        offset=offset,
    )


@router.post("/ui/store")
def ui_store_memories(
    request: Request,
    chat_id: str = Form(...),
    character_id: str = Form(...),
    messages: str = Form(...),
    debug: bool = Form(False),
) -> Any:
    """Run store pipeline from the admin UI and render results inline."""
    store_request = StoreMemoryRequest(
        chat_id=chat_id,
        character_id=character_id,
        messages=_parse_messages(messages),
        debug=debug,
    )
    result = store_memories(store_request)
    return _render_memories_page(
        request,
        selected_chat_id=chat_id,
        selected_character_id=character_id,
        store_result=result,
        store_form={
            "chat_id": chat_id,
            "character_id": character_id,
            "messages": messages,
            "debug": debug,
        },
        retrieve_form={
            "chat_id": chat_id,
            "character_id": character_id,
            "user_input": "",
            "recent_messages": "",
            "limit": 5,
            "include_archived": False,
            "debug": False,
        },
        # The store and retrieve forms live on Tools, so their results belong
        # there too - returning the memory list would drop the user somewhere
        # that has no place to show the breakdown they just asked for.
        template="tools.html",
    )


@router.post("/ui/retrieve")
def ui_retrieve_memories(
    request: Request,
    chat_id: str = Form(...),
    character_id: str = Form(...),
    user_input: str = Form(...),
    recent_messages: str = Form(""),
    limit: int = Form(5),
    include_archived: bool = Form(False),
    debug: bool = Form(False),
) -> Any:
    """Run retrieval pipeline from the admin UI and render results inline."""
    retrieve_request = RetrieveMemoryRequest(
        chat_id=chat_id,
        character_id=character_id,
        user_input=user_input,
        recent_messages=_parse_messages(recent_messages),
        limit=limit,
        include_archived=include_archived,
        debug=debug,
    )
    result = retrieve_memories(retrieve_request)
    return _render_memories_page(
        request,
        selected_chat_id=chat_id,
        selected_character_id=character_id,
        retrieve_result=result,
        store_form={
            "chat_id": chat_id,
            "character_id": character_id,
            "messages": "",
            "debug": False,
        },
        retrieve_form={
            "chat_id": chat_id,
            "character_id": character_id,
            "user_input": user_input,
            "recent_messages": recent_messages,
            "limit": limit,
            "include_archived": include_archived,
            "debug": debug,
        },
        template="tools.html",
    )


@router.post("/ui/create")
def ui_create_memory(
    chat_id: str = Form(...),
    character_id: str = Form(...),
    type: str = Form(...),
    content: str = Form(...),
    source: str = Form("manual"),
    layer: str = Form(...),
    importance: float = Form(0.5),
    pinned: bool = Form(False),
    archived: bool = Form(False),
    entities: str = Form(""),
    keywords: str = Form(""),
    redirect_query: str = Form(""),
) -> RedirectResponse:
    """Create a new memory and redirect back to UI."""
    request = CreateMemoryRequest(
        chat_id=chat_id,
        character_id=character_id,
        type=type,  # type: ignore
        content=content,
        source=source,  # type: ignore
        layer=layer,  # type: ignore
        importance=min(max(importance, 0.0), 1.0),
        pinned=pinned,
        archived=archived,
        metadata=MemoryMetadata(
            entities=parse_list(entities),
            keywords=parse_list(keywords),
        ),
    )
    create_memory(request)
    redirect_query = normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/delete-chat")
def ui_delete_chat(
    chat_id: str = Form(...),
    character_id: str = Form(""),
    redirect_query: str = Form(""),
) -> RedirectResponse:
    """Delete everything a chat owns and redirect back to UI."""
    from app.services.chat_cleanup_service import delete_chat_data

    char_id = character_id.strip() or None
    delete_chat_data(chat_id=chat_id, character_id=char_id)

    redirect_query = normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/backfill-file")
def ui_backfill_file(
    request: Request,
    chat_id: str = Form(...),
    character_id: str = Form(...),
    file: UploadFile = File(...),
) -> RedirectResponse:
    """Backfill memories from an uploaded .jsonl file."""
    from app.services.extractor import extract_memories
    from app.repositories.memory_repo import find_memory_by_normalized_content
    from app.services.text_utils import normalize_content
    from app.services.store_service import passes_memory_quality_gate
    from app.services import vector_store as vs

    content = file.file.read().decode("utf-8")
    messages = []
    detected_chat_id = None
    detected_char_id = None

    # Extract chat name from filename: "Paris1775 - 2026-06-24@...jsonl" → "Paris1775"
    if file.filename:
        fname = file.filename.rsplit(".", 1)[0]  # remove extension
        if " - " in fname:
            detected_chat_id = fname.split(" - ")[0].strip()
        else:
            detected_chat_id = fname.strip()

    # Counted rather than swallowed. A file in an unexpected shape used to produce
    # "0 messages" with nothing to distinguish it from an empty file - the reader
    # could not tell a wrong file from a wrong parser.
    unparsable_lines = 0
    unrecognized_lines = 0

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                unrecognized_lines += 1
                continue

            # Extract chat_id from metadata (first line)
            if "chat_metadata" in obj and not detected_chat_id:
                world = obj.get("world_info", "")
                if world:
                    detected_chat_id = world

            # SillyTavern format: {name, mes, is_user}
            if "mes" in obj and "is_user" in obj:
                role = "user" if obj["is_user"] else "assistant"
                text = obj["mes"].strip()
                if text:
                    messages.append(MessageInput(role=role, text=text))
                # Detect character name from assistant messages
                if not obj["is_user"] and not detected_char_id:
                    name = obj.get("name", "")
                    if name and name != "undefined":
                        detected_char_id = name

            # Standard format: {role, content}
            elif "role" in obj and "content" in obj:
                messages.append(MessageInput(role=obj["role"], text=obj["content"]))
            elif "chat_metadata" not in obj:
                unrecognized_lines += 1
        except (json.JSONDecodeError, KeyError):
            unparsable_lines += 1

    # Use detected IDs if form didn't provide specific ones
    if chat_id == "backfill" and detected_chat_id:
        chat_id = detected_chat_id
    if character_id == "backfill" and detected_char_id:
        character_id = detected_char_id

    stored = 0
    skipped = 0
    duplicates = 0

    if messages:
        candidates = extract_memories(chat_id=chat_id, character_id=character_id, messages=messages, mode="backfill")
        for candidate in candidates:
            if not passes_memory_quality_gate(candidate):
                skipped += 1
                continue
            normalized = normalize_content(candidate.content)
            existing = find_memory_by_normalized_content(chat_id=chat_id, character_id=character_id, normalized_content=normalized)
            if existing is not None:
                duplicates += 1
                continue
            created = create_memory(candidate)
            vs.add_memory(created.id, created.content, {"chat_id": created.chat_id, "character_id": created.character_id})
            stored += 1

    result = (
        f"backfill_done&stored={stored}&skipped={skipped}&duplicates={duplicates}"
        f"&total_messages={len(messages)}&unparsable_lines={unparsable_lines}"
        f"&unrecognized_lines={unrecognized_lines}"
        f"&detected_chat={chat_id}&detected_char={character_id}"
    )
    return RedirectResponse(url=f"/ui?{result}", status_code=303)


@router.get("/ui/export")
def ui_export_memories(
    chat_id: str | None = None,
    character_id: str | None = None,
) -> Any:
    """Export memories and trackers as a .jsonl download.

    Trackers are included: the export used to run through list_memories, which hides
    them, so a full export silently omitted every tracker document. There is no import
    path yet, but the export is what a manual restore would be rebuilt from, and it
    can't be the source of truth while a whole class of row is missing from it.

    Paging is explicit rather than a bare limit=10000. The old cap would have started
    truncating silently, with no signal in the file or the response.
    """
    from app.repositories.memory_repo import list_memories as lm
    from fastapi.responses import Response

    items = []
    offset = 0
    while True:
        page = lm(
            chat_id=chat_id,
            character_id=character_id,
            limit=EXPORT_PAGE_SIZE,
            offset=offset,
            include_trackers=True,
        )
        items.extend(page.items)
        offset += EXPORT_PAGE_SIZE
        if offset >= page.total or not page.items:
            break

    lines = []
    for item in items:
        record = {
            "id": item.id,
            "chat_id": item.chat_id,
            "character_id": item.character_id,
            "type": item.type,
            "layer": item.layer,
            "content": item.content,
            "importance": item.importance,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "pinned": item.pinned,
            "entities": item.metadata.entities,
            "keywords": item.metadata.keywords,
        }
        if item.type == "tracker":
            # Without this a tracker can't be told apart from any other row on the
            # way back in - tracker_type is its identity, not decoration.
            record["tracker_type"] = item.metadata.tracker_type
        lines.append(json.dumps(record, ensure_ascii=False))

    body = "\n".join(lines)
    filename = f"memories_{chat_id or 'all'}.jsonl"
    return Response(
        content=body,
        media_type="application/jsonl",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/ui/{memory_id}/update")
def ui_update_memory(
    memory_id: str,
    content: str = Form(...),
    type: str = Form(...),
    source: str = Form(...),
    layer: str = Form(...),
    importance: float = Form(0.5),
    pinned: bool = Form(False),
    archived: bool = Form(False),
    entities: str = Form(""),
    keywords: str = Form(""),
    redirect_query: str = Form(""),
) -> RedirectResponse:
    """Update a memory and redirect back to UI."""
    request = UpdateMemoryRequest(
        content=content,
        type=type,  # type: ignore
        source=source,  # type: ignore
        layer=layer,  # type: ignore
        importance=min(max(importance, 0.0), 1.0),
        pinned=pinned,
        archived=archived,
        metadata=MemoryMetadata(
            entities=parse_list(entities),
            keywords=parse_list(keywords),
        ),
    )
    update_memory(memory_id, request)
    redirect_query = normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/{memory_id}/pin")
def ui_toggle_pin(memory_id: str, redirect_query: str = Form("")) -> RedirectResponse:
    """Toggle pinned status and redirect back to UI."""
    memory = get_memory_by_id(memory_id)
    if memory:
        set_pinned(memory_id, not memory.pinned)
    redirect_query = normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/{memory_id}/archive")
def ui_toggle_archive(memory_id: str, redirect_query: str = Form("")) -> RedirectResponse:
    """Toggle archived status and redirect back to UI."""
    memory = get_memory_by_id(memory_id)
    if memory:
        set_archived(memory_id, not memory.archived)
    redirect_query = normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/{memory_id}/delete")
def ui_delete_memory(memory_id: str, redirect_query: str = Form("")) -> RedirectResponse:
    """Delete a memory and redirect back to UI."""
    delete_memory(memory_id)
    redirect_query = normalize_redirect_query(redirect_query)
    redirect_url = f"/ui?{redirect_query}" if redirect_query else "/ui"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/ui/{memory_id}/consolidate")
def ui_consolidate_memory(
    request: Request,
    memory_id: str,
    action: str = Form(...),
    related_memory_id: str = Form(""),
    note: str = Form(""),
    redirect_query: str = Form(""),
) -> Any:
    """Apply manual consolidation triage workflow from the admin UI."""
    memory = get_memory_by_id(memory_id)
    if memory is None:
        return _render_memories_page(
            request,
            consolidation_result={
                "memory_id": memory_id,
                "action": action,
                "message": "Memory not found.",
                "related_memory_id": None,
                "note": None,
            },
        )

    related_memory_id = related_memory_id.strip()
    note = note.strip()

    updated_metadata = memory.metadata.model_copy(
        update={
            "review_status": {
                "mark_consolidated_archive": "consolidated_archive",
                "mark_reviewed_keep": "reviewed_keep",
                "link_to_related_memory": "linked_to_related",
            }.get(action, memory.metadata.review_status),
            "related_memory_id": related_memory_id or memory.metadata.related_memory_id,
            "consolidation_note": note or memory.metadata.consolidation_note,
            "consolidation_history": append_consolidation_history(
                memory.metadata,
                action,
                related_memory_id,
                note,
            ),
        }
    )

    update_payload = UpdateMemoryRequest(metadata=updated_metadata)
    if action == "mark_consolidated_archive":
        update_payload.archived = True
    update_memory(memory_id, update_payload)

    updated_memory = get_memory_by_id(memory_id)
    selected_chat_id = updated_memory.chat_id if updated_memory else memory.chat_id
    selected_character_id = updated_memory.character_id if updated_memory else memory.character_id

    return _render_memories_page(
        request,
        **redirect_query_to_render_args(redirect_query),
        consolidation_result=build_consolidation_result(action, memory_id, related_memory_id, note),
    )
