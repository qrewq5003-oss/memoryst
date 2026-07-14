"""
Prompts, JSON schemas and deterministic renderers for the four tracker types.

The whole point of a tracker is that the *structure* is not the LLM's job. The model
returns JSON; Python sorts it and renders the text. That is what makes trackers immune
to the failure that motivated them - generate_tiered_consolidation once fused Thursday
evening and Friday morning into one paragraph and kept a line that the intervening
events had already falsified. A model cannot fuse two days here: one entry is one
moment, and the ordering is decided by datetime.date, not by the model's sense of
narrative flow.
"""

import re
from datetime import date, time

# Shared across all four prompts. Without it every update grows the lists by a few
# items forever, and the character budget (see the extension's maxTrackerChars) starts
# evicting real content to make room for near-duplicates.
GUARDRAIL = (
    "Не добавляй новый пункт, если это не принципиально новая информация — "
    "сначала проверь, можно ли слить с уже существующим пунктом."
)

TRACKER_TYPES = ("timeline", "relationship", "npc_whoswho", "character_pov_notes")

# The relationship document's size limits. Stated in the prompt AND enforced in
# _clamp_relationship - the prompt keeps the model from getting close, the clamp
# guarantees the result. Keep the two in sync: the numbers below are interpolated into
# RELATIONSHIP_PROMPT, so there is only one place to change them.
RELATIONSHIP_TEXT_MAX_CHARS = 150
RELATIONSHIP_ITEM_MAX_CHARS = 100
RELATIONSHIP_TEXT_FIELDS = ("affinity_evidence", "status", "trust", "tension")
RELATIONSHIP_LIST_FIELDS = ("key_facts", "goals", "open_threads")
# Live, the model invented nine dimensions and pinned eight of them at 100/100 - a fifth of
# the whole injection budget spent on lines that say nothing. The cap is on count, and the
# prompt asks for dimensions that actually differentiate; a dimension equal to the affinity
# score is not a dimension, it is a restatement.
RELATIONSHIP_MAX_DIMENSIONS = 5

# The other three trackers have the same failure mode, measured on the same live chat: one
# timeline entry ran to 600 chars ("Valeria greets Marcus with a slow, lazy kiss, noting he
# smells like office and coffee. She offers him wine or water and...") - a retelling of the
# scene rather than a line of chronology, eating a third of the whole injection budget on a
# single event. Capped, the same budget holds several events instead of one.
TIMELINE_SUMMARY_MAX_CHARS = 120
NPC_DESCRIPTION_MAX_CHARS = 120
POV_NOTE_MAX_CHARS = 100
# The prefix of a rendered timeline line - "Tuesday, March 18, 2025, 7:45 PM — Milan -
# Wanted's Apartment, Kitchen: " - ran to ~75 chars before a single word of the event, on
# every line. The date/time are re-rendered compactly from the parsed values (see
# _render_timeline) and the location is capped here.
TIMELINE_LOCATION_MAX_CHARS = 24


# --------------------------------------------------------------------------- prompts

TIMELINE_PROMPT = f"""Ты ведёшь ХРОНОЛОГИЮ событий ролевого чата.

Тебе даны: текущий документ хронологии (JSON) и новые сообщения чата,
пронумерованные как "[N][role]: текст".

Верни ПОЛНЫЙ обновлённый список записей — существующие плюс новые.

Правила:
- Одна запись — ОДИН момент времени. НИКОГДА не объединяй события разных дней
  или разных сцен в одну запись.
- Дату и время бери из внутриигрового штампа времени в сообщении
  (например "[ 🕰️ Time 7:45 PM | 🗓️ Thursday, February 13, 2025 ]"), если он есть.
  Штамп авторитетнее твоих догадок о том, сколько времени прошло.
- Если у соседних сообщений об одном и том же моменте штампы противоречат друг другу
  (свайпы), выбери версию, согласующуюся с соседними сообщениями. НЕ создавай две записи.
- "summary" — НЕ ДЛИННЕЕ {TIMELINE_SUMMARY_MAX_CHARS} СИМВОЛОВ: что произошло, одной
  строкой. Это лента событий, а не пересказ сцены — реплики, детали обстановки и
  подробности разговора сюда не пиши. Превышение будет обрезано на полуслове.
- Не переписывай существующие записи, если новые сообщения их не меняют.
- Для каждой НОВОЙ записи перечисли в "source_message_indices" номера N тех сообщений,
  из которых она взята. Для записей, которые ты переносишь из текущего документа без
  изменений, верни пустой массив.
- Пиши на языке чата.
- {GUARDRAIL}"""

