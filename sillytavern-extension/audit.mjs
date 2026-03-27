export const DEFAULT_AUDIT_MAX_RECORDS = 20;
export const DEFAULT_AUDIT_PREVIEW_CHARS = 240;
export const DEFAULT_MAX_PROMPT_MEMORIES = 4;
export const DEFAULT_MAX_PROMPT_CHARS = 520;
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

export function resolvePreGenerationHookNames(eventTypes = {}) {
    const resolved = new Set();
    for (const name of PRE_GENERATION_HOOK_CANDIDATES) {
        resolved.add(eventTypes?.[name] || name);
    }
    return [...resolved];
}

export function buildTurnKey({ chatId, characterId, chatLength, userInput }) {
    return [chatId || '', characterId || '', chatLength || 0, userInput || ''].join('::');
}
