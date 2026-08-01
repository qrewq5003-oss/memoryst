export const DEFAULT_AUDIT_MAX_RECORDS = 20;
export const DEFAULT_AUDIT_PREVIEW_CHARS = 240;
export const DEFAULT_MAX_PROMPT_MEMORIES = 4;
export const DEFAULT_MAX_PROMPT_CHARS = 1500;
export const DEFAULT_MAX_SUMMARY_ITEMS = 1;
export const DEFAULT_MAX_STABLE_ITEMS = 2;
export const DEFAULT_MAX_EPISODIC_ITEMS = 1;
export const PRE_GENERATION_HOOK_CANDIDATES = [
    'GENERATE_BEFORE_COMBINE_PROMPTS',
    'GENERATION_AFTER_COMMANDS',
    'GENERATION_STARTED',
];
const MEMORY_LINE_MAX_CHARS = 110;
const TRIM_LAYER_ORDER = ['episodic', 'stable', 'summary'];

export function nowIso() {
    return new Date().toISOString();
}

export function previewText(text, maxChars = DEFAULT_AUDIT_PREVIEW_CHARS) {
    if (!text) {
        return '';
    }
    const normalized = String(text).replace(/\s+/g, ' ').trim();
    if (normalized.length <= maxChars) {
        return normalized;
    }
    return `${normalized.slice(0, maxChars)}...`;
}

export function countMemoryBlockItems(memoryBlock) {
    if (!memoryBlock) {
        return 0;
    }
    return memoryBlock.split('\n').filter(line => line.trim().startsWith('- ')).length;
}

export function getMemoryLayer(item = {}) {
    if (item.type === 'summary' || item?.metadata?.is_summary) {
        return 'summary';
    }
    if (item.layer === 'stable') {
        return 'stable';
    }
    return 'episodic';
}

export function countItemsByLayer(items = []) {
    const counts = {
        summary: 0,
        stable: 0,
        episodic: 0,
    };
    for (const item of items) {
        counts[getMemoryLayer(item)] += 1;
    }
    return counts;
}

function truncateContent(text, maxChars = MEMORY_LINE_MAX_CHARS) {
    if (!text) {
        return '';
    }
    const normalized = String(text).replace(/\s+/g, ' ').trim();
    if (normalized.length <= maxChars) {
        return normalized;
    }
    const truncated = normalized.slice(0, maxChars).trimEnd();
    const lastSpace = truncated.lastIndexOf(' ');
    const safe = lastSpace >= Math.floor(maxChars / 2) ? truncated.slice(0, lastSpace) : truncated;
    return `${safe.trimEnd()}...`;
}

function formatMemoryLabels(item = {}) {
    const labels = [];
    if (item.pinned) {
        labels.push('[PINNED]');
    }
    const layer = getMemoryLayer(item);
    if (layer === 'summary') {
        labels.push('[SUMMARY]');
    } else if (layer === 'stable') {
        labels.push('[STABLE]');
    } else {
        labels.push('[EPISODIC]');
    }
    return labels.join(' ');
}

function formatMemoryBlockFromItems(items = []) {
    if (!items.length) {
        return '';
    }
    const lines = ['[Relevant Memory]'];
    for (const item of items) {
        lines.push(`- ${formatMemoryLabels(item)} ${truncateContent(item.content || '')}`);
    }
    return lines.join('\n');
}

function removeOneByTrimPriority(items = []) {
    for (const layer of TRIM_LAYER_ORDER) {
        for (let index = items.length - 1; index >= 0; index -= 1) {
            if (getMemoryLayer(items[index]) === layer) {
                return items.splice(index, 1)[0];
            }
        }
    }
    return items.pop() || null;
}