RELATIONSHIP_PROMPT = f"""Ты ведёшь СТАТУС ОТНОШЕНИЙ персонажа с пользователем.

Тебе даны: текущий документ статуса (JSON) и новые сообщения чата.

Это СТАТУС-ДОКУМЕНТ на сейчас, а НЕ журнал событий.

Документ целиком должен читаться за несколько секунд. Это сводка, а не пересказ сцены.

ЛИМИТЫ НИЖЕ ОТНОСЯТСЯ КО ВСЕМУ ДОКУМЕНТУ, А НЕ ТОЛЬКО К ТОМУ, ЧТО ТЫ ДОБАВЛЯЕШЬ.
Если текущий документ их нарушает — он раздут, и твоя задача сократить его ДО лимитов
в этом же ответе: слить дублирующее, выбросить мелкое, ужать абзацы до одного-двух
предложений. Не переноси нарушающее лимиты содержимое дальше только потому, что оно
уже есть в документе.

Правила:
- Обновляй и сливай существующие пункты. Удаляй устаревшие и противоречащие новым.
- Если факт из "key_facts" больше не верен — ЗАМЕНИ его, а не добавляй новый рядом.
- "affinity_score" — целое 0..100, общая расположенность персонажа к пользователю.
- "affinity_evidence" — на чём основана оценка. НЕ ДЛИННЕЕ {RELATIONSHIP_TEXT_MAX_CHARS}
  СИМВОЛОВ (одно предложение). Не пересказывай сцену и не перечисляй реплики — только суть.
- "custom_dimensions" — НЕ БОЛЬШЕ {RELATIONSHIP_MAX_DIMENSIONS} измерений, уместных ИМЕННО
  ДЛЯ ЭТОГО сеттинга, ты выбираешь их сам. В романтическом сюжете это может быть
  "влюблённость"/"влечение"; в служебном — "лояльность"/"долг"/"страх".
  Значение — целое 0..100.
  Включай только те измерения, которые РАЗЛИЧАЮТ состояние отношений: то есть заметно
  отличаются друг от друга и от "affinity_score". Измерение, чьё значение совпадает с
  affinity_score, — это не измерение, а повтор: выбрось его. Не перечисляй всё подряд и не
  выдумывай измерения, для которых в чате нет оснований. Лучше два говорящих измерения,
  чем пять одинаковых.
- "status", "trust", "tension" — КАЖДОЕ НЕ ДЛИННЕЕ {RELATIONSHIP_TEXT_MAX_CHARS} СИМВОЛОВ
  (одно-два коротких предложения). "status" — где отношения сейчас, "trust" — насколько
  персонаж доверяет пользователю, "tension" — что между ними напряжено или не разрешено.
  Это шкала состояния, а не пересказ последней сцены: обстановку, реплики и детали
  момента сюда писать не надо.
- "key_facts" — не больше 12 пунктов, "goals" и "open_threads" — не больше 6 каждый.
  Достиг предела — сливай или выбрасывай наименее важное, а не удлиняй список.
- КАЖДЫЙ пункт этих трёх списков — не длиннее {RELATIONSHIP_ITEM_MAX_CHARS} символов
  (одно предложение). Пункт, который не умещается в предложение, почти всегда на самом
  деле два пункта или лишняя подробность.
- Текст, превышающий эти лимиты, будет ОБРЕЗАН на полуслове перед сохранением — пиши
  так, чтобы обрезать было нечего.
- Пиши на языке чата.
- {GUARDRAIL}"""

NPC_PROMPT = f"""Ты ведёшь СПИСОК ВТОРОСТЕПЕННЫХ ПЕРСОНАЖЕЙ (NPC) ролевого чата.

Тебе даны: текущий список NPC (JSON) и новые сообщения чата.

Правила:
- НЕ включай двух главных участников диалога — ни того, кто пишет реплики [user],
  ни того, от чьего лица идут реплики [assistant]. Они — не NPC, даже если [assistant]
  пишет о себе в третьем лице по имени. Включай только тех, о ком в чате ГОВОРЯТ или
  кто появляется в сценах эпизодически.
- Верни полный обновлённый список.
- "importance_rank" — значимость для СЮЖЕТА, начиная с 1 (самый значимый).
  Ранги могут меняться при каждом обновлении: NPC, который вышел на первый план,
  поднимается; забытый — опускается. Ранги должны быть уникальными.
- "description" — кто это и чем важен. НЕ ДЛИННЕЕ {NPC_DESCRIPTION_MAX_CHARS} СИМВОЛОВ.
  Превышение будет обрезано на полуслове.
- Пиши на языке чата.
- {GUARDRAIL}"""

