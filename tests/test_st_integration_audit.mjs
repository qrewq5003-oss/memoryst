import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildBudgetedMemoryBlock,
    PRE_GENERATION_HOOK_CANDIDATES,
    buildTurnKey,
    buildPromptInsertionAuditSection,
    buildRetrieveAuditSection,
    buildStoreAuditSection,
    createIntegrationAuditRecord,
    finalizeIntegrationAuditRecord,
    pushAuditRecord,
    resolvePreGenerationHookNames,
} from '../sillytavern-extension/audit.mjs';

test('store audit section captures message previews and result summary', () => {
    const section = buildStoreAuditSection({
        messages: [
            { role: 'user', text: 'Алиса долго объясняла, почему боится грозы.' },
            { role: 'assistant', text: 'Маркус пообещал закрыть окна.' },
        ],
        result: {
            stored: 1,
            updated: 0,
            skipped: 1,
            items: [{ id: 'm1' }],
            debug: { candidates: [] },
        },
        previewChars: 20,
    });

    assert.equal(section.message_count, 2);
    assert.equal(section.stored, 1);
    assert.equal(section.skipped, 1);
    assert.equal(section.debug_present, true);
    assert.match(section.messages[0].text_preview, /Алиса долго объясня/);
});

test('retrieve and prompt audit sections capture memory block insertion details', () => {
    const budget = buildBudgetedMemoryBlock({
        items: [
            {
                id: 'storm-fear',
                type: 'profile',
                layer: 'stable',
                content: 'Лена боится грозы.',
                metadata: {},
            },
        ],
    });
    const retrieve = buildRetrieveAuditSection({
        userInput: 'А что насчёт этого?',
        recentMessages: [{ role: 'user', text: 'Мы говорили, что Лена боится грозы.' }],
        result: {
            items: [{ id: 'storm-fear' }],
            total_candidates: 5,
            memory_block: '[Relevant Memory]\n- [STABLE] Лена боится грозы.',
            debug: { candidates: [] },
        },
        previewChars: 80,
        stage: 'pre_generation',
        budget,
    });
    const prompt = buildPromptInsertionAuditSection({
        memoryBlock: budget.memoryBlock,
        applied: true,
        reason: 'memory_block_set_for_current_turn',
        stage: 'pre_generation',
        appliedToCurrentTurn: true,
        budget,
    });

    assert.equal(retrieve.returned_item_count, 1);
    assert.equal(retrieve.memory_block_item_count, 1);
    assert.equal(retrieve.stage, 'pre_generation');
    assert.equal(retrieve.budget_applied, true);
    assert.equal(prompt.applied, true);
    assert.equal(prompt.applied_to_current_turn, true);
    assert.equal(prompt.insertion_timing, 'current_generation_pre_prompt');
    assert.equal(prompt.role, 'system');
    assert.equal(prompt.injected_stable_count, 1);
    assert.equal(prompt.trimmed_item_count, 0);
});

test('finalized audit records preserve missing-step notes and bounded recent history', () => {
    const settings = { auditMaxRecords: 2, recentAudits: [] };
    const record = createIntegrationAuditRecord({
        chatId: 'chat-1',
        characterId: 'char-1',
        recentMessagesCount: 8,
    });

    record.store_called = true;
    record.retrieve_stage = 'pre_generation';
    record.prompt_injection_stage = 'pre_generation';
    record.store = buildStoreAuditSection({ messages: [], result: { stored: 0, updated: 0, skipped: 0, items: [] } });
    record.retrieve_called = true;
    record.applied_to_current_turn = false;
    record.retrieve = buildRetrieveAuditSection({
        userInput: 'Что дальше?',
        recentMessages: [],
        result: { items: [], total_candidates: 0, memory_block: '' },
        stage: 'pre_generation',
    });
    record.prompt_insertion_observed = false;
    record.prompt_insertion = buildPromptInsertionAuditSection({
        memoryBlock: '',
        applied: false,
        reason: 'empty_or_missing_memory_block',
        stage: 'pre_generation',
        appliedToCurrentTurn: false,
    });

    const finalized = finalizeIntegrationAuditRecord(record);
    pushAuditRecord(settings, finalized);
    pushAuditRecord(settings, finalized);
    pushAuditRecord(settings, finalized);

    assert.match(finalized.notes.join(','), /empty_memory_block/);
    assert.match(finalized.notes.join(','), /prompt_insertion_not_observed/);
    assert.equal(settings.recentAudits.length, 2);
});

test('pre-generation hook resolution and turn keys support current-turn retrieval flow', () => {
    const hookNames = resolvePreGenerationHookNames({
        GENERATE_BEFORE_COMBINE_PROMPTS: 'generate_before_combine_prompts',
    });
    const turnKey = buildTurnKey({
        chatId: 'chat-1',
        characterId: 'char-1',
        chatLength: 42,
        userInput: 'Напомни, что Лена боится грозы',
    });

    assert.ok(PRE_GENERATION_HOOK_CANDIDATES.includes('GENERATE_BEFORE_COMBINE_PROMPTS'));
    assert.ok(hookNames.includes('generate_before_combine_prompts'));
    assert.match(turnKey, /chat-1::char-1::42::/);
});
