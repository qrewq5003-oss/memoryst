import test from 'node:test';
import assert from 'node:assert/strict';

import { applyRecommendedBaselineSettings, DEFAULT_SETTINGS } from '../sillytavern-extension/settings.mjs';
import {
    buildModelOptionsMarkup,
    buildSettingsUiMarkup,
    loadSceneExtractionModelOptions,
    mountSettingsUi,
    renderSettingsUi,
} from '../sillytavern-extension/settings-ui.mjs';

class FakeInput {
    constructor({ type = 'text', value = '', checked = false } = {}) {
        this.type = type;
        this.value = value;
        this.checked = checked;
        this.textContent = '';
        this.innerHTML = '';
        this.listeners = new Map();
    }

    addEventListener(eventName, handler) {
        this.listeners.set(eventName, handler);
    }

    dispatch(eventName) {
        this.listeners.get(eventName)?.();
    }
}

class FakeElement {
    constructor(tagName = 'div') {
        this.tagName = tagName;
        this.id = '';
        this.children = [];
        this._innerHTML = '';
        this.inputs = new Map();
        this.namedNodes = new Map();
    }

    set innerHTML(value) {
        this._innerHTML = value;
        this.inputs.clear();
        this.namedNodes.clear();

        const inputPattern = /<input data-memory-setting="([^"]+)" type="([^"]+)"([^>]*)>/g;
        let match;
        while ((match = inputPattern.exec(value)) !== null) {
            const [, key, type, attrs] = match;
            const valueMatch = attrs.match(/value="([^"]*)"/);
            this.inputs.set(key, new FakeInput({
                type,
                value: valueMatch ? valueMatch[1] : '',
                checked: attrs.includes('checked'),
            }));
        }

        // Generic id-based lookup for elements not covered by the
        // data-memory-setting pattern above (buttons, selects, status spans).
        //
        // `input` and `textarea` are here because leaving them out made whole controls
        // invisible to every test in this file: the backfill file input shipped hidden by
        // SillyTavern's global stylesheet and nothing here could even see it to notice.
        // Settings inputs carry data-memory-setting and no id, so they stay with the
        // pattern above and are not matched twice.
        const idPattern = /<(button|select|span|input|textarea)\b[^>]*\bid="([^"]+)"[^>]*>/g;
        let idMatch;
        while ((idMatch = idPattern.exec(value)) !== null) {
            const [, tag, id] = idMatch;
            this.namedNodes.set(`#${id}`, new FakeInput({ type: tag }));
        }
    }

    get innerHTML() {
        return this._innerHTML;
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    querySelector(selector) {
        if (selector === '#memoryst-settings-panel') {
            return this.children.find(child => child.id === 'memoryst-settings-panel') || null;
        }

        if (selector.startsWith('[data-memory-setting="')) {
            const key = selector.match(/\[data-memory-setting="([^"]+)"\]/)?.[1];
            return key ? this.inputs.get(key) || null : null;
        }

        return this.namedNodes.get(selector) || null;
    }
}

class FakeDocument {
    constructor(withHost = true) {
        this.host = withHost ? new FakeElement('div') : null;
    }

    querySelector(selector) {
        if (!this.host) {
            return null;
        }
        if (selector === '#extensions_settings2') {
            return this.host;
        }
        return null;
    }

    createElement(tagName) {
        return new FakeElement(tagName);
    }
}

// Never let a test accidentally fall through to the real global fetch (Node
// 18+ has one) - renderSettingsUi/mountSettingsUi eagerly load the Scene
// Extraction Model dropdown on mount, so any test exercising them without an
// explicit stub would otherwise fire an uncontrolled real network call.
const rejectingFetch = async () => {
    throw new Error('unexpected fetch call in test');
};

test('settings UI markup exposes grouped sections and baseline affordance', () => {
    const markup = buildSettingsUiMarkup(DEFAULT_SETTINGS);

    assert.match(markup, /Connection/);
    assert.match(markup, /Retrieval/);
    assert.match(markup, /Prompt Injection Budget/);
    assert.match(markup, /Audit/);
    assert.match(markup, /Apply Recommended Baseline/);
});

