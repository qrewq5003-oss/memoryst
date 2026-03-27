import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildLoreAnchorBlock,
    extractLoreAnchorText,
    isAllowlistedLoreAnchorEntry,
    normalizeLoreAnchorCandidates,
} from '../sillytavern-extension/lore-anchors.mjs';

test('allowlist accepts explicit memory-anchor markers and tags', () => {
    assert.equal(isAllowlistedLoreAnchorEntry({ comment: '[memory-anchor]', content: 'Canonical house rule.' }), true);
    assert.equal(isAllowlistedLoreAnchorEntry({ tags: ['memory-anchor'], content: 'Canonical timeline fact.' }), true);
    assert.equal(isAllowlistedLoreAnchorEntry({ comment: 'regular lore', content: 'Plain background entry.' }), false);
});

test('extractLoreAnchorText prefers explicit marker text and strips marker lines', () => {
    assert.equal(
        extractLoreAnchorText({ comment: '@memory-anchor: Canon relationship fact.' }),
        'Canon relationship fact.',
    );
    assert.equal(
        extractLoreAnchorText({
            content: '@memory-anchor\nМаркус и Алиса официально скрывают семейную связь.',
        }),
        'Маркус и Алиса официально скрывают семейную связь.',
    );
});

test('buildLoreAnchorBlock is compact, allowlisted, and dedupes memory content', () => {
    const built = buildLoreAnchorBlock({
        entries: [
            {
                id: 'e1',
                comment: '@memory-anchor: У Алисы есть канонический старший брат.',
            },
            {
                id: 'e2',
                tags: ['memory-anchor'],
                content: 'Маркус и Алиса работают под прикрытием в одной команде.',
            },
        ],
        existingMemoryBlock: '[Relevant Memory]\n- [STABLE] У Алисы есть канонический старший брат.',
        maxItems: 2,
        maxChars: 220,
    });

    assert.equal(built.anchorItemCount, 1);
    assert.match(built.anchorBlock, /\[Lore Anchor\]/);
    assert.match(built.anchorBlock, /под прикрытием/);
    assert.deepEqual(built.skipped.map(item => item.reason), ['duplicate_memory_block']);
});

test('normalizeLoreAnchorCandidates ignores non-allowlisted entries', () => {
    const normalized = normalizeLoreAnchorCandidates([
        { id: 'plain', content: 'Regular lore entry.' },
        { id: 'anchor', comment: '[memory-anchor]', content: 'Curated canonical background.' },
    ]);

    assert.deepEqual(normalized.map(item => item.id), ['anchor']);
});
