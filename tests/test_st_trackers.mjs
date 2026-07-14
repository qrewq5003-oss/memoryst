import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildCharacterTrackerBlock,
    buildTrackerBlock,
    dropOneTrackerUnit,
    evaluateTrackerToasts,
    fetchTrackers,
    resolveTrackerCharacterIds,
    TRACKER_OMITTED_MARKER,
} from '../sillytavern-extension/trackers.mjs';

const CHARACTERS = [
    { name: 'Маркус' },
    { name: 'Алиса' },
    { name: 'Валерия' },
];

test('explicit @memory-tracker marker wins over the entry name and the fallback', () => {
    const { matches } = resolveTrackerCharacterIds({
        entries: [{ uid: '1', comment: 'Алиса — досье @memory-tracker: Валерия', key: ['Алиса'] }],
        characters: CHARACTERS,
        currentCharacterId: '0',
    });

    assert.deepEqual(matches.map(m => [m.characterId, m.characterName, m.source]), [['2', 'Валерия', 'marker']]);
});

test('a numeric marker is already a character_id', () => {
    const { matches } = resolveTrackerCharacterIds({
        entries: [{ uid: '1', comment: '@memory-tracker: 2' }],
        characters: CHARACTERS,
        currentCharacterId: '0',
    });

    assert.equal(matches[0].characterId, '2');
    assert.equal(matches[0].source, 'marker');
});

test('name matching honours Cyrillic word boundaries and reads the entry keys', () => {
    const { matches } = resolveTrackerCharacterIds({
        entries: [
            { uid: 'keys', comment: 'Досье', key: ['Валерия', 'академия'] },
            { uid: 'substring', comment: 'Валериус Гримм', key: ['Валериус'] },
        ],
        characters: CHARACTERS,
        currentCharacterId: null,
        isGroupChat: true,
    });

    assert.deepEqual(matches.map(m => m.characterId), ['2']);
});

test('an unmatched entry falls back to the current character in a solo chat only', () => {
    const solo = resolveTrackerCharacterIds({
        entries: [{ uid: 'lore', comment: 'Устройство академии' }],
        characters: CHARACTERS,
        currentCharacterId: '1',
        isGroupChat: false,
    });
    assert.deepEqual(solo.matches.map(m => [m.characterId, m.source]), [['1', 'fallback']]);

    const group = resolveTrackerCharacterIds({
        entries: [{ uid: 'lore', comment: 'Устройство академии' }],
        characters: CHARACTERS,
        currentCharacterId: '1',
        isGroupChat: true,
    });
    assert.deepEqual(group.matches, []);
    assert.deepEqual(group.unresolved, [{ entryId: 'lore', reason: 'no_match' }]);
});

test('a marker naming an unknown character invents nobody when there is no fallback', () => {
    // In a group chat there is no "the one character this could be about", so an
    // unresolvable marker resolves to nothing - but it is recorded, not swallowed. In a solo
    // chat the fallback still applies: see 'an unresolvable marker must not suppress...',
    // which is the live regression this test used to enshrine.
    const { matches, unresolved } = resolveTrackerCharacterIds({
        entries: [{ uid: 'ghost', comment: '@memory-tracker: Незнакомец' }],
        characters: CHARACTERS,
        currentCharacterId: '0',
        isGroupChat: true,
    });

    assert.deepEqual(matches, []);
    assert.deepEqual(unresolved, [{ entryId: 'ghost', reason: 'marker_unresolved', token: 'Незнакомец' }]);
});

test('dropOneTrackerUnit trims each tracker by its own semantics', () => {
    const timeline = dropOneTrackerUnit(
        'timeline',
        ['- 1 мая — встреча', '- 2 мая — ссора', '- 3 мая — примирение'].join('\n'),
    );
    assert.equal(timeline, [TRACKER_OMITTED_MARKER, '- 2 мая — ссора', '- 3 мая — примирение'].join('\n'));

    const npcs = dropOneTrackerUnit('npc_whoswho', '1. Отец Валерии — декан\n2. Курьер — эпизод');
    assert.equal(npcs, '1. Отец Валерии — декан');

    const notes = dropOneTrackerUnit('character_pov_notes', '- он не врёт\n- он не любит толпу');
    assert.equal(notes, '- он не врёт');
});