POV_NOTES_PROMPT = f"""Ты ведёшь личные заметки персонажа о пользователе — "note to self".

Тебе даны: текущие заметки (JSON) и новые сообщения чата.

Правила:
- Пиши ОТ ЛИЦА персонажа и в его тоне — это его собственные наблюдения, а не досье.
- Записывай только то, что персонаж действительно узнал о пользователе: через
  наблюдение, расспросы или выводы. Не пиши то, чего персонаж знать не может.
- Верни полный обновлённый список заметок. Сливай новое с существующими, не дублируй.
- КАЖДАЯ заметка — НЕ ДЛИННЕЕ {POV_NOTE_MAX_CHARS} СИМВОЛОВ. Только важное.
  Превышение будет обрезано на полуслове.
- Пиши на языке чата.
- {GUARDRAIL}"""


# --------------------------------------------------------------------------- schemas

TIMELINE_SCHEMA = {
    "name": "tracker_timeline",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "time": {"type": "string"},
                        "location": {"type": "string"},
                        "summary": {"type": "string"},
                        "source_message_indices": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                    "required": [
                        "date",
                        "time",
                        "location",
                        "summary",
                        "source_message_indices",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["entries"],
        "additionalProperties": False,
    },
}

RELATIONSHIP_SCHEMA = {
    "name": "tracker_relationship",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "affinity_score": {"type": "integer"},
            "affinity_evidence": {"type": "string"},
            "custom_dimensions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "value": {"type": "integer"},
                    },
                    "required": ["name", "value"],
                    "additionalProperties": False,
                },
            },
            "status": {"type": "string"},
            "trust": {"type": "string"},
            "tension": {"type": "string"},
            "key_facts": {"type": "array", "items": {"type": "string"}},
            "goals": {"type": "array", "items": {"type": "string"}},
            "open_threads": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "affinity_score",
            "affinity_evidence",
            "custom_dimensions",
            "status",
            "trust",
            "tension",
            "key_facts",
            "goals",
            "open_threads",
        ],
        "additionalProperties": False,
    },
}

NPC_SCHEMA = {
    "name": "tracker_npc_whoswho",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "npcs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "importance_rank": {"type": "integer"},
                    },
                    "required": ["name", "description", "importance_rank"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["npcs"],
        "additionalProperties": False,
    },
}

POV_NOTES_SCHEMA = {
    "name": "tracker_character_pov_notes",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"notes": {"type": "array", "items": {"type": "string"}}},
        "required": ["notes"],
        "additionalProperties": False,
    },
}

PROMPTS = {
    "timeline": TIMELINE_PROMPT,
    "relationship": RELATIONSHIP_PROMPT,
    "npc_whoswho": NPC_PROMPT,
    "character_pov_notes": POV_NOTES_PROMPT,
}

SCHEMAS = {
    "timeline": TIMELINE_SCHEMA,
    "relationship": RELATIONSHIP_SCHEMA,
    "npc_whoswho": NPC_SCHEMA,
    "character_pov_notes": POV_NOTES_SCHEMA,
}


def build_prompt(
    tracker_type: str,
    character_name: str | None = None,
    user_name: str | None = None,
) -> str:
    """
    The tracker's prompt, with the two participants named when the caller knows them.

    Naming them matters most for npc_whoswho. The backend only ever sees a numeric
    SillyTavern character_id, and roleplay assistants narrate themselves in the third
    person ("Валерия открыла дверь"), so from the transcript alone a model cannot tell
    the speaking character apart from a character being spoken about - it duly listed
    the main character as an NPC. The names come from the caller (the UI, or the
    extension in Stage D); without them the prompt falls back to describing the two
    participants by their [user]/[assistant] roles.
    """
    prompt = PROMPTS[tracker_type]

    participants = []
    if character_name:
        participants.append(f'Реплики [assistant] — это персонаж по имени "{character_name}".')
    if user_name:
        participants.append(f'Реплики [user] — это пользователь по имени "{user_name}".')
    if not participants:
        return prompt

    return prompt + "\n\nУчастники диалога:\n" + "\n".join(f"- {p}" for p in participants)


