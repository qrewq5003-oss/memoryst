/**
 * Memory Service Extension for SillyTavern
 * 
 * Current timing policy:
 * - retrieve happens before generation for current-turn prompt injection
 * - store happens after CHARACTER_MESSAGE_RENDERED for the completed exchange
 * 
 * Flow:
 * 1. User sends message
 * 2. Pre-generation hook fires
 * 3. Extension calls /memory/retrieve
 * 4. Retrieved memory_block is set via setExtensionPrompt() for CURRENT generation
 * 5. Assistant generates and renders response
 * 6. CHARACTER_MESSAGE_RENDERED fires
 * 7. Extension calls /memory/store to save the completed exchange
 */

import { getContext, extension_settings } from '../../extensions.js';
import { eventSource, event_types, saveSettingsDebounced, setExtensionPrompt } from '../../../script.js';
import {
    buildBudgetedMemoryBlock,
    buildPromptInsertionAuditSection,
    buildRetrieveAuditSection,
    buildStoreAuditSection,
    buildTurnKey,
    createIntegrationAuditRecord,
    finalizeIntegrationAuditRecord,
    pushAuditRecord,
    resolvePreGenerationHookNames,
} from './audit.mjs';

// === CONFIGURATION ===
const DEFAULT_SETTINGS = {
    enabled: false,
    memoryServiceUrl: 'http://localhost:8000',
    apiKey: '',
    retrieveLimit: 5,
    recentMessagesCount: 8,
    auditEnabled: false,
    auditMaxRecords: 20,
    auditPreviewChars: 240,
    maxPromptMemories: 4,
    maxPromptChars: 520,
    maxSummaryItems: 1,
    maxStableItems: 2,
    maxEpisodicItems: 1,
    recentAudits: [],
};

let settings = {};

// === STATE ===
let isStoreProcessing = false;
let isRetrieveProcessing = false;
let pendingInteractionAudit = null;
let pendingTurnKey = null;

function setMemoryPrompt(memoryBlock) {
    setExtensionPrompt('memory-service', memoryBlock || '', 0, 0, true, 'system');
}

function clearMemoryPrompt() {
    setMemoryPrompt('');
}

/**
 * Load extension settings from SillyTavern extension_settings
 */
function loadSettings() {
    settings = {
        ...DEFAULT_SETTINGS,
        ...extension_settings['memory-service'],
    };
}

/**
 * Save extension settings to SillyTavern extension_settings
 */
function saveSettings() {
    extension_settings['memory-service'] = settings;
    saveSettingsDebounced();
}

/**
 * Get current chat context
 * Returns { chatId, characterId, groupId, chat }
 */
function getChatContext() {
    const context = getContext();
    if (!context) {
        return null;
    }

    return {
        chatId: context.chatId || 'default',
        characterId: context.characterId || null,
        groupId: context.groupId || null,
        chat: context.chat || [],
    };
}

/**
 * Get recent messages from chat context
 * Returns array of { role, text } objects
 */
function getRecentMessages(count) {
    const chatContext = getChatContext();
    if (!chatContext || !chatContext.chat) {
        return [];
    }

    // Take last N messages from chat
    const recent = chatContext.chat.slice(-count);

    return recent.map(msg => ({
        role: msg.role || (msg.is_user ? 'user' : 'assistant'),
        text: msg.mes || msg.text || '',
    }));
}

/**
 * Get the last user message for retrieval query
 */
function getLastUserMessage() {
    const chatContext = getChatContext();
    if (!chatContext || !chatContext.chat) {
        return '';
    }

    // Find last user message
    for (let i = chatContext.chat.length - 1; i >= 0; i--) {
        const msg = chatContext.chat[i];
        if (msg.is_user || msg.role === 'user') {
            return msg.mes || msg.text || '';
        }
    }

    return '';
}

/**
 * Get recent messages for retrieval context
 */
function getRecentMessagesForRetrieve(count) {
    const messages = getRecentMessages(count);
    return messages.map(msg => ({
        role: msg.role,
        text: msg.text,
    }));
}

/**
 * Call Memory Service /memory/store endpoint
 */
async function storeMemories() {
    if (!settings.enabled) {
        return { called: false, reason: 'extension_disabled' };
    }

    const chatContext = getChatContext();
    if (!chatContext || !chatContext.chatId) {
        return { called: false, reason: 'missing_chat_context' };
    }

    const messages = getRecentMessages(settings.recentMessagesCount);
    if (messages.length === 0) {
        return { called: false, reason: 'no_messages' };
    }

    try {
        const headers = {
            'Content-Type': 'application/json',
        };

        // Use X-API-Key header as per backend contract
        if (settings.apiKey) {
            headers['X-API-Key'] = settings.apiKey;
        }

        const response = await fetch(`${settings.memoryServiceUrl}/memory/store`, {
            method: 'POST',
            headers,
            body: JSON.stringify({
                chat_id: chatContext.chatId,
                character_id: chatContext.characterId || chatContext.chatId,
                messages: messages,
                debug: settings.auditEnabled,
            }),
        });

        if (response.ok) {
            const result = await response.json();
            console.log('[Memory Service] Stored:', result.stored, 'Skipped:', result.skipped);
            return {
                called: true,
                messages,
                result,
            };
        } else {
            console.error('[Memory Service] Store failed:', response.status);
            return {
                called: true,
                messages,
                error: `store_http_${response.status}`,
            };
        }
    } catch (error) {
        console.error('[Memory Service] Store error:', error);
        return {
            called: true,
            messages,
            error: error?.message || String(error),
        };
    }
}

