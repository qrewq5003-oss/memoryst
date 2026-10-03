/**
 * main.mjs itself, loaded against a fake SillyTavern (tests/st_harness).
 *
 * Everything main.mjs calls has its own tests; what it does with SillyTavern's events
 * had none until 2026-10-03, because it imports SillyTavern and node could not load it.
 * These are the behaviours a user would notice if the wiring broke.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { hang, loadMain, networkDown, defaultBackend } from './st_harness/harness.mjs';

test('a normal turn: retrieve on send, inject as numbers, store after render', async () => {
    const st = await loadMain();
    assert.equal(st.calls('/memory/version').length, 1, 'handshake at load');

    await st.userSends('Где ты работаешь?');

    const [retrieve] = st.calls('/memory/retrieve');
    assert.equal(retrieve.body.user_input, 'Где ты работаешь?');
    assert.equal(retrieve.body.chat_id, 'Mai - 2026-10-02@12h40m36s920ms');
    // The avatar, never the card's index in ST's array.
    assert.equal(retrieve.body.character_id, 'Mai.png');

    const injected = st.prompts.get('memoryst');
    assert.match(injected.value, /кафе/);
    // ST does Number(position) and Number(role); a string role becomes NaN.
    assert.equal(typeof injected.position, 'number');
    assert.equal(typeof injected.role, 'number');

    assert.equal(st.calls('/memory/store').length, 0, 'nothing stored before the reply');
    await st.characterReplies('В кафе «Ботаника», ты же знаешь.');
    const [store] = st.calls('/memory/store');
    assert.deepEqual(store.body.messages.slice(-2), [
        { role: 'user', text: 'Где ты работаешь?' },
        { role: 'assistant', text: 'В кафе «Ботаника», ты же знаешь.' },
    ]);
    assert.equal(store.body.character_name, 'Mai');
    assert.deepEqual(st.toasts, []);
});

test('the evening of 2026-10-02: down at load, one sticky warning, then a count on recovery', async () => {
    const st = await loadMain({ backend: networkDown });
    assert.equal(st.toasts.length, 1, 'a page loaded with the backend down warns at once');
    assert.equal(st.toasts[0].level, 'error');
    assert.deepEqual(
        { timeOut: st.toasts[0].options.timeOut, extendedTimeOut: st.toasts[0].options.extendedTimeOut },
        { timeOut: 0, extendedTimeOut: 0 },
        'it fires once per outage, so it must not fade',
    );

    await st.userSends('Ты дома?');
    assert.equal(st.prompts.get('memoryst').value, '', 'no stale memory left in the prompt');
    await st.characterReplies('Дома.');
    await st.userSends('Я скоро буду.');
    await st.characterReplies('Жду.');
    assert.equal(st.toasts.length, 1, 'still one: no toast per turn');

    st.backend = defaultBackend;
    await st.userSends('Я пришёл.');
    assert.equal(st.toasts.length, 2);
    assert.equal(st.toasts[1].level, 'success');
    assert.match(st.toasts[1].message, /Не сохранено ходов: 2/);
});

test('a retrieve that times out costs the turn its memory, not a toast or the generation', async () => {
    const st = await loadMain({
        settings: { retrieveTimeoutMs: 500 },
        backend: (path, init) => (path.startsWith('/memory/retrieve') ? hang(path, init) : defaultBackend(path, init)),
    });
    const started = Date.now();
    await st.userSends('Где ты работаешь?');

    assert.ok(Date.now() - started < 3000, 'MESSAGE_SENT returned, so Generate() can go on');
    assert.equal(st.prompts.get('memoryst').value, '');
    assert.deepEqual(st.toasts, []);
});

test('a 5xx on store is an outage; a 4xx is an answer', async () => {
    const failing = await loadMain({
        backend: (path, init) => (path.startsWith('/memory/store')
            ? { ok: false, status: 503, json: async () => ({}) }
            : defaultBackend(path, init)),
    });
    await failing.userSends('Привет');
    await failing.characterReplies('Привет!');
    assert.deepEqual(failing.toasts.map(toast => toast.level), ['error']);

    const rejecting = await loadMain({
        backend: (path, init) => (path.startsWith('/memory/store')
            ? { ok: false, status: 422, json: async () => ({}) }
            : defaultBackend(path, init)),
    });
    await rejecting.userSends('Привет');
    await rejecting.characterReplies('Привет!');
    assert.deepEqual(rejecting.toasts, []);
});

test('a swipe takes back what the rejected reply stored, using created_ids only', async () => {
    const st = await loadMain();
    await st.userSends('Что на ужин?');
    await st.characterReplies('Паста.');
    assert.deepEqual(st.calls('/memory/m-'), []);

    await st.characterReplies('Суп.', 'swipe');

    const deletes = st.requests.filter(request => request.method === 'DELETE');
    assert.deepEqual(deletes.map(request => request.path), ['/memory/m-new']);
    const stores = st.calls('/memory/store');
    assert.equal(stores.length, 2);
    assert.equal(stores[1].body.messages.at(-1).text, 'Суп.');
});

test('a disabled extension sends nothing on a turn and says nothing', async () => {
    const st = await loadMain({ settings: { enabled: false } });
    await st.userSends('Привет');
    await st.characterReplies('Привет!');
    assert.deepEqual(st.calls('/memory/retrieve'), []);
    assert.deepEqual(st.calls('/memory/store'), []);
    assert.deepEqual(st.toasts, []);
});

test('switching chats drops the previous chat\'s memory from the prompt', async () => {
    const st = await loadMain();
    await st.userSends('Где ты работаешь?');
    assert.match(st.prompts.get('memoryst').value, /кафе/);

    st.context.chatId = 'Camila - 2026-09-22@11h55m22s610ms';
    st.context.characters = [{ avatar: 'Camila1.png', name: 'Camila' }];
    st.context.chat = [];
    await st.emit('chat_id_changed');

    assert.equal(st.prompts.get('memoryst').value, '');
});

test('deleting the reply that was just stored takes its memories back', async () => {
    const st = await loadMain();
    await st.userSends('Что на ужин?');
    await st.characterReplies('Паста.');

    st.context.chat.pop();
    await st.emit('message_deleted', st.context.chat.length);

    const deletes = st.requests.filter(request => request.method === 'DELETE');
    assert.deepEqual(deletes.map(request => request.path), ['/memory/m-new']);
});

test('once the user moves on, deleting an older message takes nothing back', async () => {
    const st = await loadMain();
    await st.userSends('Что на ужин?');
    await st.characterReplies('Паста.');
    await st.userSends('Отлично.');

    st.context.chat.splice(1, 1);
    await st.emit('message_deleted', st.context.chat.length);

    assert.deepEqual(st.requests.filter(request => request.method === 'DELETE'), []);
});

test('a regenerate appends no message, so the pre-generation hook retrieves', async () => {
    const st = await loadMain();
    st.context.chat.push({ is_user: true, mes: 'Где ты работаешь?' });

    await st.emit('generation_started', 'regenerate', {}, false);

    const [retrieve] = st.calls('/memory/retrieve');
    assert.equal(retrieve.body.user_input, 'Где ты работаешь?');
    assert.match(st.prompts.get('memoryst').value, /кафе/);
});

test('a normal turn retrieves once, on MESSAGE_SENT, not on the hook before it', async () => {
    const st = await loadMain();
    // ST fires this before appending the user's message; retrieving here would query
    // with the previous message.
    await st.emit('generation_started', 'normal', {}, false);
    assert.deepEqual(st.calls('/memory/retrieve'), []);

    await st.userSends('Где ты работаешь?');
    await st.emit('generate_before_combine_prompts', 'normal', {}, false);
    assert.equal(st.calls('/memory/retrieve').length, 1);
});
