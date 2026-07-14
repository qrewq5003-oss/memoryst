/**
 * Character trackers: lorebook -> character_id resolution, prompt block assembly,
 * char budget, and reminder-toast state.
 *
 * Trackers are NOT regenerated on mention: the block injected here is whatever the
 * backend last stored (fetched via GET /memory/trackers and cached in index.js).
 * Injection uses its own extension-prompt key and never competes with retrieved
 * memories for their budget - see buildTrackerBlock.
 */

export const TRACKER_PROMPT_KEY = 'memory-service-tracker';

export const TRACKER_TYPES = ['timeline', 'relationship', 'npc_whoswho', 'character_pov_notes'];

export const TRACKER_LABELS = {
    timeline: 'Timeline',
    relationship: 'Relationship',
    npc_whoswho: "NPC Who's Who",
    character_pov_notes: 'Character POV Notes',
};

// A relationship doc that fully honours the backend's own limits (12 key_facts + 6 goals
// + 6 open_threads, one sentence each, plus four capped scalar fields) lands around 1.5-2k
// chars on its own - so the previous 1200 guaranteed the client trimmed it every single
// time, budget as primary mechanism rather than backstop. 2000 is the compromise: a
// compact relationship fits whole, and the cap still binds all four of a character's
// trackers together so timeline can't run away.
export const DEFAULT_MAX_TRACKER_CHARS = 2000;
export const DEFAULT_TRACKER_REMINDER_THRESHOLD = 22;
export const MIN_TRACKER_REMINDER_THRESHOLD = 5;

// Timeline is trimmed from the front (oldest events first), so the surviving text must
// say that history was cut - otherwise the model reads a truncated chronology as if it
// were the whole one.
export const TRACKER_OMITTED_MARKER = '- …(ранее опущено)';

const TRACKER_MARKER_RE = /@memory-tracker\s*:\s*([^\n]+)/i;
// An explicit marker beats a name match beats the solo-chat fallback - regardless of which
// lorebook entry happened to be resolved first.
// 'always' is the current character injected unconditionally (see mergeTrackerMatches); it is
// the weakest claim, so anything the lorebook says about the same character overrides how the
// match is reported - but never removes it.
const SOURCE_PRIORITY = { marker: 3, name: 2, fallback: 1, always: 0 };
const RELATIONSHIP_LIST_HEADERS = ['Key facts:', 'Goals:', 'Open threads:'];

function normalizeWhitespace(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
}

function foldCase(text) {
    return normalizeWhitespace(text).toLowerCase();
}

function getEntryComment(entry = {}) {
    return entry.comment ?? entry.label ?? entry.title ?? entry.name ?? '';
}

function getEntryContent(entry = {}) {
    return entry.content ?? entry.entry ?? entry.text ?? entry.value ?? '';
}

function getEntryId(entry = {}) {
    return String(entry.uid ?? entry.id ?? entry.world_info_uid ?? getEntryComment(entry) ?? '');
}

function getEntryKeys(entry = {}) {
    const raw = entry.key ?? entry.keys ?? entry.keywords ?? [];
    if (Array.isArray(raw)) {
        return raw.map(key => String(key)).filter(Boolean);
    }
    if (typeof raw === 'string') {
        return raw.split(',').map(key => key.trim()).filter(Boolean);
    }
    return [];
}

