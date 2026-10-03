// Stand-in for SillyTavern's public/script.js - only what main.mjs imports.
// Values copied from SillyTavern; injection.mjs checks its own copies against these.
export const extension_prompt_types = { NONE: -1, IN_PROMPT: 0, IN_CHAT: 1, BEFORE_PROMPT: 2 };
export const extension_prompt_roles = { SYSTEM: 0, USER: 1, ASSISTANT: 2 };

export const event_types = {
    MESSAGE_SENT: 'message_sent',
    CHARACTER_MESSAGE_RENDERED: 'character_message_rendered',
    CHAT_CHANGED: 'chat_id_changed',
    MESSAGE_DELETED: 'message_deleted',
    MESSAGE_EDITED: 'message_edited',
    WORLD_INFO_ACTIVATED: 'world_info_activated',
    GENERATION_STARTED: 'generation_started',
    GENERATION_AFTER_COMMANDS: 'GENERATION_AFTER_COMMANDS',
    GENERATE_BEFORE_COMBINE_PROMPTS: 'generate_before_combine_prompts',
};

const harness = () => globalThis.__stHarness;

// SillyTavern's emit awaits each listener in turn; Generate() relies on that.
export const eventSource = {
    on: (event, listener) => harness().listeners(event).push(listener),
    makeFirst: (event, listener) => harness().listeners(event).unshift(listener),
    makeLast: (event, listener) => harness().listeners(event).push(listener),
};

export function saveSettingsDebounced() {
    harness().saves += 1;
}

export function setExtensionPrompt(key, value, position, depth, scan, role) {
    harness().prompts.set(key, { value, position, depth, scan, role });
}
