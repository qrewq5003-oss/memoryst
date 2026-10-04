/**
 * The World Info reconnaissance pass reads and never writes.
 *
 * `WORLDINFO_ENTRIES_LOADED` is the one hook that could suppress a lorebook entry: it
 * runs before selection, hands over the live arrays, and core honours `entry.disable`
 * (world-info.js:4801). Two other extensions installed here subscribe to it, and
 * SillyTavern-LorebookOrdering already sets `disable` there for its own token budget.
 * Handler order follows extension load order, which is recorded nowhere, so mutating
 * these arrays before measuring would be a silent race.
 *
 * The no-mutation test is therefore the load-bearing one. The rest of this pass is only
 * worth running if it cannot change the turn it is observing.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { summarizeEntriesLoaded } from '../sillytavern-extension/wi-recon.mjs';

const memoryBooksEntry = (chars, extra = {}) => ({
    content: 'x'.repeat(chars),
    stmemorybooks: true,
    STMB_chatId: 'Camila - 2026-09-19@18h40m19s945ms',
    ...extra,
});
const plainEntry = (chars, extra = {}) => ({ content: 'y'.repeat(chars), ...extra });

test('it counts entries and characters per bucket', () => {
    const summary = summarizeEntriesLoaded({
        globalLore: [plainEntry(100)],
        characterLore: [plainEntry(50), plainEntry(25)],
        chatLore: [],
        personaLore: [],
    });

    assert.equal(summary.total_count, 3);
    assert.equal(summary.total_chars, 175);
    assert.equal(summary.buckets.character.count, 2);
});

test('it reports what another handler already disabled', () => {
    // The number this pass exists for: above zero means someone ran first, which is the
    // load-order question a code read cannot answer.
    const summary = summarizeEntriesLoaded({
        globalLore: [plainEntry(10, { disable: true }), plainEntry(10)],
    });

    assert.equal(summary.already_disabled, 1);
});

test('disable is read the way core reads it', () => {
    // core compares `entry.disable == true`, so a string or a 1 disables there. Counting
    // only `=== true` here would report an entry as live that the scan will skip.
    const summary = summarizeEntriesLoaded({
        globalLore: [plainEntry(10, { disable: 1 }), plainEntry(10, { disable: 'true' })],
    });

    assert.equal(summary.already_disabled, 1, 'disable: 1 is truthy under ==, "true" is not');
});

test('Memory Books entries are identified by their own stamp, not by name', () => {
    // STMB_chatId and stmemorybooks are fields Memory Books writes itself; matching on a
    // lorebook filename would break the first time one is renamed.
    const summary = summarizeEntriesLoaded({
        chatLore: [memoryBooksEntry(2113), memoryBooksEntry(2021), plainEntry(300)],
    });

    assert.equal(summary.memory_books_count, 2);
    assert.equal(summary.memory_books_chars, 4134);
    assert.equal(summary.suppressible_chars, 4134, 'what a hand-over would be taking over');
});

test('it does not mutate the entries or the arrays it is given', () => {
    // The whole licence for running this on a live turn.
    const entries = [memoryBooksEntry(100), plainEntry(50, { disable: true })];
    const payload = { globalLore: entries, characterLore: [], chatLore: [], personaLore: [] };
    const before = JSON.stringify(payload);

    summarizeEntriesLoaded(payload);

    assert.equal(JSON.stringify(payload), before, 'the recon pass changed the scan input');
    assert.equal(entries.length, 2, 'an entry was removed from the array');
});

test('a malformed or missing payload is zeros, not a throw', () => {
    // It runs on every generation, including dry runs and before a chat is open.
    for (const payload of [undefined, {}, { globalLore: null }, { globalLore: 'nonsense' }]) {
        const summary = summarizeEntriesLoaded(payload);
        assert.equal(summary.total_count, 0);
        assert.equal(summary.already_disabled, 0);
    }
});

test('entries with no content at all count as zero characters, not undefined', () => {
    const summary = summarizeEntriesLoaded({ globalLore: [{}, { content: null }] });
    assert.equal(summary.total_chars, 0);
    assert.equal(summary.total_count, 2);
});