# ---------------------------------------------------------------------- date parsing

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    # The chat's language decides the language of the timestamp, so Russian month
    # names (in both nominative and genitive, as they appear in dates) parse too.
    "январь": 1, "января": 1, "февраль": 2, "февраля": 2, "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4, "май": 5, "мая": 5, "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7, "август": 8, "августа": 8, "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10, "ноябрь": 11, "ноября": 11, "декабрь": 12,
    "декабря": 12,
}

_MONTH_NAME_RE = re.compile("|".join(sorted(_MONTHS, key=len, reverse=True)), re.IGNORECASE)
_DAY_RE = re.compile(r"\b(\d{1,2})\b")
_YEAR_RE = re.compile(r"\b(\d{4})\b")
_NUMERIC_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b|\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?", re.IGNORECASE)


def parse_date(raw: str | None) -> date | None:
    """
    Parse a tracker date string as tolerantly as possible; None when it can't be read.

    Deliberately not strptime over a format list: the string comes from an in-game
    timestamp written by whatever model runs the roleplay, so it arrives in whatever
    shape and language that model felt like. A None here is not an error - it just
    means the entry sorts by message order instead of by date.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None

    numeric = _NUMERIC_DATE_RE.search(text)
    if numeric:
        try:
            if numeric.group(1):
                return date(int(numeric.group(1)), int(numeric.group(2)), int(numeric.group(3)))
            return date(int(numeric.group(6)), int(numeric.group(5)), int(numeric.group(4)))
        except ValueError:
            return None

    month_match = _MONTH_NAME_RE.search(text)
    year_match = _YEAR_RE.search(text)
    if not month_match or not year_match:
        return None

    month = _MONTHS[month_match.group(0).lower()]
    year = int(year_match.group(1))

    # Take the first 1-2 digit number that isn't the year itself.
    day = None
    for candidate in _DAY_RE.finditer(text):
        value = int(candidate.group(1))
        if 1 <= value <= 31 and candidate.group(1) != year_match.group(1):
            day = value
            break
    if day is None:
        return None

    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_time(raw: str | None) -> time | None:
    """Parse "7:45 PM" / "19:45" into a time; None when it can't be read."""
    if not raw:
        return None
    match = _TIME_RE.search(raw)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    meridiem = (match.group(3) or "").lower().replace(".", "")

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def find_dates_in_summary(summary: str) -> list[date]:
    """
    Distinct dates mentioned inside a single timeline summary.

    Two of them means the model fused separate days into one entry - the exact bug
    trackers exist to prevent. The entry is kept (dropping it would lose content) but
    the caller logs it, so a prompt that starts regressing is visible rather than
    quietly baked into the document.
    """
    found: list[date] = []
    for match in _MONTH_NAME_RE.finditer(summary or ""):
        window = summary[max(0, match.start() - 12) : match.end() + 12]
        parsed = parse_date(window)
        if parsed is None:
            # A bare "February 13" with no year still names a day; pin it to a
            # sentinel year purely so two different days compare as different.
            month = _MONTHS[match.group(0).lower()]
            day_match = _DAY_RE.search(window)
            if not day_match:
                continue
            try:
                parsed = date(1900, month, int(day_match.group(1)))
            except ValueError:
                continue
        if parsed not in found:
            found.append(parsed)
    return found


# ----------------------------------------------------------------------- normalizing