test('renderSettingsUi mounts and persists field changes through callbacks', () => {
    const document = new FakeDocument(true);
    const changes = [];

    const rendered = renderSettingsUi({
        document,
        settings: DEFAULT_SETTINGS,
        onSettingsChanged: (fieldKey, nextValue) => changes.push([fieldKey, nextValue]),
        onApplyRecommendedBaseline: () => {},
        fetchImpl: rejectingFetch,
    });

    assert.equal(rendered, true);
    const panel = document.host.querySelector('#memoryst-settings-panel');
    assert.ok(panel);

    const enabledInput = panel.querySelector('[data-memory-setting="enabled"]');
    enabledInput.checked = true;
    enabledInput.dispatch('change');

    const retrieveLimitInput = panel.querySelector('[data-memory-setting="retrieveLimit"]');
    retrieveLimitInput.value = '7';
    retrieveLimitInput.dispatch('input');

    assert.deepEqual(changes, [
        ['enabled', true],
        ['retrieveLimit', 7],
    ]);
});

test('baseline button uses recommended long-chat settings', () => {
    const document = new FakeDocument(true);
    const applied = [];

    renderSettingsUi({
        document,
        settings: DEFAULT_SETTINGS,
        onSettingsChanged: () => {},
        onApplyRecommendedBaseline: nextSettings => applied.push(nextSettings),
        fetchImpl: rejectingFetch,
    });

    const panel = document.host.querySelector('#memoryst-settings-panel');
    const baselineButton = panel.querySelector('#memoryst-apply-baseline');
    baselineButton.dispatch('click');

    assert.equal(applied.length, 1);
    assert.deepEqual(applied[0], applyRecommendedBaselineSettings(DEFAULT_SETTINGS));
    assert.equal(applied[0].retrieveLimit, 5);
    assert.equal(applied[0].maxPromptChars, 1500);
});

test('mountSettingsUi does not crash when settings host is missing', () => {
    const document = new FakeDocument(false);
    const scheduled = [];

    const mounted = mountSettingsUi({
        document,
        settings: DEFAULT_SETTINGS,
        onSettingsChanged: () => {},
        onApplyRecommendedBaseline: () => {},
        retries: 1,
        retryDelayMs: 123,
        scheduleRetry: (fn, delay) => scheduled.push(delay),
    });

    assert.equal(mounted, false);
    assert.deepEqual(scheduled, [123]);
});

test('buildModelOptionsMarkup lists the catalog with the current value selected', () => {
    const markup = buildModelOptionsMarkup(['deepseek-chat', 'deepseek/deepseek-v4-pro'], 'deepseek/deepseek-v4-pro');

    assert.match(markup, /<option value="deepseek-chat">deepseek-chat<\/option>/);
    assert.match(markup, /<option value="deepseek\/deepseek-v4-pro" selected>deepseek\/deepseek-v4-pro \(current\)<\/option>/);
    assert.match(markup, /<option value="">\(use LLM Provider panel default\)<\/option>/);
});

test('buildModelOptionsMarkup keeps the saved value selectable even if the catalog omits it', () => {
    const markup = buildModelOptionsMarkup(['deepseek-chat'], 'some/retired-model');

    assert.match(markup, /<option value="some\/retired-model" selected>some\/retired-model \(current\)<\/option>/);
    assert.match(markup, /<option value="deepseek-chat">deepseek-chat<\/option>/);
});

test('loadSceneExtractionModelOptions populates the select from the backend catalog', async () => {
    const select = new FakeInput({ type: 'select' });
    const resultEl = new FakeInput({ type: 'span' });
    const calls = [];

    const fetchImpl = async (url, options) => {
        calls.push({ url, options });
        return {
            ok: true,
            json: async () => ({ models: ['deepseek-chat', 'deepseek/deepseek-v4-pro'] }),
        };
    };

    const ok = await loadSceneExtractionModelOptions({
        select,
        resultEl,
        memoryServiceUrl: 'http://127.0.0.1:8001',
        apiKey: 'secret',
        selectedValue: 'deepseek/deepseek-v4-pro',
        fetchImpl,
    });

    assert.equal(ok, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'http://127.0.0.1:8001/memory/models');
    assert.equal(calls[0].options.headers['X-API-Key'], 'secret');
    assert.match(select.innerHTML, /deepseek\/deepseek-v4-pro \(current\)/);
    assert.match(resultEl.textContent, /Loaded 2 models/);
});