export function buildBudgetedMemoryBlock({
    items = [],
    maxPromptMemories = DEFAULT_MAX_PROMPT_MEMORIES,
    maxPromptChars = DEFAULT_MAX_PROMPT_CHARS,
    maxSummaryItems = DEFAULT_MAX_SUMMARY_ITEMS,
    maxStableItems = DEFAULT_MAX_STABLE_ITEMS,
    maxEpisodicItems = DEFAULT_MAX_EPISODIC_ITEMS,
} = {}) {
    const layerCaps = {
        summary: maxSummaryItems,
        stable: maxStableItems,
        episodic: maxEpisodicItems,
    };
    const selectedPerLayer = {
        summary: [],
        stable: [],
        episodic: [],
    };
    const trimmedItems = [];
    const trimReasons = [];

    for (const item of items) {
        const layer = getMemoryLayer(item);
        if (selectedPerLayer[layer].length < layerCaps[layer]) {
            selectedPerLayer[layer].push(item);
        } else {
            trimmedItems.push(item);
            trimReasons.push(`layer_cap:${layer}`);
        }
    }

    let selectedItems = items.filter(item =>
        selectedPerLayer[getMemoryLayer(item)].some(kept => kept.id === item.id)
    );

    while (selectedItems.length > maxPromptMemories) {
        const removed = removeOneByTrimPriority(selectedItems);
        if (!removed) {
            break;
        }
        trimmedItems.push(removed);
        trimReasons.push(`item_cap:${getMemoryLayer(removed)}`);
    }

    let memoryBlock = formatMemoryBlockFromItems(selectedItems);
    while (selectedItems.length > 0 && memoryBlock.length > maxPromptChars) {
        const removed = removeOneByTrimPriority(selectedItems);
        if (!removed) {
            break;
        }
        trimmedItems.push(removed);
        trimReasons.push(`char_budget:${getMemoryLayer(removed)}`);
        memoryBlock = formatMemoryBlockFromItems(selectedItems);
    }

    return {
        memoryBlock,
        selectedItems,
        trimmedItems,
        retrievedItemCount: items.length,
        injectedItemCount: selectedItems.length,
        retrievedByLayer: countItemsByLayer(items),
        injectedByLayer: countItemsByLayer(selectedItems),
        trimmedByLayer: countItemsByLayer(trimmedItems),
        trimmedItemCount: trimmedItems.length,
        trimReasons,
        budget: {
            maxPromptMemories,
            maxPromptChars,
            maxSummaryItems,
            maxStableItems,
            maxEpisodicItems,
        },
        actualChars: memoryBlock.length,
    };
}

export function buildMessageAuditEntries(messages, previewChars = DEFAULT_AUDIT_PREVIEW_CHARS) {
    return (messages || []).map(message => ({
        role: message.role || 'unknown',
        text_length: (message.text || '').length,
        text_preview: previewText(message.text || '', previewChars),
    }));
}

export function createIntegrationAuditRecord({
    extensionBuild = null,
    chatId,
    characterId,
    groupId = null,
    chatScopeSource = null,
    characterScopeSource = null,
    recentMessagesCount,
}) {
    return {
        interaction_id: `${chatId || 'chat'}:${Date.now()}`,
        timestamp: nowIso(),
        extension_build: extensionBuild,
        chat_id: chatId || null,
        character_id: characterId || null,
        group_id: groupId || null,
        chat_scope_source: chatScopeSource || null,
        character_scope_source: characterScopeSource || null,
        loop_pattern: 'pre_generation_retrieve_current_turn',
        recent_messages_count: recentMessagesCount,
        store_called: false,
        retrieve_called: false,
        prompt_insertion_observed: false,
        retrieve_stage: null,
        prompt_injection_stage: null,
        applied_to_current_turn: false,
        store: null,
        retrieve: null,
        prompt_insertion: null,
        notes: [],
    };
}

export function buildStoreAuditSection({
    messages,
    result,
    error = null,
    previewChars = DEFAULT_AUDIT_PREVIEW_CHARS,
}) {
    return {
        message_count: (messages || []).length,
        messages: buildMessageAuditEntries(messages || [], previewChars),
        stored: result?.stored ?? 0,
        updated: result?.updated ?? 0,
        skipped: result?.skipped ?? 0,
        stored_item_count: result?.items?.length ?? 0,
        extraction_method: result?.extraction_method ?? null,
        debug_present: Boolean(result?.debug),
        error: error ? String(error) : null,
    };
}

export function buildRetrieveAuditSection({
    userInput,
    recentMessages,
    result,
    error = null,
    previewChars = DEFAULT_AUDIT_PREVIEW_CHARS,
    stage = 'pre_generation',
    budget = null,
}) {
    const memoryBlock = result?.memory_block || '';
    const retrievedItems = result?.items || [];
    return {
        stage,
        user_input_length: (userInput || '').length,
        user_input_preview: previewText(userInput || '', previewChars),
        recent_message_count: (recentMessages || []).length,
        recent_messages: buildMessageAuditEntries(recentMessages || [], previewChars),
        returned_item_count: retrievedItems.length,
        returned_summary_count: countItemsByLayer(retrievedItems).summary,
        returned_stable_count: countItemsByLayer(retrievedItems).stable,
        returned_episodic_count: countItemsByLayer(retrievedItems).episodic,
        total_candidates: result?.total_candidates ?? 0,
        memory_block_length: memoryBlock.length,
        memory_block_item_count: countMemoryBlockItems(memoryBlock),
        memory_block_preview: previewText(memoryBlock, previewChars),
        budget_applied: Boolean(budget),
        budgeted_item_count: budget?.injectedItemCount ?? null,
        trimmed_item_count: budget?.trimmedItemCount ?? null,
        debug_present: Boolean(result?.debug),
        error: error ? String(error) : null,
    };
}