def normalize_payload(
    tracker_type: str,
    payload: dict,
    exclude_names: list[str | None] | None = None,
) -> list[dict]:
    """
    Turn a raw LLM payload into the tracker's `entries` list.

    Every tracker stores its content as a list of dicts (metadata.tracker_entries), even
    the ones that are conceptually a single document, so that watermarking, rendering and
    the API surface stay uniform across all four types.

    exclude_names drops the two main participants from the NPC list. Asking the model not
    to list them does not work - it was observed listing the main character as the top
    NPC even when the prompt named her explicitly - and this is the whole premise of
    trackers: structure is enforced in Python, not entrusted to the model's compliance.
    """
    if tracker_type == "timeline":
        entries = payload.get("entries") or []
        return [
            {
                **e,
                "summary": _truncate(e["summary"], TIMELINE_SUMMARY_MAX_CHARS),
                "location": _truncate(e.get("location") or "", TIMELINE_LOCATION_MAX_CHARS),
            }
            for e in entries
            if isinstance(e, dict) and (e.get("summary") or "").strip()
        ]

    if tracker_type == "npc_whoswho":
        npcs = payload.get("npcs") or []
        banned = {name.strip().casefold() for name in (exclude_names or []) if name and name.strip()}
        cleaned = [
            {**n, "description": _truncate(n.get("description") or "", NPC_DESCRIPTION_MAX_CHARS)}
            for n in npcs
            if isinstance(n, dict)
            and (n.get("name") or "").strip()
            # Exact match only. A substring test would also eat "Отец Валерии", who is a
            # real NPC precisely because he is *not* Валерия.
            and (n.get("name") or "").strip().casefold() not in banned
        ]
        return sorted(cleaned, key=lambda n: _rank_of(n))

    if tracker_type == "character_pov_notes":
        notes = payload.get("notes") or []
        return [
            {"note": _truncate(n, POV_NOTE_MAX_CHARS)}
            for n in notes
            if isinstance(n, str) and n.strip()
        ]

    if tracker_type == "relationship":
        return [_clamp_relationship(payload)] if isinstance(payload, dict) else []

    return []


def _truncate(text: str, limit: int) -> str:
    """Cut to `limit` characters on a word boundary, marking the cut."""
    stripped = " ".join(str(text or "").split())
    if len(stripped) <= limit:
        return stripped

    cut = stripped[: limit - 1].rstrip()
    last_space = cut.rfind(" ")
    # Only fall back to a mid-word cut if the last word is absurdly long; otherwise a
    # single unbroken token could eat the whole budget.
    if last_space >= limit // 2:
        cut = cut[:last_space].rstrip()
    return f"{cut}…"


def _pick_dimensions(dimensions, affinity_score) -> list[dict]:
    """
    Keep at most RELATIONSHIP_MAX_DIMENSIONS dimensions, preferring the ones that say
    something.

    A dimension whose value just restates affinity_score carries no information but costs a
    line of the injection budget. Observed live: nine dimensions, eight of them at 100/100
    next to an affinity of 100. So dimensions that differ from the affinity score are kept
    first, ordered by how far they diverge from it; the redundant ones only fill leftover
    slots. Order within the payload is not meaningful, so reordering costs nothing.
    """
    if not isinstance(dimensions, list):
        return []

    valid = [
        d
        for d in dimensions
        if isinstance(d, dict)
        and (d.get("name") or "").strip()
        and isinstance(d.get("value"), int)
    ]

    if not isinstance(affinity_score, int):
        return valid[:RELATIONSHIP_MAX_DIMENSIONS]

    return sorted(valid, key=lambda d: -abs(d["value"] - affinity_score))[
        :RELATIONSHIP_MAX_DIMENSIONS
    ]


def _clamp_relationship(payload: dict) -> dict:
    """
    Hard-cap the relationship document's free text in Python.

    The prompt asks for these limits and the model *mostly* obeys - phrasing them as
    character counts rather than sentence counts took affinity_evidence from 403 to 138
    chars - but "mostly" is not a limit. Measured live after three prompt iterations,
    status/trust/tension still ran 187-252 chars against a stated cap of 150. So the cap
    lives here, where it is a fact rather than a request; the prompt keeps its wording so
    the model rarely gets close enough for this to bite. Same reasoning as exclude_names
    in normalize_payload: structure is enforced in Python.
    """
    clamped = dict(payload)

    clamped["custom_dimensions"] = _pick_dimensions(
        payload.get("custom_dimensions"),
        payload.get("affinity_score"),
    )

    for field in RELATIONSHIP_TEXT_FIELDS:
        value = clamped.get(field)
        if isinstance(value, str):
            clamped[field] = _truncate(value, RELATIONSHIP_TEXT_MAX_CHARS)

    for field in RELATIONSHIP_LIST_FIELDS:
        items = clamped.get(field)
        if isinstance(items, list):
            clamped[field] = [
                _truncate(item, RELATIONSHIP_ITEM_MAX_CHARS)
                for item in items
                if isinstance(item, str) and item.strip()
            ]

    return clamped


