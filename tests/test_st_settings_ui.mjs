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