export function buildPromptInsertionAuditSection({
    memoryBlock,
    applied,
    reason,
    previewChars = DEFAULT_AUDIT_PREVIEW_CHARS,
    stage = 'pre_generation',
    appliedToCurrentTurn = true,
    budget = null,
    loreAnchorBlock = '',
    loreAnchorItemCount = 0,
    trackerBlock = '',
    trackerSubjectCount = 0,
    trackerMatchSources = [],
    trackerUnresolved = [],
    trackerRosterSize = null,
    trackerLorebookEntryCount = null,
    trackerError = null,
    trackerWiEventCount = 0,
    trackerEventTrace = [],
    trackerEntryComments = [],
}) {
    return {
        applied: Boolean(applied),
        role: 'system',
        applied_to_current_turn: Boolean(appliedToCurrentTurn),
        stage,
        insertion_timing: appliedToCurrentTurn ? 'current_generation_pre_prompt' : 'next_generation_post_render',
        insertion_method: 'setExtensionPrompt',
        memory_block_length: (memoryBlock || '').length,
        memory_block_item_count: countMemoryBlockItems(memoryBlock || ''),
        memory_block_preview: previewText(memoryBlock || '', previewChars),
        lore_anchor_applied: Boolean(loreAnchorBlock),
        lore_anchor_length: (loreAnchorBlock || '').length,
        lore_anchor_item_count: loreAnchorItemCount || 0,
        lore_anchor_preview: previewText(loreAnchorBlock || '', previewChars),
        tracker_applied: Boolean(trackerBlock),
        tracker_block_length: (trackerBlock || '').length,
        // How many characters' trackers made it in - not a char count; see tracker_block_length.
        tracker_subject_count: trackerSubjectCount || 0,
        // 'marker' | 'name' | 'fallback' per injected character - which lorebook resolution
        // branch actually fired, so the marker path can be told apart from the fallback.
        tracker_match_sources: trackerMatchSources || [],
        // Why a lorebook entry produced no tracker: an unresolvable @memory-tracker marker,
        // or an empty character roster (tracker_roster_size 0 means getContext().characters
        // gave us nothing to map names against).
        tracker_unresolved: trackerUnresolved || [],
        tracker_roster_size: trackerRosterSize,
        // null = the lorebook handler never ran this turn (no WORLD_INFO_ACTIVATED at all),
        // which is a different failure from "it ran and matched nobody".
        tracker_lorebook_entry_count: trackerLorebookEntryCount,
        // Set when tracker injection threw. SillyTavern's event bus swallows listener
        // exceptions, so without this a crash and a no-op look identical.
        tracker_error: trackerError,
        // 0 means SillyTavern never called our WORLD_INFO_ACTIVATED listener at all.
        tracker_wi_event_count: trackerWiEventCount,
        // The actual order of pre-generation hooks, lorebook activation and prompt clears
        // during this turn.
        tracker_event_trace: trackerEventTrace,
        tracker_entry_comments: trackerEntryComments,
        tracker_block_preview: previewText(trackerBlock || '', previewChars),
        injected_summary_count: budget?.injectedByLayer?.summary ?? null,
        injected_stable_count: budget?.injectedByLayer?.stable ?? null,
        injected_episodic_count: budget?.injectedByLayer?.episodic ?? null,
        trimmed_item_count: budget?.trimmedItemCount ?? 0,
        trimmed_summary_count: budget?.trimmedByLayer?.summary ?? 0,
        trimmed_stable_count: budget?.trimmedByLayer?.stable ?? 0,
        trimmed_episodic_count: budget?.trimmedByLayer?.episodic ?? 0,
        trim_reasons: budget?.trimReasons ?? [],
        max_prompt_memories: budget?.budget?.maxPromptMemories ?? null,
        max_prompt_chars: budget?.budget?.maxPromptChars ?? null,
        actual_prompt_chars: budget?.actualChars ?? (memoryBlock || '').length,
        reason: reason || null,
    };
}

