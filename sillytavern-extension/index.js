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

import { getContext, extension_settings } from '../../../extensions.js';
import { eventSource, event_types, saveSettingsDebounced, setExtensionPrompt } from '../../../../script.js';
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
import {
    normalizeExtensionSettings,
    serializeExtensionSettings,
} from './settings.mjs';
import { mountSettingsUi } from './settings-ui.mjs';
import { resolveEffectiveScope } from './scope.mjs';
import {
    buildLoreAnchorBlock,
    LORE_ANCHOR_PROMPT_KEY,
} from './lore-anchors.mjs';
import {
    buildTrackerBlock,
    evaluateTrackerToasts,
    fetchTrackers,
    resolveTrackerCharacterIds,
    TRACKER_PROMPT_KEY,
} from './trackers.mjs';
import {
    MEMORY_PROTOCOL_VERSION,
    compareVersions,
} from './version.mjs';

// === SETTINGS POLICY ===
// SillyTavern-facing knobs are grouped conceptually as:
// - connection: base endpoint/auth
// - retrieval: how much chat context is sent and how many candidates are requested
// - promptBudget: how much memory survives into the injected prompt
// - audit: opt-in observability only
//
// Runtime settings remain flat for simple call sites, but load/save normalizes grouped storage
// so defaults and docs stay synchronized and easier to reason about for real long-chat use.
let settings = {};

// === STATE ===
let isStoreProcessing = false;
let isRetrieveProcessing = false;
let pendingInteractionAudit = null;
let pendingTurnKey = null;
let currentMemoryPromptBlock = '';
let currentRetrieveBudget = null;
let currentLoreAnchorInfo = null;
let currentCompatibility = null;
// character_id -> the trackers the backend last stored for them. Filled on CHAT_CHANGED
// and after a manual update; the lorebook handler only ever reads it, so a mention never
// triggers a regeneration (or an await) in the injection path.
let currentTrackers = {};
let currentTrackerInfo = null;
const pendingTrackerFetches = new Set();

function setMemoryPrompt(memoryBlock) {
    currentMemoryPromptBlock = memoryBlock || '';
    setExtensionPrompt('memory-service', memoryBlock || '', 0, 0, true, 'system');
}

function clearMemoryPrompt() {
    setMemoryPrompt('');
    currentRetrieveBudget = null;
}

function setLoreAnchorPrompt(anchorBlock) {
    setExtensionPrompt(LORE_ANCHOR_PROMPT_KEY, anchorBlock || '', 0, 0, true, 'system');
}

function clearLoreAnchorPrompt() {
    currentLoreAnchorInfo = null;
    setLoreAnchorPrompt('');
}

function setTrackerPrompt(trackerBlock) {
    setExtensionPrompt(TRACKER_PROMPT_KEY, trackerBlock || '', 0, 0, true, 'system');
}

function clearTrackerPrompt() {
    currentTrackerInfo = null;
    setTrackerPrompt('');
}

function refreshPromptInsertionAudit(record = pendingInteractionAudit) {
    if (!record?.retrieve_called) {
        return;
    }

    const anyBlock = Boolean(
        currentMemoryPromptBlock
        || currentLoreAnchorInfo?.anchorBlock
        || currentTrackerInfo?.trackerBlock
    );

    record.prompt_insertion = buildPromptInsertionAuditSection({
        memoryBlock: currentMemoryPromptBlock,
        applied: anyBlock,
        reason: anyBlock
            ? 'budgeted_memory_block_or_lore_anchor_or_tracker_set_for_current_turn'
            : 'empty_or_missing_memory_block',
        previewChars: settings.auditPreviewChars,
        stage: 'pre_generation',
        appliedToCurrentTurn: true,
        budget: currentRetrieveBudget,
        loreAnchorBlock: currentLoreAnchorInfo?.anchorBlock || '',
        loreAnchorItemCount: currentLoreAnchorInfo?.anchorItemCount || 0,
        trackerBlock: currentTrackerInfo?.trackerBlock || '',
        trackerSubjectCount: currentTrackerInfo?.includedCharacters?.length || 0,
    });
    record.applied_to_current_turn = anyBlock;
}

/**
 * Load extension settings from SillyTavern extension_settings
 */
function loadSettings() {
    settings = normalizeExtensionSettings(extension_settings['memory-service'] || {});
}

/**
 * Save extension settings to SillyTavern extension_settings
 */
function saveSettings() {
    extension_settings['memory-service'] = serializeExtensionSettings(settings);
    saveSettingsDebounced();
}

function refreshSettingsUi() {
    mountSettingsUi({
        document: globalThis.document,
        settings,
        compatibility: currentCompatibility,
        onSettingsChanged: (fieldKey, nextValue) => {
            settings = {
                ...settings,
                [fieldKey]: nextValue,
            };
            saveSettings();
            // Connection edits can change which backend we talk to; re-check.
            if (fieldKey === 'memoryServiceUrl' || fieldKey === 'apiKey' || fieldKey === 'enabled') {
                checkBackendCompatibility();
            }
        },
        onApplyRecommendedBaseline: nextSettings => {
            settings = nextSettings;
            saveSettings();
            refreshSettingsUi();
        },
        getChatContext: () => {
            const ctx = getChatContext();
            return { chatId: ctx.chatId, characterId: ctx.characterId };
        },
    });
}

