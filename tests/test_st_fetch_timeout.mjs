import test from 'node:test';
import assert from 'node:assert/strict';

import {
    DEFAULT_RETRIEVE_TIMEOUT_MS,
    DEFAULT_STORE_TIMEOUT_MS,
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    fetchWithTimeout,
    isTimeoutError,
    resolveTimeoutMs,
} from '../sillytavern-extension/http.mjs';
import { fetchTrackers } from '../sillytavern-extension/trackers.mjs';
import {
    normalizeExtensionSettings,
    serializeExtensionSettings,
} from '../sillytavern-extension/settings.mjs';

/** A fetch that never settles - the failure this whole module exists for. */
function hangingFetch() {
    return (_url, options = {}) => new Promise((_resolve, reject) => {
        options.signal?.addEventListener('abort', () => {
            const error = new Error('The operation was aborted.');
            error.name = 'AbortError';
            reject(error);
        });
    });
}

function okFetch(body = {}) {
    return async () => ({ ok: true, status: 200, json: async () => body });
}

test('a request that never answers rejects instead of hanging forever', async () => {
    const started = Date.now();
    await assert.rejects(
        fetchWithTimeout('http://backend/memory/retrieve', {}, {
            timeoutMs: 40,
            fetchImpl: hangingFetch(),
        }),
        error => {
            assert.ok(isTimeoutError(error), 'should be recognisable as a timeout');
            assert.equal(error.message, 'fetch_timeout_40ms');
            assert.equal(error.timeoutMs, 40);
            return true;
        },
    );
    assert.ok(Date.now() - started < 2000, 'must not wait anywhere near the real budget');
});

test('a response that arrives in time passes straight through', async () => {
    const response = await fetchWithTimeout('http://backend/memory/version', {}, {
        timeoutMs: 1000,
        fetchImpl: okFetch({ protocol_version: 1 }),
    });
    assert.equal(response.ok, true);
    assert.deepEqual(await response.json(), { protocol_version: 1 });
});

test('a real network failure keeps its own error, not the timeout one', async () => {
    const boom = new Error('ECONNREFUSED');
    await assert.rejects(
        fetchWithTimeout('http://backend/memory/store', {}, {
            timeoutMs: 1000,
            fetchImpl: async () => {
                throw boom;
            },
        }),
        error => {
            assert.equal(error, boom);
            assert.equal(isTimeoutError(error), false);
            return true;
        },
    );
});

test('the abort signal reaches fetch, so the socket is actually released', async () => {
    let seenSignal = null;
    await assert.rejects(fetchWithTimeout('http://backend/x', { method: 'POST' }, {
        timeoutMs: 20,
        fetchImpl: (_url, options) => {
            seenSignal = options.signal;
            return hangingFetch()(_url, options);
        },
    }));
    assert.ok(seenSignal, 'fetch must be given a signal');
    assert.equal(seenSignal.aborted, true);
});

test('caller options survive the signal being added', async () => {
    let seen = null;
    await fetchWithTimeout('http://backend/x', {
        method: 'POST',
        headers: { 'X-API-Key': 'secret' },
        body: '{"a":1}',
    }, {
        timeoutMs: 1000,
        fetchImpl: async (_url, options) => {
            seen = options;
            return { ok: true, status: 200, json: async () => ({}) };
        },
    });
    assert.equal(seen.method, 'POST');
    assert.deepEqual(seen.headers, { 'X-API-Key': 'secret' });
    assert.equal(seen.body, '{"a":1}');
});

test('without AbortController the request still goes out, just undeadlined', async () => {
    // An environment too old for AbortController must not lose memory entirely - that
    // would be a worse regression than the missing deadline this module adds.
    const response = await fetchWithTimeout('http://backend/x', {}, {
        timeoutMs: 10,
        fetchImpl: okFetch({ ok: 1 }),
        AbortControllerImpl: undefined,
    });
    assert.equal(response.ok, true);
});

test('resolveTimeoutMs refuses the values that would abort instantly', () => {
    // 0 reads as "no limit" to a person and "abort now" to AbortController. The person wins.
    assert.equal(resolveTimeoutMs(0, 8000), 8000);
    assert.equal(resolveTimeoutMs(-1, 8000), 8000);
    assert.equal(resolveTimeoutMs('', 8000), 8000);
    assert.equal(resolveTimeoutMs(null, 8000), 8000);
    assert.equal(resolveTimeoutMs(undefined, 8000), 8000);
    assert.equal(resolveTimeoutMs('nope', 8000), 8000);
});

test('resolveTimeoutMs clamps and accepts sane values', () => {
    assert.equal(resolveTimeoutMs(1, 8000), MIN_TIMEOUT_MS);
    assert.equal(resolveTimeoutMs(5_000_000, 8000), MAX_TIMEOUT_MS);
    assert.equal(resolveTimeoutMs(12000, 8000), 12000);
    assert.equal(resolveTimeoutMs('12000', 8000), 12000);
    assert.equal(resolveTimeoutMs(1234.6, 8000), 1235);
});

test('the store budget clears the backend scene-extraction ceiling', () => {
    // app/config.py sizes SCENE_LLM_TIMEOUT at 90s; a client deadline under that would
    // abort extractions that were about to succeed.
    assert.ok(DEFAULT_STORE_TIMEOUT_MS > 90_000, 'store budget must exceed SCENE_LLM_TIMEOUT');
    // retrieve blocks generation, so it must stay far below it.
    assert.ok(DEFAULT_RETRIEVE_TIMEOUT_MS <= 10_000, 'retrieve budget must stay short');
});

test('fetchTrackers times out instead of hanging the tracker cache warm', async () => {
    await assert.rejects(
        fetchTrackers({
            memoryServiceUrl: 'http://backend',
            chatId: 'chat-1',
            characterId: '0',
            fetchImpl: hangingFetch(),
            timeoutMs: 30,
        }),
        error => isTimeoutError(error),
    );
});

test('fetchTrackers still returns items on a healthy backend', async () => {
    const items = await fetchTrackers({
        memoryServiceUrl: 'http://backend',
        chatId: 'chat-1',
        characterId: '0',
        fetchImpl: okFetch({ items: [{ tracker_type: 'timeline', content: 'x' }] }),
    });
    assert.equal(items.length, 1);
});

test('timeout settings round-trip through storage and survive a legacy flat file', () => {
    const normalized = normalizeExtensionSettings({
        connection: { memoryServiceUrl: 'http://host:8001', retrieveTimeoutMs: 4000, storeTimeoutMs: 99000 },
    });
    assert.equal(normalized.retrieveTimeoutMs, 4000);
    assert.equal(normalized.storeTimeoutMs, 99000);

    const stored = serializeExtensionSettings(normalized);
    assert.equal(stored.connection.retrieveTimeoutMs, 4000);
    assert.equal(stored.connection.storeTimeoutMs, 99000);
    assert.deepEqual(normalizeExtensionSettings(stored).retrieveTimeoutMs, 4000);
});

test('a settings.json written before timeouts existed gets the defaults', () => {
    const normalized = normalizeExtensionSettings({
        connection: { enabled: true, memoryServiceUrl: 'http://host:8001', apiKey: '' },
    });
    assert.equal(normalized.retrieveTimeoutMs, DEFAULT_RETRIEVE_TIMEOUT_MS);
    assert.equal(normalized.storeTimeoutMs, DEFAULT_STORE_TIMEOUT_MS);
});