export function finalizeIntegrationAuditRecord(record) {
    const notes = [...(record.notes || [])];

    if (!record.store_called) {
        notes.push('store_not_called');
    }
    if (!record.retrieve_called) {
        notes.push('retrieve_not_called');
    }
    if (!record.prompt_insertion_observed) {
        notes.push('prompt_insertion_not_observed');
    }
    if (record.retrieve_stage !== 'pre_generation') {
        notes.push('retrieve_not_confirmed_pre_generation');
    }
    if (record.prompt_injection_stage !== 'pre_generation') {
        notes.push('prompt_not_confirmed_current_turn');
    }
    if (record.retrieve && record.retrieve.memory_block_length === 0) {
        notes.push('empty_memory_block');
    }
    if (record.retrieve && record.retrieve.returned_item_count > 0 && record.prompt_insertion?.applied === false) {
        notes.push('retrieved_items_but_prompt_not_applied');
    }
    if (record.prompt_insertion?.trimmed_item_count > 0) {
        notes.push('memory_block_trimmed_by_budget');
    }
    if (record.prompt_insertion?.lore_anchor_applied) {
        notes.push('lore_anchor_applied');
    }

    return {
        ...record,
        notes,
    };
}

export function pushAuditRecord(settings, record) {
    const maxRecords = settings.auditMaxRecords || DEFAULT_AUDIT_MAX_RECORDS;
    const recentAudits = settings.recentAudits || [];
    settings.recentAudits = [record, ...recentAudits].slice(0, maxRecords);
}

/**
 * SillyTavern runs a dry-run generation pass (for token counting) before the real one, and
 * emits the same pre-generation hooks for it - with dryRun=true as the third argument of
 * GENERATION_STARTED / GENERATION_AFTER_COMMANDS. Two things go wrong if we treat it as a
 * real turn, and both did:
 *
 *  - the dry run's chat state does not yet hold the new user message, so the turn key is
 *    built from the *previous* one. The real pass then looks like a different turn, sails
 *    past the de-dupe guard, and calls clearTrackerPrompt() - wiping the tracker block that
 *    WORLD_INFO_ACTIVATED had just set (the lorebook does not fire on dry runs, so this
 *    always lands after it). That is what kept trackers out of the prompt.
 *  - /memory/retrieve was issued for the dry run too, querying with the stale user message.
 */
export function isDryRun(hookArgs) {
    return hookArgs.length >= 3 && hookArgs[2] === true;
}

/**
 * Does this generation still have to append the user's new message?
 *
 * Verified against SillyTavern's script.js: inside one Generate() call,
 * GENERATION_STARTED (4240) and GENERATION_AFTER_COMMANDS (4262) are both emitted
 * *before* sendMessageAsUser() (4394) puts the new message into `chat`. So on a normal
 * turn the pre-generation hooks see the *previous* message as the last one, and
 * retrieving there queries the wrong text - every turn, silently. Answering true means
 * "wait for MESSAGE_SENT instead", which fires from inside sendMessageAsUser and is
 * awaited by Generate, still ahead of prompt assembly (5073+).
 *
 * Deliberately conservative: only the well-understood normal turn defers. Swipe,
 * regenerate, continue and impersonate append nothing, so the last message in `chat`
 * is already the right one and the pre-generation path stays correct for them.
 * Anything unrecognised also retrieves at pre-generation - degrading to the old
 * behaviour beats degrading to no memory at all.
 */
export function willAppendUserMessage(hookArgs) {
    const type = hookArgs[0];
    const options = hookArgs[1] || {};
    if (options.automatic_trigger) {
        return false;
    }
    return type === undefined || type === null || type === 'normal';
}

export function resolvePreGenerationHookNames(eventTypes = {}) {
    const resolved = new Set();
    for (const name of PRE_GENERATION_HOOK_CANDIDATES) {
        resolved.add(eventTypes?.[name] || name);
    }
    return [...resolved];
}

/**
 * Identifies one generation, so the several pre-generation hooks we listen on do the work
 * once instead of once each.
 *
 * Deliberately does NOT include the chat length. SillyTavern pushes a placeholder message
 * for the reply into `chat` while generating, so the length changes *between* the hooks of
 * a single turn: the key changed mid-generation, the guard missed, and the later hook ran
 * the whole path again - re-issuing /memory/retrieve and, worse, clearing the tracker block
 * that WORLD_INFO_ACTIVATED had just set (the lorebook event fires between the two hooks).
 * That is what kept the injected tracker out of the prompt.
 *
 * Repeating the same user input on a later turn is not a problem: onMessageRendered resets
 * the pending key, so the guard only ever suppresses work inside one generation.
 */
export function buildTurnKey({ chatId, characterId, userInput }) {
    return [chatId || '', characterId || '', userInput || ''].join('::');
}