test('loadSceneExtractionModelOptions leaves the select untouched and reports an error on fetch failure', async () => {
    const select = new FakeInput({ type: 'select' });
    select.innerHTML = '<option value="deepseek/deepseek-v4-pro" selected>deepseek/deepseek-v4-pro (current)</option>';
    const resultEl = new FakeInput({ type: 'span' });

    const ok = await loadSceneExtractionModelOptions({
        select,
        resultEl,
        memoryServiceUrl: 'http://127.0.0.1:8001',
        selectedValue: 'deepseek/deepseek-v4-pro',
        fetchImpl: async () => { throw new Error('network down'); },
    });

    assert.equal(ok, false);
    assert.match(resultEl.textContent, /Could not load model list: network down/);
    // Untouched: still shows the previously-saved value, not wiped out.
    assert.match(select.innerHTML, /deepseek\/deepseek-v4-pro \(current\)/);
});

test('renderSettingsUi wires the Scene Extraction Model select+Confirm to the same save callback as other fields', async () => {
    const document = new FakeDocument(true);
    const changes = [];

    const fetchImpl = async () => ({
        ok: true,
        json: async () => ({ models: ['deepseek-chat', 'deepseek/deepseek-v4-pro', 'zai-org/glm-4.7'] }),
    });

    renderSettingsUi({
        document,
        settings: { ...DEFAULT_SETTINGS, sceneExtractionModel: 'deepseek-chat' },
        onSettingsChanged: (fieldKey, nextValue) => changes.push([fieldKey, nextValue]),
        onApplyRecommendedBaseline: () => {},
        fetchImpl,
    });

    // Eager load on mount happens async - let the microtask queue drain.
    await new Promise(resolve => setTimeout(resolve, 0));

    const panel = document.host.querySelector('#memoryst-settings-panel');
    const select = panel.querySelector('#memoryst-scene-extraction-model');
    const confirmBtn = panel.querySelector('#memoryst-scene-extraction-save');
    assert.ok(select);
    assert.ok(confirmBtn);
    assert.match(select.innerHTML, /zai-org\/glm-4\.7/);

    // Simulate picking a different model from the dropdown, then confirming.
    select.value = 'deepseek/deepseek-v4-pro';
    confirmBtn.dispatch('click');

    assert.deepEqual(changes, [['sceneExtractionModel', 'deepseek/deepseek-v4-pro']]);

    // Not part of the generic field loop - no data-memory-setting input should
    // exist for it (regression guard against dual-registration/auto-save).
    assert.equal(panel.querySelector('[data-memory-setting="sceneExtractionModel"]'), null);
});

/**
 * Two defects the panel's own screenshot showed, both invisible to the tests that existed.
 *
 * The first is a CSS specificity trap. `#memoryst-settings-panel p { margin: 0 }` outranks
 * a bare `.memoryst-settings-intro { margin-top: 6px }`, so that margin never applied. With
 * one intro paragraph nothing looked wrong; adding the settings-key warning as a second
 * one made the two run together as a single block of prose, so the warning read as the
 * tail of a sentence about retrieval controls.
 *
 * The second is older: SillyTavern sets display:none on every input[type=file] globally,
 * so the backfill upload control was in the markup and absent from the screen. The label
 * above it promised an upload the panel offered no way to begin. Confirmed present before
 * the rename - identical markup, only the id differed.
 */
test('the settings-key warning is its own block, not a second intro paragraph', () => {
    const html = buildSettingsUiMarkup(DEFAULT_SETTINGS);
    assert.match(html, /class="memoryst-settings-note"/);
    // Sharing the intro class is what made it read as one paragraph.
    const notes = html.match(/<p class="memoryst-settings-intro">/g) ?? [];
    assert.equal(notes.length, 1, 'the warning is still styled as an intro paragraph');
});

test('the spacing rules outrank the panel-wide margin reset', () => {
    const html = buildSettingsUiMarkup(DEFAULT_SETTINGS);
    // An id in the selector, so it beats `#memoryst-settings-panel p { margin: 0 }`.
    for (const cls of ['memoryst-settings-intro', 'memoryst-settings-note']) {
        assert.match(
            html,
            new RegExp(`#memoryst-settings-panel\\s+\\.${cls}\\s*\\{`),
            `.${cls} is styled by a bare class rule, which the margin reset overrides`,
        );
    }
});