/**
 * Call Memory Service /memory/retrieve endpoint for current-turn injection.
 */
async function retrieveMemories() {
    if (!settings.enabled) {
        return { called: false, reason: 'extension_disabled', memoryBlock: '' };
    }

    const chatContext = getChatContext();
    if (!chatContext || !chatContext.chatId) {
        return { called: false, reason: 'missing_chat_context', memoryBlock: '' };
    }

    const user_input = getLastUserMessage();
    if (!user_input) {
        return { called: false, reason: 'no_last_user_message', memoryBlock: '' };
    }

    const recent_messages = getRecentMessagesForRetrieve(3);

    try {
        const headers = {
            'Content-Type': 'application/json',
        };

        // Use X-API-Key header as per backend contract
        if (settings.apiKey) {
            headers['X-API-Key'] = settings.apiKey;
        }

        const response = await fetch(`${settings.memoryServiceUrl}/memory/retrieve`, {
            method: 'POST',
            headers,
            body: JSON.stringify({
                chat_id: chatContext.chatId,
                character_id: chatContext.characterId || chatContext.chatId,
                user_input: user_input,
                recent_messages: recent_messages,
                limit: settings.retrieveLimit,
                debug: settings.auditEnabled,
            }),
        });

        if (response.ok) {
            const result = await response.json();
            const retrievedItems = result.items || [];
            const budgeted = buildBudgetedMemoryBlock({
                items: retrievedItems,
                maxPromptMemories: settings.maxPromptMemories,
                maxPromptChars: settings.maxPromptChars,
                maxSummaryItems: settings.maxSummaryItems,
                maxStableItems: settings.maxStableItems,
                maxEpisodicItems: settings.maxEpisodicItems,
            });
            const injectedMemoryBlock = budgeted.memoryBlock || result.memory_block || '';
            console.log(
                '[Memory Service] Retrieved:',
                retrievedItems.length,
                'items; injected:',
                budgeted.injectedItemCount,
                'trimmed:',
                budgeted.trimmedItemCount,
            );

            if (injectedMemoryBlock) {
                setMemoryPrompt(injectedMemoryBlock);
                console.log('[Memory Service] Budgeted memory block set for CURRENT generation');
            } else {
                clearMemoryPrompt();
            }

            return {
                called: true,
                userInput: user_input,
                recentMessages: recent_messages,
                result,
                memoryBlock: injectedMemoryBlock,
                rawMemoryBlock: result.memory_block || '',
                budget: budgeted,
                promptApplied: Boolean(injectedMemoryBlock),
            };
        } else {
            console.error('[Memory Service] Retrieve failed:', response.status);
            clearMemoryPrompt();
            return {
                called: true,
                userInput: user_input,
                recentMessages: recent_messages,
                error: `retrieve_http_${response.status}`,
                memoryBlock: '',
                promptApplied: false,
            };
        }
    } catch (error) {
        console.error('[Memory Service] Retrieve error:', error);
        clearMemoryPrompt();
        return {
            called: true,
            userInput: user_input,
            recentMessages: recent_messages,
            error: error?.message || String(error),
            memoryBlock: '',
            promptApplied: false,
        };
    }

    return { called: false, reason: 'unknown', memoryBlock: '' };
}

function persistIntegrationAudit(record) {
    if (!settings.auditEnabled) {
        return;
    }

    const finalized = finalizeIntegrationAuditRecord(record);
    pushAuditRecord(settings, finalized);
    saveSettings();
    console.log('[Memory Service][Audit]', finalized);
}

function exposeAuditHelpers() {
    globalThis.memoryServiceAudit = {
        getRecentAudits: () => settings.recentAudits || [],
        clearRecentAudits: () => {
            settings.recentAudits = [];
            saveSettings();
        },
        printRecentAudits: () => {
            console.table((settings.recentAudits || []).map(item => ({
                timestamp: item.timestamp,
                chat_id: item.chat_id,
                store_called: item.store_called,
                retrieve_called: item.retrieve_called,
                prompt_insertion_observed: item.prompt_insertion_observed,
                notes: (item.notes || []).join(','),
            })));
        },
    };
}

