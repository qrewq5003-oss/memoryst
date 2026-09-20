import test from 'node:test';
import assert from 'node:assert/strict';

import {
    SUPERSEDING_RENDER_TYPES,
    buildStoredTurn,
    isSupersedingRender,
    shouldDiscardAfterDelete,
    shouldDiscardAfterEdit,
} from '../sillytavern-extension/supersede.mjs';

function storedTurn(overrides = {}) {
    return buildStoredTurn({
        chatId: 'chat-1',
        characterId: '0',
        createdIds: ['m1', 'm2'],
        chatLength: 20,
        recentMessagesCount: 8,
        ...overrides,
    });
}

test('a swipe and a regenerate supersede the reply; a fresh reply does not', () => {
    for (const type of SUPERSEDING_RENDER_TYPES) {
        assert.equal(isSupersedingRender(type), true, type);
    }
    for (const type of ['normal', 'first_message', undefined, null, '']) {
        assert.equal(isSupersedingRender(type), false, String(type));
    }
});

test("'continue' is not superseding", () => {
    // It extends the same message rather than replacing it, so the next store sees the
    // longer text and the backend's dedupe merges it. Treating it as a replacement would
    // delete memories for text that is still on screen.
    assert.equal(isSupersedingRender('continue'), false);
});

test('a store that created nothing leaves nothing to undo', () => {
    // The turn's facts were all merged into existing memories. Deleting those would take
    // rows that predate the turn, so there is deliberately no stored turn at all.
    assert.equal(buildStoredTurn({ chatId: 'chat-1', createdIds: [] }), null);
    assert.equal(buildStoredTurn({ chatId: 'chat-1', createdIds: [null, ''] }), null);
});

test('a store without a chat id is not recorded', () => {
    assert.equal(buildStoredTurn({ chatId: null, createdIds: ['m1'] }), null);
});

test('deleting a message shortens the chat and discards the turn', () => {
    assert.equal(
        shouldDiscardAfterDelete(storedTurn(), { chatId: 'chat-1', chatLengthAfterDelete: 19 }),
        true,
    );
});

test('a chat that did not get shorter leaves the turn alone', () => {
    for (const length of [20, 21]) {
        assert.equal(
            shouldDiscardAfterDelete(storedTurn(), { chatId: 'chat-1', chatLengthAfterDelete: length }),
            false,
        );
    }
});

test('a delete in a different chat never touches this turn', () => {
    assert.equal(
        shouldDiscardAfterDelete(storedTurn(), { chatId: 'chat-2', chatLengthAfterDelete: 1 }),
        false,
    );
});

test('editing a message inside the stored window discards the turn', () => {
    // chatLength 20, window 8 -> messages 12..19 were the ones sent to store.
    for (const index of [12, 15, 19]) {
        assert.equal(
            shouldDiscardAfterEdit(storedTurn(), { chatId: 'chat-1', editedMessageIndex: index }),
            true,
            `index ${index}`,
        );
    }
});

test('editing an older message leaves the turn alone', () => {
    // Nothing here could fix memories extracted from message 3 of a long chat, and
    // discarding would delete a turn the user never touched.
    for (const index of [0, 5, 11]) {
        assert.equal(
            shouldDiscardAfterEdit(storedTurn(), { chatId: 'chat-1', editedMessageIndex: index }),
            false,
            `index ${index}`,
        );
    }
});

test('a nonsense edit index is ignored rather than guessed at', () => {
    for (const index of [null, undefined, -1, 'x', NaN]) {
        assert.equal(
            shouldDiscardAfterEdit(storedTurn(), { chatId: 'chat-1', editedMessageIndex: index }),
            false,
            String(index),
        );
    }
});

test('a chat shorter than the store window still has a sane window', () => {
    // chatLength 3, window 8: the window cannot start below 0, and every message is in it.
    const short = storedTurn({ chatLength: 3, recentMessagesCount: 8 });
    assert.equal(shouldDiscardAfterEdit(short, { chatId: 'chat-1', editedMessageIndex: 0 }), true);
});

test('with no stored turn every event is a no-op', () => {
    assert.equal(shouldDiscardAfterDelete(null, { chatId: 'chat-1', chatLengthAfterDelete: 0 }), false);
    assert.equal(shouldDiscardAfterEdit(null, { chatId: 'chat-1', editedMessageIndex: 5 }), false);
});
