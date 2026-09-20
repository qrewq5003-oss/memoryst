import test from 'node:test';
import assert from 'node:assert/strict';

import {
    DEFAULT_PROMPT_DEPTH,
    DEFAULT_PROMPT_POSITION,
    DEFAULT_PROMPT_ROLE,
    MAX_INJECTION_DEPTH,
    PROMPT_POSITION_BEFORE_PROMPT,
    PROMPT_POSITION_IN_CHAT,
    PROMPT_POSITION_IN_PROMPT,
    PROMPT_ROLE_ASSISTANT,
    PROMPT_ROLE_SYSTEM,
    PROMPT_ROLE_USER,
    findEnumDrift,
    resolveInjectionDepth,
    resolveInjectionSettings,
} from '../sillytavern-extension/injection.mjs';
import {
    buildSettingsUiMarkup,
    mountSettingsUi,
} from '../sillytavern-extension/settings-ui.mjs';
import {
    DEFAULT_SETTINGS,
    normalizeExtensionSettings,
    serializeExtensionSettings,
} from '../sillytavern-extension/settings.mjs';

test('every resolved value is a number, never a string', () => {
    // The original bug: setExtensionPrompt does Number(role), and the extension passed
    // the string 'system', so the stored role was NaN.
    const resolved = resolveInjectionSettings(DEFAULT_SETTINGS);
    for (const key of ['position', 'depth', 'role']) {
        assert.equal(typeof resolved[key], 'number', `${key} must be a number`);
        assert.ok(Number.isFinite(resolved[key]), `${key} must not be NaN`);
    }
    assert.equal(typeof resolved.scan, 'boolean');
});

test('the role is the enum value, not the word', () => {
    assert.equal(resolveInjectionSettings({ promptRole: PROMPT_ROLE_SYSTEM }).role, 0);
    assert.equal(resolveInjectionSettings({ promptRole: PROMPT_ROLE_USER }).role, 1);
    assert.equal(resolveInjectionSettings({ promptRole: PROMPT_ROLE_ASSISTANT }).role, 2);
});

test("a settings.json holding the old string 'system' comes back as the enum", () => {
    // Exactly what Number('system') used to produce, arriving from stored settings.
    const resolved = resolveInjectionSettings({ promptRole: 'system', promptPosition: 'in_prompt' });
    assert.equal(resolved.role, DEFAULT_PROMPT_ROLE);
    assert.equal(resolved.position, DEFAULT_PROMPT_POSITION);
    assert.ok(!Number.isNaN(resolved.role));
});

test('a numeric string from a hand-edited settings file is accepted', () => {
    assert.equal(resolveInjectionSettings({ promptPosition: '1' }).position, PROMPT_POSITION_IN_CHAT);
    assert.equal(resolveInjectionSettings({ promptRole: '2' }).role, PROMPT_ROLE_ASSISTANT);
});

test('a position outside the enum falls back rather than injecting nowhere', () => {
    for (const bogus of [7, -1, 1.5, null, {}, 'BEFORE']) {
        assert.equal(resolveInjectionSettings({ promptPosition: bogus }).position, DEFAULT_PROMPT_POSITION);
    }
});

test('depth is clamped to what SillyTavern accepts', () => {
    assert.equal(resolveInjectionDepth(-5), 0);
    assert.equal(resolveInjectionDepth(0), 0);
    assert.equal(resolveInjectionDepth(4), 4);
    assert.equal(resolveInjectionDepth(2.6), 3);
    assert.equal(resolveInjectionDepth(99999), MAX_INJECTION_DEPTH);
    assert.equal(resolveInjectionDepth('nonsense'), DEFAULT_PROMPT_DEPTH);
});

test('scan defaults on, and false survives being stored', () => {
    assert.equal(resolveInjectionSettings({}).scan, true);
    assert.equal(resolveInjectionSettings({ promptScan: false }).scan, false);
});

test('the defaults reproduce the hardcoded behaviour this replaced', () => {
    // Upgrading must not move anyone's memory block.
    const resolved = resolveInjectionSettings(normalizeExtensionSettings({}));
    assert.deepEqual(resolved, {
        position: PROMPT_POSITION_IN_PROMPT,
        depth: 2,
        scan: true,
        role: PROMPT_ROLE_SYSTEM,
    });
});

