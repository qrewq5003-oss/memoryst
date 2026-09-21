/**
 * Whether a turn gets memory, and which block it gets.
 *
 * Both decisions used to sit inline in main.mjs, which exports nothing and no test can
 * import. Neither raises anything when wrong: the first quietly serves or skips a turn
 * and writes the reason a later investigation will read, the second decides what text is
 * injected into the prompt.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import {
    SKIP_DISABLED,
    SKIP_NO_CHAT,
    SKIP_NO_MESSAGES,
    SKIP_NO_USER_MESSAGE,
    chooseMemoryBlock,
    shouldRetrieve,
    shouldStore,
} from '../sillytavern-extension/retrieve-policy.mjs';

const chatContext = { chatId: 'chat-1' };

test('a served turn proceeds with no reason attached', () => {
    assert.deepEqual(
        shouldRetrieve({ enabled: true, chatContext, userInput: 'где ты работаешь?' }),
        { proceed: true },
    );
});

test('a disabled extension reports being off, not a missing chat', () => {
    // Order is not cosmetic: "missing_chat_context" reads as a fault, and this is a
    // setting. Reading the audit weeks later, that difference is the whole message.
    const off = shouldRetrieve({ enabled: false, chatContext: null, userInput: '' });
    assert.equal(off.reason, SKIP_DISABLED);
});

test('no chat outranks no user message', () => {
    const noChat = shouldRetrieve({ enabled: true, chatContext: null, userInput: '' });
    assert.equal(noChat.reason, SKIP_NO_CHAT);
});

test('a context without a chatId counts as no chat', () => {
    // getContext() returns an object before a chat is open; the object is not the point,
    // the chatId is.
    assert.equal(
        shouldRetrieve({ enabled: true, chatContext: {}, userInput: 'x' }).reason,
        SKIP_NO_CHAT,
    );
});

test('an empty user message stops a retrieve', () => {
    assert.equal(
        shouldRetrieve({ enabled: true, chatContext, userInput: '' }).reason,
        SKIP_NO_USER_MESSAGE,
    );
});

test('store shares the first two reasons with retrieve', () => {
    // They used to be spelled inline in both, so a rename would have left the audit
    // saying two different things about one condition depending on which half of the
    // turn hit it.
    assert.equal(shouldStore({ enabled: false, chatContext, messageCount: 4 }).reason, SKIP_DISABLED);
    assert.equal(shouldStore({ enabled: true, chatContext: null, messageCount: 4 }).reason, SKIP_NO_CHAT);
});

test('store needs messages, not a user message', () => {
    // A scene can be assistant messages alone, which is why the third check differs.
    assert.deepEqual(shouldStore({ enabled: true, chatContext, messageCount: 2 }), { proceed: true });
    assert.equal(
        shouldStore({ enabled: true, chatContext, messageCount: 0 }).reason,
        SKIP_NO_MESSAGES,
    );
});

test('the client budget wins over the backend block', () => {
    assert.equal(
        chooseMemoryBlock({ budgetedBlock: 'из бюджета', backendBlock: 'от бэкенда' }),
        'из бюджета',
    );
});

test('the backend block is the fallback, not the default', () => {
    assert.equal(
        chooseMemoryBlock({ budgetedBlock: '', backendBlock: 'от бэкенда' }),
        'от бэкенда',
    );
});

test('neither block gives an empty string, never undefined', () => {
    // setExtensionPrompt stringifies its value, so undefined would reach the prompt as
    // the literal word "undefined".
    assert.equal(chooseMemoryBlock({}), '');
    assert.equal(chooseMemoryBlock({ budgetedBlock: undefined, backendBlock: null }), '');
});
