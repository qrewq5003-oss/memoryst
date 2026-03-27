import test from 'node:test';
import assert from 'node:assert/strict';

import {
    DEFAULT_AUDIT_SETTINGS,
    DEFAULT_CONNECTION_SETTINGS,
    DEFAULT_PROMPT_BUDGET_SETTINGS,
    DEFAULT_RETRIEVAL_SETTINGS,
    DEFAULT_SETTINGS,
    LONG_CHAT_RECOMMENDED_BASELINE,
    normalizeExtensionSettings,
    serializeExtensionSettings,
} from '../sillytavern-extension/settings.mjs';

test('default extension settings stay coherent across grouped and flat views', () => {
    assert.equal(DEFAULT_SETTINGS.enabled, DEFAULT_CONNECTION_SETTINGS.enabled);
    assert.equal(DEFAULT_SETTINGS.retrieveLimit, DEFAULT_RETRIEVAL_SETTINGS.retrieveLimit);
    assert.equal(DEFAULT_SETTINGS.recentMessagesCount, DEFAULT_RETRIEVAL_SETTINGS.recentMessagesCount);
    assert.equal(DEFAULT_SETTINGS.maxPromptChars, DEFAULT_PROMPT_BUDGET_SETTINGS.maxPromptChars);
    assert.equal(DEFAULT_SETTINGS.auditMaxRecords, DEFAULT_AUDIT_SETTINGS.auditMaxRecords);

    assert.equal(LONG_CHAT_RECOMMENDED_BASELINE.retrieveLimit, DEFAULT_RETRIEVAL_SETTINGS.retrieveLimit);
    assert.equal(LONG_CHAT_RECOMMENDED_BASELINE.maxPromptMemories, DEFAULT_PROMPT_BUDGET_SETTINGS.maxPromptMemories);
    assert.equal(LONG_CHAT_RECOMMENDED_BASELINE.maxPromptChars, DEFAULT_PROMPT_BUDGET_SETTINGS.maxPromptChars);
});

test('normalizeExtensionSettings supports legacy flat settings shape', () => {
    const normalized = normalizeExtensionSettings({
        enabled: true,
        retrieveLimit: 7,
        recentMessagesCount: 10,
        maxPromptChars: 640,
        auditEnabled: true,
        recentAudits: [{ interaction_id: 'a1' }],
    });

    assert.equal(normalized.enabled, true);
    assert.equal(normalized.retrieveLimit, 7);
    assert.equal(normalized.recentMessagesCount, 10);
    assert.equal(normalized.maxPromptChars, 640);
    assert.equal(normalized.auditEnabled, true);
    assert.deepEqual(normalized.recentAudits, [{ interaction_id: 'a1' }]);
});

test('normalizeExtensionSettings supports grouped settings shape and serialization stays grouped', () => {
    const normalized = normalizeExtensionSettings({
        connection: {
            enabled: true,
            memoryServiceUrl: 'http://localhost:9000',
        },
        retrieval: {
            retrieveLimit: 6,
            recentMessagesCount: 12,
        },
        promptBudget: {
            maxPromptMemories: 5,
            maxPromptChars: 600,
            maxSummaryItems: 1,
            maxStableItems: 3,
            maxEpisodicItems: 1,
        },
        audit: {
            auditEnabled: true,
            auditMaxRecords: 12,
            auditPreviewChars: 180,
        },
        recentAudits: [],
    });

    assert.equal(normalized.memoryServiceUrl, 'http://localhost:9000');
    assert.equal(normalized.retrieveLimit, 6);
    assert.equal(normalized.maxStableItems, 3);
    assert.equal(normalized.auditPreviewChars, 180);

    const serialized = serializeExtensionSettings(normalized);
    assert.equal(serialized.connection.enabled, true);
    assert.equal(serialized.retrieval.retrieveLimit, 6);
    assert.equal(serialized.promptBudget.maxPromptChars, 600);
    assert.equal(serialized.audit.auditEnabled, true);
    assert.ok(!Object.hasOwn(serialized, 'retrieveLimit'));
});