/**
 * Handshake with the backend to detect an incompatible/stale pairing.
 *
 * Non-blocking and best-effort: the result only drives a warning banner and
 * console message; retrieve/store keep working regardless. The guarded failure
 * mode is a stale extension copy (broken symlink into SillyTavern's public/)
 * silently talking to an updated backend.
 */
async function checkBackendCompatibility() {
    if (!settings.enabled || !settings.memoryServiceUrl) {
        return;
    }

    let backendInfo = null;
    let reachable = true;

    try {
        const headers = {};
        if (settings.apiKey) {
            headers['X-API-Key'] = settings.apiKey;
        }

        const response = await fetch(`${settings.memoryServiceUrl}/memory/version`, {
            method: 'GET',
            headers,
        });

        if (response.ok) {
            backendInfo = await response.json();
        } else if (response.status === 404) {
            // Backend predates the /memory/version endpoint -> treat as outdated
            // (backendInfo stays null so compareVersions reports backend_outdated).
            backendInfo = null;
        } else {
            reachable = false;
        }
    } catch (error) {
        reachable = false;
        console.warn('[Memory Service] Version check request failed:', error?.message || error);
    }

    currentCompatibility = compareVersions({
        extensionProtocol: MEMORY_PROTOCOL_VERSION,
        backendInfo,
        reachable,
    });

    if (currentCompatibility.warn) {
        console.warn('[Memory Service]', currentCompatibility.message);
    } else if (currentCompatibility.status === 'ok') {
        console.log(
            '[Memory Service] Backend compatible (protocol v%s, %s)',
            currentCompatibility.backendProtocol,
            currentCompatibility.backendGitCommit || currentCompatibility.backendServiceVersion || 'unknown build',
        );
    }

    refreshSettingsUi();
}

/**
 * Get current chat context
 * Returns { chatId, characterId, groupId, chat }
 */
