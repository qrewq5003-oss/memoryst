/**
 * Turning SillyTavern's `chat` array into the messages the memory service sees.
 *
 * This is the narrowest point in the whole extension: every retrieve queries with what
 * `lastUserText` returns, and every store extracts from what `recentMessages` returns.
 * An off-by-one, a missed field alias or a misread role does not raise anything - it
 * quietly feeds the wrong text to both paths, and the only symptom is memory that looks
 * subtly wrong later.
 *
 * Extracted from main.mjs on 2026-09-21 because main.mjs is 1310 lines with no exports
 * and no test can import it: it pulls SillyTavern modules at the top level. Everything
 * testable here has been moved out into modules like this one, and this was the last
 * piece of the turn path still verifiable only by reading an audit record afterwards.
 *
 * Pure on purpose: the caller passes the array, so a test does not need SillyTavern.
 */

/**
 * ST messages carry the author in `is_user`; `role` is not a field ST itself sets, but
 * other extensions and imported chats do set it, and this extension has always honoured
 * both. Where they disagree, `role` wins - an explicit value beats an inferred one.
 */
function roleOf(message) {
    return message?.role || (message?.is_user ? 'user' : 'assistant');
}

/**
 * `mes` is ST's field. `text` is what imported/synthetic messages use, and what this
 * extension's own fixtures use. Neither can be dropped: a message with only `text`
 * would otherwise reach the backend as an empty string and be stored as one.
 */
function textOf(message) {
    return message?.mes || message?.text || '';
}

/**
 * The last `count` messages, in chat order, as {role, text}.
 *
 * A non-positive count returns nothing rather than the whole chat: `slice(-0)` is
 * `slice(0)`, which is the entire array, and sending an entire long chat to extraction
 * would be expensive and wrong. That is a real JavaScript trap, not a hypothetical.
 */
export function recentMessages(chat, count) {
    if (!Array.isArray(chat) || !Number.isFinite(count) || count <= 0) {
        return [];
    }
    return chat.slice(-count).map(message => ({
        role: roleOf(message),
        text: textOf(message),
    }));
}

/**
 * The text of the most recent user message, searching backwards, or '' if there is none.
 *
 * The backwards scan matters: the query for this turn is the message the user just sent,
 * not the first one they ever sent.
 */
export function lastUserText(chat) {
    if (!Array.isArray(chat)) {
        return '';
    }
    for (let index = chat.length - 1; index >= 0; index -= 1) {
        if (roleOf(chat[index]) === 'user') {
            return textOf(chat[index]);
        }
    }
    return '';
}
