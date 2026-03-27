import test from 'node:test';
import assert from 'node:assert/strict';

import { createIntegrationAuditRecord } from '../sillytavern-extension/audit.mjs';
import { resolveEffectiveScope } from '../sillytavern-extension/scope.mjs';

test('resolveEffectiveScope keeps chat and character scopes distinct when both are present', () => {
    const scope = resolveEffectiveScope({
        chatId: 'chat-alpha',
        characterId: 'char-marcus',
        groupId: null,
        chat: [{ is_user: true, mes: 'Привет' }],
    });

    assert.equal(scope.chatId, 'chat-alpha');
    assert.equal(scope.characterId, 'char-marcus');
    assert.equal(scope.chatScopeSource, 'chatId');
    assert.equal(scope.characterScopeSource, 'characterId');
    assert.equal(scope.scopeKey, 'chat-alpha::char-marcus');
});

test('resolveEffectiveScope uses stable chat fallback for missing character id', () => {
    const scope = resolveEffectiveScope({
        chatId: 'chat-beta',
        groupId: 'group-1',
        chat: [],
    });

    assert.equal(scope.chatId, 'chat-beta');
    assert.equal(scope.characterId, 'chat-beta');
    assert.equal(scope.chatScopeSource, 'chatId');
    assert.equal(scope.characterScopeSource, 'chatId_fallback');
});

test('audit records capture effective scope identifiers and sources', () => {
    const record = createIntegrationAuditRecord({
        chatId: 'chat-gamma',
        characterId: 'char-elena',
        groupId: 'group-7',
        chatScopeSource: 'chatId',
        characterScopeSource: 'characterId',
        recentMessagesCount: 8,
    });

    assert.equal(record.chat_id, 'chat-gamma');
    assert.equal(record.character_id, 'char-elena');
    assert.equal(record.group_id, 'group-7');
    assert.equal(record.chat_scope_source, 'chatId');
    assert.equal(record.character_scope_source, 'characterId');
});
