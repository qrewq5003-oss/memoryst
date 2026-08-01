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
    isDryRun,
    pushAuditRecord,
    resolvePreGenerationHookNames,
    willAppendUserMessage,
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
            extraction_method: 'llm',
        },
        previewChars: 20,
    });

    assert.equal(section.message_count, 2);
    assert.equal(section.stored, 1);
    assert.equal(section.skipped, 1);
    assert.equal(section.debug_present, true);
    assert.equal(section.extraction_method, 'llm');
    assert.match(section.messages[0].text_preview, /Алиса долго объясня/);
});

test('store audit section defaults extraction_method to null when the backend omits it', () => {
    const section = buildStoreAuditSection({
        messages: [],
        result: { stored: 0, updated: 0, skipped: 0, items: [] },
    });

    assert.equal(section.extraction_method, null);
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
        loreAnchorBlock: '[Lore Anchor]\n- Canonical family tie.',
        loreAnchorItemCount: 1,
    });

    assert.equal(retrieve.returned_item_count, 1);
    assert.equal(retrieve.memory_block_item_count, 1);
    assert.equal(retrieve.stage, 'pre_generation');
    assert.equal(retrieve.budget_applied, true);
    assert.equal(prompt.applied, true);
    assert.equal(prompt.applied_to_current_turn, true);
    assert.equal(prompt.insertion_timing, 'current_generation_pre_prompt');
    assert.equal(prompt.role, 'system');
    assert.equal(prompt.lore_anchor_applied, true);
    assert.equal(prompt.lore_anchor_item_count, 1);
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
        userInput: 'Напомни, что Лена боится грозы',
    });

    assert.ok(PRE_GENERATION_HOOK_CANDIDATES.includes('GENERATE_BEFORE_COMBINE_PROMPTS'));
    assert.ok(hookNames.includes('generate_before_combine_prompts'));
    // The chat length is deliberately absent: SillyTavern grows `chat` mid-generation, so
    // including it made the key change between the hooks of a single turn.
    assert.equal(turnKey, 'chat-1::char-1::Напомни, что Лена боится грозы');
});

test('the turn key is stable while SillyTavern grows the chat mid-generation', () => {
    // Live regression: ST pushes a placeholder message for the reply into `chat` during
    // generation, so chat.length differed between GENERATION_STARTED and
    // GENERATE_BEFORE_COMBINE_PROMPTS. With the length in the key, the second hook saw a
    // "new" turn, re-ran retrieve, and cleared the tracker block that WORLD_INFO_ACTIVATED
    // had set in between - which is exactly why the injected tracker never reached the
    // prompt.
    const before = buildTurnKey({ chatId: 'c', characterId: '20', userInput: 'привет' });
    const during = buildTurnKey({ chatId: 'c', characterId: '20', userInput: 'привет' });

    assert.equal(before, during);
    assert.notEqual(
        before,
        buildTurnKey({ chatId: 'c', characterId: '20', userInput: 'другой ввод' }),
        'a different user input is still a different turn',
    );
});

// --- Which entry point serves a turn -----------------------------------------
//
// The bug these encode: inside one SillyTavern Generate() call, GENERATION_STARTED
// (script.js:4240) and GENERATION_AFTER_COMMANDS (4262) are emitted BEFORE
// sendMessageAsUser() (4394) puts the new message into `chat`. Retrieving from those
// hooks queried the previous turn's text on every normal turn, and the audit's
// user_input_preview showing an old message was the symptom, not a preview bug.
//
// MESSAGE_SENT fires from inside sendMessageAsUser and is awaited by Generate, so it
// sits after the message lands and before prompt assembly (5073+) - the only window
// that is correct on both sides.

test('a normal turn defers to MESSAGE_SENT, because its message does not exist yet', () => {
    // How SillyTavern emits a plain user turn: type is undefined.
    assert.equal(willAppendUserMessage([undefined, {}, false]), true);
    assert.equal(willAppendUserMessage(['normal', {}, false]), true);
    // Options object absent entirely.
    assert.equal(willAppendUserMessage([undefined]), true);
});

test('generation types that append nothing keep retrieving at pre-generation', () => {
    // For these the last message in `chat` is already the right one, so the old path
    // is correct and must not be deferred - deferring would leave them with no
    // retrieval at all, since MESSAGE_SENT never fires.
    for (const type of ['swipe', 'regenerate', 'continue', 'impersonate', 'quiet']) {
        assert.equal(willAppendUserMessage([type, {}, false]), false, type);
    }
});

test('an unrecognised generation type degrades to the old behaviour, not to silence', () => {
    assert.equal(willAppendUserMessage(['some_future_type', {}, false]), false);
});

test('an automatic trigger appends no user message', () => {
    assert.equal(willAppendUserMessage([undefined, { automatic_trigger: true }, false]), false);
});

test('dry runs are still ignored by both entry points', () => {
    assert.equal(isDryRun([undefined, {}, true]), true);
    assert.equal(isDryRun(['swipe', {}, true]), true);
    assert.equal(isDryRun([undefined, {}, false]), false);
    // MESSAGE_SENT carries a single argument and is never a dry run.
    assert.equal(isDryRun([3]), false);
});

test('the turn key is stable once built from the real current message', () => {
    // Both entry points build the key the same way, so a turn served by MESSAGE_SENT
    // cannot be re-served by a later hook seeing the same input.
    const fromMessageSent = buildTurnKey({ chatId: 'c', characterId: '4', userInput: 'Привет' });
    const fromPreGeneration = buildTurnKey({ chatId: 'c', characterId: '4', userInput: 'Привет' });
    assert.equal(fromMessageSent, fromPreGeneration);
});
