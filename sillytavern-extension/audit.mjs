export const DEFAULT_AUDIT_MAX_RECORDS = 20;
export const DEFAULT_AUDIT_PREVIEW_CHARS = 240;
export const PRE_GENERATION_HOOK_CANDIDATES = [
    'GENERATE_BEFORE_COMBINE_PROMPTS',
    'GENERATION_AFTER_COMMANDS',
    'GENERATION_STARTED',
];

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

export function buildMessageAuditEntries(messages, previewChars = DEFAULT_AUDIT_PREVIEW_CHARS) {
    return (messages || []).map(message => ({
        role: message.role || 'unknown',
        text_length: (message.text || '').length,
        text_preview: previewText(message.text || '', previewChars),
    }));
}

export function createIntegrationAuditRecord({ chatId, characterId, recentMessagesCount }) {
    return {
        interaction_id: `${chatId || 'chat'}:${Date.now()}`,
        timestamp: nowIso(),
        chat_id: chatId || null,
        character_id: characterId || null,
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
}) {
    const memoryBlock = result?.memory_block || '';
    return {
        stage,
        user_input_length: (userInput || '').length,
        user_input_preview: previewText(userInput || '', previewChars),
        recent_message_count: (recentMessages || []).length,
        recent_messages: buildMessageAuditEntries(recentMessages || [], previewChars),
        returned_item_count: result?.items?.length ?? 0,
        total_candidates: result?.total_candidates ?? 0,
        memory_block_length: memoryBlock.length,
        memory_block_item_count: countMemoryBlockItems(memoryBlock),
        memory_block_preview: previewText(memoryBlock, previewChars),
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
