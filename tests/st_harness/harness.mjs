/**
 * Loads the real main.mjs against a fake SillyTavern and a fake backend.
 *
 * main.mjs is where the extension's modules are wired to SillyTavern's events, and until
 * 2026-10-03 no test loaded it: every module it calls was covered, the calling was not.
 * The outage of 2026-10-02 - twenty turns failing into the console - lived exactly there.
 *
 * Each loadMain() gets a fresh copy of main.mjs (its module state is the turn state) and
 * a fresh harness. Its own imports stay shared, which is harmless: they are pure.
 */

import { register } from 'node:module';

import { normalizeExtensionSettings, serializeExtensionSettings } from '../../sillytavern-extension/settings.mjs';

register(new URL('./hooks.mjs', import.meta.url));

export const BACKEND = 'http://memoryst.test';
const SETTINGS_KEY = 'memory-service';
let loadCount = 0;

function jsonResponse(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

/** A fetch that never answers on its own - only the caller's AbortSignal ends it. */
export function hang(_url, options) {
    return new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => {
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        });
    });
}

export function networkDown() {
    return Promise.reject(new TypeError('Failed to fetch'));
}

export function defaultBackend(path, { method }) {
    if (path.startsWith('/memory/version')) {
        return jsonResponse(200, { protocol_version: globalThis.__stHarness.protocolVersion });
    }
    if (path.startsWith('/memory/retrieve')) {
        return jsonResponse(200, {
            items: [{ id: 'm-cafe', layer: 'stable', type: 'fact', content: 'Mai работает в кафе.' }],
            memory_block: '',
        });
    }
    if (path.startsWith('/memory/store')) {
        return jsonResponse(200, {
            stored: 1, updated: 0, skipped: 0,
            items: [{ id: 'm-new' }], created_ids: ['m-new'], extraction_method: 'llm',
        });
    }
    if (method === 'DELETE') {
        return jsonResponse(200, { deleted: true });
    }
    // trackers, audit mirror, models: answered, nothing to say.
    return jsonResponse(404, {});
}

/**
 * @param {object} [options]
 * @param {object} [options.settings] - flat overrides, e.g. { retrieveTimeoutMs: 500 }
 * @param {Function} [options.backend] - (path, init) => response | Promise; default above
 */
export async function loadMain({ settings = {}, backend = defaultBackend } = {}) {
    const { MEMORY_PROTOCOL_VERSION } = await import('../../sillytavern-extension/version.mjs');
    const listeners = new Map();
    const harness = {
        protocolVersion: MEMORY_PROTOCOL_VERSION,
        backend,
        requests: [],
        toasts: [],
        prompts: new Map(),
        saves: 0,
        context: {
            chatId: 'Mai - 2026-10-02@12h40m36s920ms',
            characterId: 0,
            characters: [{ avatar: 'Mai.png', name: 'Mai' }],
            name1: 'Wanted',
            name2: 'Mai',
            chat: [{ is_user: false, mes: 'Привет. Ты сегодня рано.' }],
        },
        listeners(event) {
            if (!listeners.has(event)) {
                listeners.set(event, []);
            }
            return listeners.get(event);
        },
        async emit(event, ...args) {
            for (const listener of [...this.listeners(event)]) {
                await listener(...args);
            }
            await settle();
        },
        /** The user sends a message: ST appends it, then emits MESSAGE_SENT. */
        async userSends(text) {
            this.context.chat.push({ is_user: true, mes: text });
            await this.emit('message_sent', this.context.chat.length - 1);
        },
        /** The reply is rendered: ST appends it, then emits CHARACTER_MESSAGE_RENDERED. */
        async characterReplies(text, renderType = 'normal') {
            if (renderType === 'swipe' || renderType === 'regenerate') {
                this.context.chat[this.context.chat.length - 1] = { is_user: false, mes: text };
            } else {
                this.context.chat.push({ is_user: false, mes: text });
            }
            await this.emit('character_message_rendered', this.context.chat.length - 1, renderType);
        },
        calls(pathPrefix) {
            return this.requests.filter(request => request.path.startsWith(pathPrefix));
        },
    };
    globalThis.__stHarness = harness;

    // main.mjs narrates every turn to the console; keep it, but off the test output.
    harness.console = [];
    for (const level of ['log', 'warn', 'error']) {
        console[level] = (...args) => harness.console.push({ level, text: args.join(' ') });
    }

    globalThis.fetch = async (url, init = {}) => {
        const path = String(url).replace(BACKEND, '');
        const body = typeof init.body === 'string' ? JSON.parse(init.body) : null;
        harness.requests.push({ path, method: init.method || 'GET', body });
        return harness.backend(path, init);
    };
    globalThis.toastr = Object.fromEntries(['info', 'success', 'warning', 'error'].map(level => [
        level,
        (message, title, options) => harness.toasts.push({ level, message, title, options }),
    ]));

    const stored = globalThis.__stExtensionSettings;
    if (stored) {
        delete stored[SETTINGS_KEY];
    }
    const { extension_settings } = await import('./st-extensions.mjs');
    extension_settings[SETTINGS_KEY] = serializeExtensionSettings(normalizeExtensionSettings({}));
    Object.assign(extension_settings[SETTINGS_KEY].connection, {
        enabled: true,
        memoryServiceUrl: BACKEND,
        ...pick(settings, ['retrieveTimeoutMs', 'storeTimeoutMs', 'enabled', 'apiKey']),
    });

    loadCount += 1;
    await import(`../../sillytavern-extension/main.mjs?harness=${loadCount}`);
    await settle();
    return harness;
}

function pick(source, keys) {
    return Object.fromEntries(keys.filter(key => key in source).map(key => [key, source[key]]));
}

/** Let fire-and-forget work (the handshake, the audit mirror) finish. */
export async function settle() {
    for (let i = 0; i < 20; i += 1) {
        await new Promise(resolve => setImmediate(resolve));
    }
}
