/**
 * Memory Service Extension for SillyTavern
 * 
 * V1 LIMITATION - IMPORTANT:
 * This extension uses a POST-RENDER retrieve pattern.
 * 
 * Flow:
 * 1. User sends message -> Assistant generates and renders response
 * 2. CHARACTER_MESSAGE_RENDERED event fires
 * 3. Extension calls /memory/store to save the exchange
 * 4. Extension calls /memory/retrieve to get relevant memories
 * 5. Retrieved memory_block is set via setExtensionPrompt() for the NEXT generation
 * 
 * This means: memory_block from exchange N is used in generation N+1.
 * This is the expected v1 pattern. NOT a pre-generation injection.
 */

import { getContext, extension_settings } from '../../extensions.js';
import { eventSource, event_types, saveSettingsDebounced, setExtensionPrompt } from '../../../script.js';

// === CONFIGURATION ===
const DEFAULT_SETTINGS = {
    enabled: false,
    memoryServiceUrl: 'http://localhost:8000',
    apiKey: '',
    retrieveLimit: 5,
    recentMessagesCount: 8,
};

let settings = {};

// === STATE ===
let isProcessing = false;

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
        return;
    }

    const chatContext = getChatContext();
    if (!chatContext || !chatContext.chatId) {
        return;
    }

    const messages = getRecentMessages(settings.recentMessagesCount);
    if (messages.length === 0) {
        return;
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
            }),
        });

        if (response.ok) {
            const result = await response.json();
            console.log('[Memory Service] Stored:', result.stored, 'Skipped:', result.skipped);
        } else {
            console.error('[Memory Service] Store failed:', response.status);
        }
    } catch (error) {
        console.error('[Memory Service] Store error:', error);
    }
}

/**
 * Call Memory Service /memory/retrieve endpoint
 * 
 * V1 NOTE: This is called AFTER the current generation completes.
 * The retrieved memory_block will be used for the NEXT generation.
 */
async function retrieveMemories() {
    if (!settings.enabled) {
        return '';
    }

    const chatContext = getChatContext();
    if (!chatContext || !chatContext.chatId) {
        return '';
    }

    const user_input = getLastUserMessage();
    if (!user_input) {
        return '';
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
            }),
        });

        if (response.ok) {
            const result = await response.json();
            console.log('[Memory Service] Retrieved:', result.items.length, 'items');

            // Set the memory block for the NEXT generation
            // V1 PATTERN: post-render retrieve -> next generation
            if (result.memory_block) {
                // setExtensionPrompt signature: (name, content, priority, depth, scan, role)
                setExtensionPrompt('memory-service', result.memory_block, 0, 0, true, 'system');
                console.log('[Memory Service] Memory block set for NEXT generation');
            }

            return result.memory_block || '';
        } else {
            console.error('[Memory Service] Retrieve failed:', response.status);
        }
    } catch (error) {
        console.error('[Memory Service] Retrieve error:', error);
    }

    return '';
}

/**
 * Main handler called after message is rendered
 * 
 * V1 PATTERN EXPLANATION:
 * This handler runs AFTER the assistant's response is generated and rendered.
 * Therefore:
 * - The current generation did NOT have access to retrieved memories
 * - Retrieved memories will be available for the NEXT generation
 * 
 * This is the safe and expected v1 pattern.
 */
async function onMessageRendered() {
    if (!settings.enabled || isProcessing) {
        return;
    }

    isProcessing = true;

    try {
        // Step 1: Store the just-completed exchange
        await storeMemories();

        // Step 2: Retrieve memories for NEXT generation
        await retrieveMemories();
    } finally {
        isProcessing = false;
    }
}

/**
 * Handle chat change - clear prompt if chat changes
 */
function onChatChanged() {
    setExtensionPrompt('memory-service', '', 0, 0, true, 'system');
}

/**
 * Initialize extension
 * 
 * Event wiring uses eventSource.makeLast() directly with event_types.CHARACTER_MESSAGE_RENDERED.
 * This ensures the handler runs after core rendering is complete.
 * Pattern confirmed from local SillyTavern extension samples.
 */
function init() {
    console.log('[Memory Service] Extension initializing...');

    loadSettings();

    // Register event listener using makeLast for post-render execution
    // This is the confirmed pattern from local SillyTavern extension samples
    eventSource.makeLast(event_types.CHARACTER_MESSAGE_RENDERED, onMessageRendered);

    // Also handle chat changes to clear old prompts
    eventSource.on(event_types.CHAT_CHANGED, onChatChanged);

    console.log('[Memory Service] Extension initialized');
    console.log('[Memory Service] V1 PATTERN: retrieve happens AFTER render, memory_block applies to NEXT generation');
}

// Start the extension
init();