test('relationship trimming eats list items from the tail and never the affinity line', () => {
    const content = [
        'Affinity: 62/100 — держит дистанцию',
        'Status: напряжённые',
        'Key facts:',
        '- знает про брата',
        'Open threads:',
        '- обещал вернуться',
    ].join('\n');

    const once = dropOneTrackerUnit('relationship', content);
    assert.equal(once, [
        'Affinity: 62/100 — держит дистанцию',
        'Status: напряжённые',
        'Key facts:',
        '- знает про брата',
    ].join('\n'), 'the emptied "Open threads:" header goes with its last item');

    const twice = dropOneTrackerUnit('relationship', once);
    assert.equal(twice, 'Affinity: 62/100 — держит дистанцию\nStatus: напряжённые');

    assert.equal(dropOneTrackerUnit('relationship', twice), null, 'scalar lines are not droppable');
});

test('the char budget is shared across a character\'s trackers, shrinking the largest first', () => {
    const trackers = [
        {
            tracker_type: 'timeline',
            content: Array.from({ length: 12 }, (_, i) => `- ${i + 1} мая — событие номер ${i + 1}`).join('\n'),
        },
        { tracker_type: 'relationship', content: 'Affinity: 62/100 — держит дистанцию' },
        { tracker_type: 'character_pov_notes', content: '- он не врёт' },
    ];

    const built = buildCharacterTrackerBlock({ trackers, characterName: 'Валерия', maxChars: 220 });

    assert.ok(built.actualChars <= 220, `block is ${built.actualChars} chars`);
    assert.match(built.block, /^\[Character Tracker: Валерия\]/);
    assert.match(built.block, /…\(ранее опущено\)/);
    assert.match(built.block, /событие номер 12/, 'the newest timeline entry survives');
    assert.doesNotMatch(built.block, /событие номер 1 /, 'the oldest timeline entry is cut');
    assert.match(built.block, /Affinity: 62\/100/, 'relationship is the last thing to go');
    assert.ok(built.trimReasons.includes('char_budget_trim:timeline'));
});

test('an unshrinkable overflow drops whole trackers, least important first', () => {
    const trackers = [
        { tracker_type: 'relationship', content: 'Affinity: 62/100 — держит дистанцию' },
        { tracker_type: 'npc_whoswho', content: '1. Отец Валерии — декан академии' },
        { tracker_type: 'character_pov_notes', content: '- он не врёт, и это раздражает' },
    ];

    const built = buildCharacterTrackerBlock({ trackers, characterName: null, maxChars: 90 });

    assert.deepEqual(built.sections.map(s => s.trackerType), ['relationship']);
    assert.deepEqual(built.trimReasons, [
        'char_budget_dropped:character_pov_notes',
        'char_budget_dropped:npc_whoswho',
    ]);
});

test('buildTrackerBlock budgets each matched character separately and skips empty ones', () => {
    const built = buildTrackerBlock({
        matches: [
            { characterId: '2', characterName: 'Валерия' },
            { characterId: '0', characterName: 'Маркус' },
        ],
        trackersByCharacter: {
            2: [{ tracker_type: 'relationship', content: 'Affinity: 62/100' }],
            0: [],
        },
        maxTrackerChars: 1200,
    });

    assert.equal(built.includedCharacters.length, 1);
    assert.equal(built.includedCharacters[0].characterId, '2');
    assert.equal(built.trackerCharCount, built.trackerBlock.length);
    assert.match(built.trackerBlock, /\[Character Tracker: Валерия\]\n\[Relationship\]\nAffinity: 62\/100/);
});