def _rank_of(npc: dict) -> int:
    rank = npc.get("importance_rank")
    return rank if isinstance(rank, int) else 10**6


def sort_timeline_entries(entries: list[dict]) -> list[dict]:
    """
    Chronological order, decided in Python rather than trusted to the model.

    Entries whose date can't be parsed keep their relative order and sort after the
    dated ones, tie-broken by the earliest source message they came from.
    """
    def key(indexed: tuple[int, dict]) -> tuple:
        position, entry = indexed
        parsed_date = parse_date(entry.get("date"))
        parsed_time = parse_time(entry.get("time"))
        sequences = entry.get("source_sequence_indices") or []
        min_sequence = min(sequences) if sequences else 10**9
        return (
            parsed_date is None,
            parsed_date or date.min,
            parsed_time or time.min,
            min_sequence,
            position,
        )

    return [entry for _, entry in sorted(enumerate(entries), key=key)]


# ------------------------------------------------------------------------- rendering

def render_tracker(tracker_type: str, entries: list[dict]) -> str:
    """Render a tracker's entries into the text that gets stored and injected."""
    if tracker_type == "timeline":
        return _render_timeline(entries)
    if tracker_type == "relationship":
        return _render_relationship(entries)
    if tracker_type == "npc_whoswho":
        return _render_npcs(entries)
    if tracker_type == "character_pov_notes":
        return _render_pov_notes(entries)
    return ""


def _render_timeline(entries: list[dict]) -> str:
    """
    One line per entry, with the stamp rendered compactly.

    The model writes dates as it finds them in the chat ("Tuesday, March 18, 2025, 7:45
    PM"), which is 32 characters of prefix on every single line, before the event itself.
    Re-rendering from the parsed date/time gives "2025-03-18 19:45" - the same information
    in half the space, and the injected block holds correspondingly more events. The raw
    string is kept only when it cannot be parsed, since something is better than nothing.
    """
    lines = []
    for entry in entries:
        raw_date = (entry.get("date") or "").strip()
        raw_time = (entry.get("time") or "").strip()
        parsed_date = parse_date(raw_date)
        parsed_time = parse_time(raw_time)

        stamp_parts = [
            parsed_date.isoformat() if parsed_date else _truncate(raw_date, 20),
            parsed_time.strftime("%H:%M") if parsed_time else _truncate(raw_time, 8),
        ]
        stamp = " ".join(part for part in stamp_parts if part)

        location = (entry.get("location") or "").strip()
        summary = (entry.get("summary") or "").strip()

        prefix = stamp or "—"
        if location:
            lines.append(f"- {prefix} — {location}: {summary}")
        else:
            lines.append(f"- {prefix} — {summary}")
    return "\n".join(lines)


def _render_relationship(entries: list[dict]) -> str:
    if not entries:
        return ""
    doc = entries[0]
    lines = []

    affinity = doc.get("affinity_score")
    if isinstance(affinity, int):
        evidence = (doc.get("affinity_evidence") or "").strip()
        lines.append(f"Affinity: {affinity}/100" + (f" — {evidence}" if evidence else ""))

    for dimension in doc.get("custom_dimensions") or []:
        if not isinstance(dimension, dict):
            continue
        name = (dimension.get("name") or "").strip()
        value = dimension.get("value")
        if name and isinstance(value, int):
            lines.append(f"{name}: {value}/100")

    for label, field in (("Status", "status"), ("Trust", "trust"), ("Tension", "tension")):
        value = (doc.get(field) or "").strip()
        if value:
            lines.append(f"{label}: {value}")

    for label, field in (
        ("Key facts", "key_facts"),
        ("Goals", "goals"),
        ("Open threads", "open_threads"),
    ):
        items = [i.strip() for i in (doc.get(field) or []) if isinstance(i, str) and i.strip()]
        if items:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in items)

    return "\n".join(lines)


def _render_npcs(entries: list[dict]) -> str:
    lines = []
    for position, npc in enumerate(entries, start=1):
        name = (npc.get("name") or "").strip()
        description = (npc.get("description") or "").strip()
        lines.append(f"{position}. {name} — {description}" if description else f"{position}. {name}")
    return "\n".join(lines)


def _render_pov_notes(entries: list[dict]) -> str:
    return "\n".join(f"- {(entry.get('note') or '').strip()}" for entry in entries if entry.get("note"))