function escapeRegExp(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Word-boundary match that works for Cyrillic. JS `\b` is ASCII-only, so "Валерия"
 * would fail to anchor at all and any substring test would match "Валерию" inside
 * "Валериус" - hence explicit letter/digit lookarounds.
 */
function containsName(haystack, name) {
    if (!haystack || !name) {
        return false;
    }
    const pattern = new RegExp(`(?<![\\p{L}\\p{N}_])${escapeRegExp(name)}(?![\\p{L}\\p{N}_])`, 'iu');
    return pattern.test(haystack);
}

/**
 * name -> SillyTavern character index, as a string, because memoryst's character_id is
 * that index stringified (see scope.mjs). Longest names first so "Валерия Ким" wins over
 * "Валерия" when both exist.
 */
export function buildCharacterNameIndex(characters = [], currentCharacterId = null, currentCharacterName = null) {
    const byName = [];
    const currentName = normalizeWhitespace(currentCharacterName);

    (characters || []).forEach((character, index) => {
        const name = normalizeWhitespace(character?.name);
        if (!name) {
            return;
        }
        // The chat scope's id wins over the array position for the character of this chat.
        const isCurrent = currentName && foldCase(name) === foldCase(currentName);
        byName.push({
            name,
            characterId: isCurrent && currentCharacterId ? String(currentCharacterId) : String(index),
        });
    });

    if (currentName && currentCharacterId && !byName.some(c => foldCase(c.name) === foldCase(currentName))) {
        // The roster did not even contain the current character (an empty or unusual context):
        // the name we are certain about is still worth matching on.
        byName.push({ name: currentName, characterId: String(currentCharacterId) });
    }

    return byName.sort((a, b) => b.name.length - a.name.length);
}

function resolveMarker(entry, nameIndex) {
    const source = `${getEntryComment(entry)}\n${getEntryContent(entry)}`;
    const match = source.match(TRACKER_MARKER_RE);
    if (!match) {
        return null;
    }

    const token = normalizeWhitespace(match[1]);
    if (!token) {
        return null;
    }

    // "@memory-tracker: 20" is already a character_id; anything else is a name to look up.
    if (/^\d+$/.test(token)) {
        const known = nameIndex.find(candidate => candidate.characterId === token);
        return { characterId: token, characterName: known?.name || null, source: 'marker' };
    }

    const exact = nameIndex.find(candidate => foldCase(candidate.name) === foldCase(token));
    if (exact) {
        return { characterId: exact.characterId, characterName: exact.name, source: 'marker' };
    }

    // "@memory-tracker: Валерия" against a card named "Валерия Мендоса" is the obvious
    // intent, and demanding the full card name character-for-character is a trap: the
    // marker silently stops working and nothing says why.
    const partial = nameIndex.find(candidate =>
        containsName(candidate.name, token) || containsName(token, candidate.name));
    if (partial) {
        return { characterId: partial.characterId, characterName: partial.name, source: 'marker' };
    }

    return { characterId: null, characterName: token, source: 'marker_unresolved' };
}

function resolveByName(entry, nameIndex) {
    const haystacks = [getEntryComment(entry), ...getEntryKeys(entry)]
        .map(value => normalizeWhitespace(value))
        .filter(Boolean);

    for (const candidate of nameIndex) {
        if (haystacks.some(haystack => containsName(haystack, candidate.name))) {
            return { characterId: candidate.characterId, characterName: candidate.name, source: 'name' };
        }
    }

    return null;
}

/**
 * Which characters the activated lorebook entries point at, in priority order per entry:
 * explicit @memory-tracker marker, then character name, then (solo chats only) the
 * current character.
 *
 * Returns one record per distinct character_id, first resolution wins.
 */
export function resolveTrackerCharacterIds({
    entries = [],
    characters = [],
    currentCharacterId = null,
    currentCharacterName = null,
    isGroupChat = false,
} = {}) {
    // The roster index is what a name resolves to, and SillyTavern's index is not stable
    // across deletions and reordering - the spec admits as much. So when a marker or a key
    // names the character we are actually talking to, take the id from the chat scope, which
    // is authoritative, instead of trusting the position in the array. Otherwise a marker
    // could point at the wrong character_id, find no trackers there, and end up *worse* than
    // no marker at all.
    const nameIndex = buildCharacterNameIndex(characters, currentCharacterId, currentCharacterName);
    const resolved = new Map();
    const unresolved = [];

    for (const entry of entries || []) {
        const marker = resolveMarker(entry, nameIndex);

        // A marker naming somebody we cannot map to a character index must NOT suppress the
        // other branches. It did, and the result was the worst possible failure: adding a
        // marker to a lorebook entry turned tracker injection off entirely, silently, with
        // the audit showing an empty match list and no reason. A marker is a hint about who
        // the entry is about, not a veto on the entry.
        if (marker && !marker.characterId) {
            unresolved.push({ entryId: getEntryId(entry), reason: marker.source, token: marker.characterName });
        }

        const match = (marker?.characterId ? marker : null)
            || resolveByName(entry, nameIndex)
            // A solo chat has exactly one character the entry could possibly be about, so
            // an unmatched entry still means "these trackers are relevant now".
            || (!isGroupChat && currentCharacterId
                ? { characterId: String(currentCharacterId), characterName: null, source: 'fallback' }
                : null);

        if (!match || !match.characterId) {
            if (!marker) {
                unresolved.push({ entryId: getEntryId(entry), reason: 'no_match' });
            }
            continue;
        }

        const existing = resolved.get(match.characterId);
        if (existing) {
            existing.entryIds.push(getEntryId(entry));
            // The strongest resolution wins, not the first one seen. A lorebook fires several
            // entries at once and their order is SillyTavern's business: live, a plain entry
            // resolved to the current character by fallback, the very next entry named the
            // same character by explicit marker, and the fallback kept the slot - so the
            // marker looked ignored and the block lost its heading name.
            if (SOURCE_PRIORITY[match.source] > SOURCE_PRIORITY[existing.source]) {
                existing.source = match.source;
                existing.characterName = match.characterName || existing.characterName;
            } else {
                existing.characterName = existing.characterName || match.characterName;
            }
            continue;
        }

        resolved.set(match.characterId, {
            characterId: match.characterId,
            characterName: match.characterName,
            source: match.source,
            entryIds: [getEntryId(entry)],
        });
    }

    return { matches: [...resolved.values()], unresolved };
}

/**
 * Fold lorebook-resolved matches into a base list (typically the current character, injected
 * unconditionally), keeping the strongest resolution for each character.
 *
 * The lorebook path stays exactly as it was - it is what can pull in a *secondary* character's
 * trackers by marker or name. What changes is that the main character no longer depends on it:
 * lorebook entries created by STMemoryBooks are vectorized, so they only fire when a semantic
 * search happens to surface them, and the tracker of the character you are actually talking to
 * would reach the prompt only on those turns.
 */
export function mergeTrackerMatches(base = [], extra = []) {
    const merged = new Map();

    for (const match of [...base, ...extra]) {
        if (!match?.characterId) {
            continue;
        }
        const existing = merged.get(match.characterId);
        if (!existing) {
            merged.set(match.characterId, { ...match, entryIds: [...(match.entryIds || [])] });
            continue;
        }

        existing.entryIds.push(...(match.entryIds || []));
        if (SOURCE_PRIORITY[match.source] > SOURCE_PRIORITY[existing.source]) {
            existing.source = match.source;
            existing.characterName = match.characterName || existing.characterName;
        } else {
            existing.characterName = existing.characterName || match.characterName;
        }
    }

    return [...merged.values()];
}

// ------------------------------------------------------------------ char budget

function splitLines(content) {
    return String(content || '')
        .split('\n')
        .map(line => line.trimEnd())
        .filter(line => line.trim());
}

/**
 * Remove the least valuable single unit from one tracker's rendered text, per that
 * tracker's own semantics. Returns null when nothing can be dropped without destroying
 * the tracker's meaning (the caller then drops the whole section).
 */
export function dropOneTrackerUnit(trackerType, content) {
    const lines = splitLines(content);
    if (!lines.length) {
        return null;
    }

    if (trackerType === 'timeline') {
        // Oldest first: recent chronology is what the current scene needs.
        const events = lines.filter(line => line !== TRACKER_OMITTED_MARKER);
        if (events.length <= 1) {
            return null;
        }
        return [TRACKER_OMITTED_MARKER, ...events.slice(1)].join('\n');
    }

    if (trackerType === 'npc_whoswho' || trackerType === 'character_pov_notes') {
        // Both are ordered by importance already (NPCs by rank, notes by the model), so
        // the tail is the cheapest thing to lose.
        if (lines.length <= 1) {
            return null;
        }
        return lines.slice(0, -1).join('\n');
    }

    if (trackerType === 'relationship') {
        // Only the list sections shrink; affinity/status/trust/tension are the point of
        // the tracker and are never dropped here.
        const lastItem = lines.map(line => line.startsWith('- ')).lastIndexOf(true);
        if (lastItem === -1) {
            return null;
        }
        const kept = lines.filter((_, index) => index !== lastItem);
        const pruned = kept.filter((line, index) => {
            if (!RELATIONSHIP_LIST_HEADERS.includes(line)) {
                return true;
            }
            return (kept[index + 1] || '').startsWith('- ');
        });
        return pruned.length ? pruned.join('\n') : null;
    }

    return null;
}

function formatTrackerSections(sections) {
    return sections.flatMap(section => [`[${TRACKER_LABELS[section.trackerType]}]`, section.content]);
}

function truncateTrackerContent(content, available) {
    const normalized = String(content || '').trim();
    if (available <= 1 || !normalized) {
        return '';
    }
    if (normalized.length <= available) {
        return normalized;
    }
    const cut = normalized.slice(0, Math.max(0, available - 1)).trimEnd();
    return cut ? `${cut}…` : '';
}

function formatCharacterBlock(characterName, sections) {
    const heading = characterName
        ? `[Character Tracker: ${characterName}]`
        : '[Character Tracker]';
    return [heading, ...formatTrackerSections(sections)].join('\n');
}

/**
 * One character's trackers, trimmed to fit `maxChars` in total.
 *
 * The budget is per character and shared across all four trackers (not one budget each),
 * so a chat where every tracker is full can't quietly triple the injected prompt. Shrink
 * the currently largest tracker first, one unit at a time, so all four survive as long as
 * possible; only when nothing can shrink further do whole trackers get dropped, least
 * important first.
 */
export function buildCharacterTrackerBlock({
    trackers = [],
    characterName = null,
    maxChars = DEFAULT_MAX_TRACKER_CHARS,
} = {}) {
    const trimReasons = [];
    let sections = TRACKER_TYPES
        .map(trackerType => {
            const tracker = trackers.find(item => item?.tracker_type === trackerType);
            const content = splitLines(tracker?.content).join('\n');
            return content ? { trackerType, content } : null;
        })
        .filter(Boolean);

    if (!sections.length) {
        return { block: '', sections: [], trimReasons, actualChars: 0 };
    }

    // Least important first: POV notes are the character's own colour, the relationship
    // status is the one thing a mention of this character most needs in the prompt.
    const dropOrder = ['character_pov_notes', 'npc_whoswho', 'timeline', 'relationship'];

    let block = formatCharacterBlock(characterName, sections);
    while (block.length > maxChars && sections.length) {
        const shrinkable = [...sections]
            .filter(section => dropOneTrackerUnit(section.trackerType, section.content) !== null)
            .sort((a, b) => b.content.length - a.content.length);

        if (shrinkable.length) {
            const target = shrinkable[0];
            target.content = dropOneTrackerUnit(target.trackerType, target.content);
            trimReasons.push(`char_budget_trim:${target.trackerType}`);
        } else if (sections.length > 1) {
            const victim = dropOrder.find(trackerType =>
                sections.some(section => section.trackerType === trackerType));
            if (!victim) {
                break;
            }
            sections = sections.filter(section => section.trackerType !== victim);
            trimReasons.push(`char_budget_dropped:${victim}`);
        } else {
            // The last surviving tracker still overflows with nothing left to drop - its
            // own non-list lines are simply too long. A relationship doc observed live ran
            // to 8.4k chars, most of it an essay in affinity_evidence and a paragraph of
            // status. Cut it to fit rather than drop it: a truncated tracker in the prompt
            // beats no tracker at all, which is what dropping the only section produced.
            const only = sections[0];
            const overhead = formatCharacterBlock(characterName, [{ ...only, content: '' }]).length;
            const truncated = truncateTrackerContent(only.content, maxChars - overhead);

            if (!truncated) {
                sections = [];
                trimReasons.push(`char_budget_dropped:${only.trackerType}`);
            } else {
                only.content = truncated;
                trimReasons.push(`char_budget_truncated:${only.trackerType}`);
            }
        }

        block = sections.length ? formatCharacterBlock(characterName, sections) : '';
    }

    return {
        block,
        sections,
        trimReasons,
        actualChars: block.length,
    };
}

/**
 * The full injected block: every matched character's trackers, each budgeted separately.
 */
export function buildTrackerBlock({
    matches = [],
    trackersByCharacter = {},
    maxTrackerChars = DEFAULT_MAX_TRACKER_CHARS,
} = {}) {
    const blocks = [];
    const includedCharacters = [];
    const trimReasons = [];

    for (const match of matches) {
        const trackers = trackersByCharacter[match.characterId] || [];
        const built = buildCharacterTrackerBlock({
            trackers,
            characterName: match.characterName,
            maxChars: maxTrackerChars,
        });

        if (!built.block) {
            continue;
        }

        blocks.push(built.block);
        includedCharacters.push({
            characterId: match.characterId,
            characterName: match.characterName,
            // Which resolution branch won: 'marker' | 'name' | 'fallback'. Surfaced all the
            // way into the audit record, because "the tracker was injected" and "the
            // @memory-tracker marker is what injected it" are different claims, and only
            // the audit can tell them apart after the fact.
            source: match.source,
            trackerTypes: built.sections.map(section => section.trackerType),
            chars: built.actualChars,
        });
        trimReasons.push(...built.trimReasons);
    }

    const trackerBlock = blocks.join('\n\n');
    return {
        trackerBlock,
        trackerCharCount: trackerBlock.length,
        includedCharacters,
        trimReasons,
    };
}

// ------------------------------------------------------------------ reminder toasts

export function buildTrackerToastKey({ chatId, characterId, trackerType }) {
    return `${chatId || ''}::${characterId || ''}::${trackerType}`;
}

/**
 * Which trackers are stale enough to nag about, given what we last nagged about.
 *
 * The counters come free with every /memory/store response, so this costs no extra
 * request. A tracker that already fired a toast stays quiet until it drifts another full
 * threshold's worth of messages - otherwise every message past the threshold would fire
 * one. The returned state is persisted in extension_settings, so the quiet period
 * survives a page reload.
 */
export function evaluateTrackerToasts({
    trackers = [],
    chatId = null,
    characterId = null,
    threshold = DEFAULT_TRACKER_REMINDER_THRESHOLD,
    lastTrackerToastAt = {},
    characterName = null,
} = {}) {
    const effectiveThreshold = Math.max(MIN_TRACKER_REMINDER_THRESHOLD, Number(threshold) || 0);
    const nextLastTrackerToastAt = { ...lastTrackerToastAt };
    const toasts = [];

    for (const tracker of trackers || []) {
        const trackerType = tracker?.tracker_type;
        if (!TRACKER_TYPES.includes(trackerType)) {
            continue;
        }

        const messagesSinceUpdate = Number(tracker.messages_since_update) || 0;
        const key = buildTrackerToastKey({ chatId, characterId, trackerType });
        const previous = Number(nextLastTrackerToastAt[key]) || 0;
        // The counter went backwards, so the tracker was updated: forget that we nagged.
        const lastToastAt = messagesSinceUpdate < previous ? 0 : previous;

        if (messagesSinceUpdate < lastToastAt + effectiveThreshold) {
            if (lastToastAt !== previous) {
                delete nextLastTrackerToastAt[key];
            }
            continue;
        }

        nextLastTrackerToastAt[key] = messagesSinceUpdate;
        toasts.push({
            trackerType,
            messagesSinceUpdate,
            message: characterName
                ? `Пора обновить трекер: ${TRACKER_LABELS[trackerType]} (${characterName})`
                : `Пора обновить трекер: ${TRACKER_LABELS[trackerType]}`,
        });
    }

    return { toasts, lastTrackerToastAt: nextLastTrackerToastAt };
}

// ------------------------------------------------------------------ backend fetch

export async function fetchTrackers({
    memoryServiceUrl,
    apiKey = '',
    chatId,
    characterId,
    fetchImpl = typeof fetch !== 'undefined' ? fetch : undefined,
} = {}) {
    if (!memoryServiceUrl || !chatId || !characterId || typeof fetchImpl !== 'function') {
        return [];
    }

    const headers = {};
    if (apiKey) {
        headers['X-API-Key'] = apiKey;
    }

    const query = new URLSearchParams({ chat_id: chatId, character_id: String(characterId) });
    const response = await fetchImpl(`${memoryServiceUrl}/memory/trackers?${query}`, { headers });
    if (!response.ok) {
        throw new Error(`trackers_http_${response.status}`);
    }

    const data = await response.json();
    return Array.isArray(data.items) ? data.items : [];
}
