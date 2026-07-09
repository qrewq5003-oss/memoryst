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
        const idPattern = /<(button|select|span)\b[^>]*\bid="([^"]+)"[^>]*>/g;
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
        if (selector === '#memory-service-settings-panel') {
            return this.children.find(child => child.id === 'memory-service-settings-panel') || null;
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
    const panel = document.host.querySelector('#memory-service-settings-panel');
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

    const panel = document.host.querySelector('#memory-service-settings-panel');
    const baselineButton = panel.querySelector('#memory-service-apply-baseline');
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

    const panel = document.host.querySelector('#memory-service-settings-panel');
    const select = panel.querySelector('#memory-service-scene-extraction-model');
    const confirmBtn = panel.querySelector('#memory-service-scene-extraction-save');
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