test('enum drift against SillyTavern is reported, and silence means agreement', () => {
    const real = { IN_PROMPT: 0, IN_CHAT: 1, BEFORE_PROMPT: 2 };
    const roles = { SYSTEM: 0, USER: 1, ASSISTANT: 2 };
    assert.deepEqual(findEnumDrift(real, roles), []);

    const moved = findEnumDrift({ ...real, IN_CHAT: 5 }, roles);
    assert.equal(moved.length, 1);
    assert.match(moved[0], /IN_CHAT/);
    assert.match(moved[0], /5/);
});

test('a SillyTavern that exports no enums is not reported as drift', () => {
    // Nothing to compare against is not disagreement, and warning would be noise.
    assert.deepEqual(findEnumDrift(undefined, undefined), []);
    assert.deepEqual(findEnumDrift({}, {}), []);
});

test('placement settings round-trip through storage', () => {
    const normalized = normalizeExtensionSettings({
        injection: {
            promptPosition: PROMPT_POSITION_IN_CHAT,
            promptDepth: 4,
            promptRole: PROMPT_ROLE_USER,
            promptScan: false,
        },
    });
    assert.equal(normalized.promptPosition, PROMPT_POSITION_IN_CHAT);
    assert.equal(normalized.promptDepth, 4);
    assert.equal(normalized.promptRole, PROMPT_ROLE_USER);
    assert.equal(normalized.promptScan, false);

    const stored = serializeExtensionSettings(normalized);
    assert.equal(stored.injection.promptPosition, PROMPT_POSITION_IN_CHAT);
    assert.equal(normalizeExtensionSettings(stored).promptDepth, 4);
    assert.equal(normalizeExtensionSettings(stored).promptScan, false);
});

test('a settings file written before placement existed gets the old behaviour', () => {
    const normalized = normalizeExtensionSettings({
        connection: { enabled: true, memoryServiceUrl: 'http://host:8001' },
    });
    assert.equal(normalized.promptPosition, PROMPT_POSITION_IN_PROMPT);
    assert.equal(normalized.promptRole, PROMPT_ROLE_SYSTEM);
    assert.equal(normalized.promptScan, true);
});

test('the panel renders selects with the stored value marked selected', () => {
    const markup = buildSettingsUiMarkup(normalizeExtensionSettings({
        injection: { promptPosition: PROMPT_POSITION_BEFORE_PROMPT, promptRole: PROMPT_ROLE_ASSISTANT },
    }));
    assert.match(markup, /<select data-memory-setting="promptPosition">/);
    assert.match(markup, /<option value="2" selected>Before prompt[^<]*<\/option>/);
    assert.match(markup, /<select data-memory-setting="promptRole">/);
    assert.match(markup, /<option value="2" selected>Assistant<\/option>/);
});

test('a select hands the settings layer a number, not the option string', () => {
    // The whole point: whatever the panel writes has to survive Number() in SillyTavern.
    const changes = [];
    const select = {
        value: String(PROMPT_POSITION_IN_CHAT),
        listeners: {},
        addEventListener(name, fn) {
            this.listeners[name] = fn;
        },
    };
    const panel = {
        innerHTML: '',
        querySelector(selector) {
            return selector.includes('promptPosition') ? select : null;
        },
    };
    const documentRef = {
        querySelector: () => ({
            querySelector: () => panel,
            appendChild: () => {},
        }),
        createElement: () => panel,
    };

    mountSettingsUi({
        document: documentRef,
        settings: normalizeExtensionSettings({}),
        onSettingsChanged: (key, value) => changes.push([key, value]),
    });

    // Selects fire 'change', not 'input' - binding the wrong one loses every edit.
    assert.equal(typeof select.listeners.change, 'function');
    select.listeners.change();

    assert.deepEqual(changes, [['promptPosition', PROMPT_POSITION_IN_CHAT]]);
    assert.equal(typeof changes[0][1], 'number');
});