test('a toast fires at the threshold and then stays quiet for a full threshold more', () => {
    const base = { chatId: 'chat1', characterId: '2', threshold: 22 };

    const quiet = evaluateTrackerToasts({
        ...base,
        trackers: [{ tracker_type: 'timeline', messages_since_update: 21 }],
        lastTrackerToastAt: {},
    });
    assert.deepEqual(quiet.toasts, []);

    const first = evaluateTrackerToasts({
        ...base,
        trackers: [{ tracker_type: 'timeline', messages_since_update: 22 }],
        lastTrackerToastAt: {},
        characterName: 'Валерия',
    });
    assert.equal(first.toasts.length, 1);
    assert.match(first.toasts[0].message, /Пора обновить трекер: Timeline \(Валерия\)/);
    assert.deepEqual(first.lastTrackerToastAt, { 'chat1::2::timeline': 22 });

    const stillQuiet = evaluateTrackerToasts({
        ...base,
        trackers: [{ tracker_type: 'timeline', messages_since_update: 30 }],
        lastTrackerToastAt: first.lastTrackerToastAt,
    });
    assert.deepEqual(stillQuiet.toasts, [], 'every message past the threshold must not re-nag');

    const second = evaluateTrackerToasts({
        ...base,
        trackers: [{ tracker_type: 'timeline', messages_since_update: 44 }],
        lastTrackerToastAt: first.lastTrackerToastAt,
    });
    assert.equal(second.toasts.length, 1, 'nags again only after another full threshold');
});

test('updating a tracker resets its quiet period', () => {
    const afterUpdate = evaluateTrackerToasts({
        chatId: 'chat1',
        characterId: '2',
        threshold: 22,
        trackers: [{ tracker_type: 'timeline', messages_since_update: 3 }],
        lastTrackerToastAt: { 'chat1::2::timeline': 44 },
    });

    assert.deepEqual(afterUpdate.toasts, []);
    assert.deepEqual(afterUpdate.lastTrackerToastAt, {}, 'the stale watermark is forgotten, not kept');
});

test('the reminder threshold is floored, so a 0 in settings cannot toast every message', () => {
    const { toasts } = evaluateTrackerToasts({
        chatId: 'chat1',
        characterId: '2',
        threshold: 0,
        trackers: [{ tracker_type: 'relationship', messages_since_update: 4 }],
        lastTrackerToastAt: {},
    });

    assert.deepEqual(toasts, []);
});

test('fetchTrackers sends the scope as query params and the api key as a header', async () => {
    const calls = [];
    const items = await fetchTrackers({
        memoryServiceUrl: 'http://localhost:8001',
        apiKey: 'secret',
        chatId: 'chat1',
        characterId: '2',
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            return { ok: true, json: async () => ({ items: [{ tracker_type: 'timeline' }] }) };
        },
    });

    assert.equal(calls[0].url, 'http://localhost:8001/memory/trackers?chat_id=chat1&character_id=2');
    assert.equal(calls[0].options.headers['X-API-Key'], 'secret');
    assert.deepEqual(items, [{ tracker_type: 'timeline' }]);
});

test('an unavailable tracker endpoint warns in the settings panel, not only in the console', async () => {
    const { buildTrackerStatusBannerMarkup, buildSettingsUiMarkup } = await import('../sillytavern-extension/settings-ui.mjs');

    assert.equal(buildTrackerStatusBannerMarkup(null), '', 'no fetch yet: stay silent');
    assert.equal(buildTrackerStatusBannerMarkup({ status: 'ok' }), '', 'a working backend: stay silent');

    const unsupported = buildTrackerStatusBannerMarkup({ status: 'unsupported', detail: 'trackers_http_404' });
    assert.match(unsupported, /Трекеры недоступны/);
    assert.match(unsupported, /не поддерживает эту функцию/);
    assert.match(unsupported, /data-tracker-status="unsupported"/);
    assert.match(unsupported, /memory-service-compat-banner/, 'reuses the mismatch banner styling');

    const errored = buildTrackerStatusBannerMarkup({ status: 'error', detail: 'Failed to fetch' });
    assert.match(errored, /Бэкенд не ответил/);
    assert.match(errored, /Failed to fetch/);

    // The banner has to land inside the Trackers group, next to the knobs it invalidates.
    const panel = buildSettingsUiMarkup({}, null, { status: 'unsupported', detail: 'trackers_http_404' });
    const trackerSection = panel.slice(panel.indexOf('<h4>Trackers</h4>'), panel.indexOf('<h4>Audit</h4>'));
    assert.match(trackerSection, /data-tracker-status="unsupported"/);
    assert.match(trackerSection, /data-memory-setting="trackerInjectionEnabled"/);
});

test('the tracker banner escapes the backend-supplied failure detail', async () => {
    const { buildTrackerStatusBannerMarkup } = await import('../sillytavern-extension/settings-ui.mjs');

    const banner = buildTrackerStatusBannerMarkup({ status: 'error', detail: '<script>alert(1)</script>' });
    assert.doesNotMatch(banner, /<script>/);
    assert.match(banner, /&lt;script&gt;/);
});