function getChatContext() {
    return resolveEffectiveScope(getContext());
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

        const body = {
            chat_id: chatContext.chatId,
            character_id: chatContext.characterId,
            messages: messages,
            debug: settings.auditEnabled,
        };
        if (settings.sceneExtractionModel) {
            body.model = settings.sceneExtractionModel;
        }

        const response = await fetch(`${settings.memoryServiceUrl}/memory/store`, {
            method: 'POST',
            headers,
            body: JSON.stringify(body),
        });

        if (response.ok) {
            const result = await response.json();
            console.log('[Memory Service] Stored:', result.stored, 'Skipped:', result.skipped, 'Extraction method:', result.extraction_method);
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
                character_id: chatContext.characterId,
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

/**
 * Pull one character's trackers into the cache. Fire-and-forget everywhere it is called:
 * a failure only means the tracker block is missing from this turn's prompt, which is not
 * worth blocking or breaking generation over.
 */
async function refreshTrackersFor(characterId) {
    if (!settings.enabled || !settings.trackerInjectionEnabled || !characterId) {
        return;
    }

    const chatContext = getChatContext();
    if (!chatContext?.chatId) {
        return;
    }

    const fetchKey = `${chatContext.chatId}::${characterId}`;
    if (pendingTrackerFetches.has(fetchKey)) {
        return;
    }
    pendingTrackerFetches.add(fetchKey);

    try {
        currentTrackers[characterId] = await fetchTrackers({
            memoryServiceUrl: settings.memoryServiceUrl,
            apiKey: settings.apiKey,
            chatId: chatContext.chatId,
            characterId,
        });
    } catch (error) {
        console.warn('[Memory Service] Tracker fetch failed:', error?.message || error);
    } finally {
        pendingTrackerFetches.delete(fetchKey);
    }
}

function getCharacterRoster() {
    const rawContext = getContext();
    return Array.isArray(rawContext?.characters) ? rawContext.characters : [];
}

function onWorldInfoActivated(entries = []) {
    if (!settings.enabled) {
        return;
    }

    const loreAnchorInfo = buildLoreAnchorBlock({
        entries,
        existingMemoryBlock: currentMemoryPromptBlock,
    });

    if (loreAnchorInfo.anchorBlock) {
        currentLoreAnchorInfo = loreAnchorInfo;
        setLoreAnchorPrompt(loreAnchorInfo.anchorBlock);
    } else {
        clearLoreAnchorPrompt();
    }

    injectTrackersFor(entries);

    refreshPromptInsertionAudit();
}

/**
 * Trackers ride the same WORLD_INFO_ACTIVATED signal as lore anchors: a lorebook entry
 * firing is exactly the "this character just came up" trigger we want, and no separate
 * name detection has to be invented for it.
 *
 * The block goes in under its own prompt key, so it never passes through
 * buildBudgetedMemoryBlock and never costs a retrieved memory its slot.
 */
function injectTrackersFor(entries = []) {
    if (!settings.trackerInjectionEnabled) {
        clearTrackerPrompt();
        return;
    }

    const chatContext = getChatContext();
    const { matches } = resolveTrackerCharacterIds({
        entries,
        characters: getCharacterRoster(),
        currentCharacterId: chatContext?.characterId || null,
        isGroupChat: Boolean(chatContext?.groupId),
    });

    if (!matches.length) {
        clearTrackerPrompt();
        return;
    }

    // A character we have never fetched trackers for (e.g. a second character in a group
    // chat) can't be injected this turn - fetching is async and the prompt is being built
    // now - but warming the cache means the next mention lands.
    for (const match of matches) {
        if (!currentTrackers[match.characterId]) {
            refreshTrackersFor(match.characterId);
        }
    }

    const trackerInfo = buildTrackerBlock({
        matches,
        trackersByCharacter: currentTrackers,
        maxTrackerChars: settings.maxTrackerChars,
    });

    if (!trackerInfo.trackerBlock) {
        clearTrackerPrompt();
        return;
    }

    currentTrackerInfo = trackerInfo;
    setTrackerPrompt(trackerInfo.trackerBlock);
    console.log(
        '[Memory Service] Tracker block injected for',
        trackerInfo.includedCharacters.map(item => item.characterName || item.characterId).join(', '),
        `(${trackerInfo.trackerCharCount} chars)`,
    );
}

/**
 * Nag when a tracker has fallen too far behind the chat. The counters ride along in the
 * /memory/store response we already make every turn, so this costs no extra request.
 */
function notifyStaleTrackers(storeResult) {
    const trackers = storeResult?.result?.trackers;
    if (!Array.isArray(trackers) || !trackers.length) {
        return;
    }

    const chatContext = getChatContext();
    const characterId = chatContext?.characterId || null;
    const roster = getCharacterRoster();
    const characterName = roster[Number(characterId)]?.name || null;

    const { toasts, lastTrackerToastAt } = evaluateTrackerToasts({
        trackers,
        chatId: chatContext?.chatId || null,
        characterId,
        threshold: settings.trackerReminderThreshold,
        lastTrackerToastAt: settings.lastTrackerToastAt,
        characterName,
    });

    settings.lastTrackerToastAt = lastTrackerToastAt;
    saveSettings();

    for (const toast of toasts) {
        globalThis.toastr?.info?.(toast.message, 'Memory Service');
    }
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
    globalThis.memoryServiceLoreAnchors = {
        getCurrentAnchorBlock: () => currentLoreAnchorInfo?.anchorBlock || '',
        getCurrentAnchorEntries: () => currentLoreAnchorInfo?.selectedAnchors || [],
    };
    globalThis.memoryServiceTrackers = {
        getCachedTrackers: () => currentTrackers,
        getCurrentTrackerBlock: () => currentTrackerInfo?.trackerBlock || '',
        // Trackers are updated from the backend's own web UI, which the extension has no
        // way to observe - call this to pick up an update without switching chats.
        refresh: () => refreshTrackersFor(getChatContext()?.characterId || null),
    };
}

/**
 * Retrieve and inject memories before the current generation starts.
 */
async function onBeforeGeneration() {
    if (!settings.enabled || isRetrieveProcessing) {
        return;
    }

    clearLoreAnchorPrompt();
    clearTrackerPrompt();

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
            characterId: chatContext?.characterId || null,
            groupId: chatContext?.groupId || null,
            chatScopeSource: chatContext?.chatScopeSource || null,
            characterScopeSource: chatContext?.characterScopeSource || null,
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
            currentRetrieveBudget = retrieveResult.budget || null;
            refreshPromptInsertionAudit(auditRecord);
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
            characterId: chatContext?.characterId || null,
            groupId: chatContext?.groupId || null,
            chatScopeSource: chatContext?.chatScopeSource || null,
            characterScopeSource: chatContext?.characterScopeSource || null,
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
            notifyStaleTrackers(storeResult);
        } else if (storeResult.reason) {
            auditRecord.notes.push(storeResult.reason);
        }

        persistIntegrationAudit(auditRecord);
        clearMemoryPrompt();
        clearLoreAnchorPrompt();
        clearTrackerPrompt();
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
    clearLoreAnchorPrompt();
    clearTrackerPrompt();
    pendingInteractionAudit = null;
    pendingTurnKey = null;

    // Trackers are scoped per chat, so the previous chat's cache is meaningless here.
    currentTrackers = {};
    refreshTrackersFor(getChatContext()?.characterId || null);
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
    eventSource.on(event_types.WORLD_INFO_ACTIVATED || 'WORLD_INFO_ACTIVATED', onWorldInfoActivated);
    exposeAuditHelpers();
    refreshSettingsUi();
    refreshTrackersFor(getChatContext()?.characterId || null);
    // Fire-and-forget: warns in the UI/console if the backend is an
    // incompatible or stale pairing, without blocking initialization.
    checkBackendCompatibility();

    console.log('[Memory Service] Extension initialized');
    console.log('[Memory Service] Current-turn pattern: retrieve happens before generation, store after render');
    if (settings.auditEnabled) {
        console.log('[Memory Service] Integration audit mode enabled');
    }
}

// Start the extension
init();
