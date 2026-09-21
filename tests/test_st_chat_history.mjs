/**
 * What the memory service actually sees of the chat.
 *
 * Every retrieve queries with `lastUserText` and every store extracts from
 * `recentMessages`, so a fault here does not raise anything - it feeds the wrong text to
 * both paths, and the only symptom is memory that looks subtly wrong days later.
 *
 * Until 2026-09-21 this lived inside main.mjs, which is 1282 lines, exports nothing and
 * cannot be imported outside SillyTavern - so none of it had a behavioural test. The
 * tests that named main.mjs read it as a string and asserted with regexes.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { lastUserText, recentMessages } from '../sillytavern-extension/chat-history.mjs';

const chat = [
    { is_user: true, mes: 'привет' },
    { is_user: false, mes: 'здравствуй' },
    { is_user: true, mes: 'как дела?' },
    { is_user: false, mes: 'хорошо' },
];

test('recentMessages takes the last N in chat order', () => {
    assert.deepEqual(recentMessages(chat, 2), [
        { role: 'user', text: 'как дела?' },
        { role: 'assistant', text: 'хорошо' },
    ]);
});

test('a count larger than the chat returns the whole chat, not padding', () => {
    assert.equal(recentMessages(chat, 99).length, 4);
});

test('a count of zero returns nothing rather than the entire chat', () => {
    // slice(-0) is slice(0) - the whole array. Getting this wrong would send an entire
    // long chat to extraction, which is expensive and wrong rather than merely odd.
    assert.deepEqual(recentMessages(chat, 0), []);
    assert.deepEqual(recentMessages(chat, -3), []);
});

test('a missing or malformed chat is empty, not a crash', () => {
    // getContext()?.chat is undefined before a chat is open, and this runs on hooks that
    // fire then.
    for (const value of [undefined, null, 'not an array', {}]) {
        assert.deepEqual(recentMessages(value, 4), []);
        assert.equal(lastUserText(value), '');
    }
});

test('a non-numeric count is empty rather than NaN-sliced', () => {
    assert.deepEqual(recentMessages(chat, undefined), []);
    assert.deepEqual(recentMessages(chat, NaN), []);
});

test('text comes from mes, falling back to text', () => {
    // `mes` is ST's own field; `text` is what imported and synthetic messages carry.
    // Dropping the fallback would send an empty string to the backend and store one.
    const mixed = [{ is_user: true, text: 'из импорта' }, { is_user: false, mes: 'из ST' }];
    assert.deepEqual(recentMessages(mixed, 2), [
        { role: 'user', text: 'из импорта' },
        { role: 'assistant', text: 'из ST' },
    ]);
});

test('a message with neither field becomes an empty string, not undefined', () => {
    assert.deepEqual(recentMessages([{ is_user: true }], 1), [{ role: 'user', text: '' }]);
});

test('holes in the array do not throw', () => {
    const sparse = [{ is_user: true, mes: 'a' }, null, undefined];
    assert.equal(recentMessages(sparse, 3).length, 3);
});

test('lastUserText scans backwards, not forwards', () => {
    // The query for this turn is the message just sent, not the first one ever sent.
    assert.equal(lastUserText(chat), 'как дела?');
});

test('lastUserText is empty when the user has not spoken', () => {
    assert.equal(lastUserText([{ is_user: false, mes: 'greeting' }]), '');
    assert.equal(lastUserText([]), '');
});

test('an explicit role beats the inferred one, consistently in both functions', () => {
    /* A deliberate behaviour change, made when this was extracted on 2026-09-21.
     *
     * The two functions used to disagree about who the user is. recentMessages read
     * `msg.role || (msg.is_user ? ...)`, so an explicit role won; getLastUserMessage
     * read `msg.is_user || msg.role === 'user'`, so is_user won. A message carrying
     * both, disagreeing, was classified one way for the store path and the other way
     * for the retrieve query.
     *
     * ST itself does not set `role` on chat messages, so the two only diverge when
     * another extension or an imported chat sets it - which is exactly when a wrong
     * answer would be hardest to trace. They now agree, and the explicit field wins,
     * because that is what the store path already did with the larger share of the text.
     */
    const contradictory = [{ is_user: true, role: 'assistant', mes: 'кто это сказал' }];
    assert.deepEqual(recentMessages(contradictory, 1), [
        { role: 'assistant', text: 'кто это сказал' },
    ]);
    assert.equal(lastUserText(contradictory), '', 'the two must not disagree');
});

test('role: user is honoured even without is_user', () => {
    const imported = [{ role: 'user', text: 'из другого формата' }];
    assert.equal(lastUserText(imported), 'из другого формата');
});