/**
 * Retrieve and inject memories before the current generation starts.
 */
async function onBeforeGeneration() {
    if (!settings.enabled || isRetrieveProcessing) {
        return;
    }

    const chatContext = getChatContext();
    const userInput = getLastUserMessage();
    const turnKey = buildTurnKey({
        chatId: chatContext?.chatId || null,
        characterId: chatContext?.characterId || chatContext?.chatId || null,
        chatLength: chatContext?.chat?.length || 0,
        userInput,
    });

    if (pendingTurnKey === turnKey && pendingInteractionAudit?.retrieve_called) {
        return;
    }

    isRetrieveProcessing = true;

    try {
        const auditRecord = createIntegrationAuditRecord({
            chatId: chatContext?.chatId || null,
            characterId: chatContext?.characterId || chatContext?.chatId || null,
            recentMessagesCount: settings.recentMessagesCount,
        });
        auditRecord.retrieve_stage = 'pre_generation';
        auditRecord.prompt_injection_stage = 'pre_generation';

        const retrieveResult = await retrieveMemories();
        if (retrieveResult.called) {
            auditRecord.retrieve_called = true;
            auditRecord.retrieve = buildRetrieveAuditSection({
                userInput: retrieveResult.userInput || '',
                recentMessages: retrieveResult.recentMessages || [],
                result: retrieveResult.result || null,
                error: retrieveResult.error || null,
                previewChars: settings.auditPreviewChars,
                stage: 'pre_generation',
                budget: retrieveResult.budget || null,
            });
            auditRecord.prompt_insertion_observed = true;
            auditRecord.applied_to_current_turn = Boolean(retrieveResult.promptApplied);
            auditRecord.prompt_insertion = buildPromptInsertionAuditSection({
                memoryBlock: retrieveResult.memoryBlock || '',
                applied: retrieveResult.promptApplied,
                reason: retrieveResult.promptApplied ? 'budgeted_memory_block_set_for_current_turn' : 'empty_or_missing_memory_block',
                previewChars: settings.auditPreviewChars,
                stage: 'pre_generation',
                appliedToCurrentTurn: true,
                budget: retrieveResult.budget || null,
            });
        } else {
            clearMemoryPrompt();
            if (retrieveResult.reason) {
                auditRecord.notes.push(retrieveResult.reason);
            }
        }

        pendingInteractionAudit = auditRecord;
        pendingTurnKey = turnKey;
    } finally {
        isRetrieveProcessing = false;
    }
}

/**
 * Store the completed exchange after the assistant message is rendered.
 */
async function onMessageRendered() {
    if (!settings.enabled || isStoreProcessing) {
        return;
    }

    isStoreProcessing = true;

    try {
        const chatContext = getChatContext();
        const auditRecord = pendingInteractionAudit || createIntegrationAuditRecord({
            chatId: chatContext?.chatId || null,
            characterId: chatContext?.characterId || chatContext?.chatId || null,
            recentMessagesCount: settings.recentMessagesCount,
        });

        const storeResult = await storeMemories();
        if (storeResult.called) {
            auditRecord.store_called = true;
            auditRecord.store = buildStoreAuditSection({
                messages: storeResult.messages || [],
                result: storeResult.result || null,
                error: storeResult.error || null,
                previewChars: settings.auditPreviewChars,
            });
        } else if (storeResult.reason) {
            auditRecord.notes.push(storeResult.reason);
        }

        persistIntegrationAudit(auditRecord);
        clearMemoryPrompt();
        pendingInteractionAudit = null;
        pendingTurnKey = null;
    } finally {
        isStoreProcessing = false;
    }
}

/**
 * Handle chat change - clear prompt if chat changes
 */
function onChatChanged() {
    clearMemoryPrompt();
    pendingInteractionAudit = null;
    pendingTurnKey = null;
}

/**
 * Initialize extension
 */
function init() {
    console.log('[Memory Service] Extension initializing...');

    loadSettings();

    // Register likely pre-generation hooks for current-turn retrieval.
    for (const hookName of resolvePreGenerationHookNames(event_types)) {
        if (typeof eventSource.makeFirst === 'function') {
            eventSource.makeFirst(hookName, onBeforeGeneration);
        } else {
            eventSource.on(hookName, onBeforeGeneration);
        }
    }

    // Store happens after render because the assistant reply is only complete at this point.
    eventSource.makeLast(event_types.CHARACTER_MESSAGE_RENDERED, onMessageRendered);

    eventSource.on(event_types.CHAT_CHANGED, onChatChanged);
    exposeAuditHelpers();

    console.log('[Memory Service] Extension initialized');
    console.log('[Memory Service] Current-turn pattern: retrieve happens before generation, store after render');
    if (settings.auditEnabled) {
        console.log('[Memory Service] Integration audit mode enabled');
    }
}

// Start the extension
init();
