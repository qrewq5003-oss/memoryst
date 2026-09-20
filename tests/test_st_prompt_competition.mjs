import test from 'node:test';
import assert from 'node:assert/strict';

import {
    DEFAULT_MAX_INJECTOR_ENTRIES,
    KNOWN_INJECTORS,
    MEMORYST_PROMPT_KEYS,
    summarizeForeignInjectors,
    summarizeWorldInfo,
} from '../sillytavern-extension/injectors.mjs';

function prompt(value, extra = {}) {
    return { value, position: 0, depth: 0, ...extra };
}

test('ours and theirs are counted apart', () => {
    const summary = summarizeForeignInjectors({
        'memory-service': prompt('x'.repeat(525)),
        '3_vectors': prompt('y'.repeat(3047)),
        '4_vectors_data_bank': prompt('z'.repeat(14544)),
    });

    assert.equal(summary.own_chars, 525);
    assert.equal(summary.foreign_chars, 3047 + 14544);
    assert.equal(summary.total_chars, 525 + 3047 + 14544);
    assert.equal(summary.foreign_count, 2);
});

test('own_share is the number the exercise is about', () => {
    // Measured at under 2% in August; the field exists so nobody has to derive it again.
    const summary = summarizeForeignInjectors({
        'memory-service': prompt('x'.repeat(525)),
        '4_vectors_data_bank': prompt('z'.repeat(29075)),
    });
    assert.ok(summary.own_share < 0.02, `expected under 2%, got ${summary.own_share}`);
});

test('all three memoryst keys count as ours', () => {
    const prompts = {};
    for (const key of MEMORYST_PROMPT_KEYS) {
        prompts[key] = prompt('a'.repeat(100));
    }
    const summary = summarizeForeignInjectors(prompts);
    assert.equal(summary.own_chars, 300);
    assert.equal(summary.foreign_chars, 0);
    assert.equal(summary.own_share, 1);
});

test('empty prompts are not reported as competitors', () => {
    // Every extension that has ever injected leaves its key behind with an empty value.
    // Counting those would report a dozen rivals in a prompt none of them contributed to.
    const summary = summarizeForeignInjectors({
        '1_memory': prompt(''),
        '3_vectors': prompt(''),
        'memory-service': prompt(''),
        'DEPTH_PROMPT': prompt('real'),
    });
    assert.equal(summary.foreign_count, 1);
    assert.equal(summary.foreign[0].key, 'DEPTH_PROMPT');
    assert.equal(summary.own_chars, 0);
});

test('an empty prompt object is not a division by zero', () => {
    const summary = summarizeForeignInjectors({});
    assert.equal(summary.total_chars, 0);
    assert.equal(summary.own_share, 0);
    assert.deepEqual(summary.foreign, []);
});

test('the biggest injector is listed first', () => {
    const summary = summarizeForeignInjectors({
        '1_memory': prompt('a'.repeat(5323)),
        '3_vectors': prompt('b'.repeat(4741)),
        '4_vectors_data_bank': prompt('c'.repeat(14544)),
    });
    assert.deepEqual(summary.foreign.map(item => item.key), [
        '4_vectors_data_bank',
        '1_memory',
        '3_vectors',
    ]);
});

test('known injectors are named, unknown ones are still reported', () => {
    const summary = summarizeForeignInjectors({
        '3_vectors': prompt('a'.repeat(10)),
        'something_new': prompt('b'.repeat(10)),
    });
    const byKey = Object.fromEntries(summary.foreign.map(item => [item.key, item]));
    assert.equal(byKey['3_vectors'].label, KNOWN_INJECTORS['3_vectors']);
    // An injector nobody has heard of is exactly what this is for.
    assert.equal(byKey.something_new.label, null);
});

test('position and depth are recorded, because they change what a block does', () => {
    const summary = summarizeForeignInjectors({
        '3_vectors': prompt('a'.repeat(10), { position: 1, depth: 4 }),
    });
    assert.equal(summary.foreign[0].position, 1);
    assert.equal(summary.foreign[0].depth, 4);
});

test('a plain string prompt is measured too', () => {
    const summary = summarizeForeignInjectors({ '3_vectors': 'abcde' });
    assert.equal(summary.foreign_chars, 5);
    assert.equal(summary.foreign[0].position, null);
});

test('the list is capped but the counts are not', () => {
    const prompts = {};
    for (let index = 0; index < DEFAULT_MAX_INJECTOR_ENTRIES + 5; index += 1) {
        prompts[`ext_${index}`] = prompt('a'.repeat(index + 1));
    }
    const summary = summarizeForeignInjectors(prompts);

    assert.equal(summary.foreign.length, DEFAULT_MAX_INJECTOR_ENTRIES);
    assert.equal(summary.truncated, 5);
    // Counted over all of them, not just the ones listed.
    assert.equal(summary.foreign_count, DEFAULT_MAX_INJECTOR_ENTRIES + 5);
});

test('missing or malformed input does not throw', () => {
    for (const input of [undefined, null, {}]) {
        assert.equal(summarizeForeignInjectors(input).total_chars, 0);
    }
});

test('world info is measured from the entries the handler already gets', () => {
    // Lorebook text never passes through extension_prompts, so without this the largest
    // constant contributor on some chats is missing: 2113 characters every turn on the
    // chat this was measured on.
    const summary = summarizeWorldInfo([
        { content: 'a'.repeat(2000) },
        { entry: 'b'.repeat(113) },
    ]);
    assert.equal(summary.entry_count, 2);
    assert.equal(summary.chars, 2113);
});

test('no world info is zero, not a crash', () => {
    for (const input of [undefined, null, [], 'nonsense']) {
        assert.equal(summarizeWorldInfo(input).chars, 0);
    }
});
