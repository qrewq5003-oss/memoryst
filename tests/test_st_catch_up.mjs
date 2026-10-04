/**
 * The processed boundary and catch-up (catch-up.mjs).
 *
 * 2026-10-02: 20 turns with the backend down, nothing stored, and a store only ever
 * sends its last 8 messages - so a gap longer than that was lost for good. The boundary
 * records how far the chat is known to be processed, and catch-up re-sends the rest.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import {
    CATCH_UP_MAX_MESSAGES,
    METADATA_KEY,
    boundaryAfterCatchUp,
    boundaryAfterStore,
    planCatchUp,
    readBoundary,
    writeBoundary,
} from '../sillytavern-extension/catch-up.mjs';
import { messagesInRange } from '../sillytavern-extension/chat-history.mjs';

const W = 8;

test('a boundary belongs to its chat: a branch copies the metadata, not the progress', () => {
    const meta = {};
    writeBoundary(meta, 'chat-A', 41);
    assert.equal(readBoundary(meta, 'chat-A'), 41);
    assert.equal(readBoundary(meta, 'chat-A - Branch #1'), null);
    assert.equal(readBoundary({}, 'chat-A'), null);
    assert.equal(readBoundary({ [METADATA_KEY]: { chatId: 'chat-A', processedThrough: 'x' } }, 'chat-A'), null);
});

test('an unknown boundary starts at the end - old chats were stored turn by turn', () => {
    assert.equal(boundaryAfterStore({ boundary: null, chatLength: 120, windowSize: W, ok: true }), 119);
    assert.equal(planCatchUp({ boundary: 119, chatLength: 120, windowSize: W }), null);
});

test('a failed store leaves the boundary where it was', () => {
    assert.equal(boundaryAfterStore({ boundary: 50, chatLength: 54, windowSize: W, ok: false }), 50);
    assert.equal(boundaryAfterStore({ boundary: null, chatLength: 54, windowSize: W, ok: false }), null);
});

test('one failed turn heals itself: the next window covers it', () => {
    // Boundary 50, two failed messages, then a good store over the last 8 of 54.
    assert.equal(boundaryAfterStore({ boundary: 50, chatLength: 54, windowSize: W, ok: true }), 53);
});

test('a gap behind the window is left for catch-up, which covers it to the end', () => {
    // 20 messages missed: the store window (52..59) no longer reaches the boundary (39).
    assert.equal(boundaryAfterStore({ boundary: 39, chatLength: 60, windowSize: W, ok: true }), 39);
    assert.deepEqual(planCatchUp({ boundary: 39, chatLength: 60, windowSize: W }), { start: 40, end: 59 });
    assert.equal(boundaryAfterCatchUp({ boundary: 39, range: { start: 40, end: 59 }, ok: true }), 59);
});

test('a long gap is caught up in slices, each continuing where the last stopped', () => {
    const range = planCatchUp({ boundary: 9, chatLength: 200, windowSize: W });
    assert.deepEqual(range, { start: 10, end: 10 + CATCH_UP_MAX_MESSAGES - 1 });
    const next = boundaryAfterCatchUp({ boundary: 9, range, ok: true });
    assert.deepEqual(planCatchUp({ boundary: next, chatLength: 200, windowSize: W }).start, next + 1);
});

test('a catch-up with a failed scene keeps the whole range open', () => {
    assert.equal(boundaryAfterCatchUp({ boundary: 39, range: { start: 40, end: 59 }, ok: false }), 39);
});

test('deleted messages pull the boundary back to the end of the chat', () => {
    assert.equal(boundaryAfterStore({ boundary: 99, chatLength: 80, windowSize: W, ok: true }), 79);
    assert.equal(boundaryAfterStore({ boundary: 99, chatLength: 80, windowSize: W, ok: false }), 79);
});

test('a range of messages, clamped, empty ones dropped', () => {
    const chat = [
        { is_user: true, mes: 'а' }, { is_user: false, mes: 'б' }, { is_user: false, mes: '' }, { is_user: true, mes: 'г' },
    ];
    assert.deepEqual(messagesInRange(chat, 1, 3), [
        { role: 'assistant', text: 'б' }, { role: 'user', text: 'г' },
    ]);
    assert.deepEqual(messagesInRange(chat, -5, 0), [{ role: 'user', text: 'а' }]);
    assert.deepEqual(messagesInRange(chat, 3, 99), [{ role: 'user', text: 'г' }]);
});