test('a single oversized tracker is truncated to fit, never dropped to nothing', () => {
    // Modelled on a relationship doc observed live: 8.4k chars whose non-list lines alone
    // (an essay in affinity_evidence, a paragraph of status) blow the budget, so unit
    // trimming can never get it under. Dropping it left the prompt with no tracker at all.
    const trackers = [{
        tracker_type: 'relationship',
        content: [
            `Affinity: 99/100 — ${'подробное обоснование '.repeat(40)}`,
            `Status: ${'развёрнутое описание сцены '.repeat(40)}`,
            'Key facts:',
            '- знает про брата',
        ].join('\n'),
    }];

    const built = buildCharacterTrackerBlock({ trackers, characterName: 'Валерия', maxChars: 1200 });

    assert.ok(built.block.length > 0, 'the tracker must survive in some form');
    assert.ok(built.actualChars <= 1200, `block is ${built.actualChars} chars`);
    assert.match(built.block, /^\[Character Tracker: Валерия\]\n\[Relationship\]\nAffinity: 99\/100/);
    assert.match(built.block, /…$/, 'the cut is marked');
    assert.ok(built.trimReasons.includes('char_budget_truncated:relationship'));
    assert.ok(!built.trimReasons.includes('char_budget_dropped:relationship'));
});

test('an unresolvable marker must not suppress the other branches', () => {
    // Live regression: adding "@memory-tracker: Valeria Mendoza" to a lorebook entry turned
    // tracker injection OFF - the marker parsed, failed to map to a character index, and the
    // early return took the fallback with it. A marker is a hint about who an entry is
    // about, not a veto on the entry.
    const { matches, unresolved } = resolveTrackerCharacterIds({
        entries: [{ uid: 'e1', comment: 'Досье @memory-tracker: Кто-то Неизвестный' }],
        characters: CHARACTERS,
        currentCharacterId: '2',
        isGroupChat: false,
    });

    assert.deepEqual(matches.map(m => [m.characterId, m.source]), [['2', 'fallback']]);
    assert.deepEqual(unresolved, [{ entryId: 'e1', reason: 'marker_unresolved', token: 'Кто-то Неизвестный' }]);
});

test('a marker matches a partial name rather than demanding the full card name', () => {
    const { matches } = resolveTrackerCharacterIds({
        entries: [{ uid: 'e1', comment: '@memory-tracker: Валерия' }],
        characters: [{ name: 'Маркус' }, { name: 'Валерия Мендоса' }],
        currentCharacterId: '0',
    });

    assert.deepEqual(matches.map(m => [m.characterId, m.characterName, m.source]), [
        ['1', 'Валерия Мендоса', 'marker'],
    ]);
});

test('with an empty character roster the marker cannot resolve, but injection still happens', () => {
    // getContext().characters being empty is exactly what tracker_roster_size in the audit
    // exists to reveal.
    const { matches, unresolved } = resolveTrackerCharacterIds({
        entries: [{ uid: 'e1', comment: '@memory-tracker: Валерия' }],
        characters: [],
        currentCharacterId: '20',
        isGroupChat: false,
    });

    assert.deepEqual(matches.map(m => [m.characterId, m.source]), [['20', 'fallback']]);
    assert.equal(unresolved[0].reason, 'marker_unresolved');
});

test('the strongest resolution wins regardless of lorebook entry order', () => {
    // Live regression: the lorebook fired two entries. The plain one came first and resolved
    // to the current character by fallback; the marked one followed and mapped to the same
    // character, but the first source kept the slot - so the marker looked ignored and the
    // block heading lost the character's name.
    const { matches } = resolveTrackerCharacterIds({
        entries: [
            { uid: 'plain', comment: '002 - Утро после Дня святого Валентина' },
            { uid: 'marked', comment: '001 - Массаж @memory-tracker: Валерия' },
        ],
        characters: CHARACTERS,
        currentCharacterId: '2',
        isGroupChat: false,
    });

    assert.equal(matches.length, 1);
    assert.equal(matches[0].source, 'marker');
    assert.equal(matches[0].characterName, 'Валерия');
    assert.deepEqual(matches[0].entryIds, ['plain', 'marked']);
});
