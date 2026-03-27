import test from 'node:test';
import assert from 'node:assert/strict';

import { applyRecommendedBaselineSettings, DEFAULT_SETTINGS } from '../sillytavern-extension/settings.mjs';
import {
    buildSettingsUiMarkup,
    mountSettingsUi,
    renderSettingsUi,
} from '../sillytavern-extension/settings-ui.mjs';

class FakeInput {
    constructor({ type = 'text', value = '', checked = false } = {}) {
        this.type = type;
        this.value = value;
        this.checked = checked;
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

        if (value.includes('id="memory-service-apply-baseline"')) {
            this.namedNodes.set('#memory-service-apply-baseline', new FakeInput({ type: 'button' }));
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
    });

    const panel = document.host.querySelector('#memory-service-settings-panel');
    const baselineButton = panel.querySelector('#memory-service-apply-baseline');
    baselineButton.dispatch('click');

    assert.equal(applied.length, 1);
    assert.deepEqual(applied[0], applyRecommendedBaselineSettings(DEFAULT_SETTINGS));
    assert.equal(applied[0].retrieveLimit, 5);
    assert.equal(applied[0].maxPromptChars, 520);
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