test('the hidden file input is reachable through a label', () => {
    const html = buildSettingsUiMarkup(DEFAULT_SETTINGS);
    assert.match(html, /<input type="file" id="memoryst-backfill-file"/);
    // SillyTavern hides the input itself; a label pointing at it still opens the picker.
    assert.match(html, /<label for="memoryst-backfill-file"[^>]*class="menu_button"/);
});

test('the file button keeps its label on one line', () => {
    // ST's .menu_button is `width: min-content`, which wrapped "Choose file" onto two
    // lines. min-content of nowrap text is the whole string, so the button sizes to fit.
    const html = buildSettingsUiMarkup(DEFAULT_SETTINGS);
    const label = html.match(/<label for="memoryst-backfill-file"[^>]*>/)?.[0] ?? '';
    assert.match(label, /white-space:\s*nowrap/);
});

test('the chosen filename is echoed, since the input cannot be seen', () => {
    const html = buildSettingsUiMarkup(DEFAULT_SETTINGS);
    assert.match(html, /id="memoryst-backfill-filename"/);
});

test('picking a file writes its name into the readout', () => {
    const document = new FakeDocument();
    mountSettingsUi({ document, settings: DEFAULT_SETTINGS, onChange() {} });
    const panel = document.host.querySelector('#memoryst-settings-panel');

    const fileInput = panel.querySelector('#memoryst-backfill-file');
    const readout = panel.querySelector('#memoryst-backfill-filename');
    assert.ok(fileInput, 'the fake DOM cannot see the file input');
    assert.ok(readout, 'the fake DOM cannot see the filename readout');

    fileInput.files = [{ name: 'Valeria Mendoza - 2026-07-09.jsonl' }];
    fileInput.dispatch('change');

    assert.equal(readout.textContent, 'Valeria Mendoza - 2026-07-09.jsonl');
});

test('no file chosen leaves the readout empty rather than showing undefined', () => {
    const document = new FakeDocument();
    mountSettingsUi({ document, settings: DEFAULT_SETTINGS, onChange() {} });
    const panel = document.host.querySelector('#memoryst-settings-panel');

    const fileInput = panel.querySelector('#memoryst-backfill-file');
    const readout = panel.querySelector('#memoryst-backfill-filename');
    fileInput.files = [];
    fileInput.dispatch('change');

    assert.equal(readout.textContent, '');
});

// The outage toast sends the user here to recover missed turns. The chat file sits in
// Termux's private storage, which a browser file picker on Android cannot open - so with
// nothing chosen and nothing pasted, the button has to read the open chat itself.
async function clickBackfill({ messages, pasted = '' }) {
    const document = new FakeDocument();
    const requests = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, init) => {
        requests.push({ url, body: JSON.parse(init.body) });
        return {
            ok: true,
            status: 200,
            json: async () => ({ stored: 3, skipped: 0, duplicates: 2, processed: messages.length }),
        };
    };
    try {
        mountSettingsUi({
            document,
            settings: { ...DEFAULT_SETTINGS, memoryServiceUrl: 'http://memoryst.test' },
            onChange() {},
            getChatContext: () => ({ chatId: 'Mai - 2026-10-02', characterId: 'Mai.png', messages }),
        });
        const panel = document.host.querySelector('#memoryst-settings-panel');
        panel.querySelector('#memoryst-backfill-file').files = [];
        panel.querySelector('#memoryst-backfill-text').value = pasted;
        await panel.querySelector('#memoryst-backfill-btn').listeners.get('click')();
        return { requests, status: panel.querySelector('#memoryst-backfill-status').textContent };
    } finally {
        globalThis.fetch = originalFetch;
    }
}

test('Backfill with nothing chosen imports the open chat', async () => {
    const messages = [
        { role: 'user', text: 'Ты дома?' },
        { role: 'assistant', text: 'Дома.' },
        { role: 'assistant', text: '' },
    ];
    const { requests, status } = await clickBackfill({ messages });

    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, 'http://memoryst.test/memory/backfill');
    assert.deepEqual(requests[0].body, {
        chat_id: 'Mai - 2026-10-02',
        character_id: 'Mai.png',
        messages: messages.slice(0, 2),
    });
    assert.match(status, /Done: 3 stored/);
});

