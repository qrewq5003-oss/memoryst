import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildBudgetedMemoryBlock,
    buildPromptInsertionAuditSection,
    buildRetrieveAuditSection,
} from '../sillytavern-extension/audit.mjs';

function memory(id, {
    type = 'event',
    layer = 'episodic',
    content = '',
    pinned = false,
    isSummary = false,
} = {}) {
    return {
        id,
        type,
        layer,
        content,
        pinned,
        metadata: {
            is_summary: isSummary,
        },
    };
}

test('budget policy trims episodic first and keeps summary under pressure', () => {
    const budget = buildBudgetedMemoryBlock({
        items: [
            memory('summary', {
                type: 'summary',
                layer: 'stable',
                content: 'Краткая сводка: Алиса и Маркус удерживают общий план проекта.',
                isSummary: true,
            }),
            memory('stable', {
                type: 'relationship',
                layer: 'stable',
                content: 'Маркус доверяет Алисе в проекте.',
            }),
            memory('episodic-a', {
                type: 'event',
                layer: 'episodic',
                content: 'Вчера они спорили о времени встречи.',
            }),
            memory('episodic-b', {
                type: 'event',
                layer: 'episodic',
                content: 'Позже они перенесли встречу на утро.',
            }),
        ],
        maxPromptMemories: 3,
        maxPromptChars: 260,
        maxSummaryItems: 1,
        maxStableItems: 1,
        maxEpisodicItems: 2,
    });

    assert.deepEqual(
        budget.selectedItems.map(item => item.id),
        ['summary', 'stable', 'episodic-a'],
    );
    assert.equal(budget.injectedByLayer.summary, 1);
    assert.equal(budget.injectedByLayer.stable, 1);
    assert.equal(budget.injectedByLayer.episodic, 1);
    assert.equal(budget.trimmedByLayer.episodic, 1);
    assert.match(budget.trimReasons.join(','), /item_cap:episodic/);
});

test('budget policy applies layer caps before prompt assembly deterministically', () => {
    const budget = buildBudgetedMemoryBlock({
        items: [
            memory('summary-a', {
                type: 'summary',
                layer: 'stable',
                content: 'Сводка A.',
                isSummary: true,
            }),
            memory('summary-b', {
                type: 'summary',
                layer: 'stable',
                content: 'Сводка B.',
                isSummary: true,
            }),
            memory('stable-a', {
                type: 'profile',
                layer: 'stable',
                content: 'Алиса любит джаз.',
            }),
            memory('stable-b', {
                type: 'profile',
                layer: 'stable',
                content: 'Алиса боится грозы.',
            }),
        ],
        maxPromptMemories: 4,
        maxPromptChars: 400,
        maxSummaryItems: 1,
        maxStableItems: 1,
        maxEpisodicItems: 1,
    });

    assert.deepEqual(
        budget.selectedItems.map(item => item.id),
        ['summary-a', 'stable-a'],
    );
    assert.equal(budget.trimmedItemCount, 2);
    assert.match(budget.trimReasons.join(','), /layer_cap:summary/);
    assert.match(budget.trimReasons.join(','), /layer_cap:stable/);
});

test('audit sections reflect retrieved vs injected composition after budget trimming', () => {
    const items = [
        memory('summary', {
            type: 'summary',
            layer: 'stable',
            content: 'Краткая сводка: Алиса и Маркус всё ещё держат проект.',
            isSummary: true,
        }),
        memory('episodic', {
            type: 'event',
            layer: 'episodic',
            content: 'Вчера они снова спорили о сроках поездки.',
        }),
        memory('episodic-2', {
            type: 'event',
            layer: 'episodic',
            content: 'Позже Алиса предложила новый график на утро.',
        }),
    ];
    const budget = buildBudgetedMemoryBlock({
        items,
        maxPromptMemories: 2,
        maxPromptChars: 220,
        maxSummaryItems: 1,
        maxStableItems: 1,
        maxEpisodicItems: 1,
    });
    const retrieve = buildRetrieveAuditSection({
        userInput: 'Что сейчас важно помнить?',
        recentMessages: [{ role: 'user', text: 'Напомни общий контекст проекта.' }],
        result: {
            items,
            total_candidates: 7,
            memory_block: '[Relevant Memory]\n- [SUMMARY] raw backend block',
        },
        budget,
    });
    const prompt = buildPromptInsertionAuditSection({
        memoryBlock: budget.memoryBlock,
        applied: true,
        reason: 'budgeted_memory_block_set_for_current_turn',
        budget,
    });

    assert.equal(retrieve.returned_item_count, 3);
    assert.equal(retrieve.returned_summary_count, 1);
    assert.equal(retrieve.returned_episodic_count, 2);
    assert.equal(retrieve.budgeted_item_count, 2);
    assert.equal(retrieve.trimmed_item_count, 1);
    assert.equal(prompt.injected_summary_count, 1);
    assert.equal(prompt.injected_episodic_count, 1);
    assert.equal(prompt.trimmed_item_count, 1);
    assert.match(prompt.trim_reasons.join(','), /(layer_cap|item_cap|char_budget):episodic/);
});

test('long-chat budget scenario keeps summary and stable core while limiting episodic noise', () => {
    const budget = buildBudgetedMemoryBlock({
        items: [
            memory('summary', {
                type: 'summary',
                layer: 'stable',
                content: 'Краткая сводка: Алиса и Маркус после длинной дуги снова сотрудничают, но всё ещё осторожничают.',
                isSummary: true,
            }),
            memory('stable-a', {
                type: 'relationship',
                layer: 'stable',
                content: 'Маркус снова доверяет Алисе в проекте.',
            }),
            memory('stable-b', {
                type: 'profile',
                layer: 'stable',
                content: 'Алисе важно закончить фильм до фестиваля.',
            }),
            memory('episodic-a', {
                type: 'event',
                layer: 'episodic',
                content: 'Вчера они спорили о времени встречи у вокзала.',
            }),
            memory('episodic-b', {
                type: 'event',
                layer: 'episodic',
                content: 'Позже Алиса долго искала новый реквизит на рынке.',
            }),
        ],
        maxPromptMemories: 4,
        maxPromptChars: 340,
        maxSummaryItems: 1,
        maxStableItems: 2,
        maxEpisodicItems: 1,
    });

    assert.equal(budget.injectedByLayer.summary, 1);
    assert.equal(budget.injectedByLayer.stable, 2);
    assert.equal(budget.injectedByLayer.episodic, 1);
    assert.equal(budget.trimmedByLayer.episodic, 1);
    assert.equal(budget.selectedItems.length, 4);
    assert.ok(budget.memoryBlock.length <= 340);
});
