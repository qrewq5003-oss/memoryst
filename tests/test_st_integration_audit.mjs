import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildPromptInsertionAuditSection,
    buildRetrieveAuditSection,
    buildStoreAuditSection,
    createIntegrationAuditRecord,
    finalizeIntegrationAuditRecord,
    pushAuditRecord,
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
    });
    const prompt = buildPromptInsertionAuditSection({
        memoryBlock: '[Relevant Memory]\n- [STABLE] Лена боится грозы.',
        applied: true,
        reason: 'memory_block_set',
    });

    assert.equal(retrieve.returned_item_count, 1);
    assert.equal(retrieve.memory_block_item_count, 1);
    assert.equal(prompt.applied, true);
    assert.equal(prompt.insertion_timing, 'next_generation_post_render');
    assert.equal(prompt.role, 'system');
});

test('finalized audit records preserve missing-step notes and bounded recent history', () => {
    const settings = { auditMaxRecords: 2, recentAudits: [] };
    const record = createIntegrationAuditRecord({
        chatId: 'chat-1',
        characterId: 'char-1',
        recentMessagesCount: 8,
    });

    record.store_called = true;
    record.store = buildStoreAuditSection({ messages: [], result: { stored: 0, updated: 0, skipped: 0, items: [] } });
    record.retrieve_called = true;
    record.retrieve = buildRetrieveAuditSection({
        userInput: 'Что дальше?',
        recentMessages: [],
        result: { items: [], total_candidates: 0, memory_block: '' },
    });
    record.prompt_insertion_observed = false;
    record.prompt_insertion = buildPromptInsertionAuditSection({
        memoryBlock: '',
        applied: false,
        reason: 'empty_or_missing_memory_block',
    });

    const finalized = finalizeIntegrationAuditRecord(record);
    pushAuditRecord(settings, finalized);
    pushAuditRecord(settings, finalized);
    pushAuditRecord(settings, finalized);

    assert.match(finalized.notes.join(','), /empty_memory_block/);
    assert.match(finalized.notes.join(','), /prompt_insertion_not_observed/);
    assert.equal(settings.recentAudits.length, 2);
});