test('pasted text still wins over the open chat', async () => {
    const { requests } = await clickBackfill({
        messages: [{ role: 'user', text: 'из чата' }],
        pasted: 'user: из поля',
    });
    assert.deepEqual(requests[0].body.messages, [{ role: 'user', text: 'из поля' }]);
});

test('no open chat and nothing given: says what to do, sends nothing', async () => {
    const { requests, status } = await clickBackfill({ messages: [] });
    assert.deepEqual(requests, []);
    assert.match(status, /Open a chat/);
});

// ---------------------------------------------------------------- the other buttons
//
// Every button of the panel, driven through the same fake DOM: this was the one open
// remainder of finding 2 in docs/coverage_gap_audit_2026-10-03.md, and the safety net
// the split of renderSettingsUi (docs/refactoring_plan_2026-10-13.md, item 2) needs.

/** Mount the panel against a scripted backend; returns the panel and what was sent. */
function mountPanel({ backend, chatContext = {}, settings = {}, confirmImpl, changes = [] } = {}) {
    const document = new FakeDocument();
    const requests = [];
    const fetchImpl = async (url, init = {}) => {
        // The panel loads the model catalog on mount; that one is covered above and
        // is not what these tests send.
        if (!String(url).endsWith('/memory/models')) {
            requests.push({ url, method: init.method || 'GET', headers: init.headers, body: init.body ? JSON.parse(init.body) : null });
        }
        if (typeof backend !== 'function') {
            return { ok: true, status: 200, json: async () => ({}) };
        }
        return backend(url, init);
    };
    const originalFetch = globalThis.fetch;
    // The backfill and delete handlers do not take fetchImpl; they use the global.
    globalThis.fetch = fetchImpl;
    mountSettingsUi({
        document,
        settings: { ...DEFAULT_SETTINGS, memoryServiceUrl: 'http://memoryst.test', apiKey: 'secret', ...settings },
        onSettingsChanged: (key, value) => changes.push([key, value]),
        onApplyRecommendedBaseline() {},
        getChatContext: () => chatContext,
        fetchImpl,
        confirmImpl,
    });
    const panel = document.host.querySelector('#memoryst-settings-panel');
    const click = async id => panel.querySelector(id).listeners.get('click')();
    const status = () => panel.querySelector('#memoryst-backfill-status').textContent;
    const restore = () => { globalThis.fetch = originalFetch; };
    return { panel, requests, click, status, restore, changes };
}

const jsonlExport = [
    JSON.stringify({ chat_metadata: {}, user_name: 'Wanted', character_name: 'Mai' }),
    JSON.stringify({ name: 'Wanted', mes: 'Ты дома?', is_user: true }),
    JSON.stringify({ name: 'Mai', mes: 'Дома.', is_user: false }),
    JSON.stringify({ role: 'user', content: 'из другого формата' }),
    JSON.stringify({ name: 'Mai', mes: '   ', is_user: false }),
    '{not json',
    JSON.stringify({ something: 'else' }),
    '',
].join('\n');

test('Backfill from a .jsonl file: both line shapes, the ignored ones counted', async () => {
    const p = mountPanel({
        backend: async () => ({ ok: true, status: 200, json: async () => ({ stored: 2, skipped: 0, duplicates: 1, processed: 3 }) }),
        chatContext: { chatId: 'Mai - 2026-10-02', characterId: 'Mai.png', messages: [{ role: 'user', text: 'не это' }] },
    });
    try {
        const fileInput = p.panel.querySelector('#memoryst-backfill-file');
        fileInput.files = [{ name: 'Mai.jsonl', text: async () => jsonlExport }];
        p.panel.querySelector('#memoryst-backfill-text').value = 'user: и не это';
        const seen = [];
        const backfillBtn = p.panel.querySelector('#memoryst-backfill-btn');
        Object.defineProperty(backfillBtn, 'disabled', {
            set(value) { seen.push(value); },
            get() { return seen.at(-1) ?? false; },
        });

        await p.click('#memoryst-backfill-btn');

        assert.equal(p.requests.length, 1, 'the file wins over the paste and the open chat');
        assert.equal(p.requests[0].url, 'http://memoryst.test/memory/backfill');
        assert.equal(p.requests[0].headers['X-API-Key'], 'secret');
        assert.deepEqual(p.requests[0].body, {
            chat_id: 'Mai - 2026-10-02',
            character_id: 'Mai.png',
            messages: [
                { role: 'user', text: 'Ты дома?' },
                { role: 'assistant', text: 'Дома.' },
                { role: 'user', text: 'из другого формата' },
            ],
        });
        assert.match(p.status(), /Done: 2 stored, 0 skipped, 1 duplicates \(3 processed\)/);
        assert.deepEqual(seen, [true, false], 'the button is locked for the request and released after');
    } finally {
        p.restore();
    }
});

