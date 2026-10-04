/**
 * The user hears about an outage once, and about the recovery once.
 *
 * 2026-10-02: memoryst was down for a whole evening of play, 20 turns, every retrieve
 * and store failing with `Failed to fetch` - and the extension said so only to the
 * browser console. Nothing was stored and nobody knew.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    BACKEND_DOWN_MESSAGE,
    backendBackMessage,
    classifyOutcome,
    createBackendStatus,
} from '../sillytavern-extension/backend-status.mjs';

const timeoutError = Object.assign(new Error('fetch_timeout_8000ms'), { name: 'MemoryServiceTimeout' });
const networkError = new TypeError('Failed to fetch');

test('a network failure and a 5xx are an outage; a 4xx is a live backend', () => {
    assert.equal(classifyOutcome({ error: networkError }), 'down');
    assert.equal(classifyOutcome({ httpStatus: 502 }), 'down');
    assert.equal(classifyOutcome({ httpStatus: 422 }), 'rejected');
    assert.equal(classifyOutcome({ httpStatus: 200 }), 'ok');
});

test('our own deadline is not an outage: the backend is likely busy extracting', () => {
    assert.equal(classifyOutcome({ error: timeoutError }), 'timeout');
    const status = createBackendStatus();
    assert.equal(status.record('store', 'timeout'), null);
    assert.equal(status.snapshot().state, 'unknown');
});

test('the evening of 2026-10-02: one warning, not twenty', () => {
    const status = createBackendStatus();
    const notices = [];
    for (let turn = 0; turn < 20; turn += 1) {
        notices.push(status.record('retrieve', 'down'), status.record('store', 'down'));
    }
    const shown = notices.filter(Boolean);
    assert.deepEqual(shown, [{ level: 'error', message: BACKEND_DOWN_MESSAGE }]);
    assert.deepEqual(status.snapshot(), { state: 'down', missedStores: 20 });
});

test('the recovery says how many turns were not stored, then forgets them', () => {
    const status = createBackendStatus();
    status.record('store', 'down');
    status.record('retrieve', 'down');
    status.record('store', 'down');

    const back = status.record('handshake', 'ok');
    assert.deepEqual(back, { level: 'success', message: backendBackMessage(2), missedStores: 2 });
    assert.match(back.message, /2/);
    assert.match(back.message, /Backfill Current Chat/);
    assert.deepEqual(status.snapshot(), { state: 'up', missedStores: 0 });

    // Nothing more to say while it stays up.
    assert.equal(status.record('retrieve', 'ok'), null);
});

test('a page loaded with the backend down warns; one loaded with it up says nothing', () => {
    const downAtLoad = createBackendStatus();
    assert.equal(downAtLoad.record('handshake', 'down').level, 'error');

    const upAtLoad = createBackendStatus();
    assert.equal(upAtLoad.record('handshake', 'ok'), null);
});

test('an answered 4xx ends an outage', () => {
    const status = createBackendStatus();
    status.record('retrieve', 'down');
    assert.equal(status.record('retrieve', 'rejected').level, 'success');
    assert.equal(backendBackMessage(0), 'memoryst снова на связи.');
});

test('a second outage warns again', () => {
    const status = createBackendStatus();
    status.record('retrieve', 'down');
    status.record('retrieve', 'ok');
    assert.equal(status.record('store', 'down').level, 'error');
});

// main.mjs imports SillyTavern and cannot be loaded here, so its wiring is checked as
// text: every request that tells us something about the backend must report it, and a
// throw after a response must not be mistaken for an outage.
test('main.mjs reports every request, and only unanswered throws count as down', () => {
    const source = readFileSync(new URL('../sillytavern-extension/main.mjs', import.meta.url), 'utf8');
    for (const kind of ['handshake', 'retrieve', 'store']) {
        assert.match(
            source,
            new RegExp(`reportBackendOutcome\\('${kind}', classifyOutcome\\(\\{ httpStatus: response\\.status \\}\\)\\)`),
            `${kind}: answered requests are not reported`,
        );
        assert.match(
            source,
            new RegExp(`if \\(!responded\\) \\{\\s+reportBackendOutcome\\('${kind}', classifyOutcome\\(\\{ error \\}\\)\\)`),
            `${kind}: a throw is reported without checking whether a response arrived`,
        );
    }
});
