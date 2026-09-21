import test from 'node:test';
import assert from 'node:assert/strict';

import { createIntegrationAuditRecord } from '../sillytavern-extension/audit.mjs';
import { resolveEffectiveScope } from '../sillytavern-extension/scope.mjs';

test('the character id is the avatar, not the position in the roster', () => {
    // SillyTavern's characterId is an array index; storing it as identity made a chat's
    // memories follow whichever card later sat at that position.
    const scope = resolveEffectiveScope({
        chatId: 'chat-alpha',
        characterId: 1,
        characters: [{ name: 'Алиса', avatar: 'Alisa.png' }, { name: 'Маркус', avatar: 'Markus.png' }],
        groupId: null,
        chat: [{ is_user: true, mes: 'Привет' }],
    });

    assert.equal(scope.chatId, 'chat-alpha');
    assert.equal(scope.characterId, 'Markus.png');
    assert.equal(scope.characterIndex, 1);
    assert.equal(scope.chatScopeSource, 'chatId');
    assert.equal(scope.characterScopeSource, 'character_avatar');
    assert.equal(scope.scopeKey, 'chat-alpha::Markus.png');
});

test('the same index pointing at a different card yields a different id', () => {
    // The whole failure, in one assertion: a reorder must not silently rename a scope.
    const before = resolveEffectiveScope({
        chatId: 'chat-alpha',
        characterId: 0,
        characters: [{ name: 'Алиса', avatar: 'Alisa.png' }],
    });
    const after = resolveEffectiveScope({
        chatId: 'chat-alpha',
        characterId: 0,
        characters: [{ name: 'Маркус', avatar: 'Markus.png' }],
    });
    assert.notEqual(before.characterId, after.characterId);
});

test('a roster without avatars falls back to the name, then to the index', () => {
    const byName = resolveEffectiveScope({
        chatId: 'c', characterId: 0, characters: [{ name: 'Алиса' }],
    });
    assert.equal(byName.characterId, 'Алиса');

    // The old, unstable behaviour, kept on purpose: a turn stored under a shaky id beats
    // a turn stored under none.
    const byIndex = resolveEffectiveScope({ chatId: 'c', characterId: 3, characters: [] });
    assert.equal(byIndex.characterId, '3');
    assert.equal(byIndex.characterScopeSource, 'character_index_fallback');
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