test('a .jsonl with nothing usable says what it ignored, and sends nothing', async () => {
    const p = mountPanel();
    try {
        const fileInput = p.panel.querySelector('#memoryst-backfill-file');
        fileInput.files = [{ name: 'x.jsonl', text: async () => '{oops\n{"a":1}\n' }];
        await p.click('#memoryst-backfill-btn');
        assert.deepEqual(p.requests, []);
        assert.match(p.status(), /No usable messages in \.jsonl file\. Ignored 1 not valid JSON, 1 in an unrecognized shape\./);

        fileInput.files = [{ name: 'empty.jsonl', text: async () => '' }];
        await p.click('#memoryst-backfill-btn');
        assert.deepEqual(p.requests, []);
        assert.equal(p.status(), 'No valid messages in .jsonl file.');
        assert.equal(p.panel.querySelector('#memoryst-backfill-btn').disabled, false);
    } finally {
        p.restore();
    }
});

test('a file that cannot be read is an error on the status line, not a hang', async () => {
    const p = mountPanel();
    try {
        p.panel.querySelector('#memoryst-backfill-file').files = [{ name: 'x.jsonl', text: async () => { throw new Error('NotReadableError'); } }];
        await p.click('#memoryst-backfill-btn');
        assert.equal(p.status(), 'Error: NotReadableError');
        assert.equal(p.panel.querySelector('#memoryst-backfill-btn').disabled, false);
    } finally {
        p.restore();
    }
});

test('pasted text with no recognisable line explains the format', async () => {
    const p = mountPanel();
    try {
        p.panel.querySelector('#memoryst-backfill-file').files = [];
        p.panel.querySelector('#memoryst-backfill-text').value = 'просто текст без роли\nи ещё';
        await p.click('#memoryst-backfill-btn');
        assert.deepEqual(p.requests, []);
        assert.equal(p.status(), 'No valid messages. Use format: "user: text"');
    } finally {
        p.restore();
    }
});

test('Backfill reports a backend error and a transport error, and releases the button', async () => {
    const http = mountPanel({
        backend: async () => ({ ok: false, status: 500, statusText: 'Internal Server Error' }),
        chatContext: { chatId: 'c', characterId: 'Mai.png', messages: [{ role: 'user', text: 'x' }] },
    });
    try {
        http.panel.querySelector('#memoryst-backfill-file').files = [];
        await http.click('#memoryst-backfill-btn');
        assert.equal(http.status(), 'Error: 500 Internal Server Error');
        assert.equal(http.panel.querySelector('#memoryst-backfill-btn').disabled, false);
    } finally {
        http.restore();
    }

    const network = mountPanel({
        backend: async () => { throw new Error('Failed to fetch'); },
        chatContext: { chatId: 'c', characterId: 'Mai.png', messages: [{ role: 'user', text: 'x' }] },
    });
    try {
        network.panel.querySelector('#memoryst-backfill-file').files = [];
        await network.click('#memoryst-backfill-btn');
        assert.equal(network.status(), 'Error: Failed to fetch');
        assert.equal(network.panel.querySelector('#memoryst-backfill-btn').disabled, false);
    } finally {
        network.restore();
    }
});

test('Delete Chat Memories: no chat open means nothing to ask about', async () => {
    let asked = 0;
    const p = mountPanel({ chatContext: {}, confirmImpl: () => { asked += 1; return true; } });
    try {
        await p.click('#memoryst-delete-chat-btn');
        assert.equal(asked, 0);
        assert.deepEqual(p.requests, []);
        assert.equal(p.status(), 'No chat selected.');
    } finally {
        p.restore();
    }
});

test('Delete Chat Memories: declining the confirm sends nothing', async () => {
    const questions = [];
    const p = mountPanel({
        chatContext: { chatId: 'Mai - 2026-10-02' },
        confirmImpl: question => { questions.push(question); return false; },
    });
    try {
        await p.click('#memoryst-delete-chat-btn');
        assert.deepEqual(p.requests, []);
        assert.equal(p.status(), '');
        assert.match(questions[0], /Delete ALL memories for chat "Mai - 2026-10-02"\? This cannot be undone\./);
    } finally {
        p.restore();
    }
});

test('Delete Chat Memories: confirmed, it deletes exactly this chat and says how many', async () => {
    const p = mountPanel({
        backend: async () => ({ ok: true, status: 200, json: async () => ({ deleted: 12 }) }),
        chatContext: { chatId: 'Mai - 2026-10-02@12h40m36s920ms' },
        confirmImpl: () => true,
    });
    try {
        await p.click('#memoryst-delete-chat-btn');
        assert.equal(p.requests.length, 1);
        assert.equal(p.requests[0].method, 'DELETE');
        assert.equal(p.requests[0].url, 'http://memoryst.test/memory/chat/Mai%20-%202026-10-02%4012h40m36s920ms');
        assert.equal(p.requests[0].headers['X-API-Key'], 'secret');
        assert.equal(p.requests[0].headers['Content-Type'], undefined, 'no body, no content type');
        assert.equal(p.status(), 'Deleted 12 memories for this chat.');
        assert.equal(p.panel.querySelector('#memoryst-delete-chat-btn').disabled, false);
    } finally {
        p.restore();
    }
});

test('Delete Chat Memories reports a backend error and a transport error', async () => {
    const http = mountPanel({
        backend: async () => ({ ok: false, status: 404, statusText: 'Not Found' }),
        chatContext: { chatId: 'c' },
        confirmImpl: () => true,
    });
    try {
        await http.click('#memoryst-delete-chat-btn');
        assert.equal(http.status(), 'Error: 404 Not Found');
        assert.equal(http.panel.querySelector('#memoryst-delete-chat-btn').disabled, false);
    } finally {
        http.restore();
    }

    const network = mountPanel({
        backend: async () => { throw new Error('Failed to fetch'); },
        chatContext: { chatId: 'c' },
        confirmImpl: () => true,
    });
    try {
        await network.click('#memoryst-delete-chat-btn');
        assert.equal(network.status(), 'Error: Failed to fetch');
        assert.equal(network.panel.querySelector('#memoryst-delete-chat-btn').disabled, false);
    } finally {
        network.restore();
    }
});

test('without a confirm() at all, a delete is refused rather than waved through', async () => {
    const p = mountPanel({ chatContext: { chatId: 'c' } });
    try {
        assert.equal(typeof globalThis.confirm, 'undefined', 'node has no confirm, which is the point');
        await p.click('#memoryst-delete-chat-btn');
        assert.deepEqual(p.requests, []);
    } finally {
        p.restore();
    }
});

test('Confirm on the model select with the blank option saves the provider default', async () => {
    const p = mountPanel({ backend: async () => ({ ok: true, json: async () => ({ models: ['deepseek-chat'] }) }) });
    try {
        await new Promise(resolve => setTimeout(resolve, 0));
        p.panel.querySelector('#memoryst-scene-extraction-model').value = '';
        await p.click('#memoryst-scene-extraction-save');
        assert.deepEqual(p.changes, [['sceneExtractionModel', '']]);
        assert.equal(p.panel.querySelector('#memoryst-scene-extraction-result').textContent, 'Saved: (provider default)');
    } finally {
        p.restore();
    }
});

test('a backend that answers the model catalog with an error says so, with the status', async () => {
    const select = new FakeInput({ type: 'select' });
    const resultEl = new FakeInput({ type: 'span' });
    const ok = await loadSceneExtractionModelOptions({
        select,
        resultEl,
        memoryServiceUrl: 'http://127.0.0.1:8001',
        fetchImpl: async () => ({ ok: false, status: 502 }),
    });
    assert.equal(ok, false);
    assert.equal(resultEl.textContent, 'Could not load model list: 502');
});
