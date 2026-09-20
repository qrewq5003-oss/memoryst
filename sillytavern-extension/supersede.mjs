/**
 * Undoing a turn the user took back.
 *
 * `/memory/store` runs on CHARACTER_MESSAGE_RENDERED, and SillyTavern renders a message
 * for a swipe and a regenerate too. So every alternative the user cycled past was
 * extracted and kept: a chat where you swipe three times before settling left four
 * versions of that moment in memory, three of which never happened. Deleting or editing
 * a message had the same shape - the memories extracted from the old text stayed.
 *
 * What makes the undo possible is that `/memory/store` reports `created_ids`. Those are
 * the memories the call *created*; `items` is wider, because a candidate that soft-matches
 * an existing memory is merged into it instead. Deleting all of `items` would destroy
 * rows that predate the turn, which is why the backend reports the two separately.
 *
 * The limit that follows, and it is real: a rejected reply whose facts were *merged* into
 * an older memory cannot be undone here, only one that created its own. Soft-matching
 * needs the same type plus overlapping entities and keywords, so what got merged was
 * already about the same thing - the contamination is bounded, not absent.
 *
 * Nothing here re-extracts. It does not have to: the next store sends the last N messages
 * again, so an edited message is re-read on the following turn on its own.
 */

// The render types that replace the reply rather than adding one. SillyTavern passes the
// generation type as the second argument to CHARACTER_MESSAGE_RENDERED (script.js:6634).
// 'continue' is deliberately absent: it extends the same message, so the next store sees
// the longer text and the backend's dedupe merges it rather than double-counting.
export const SUPERSEDING_RENDER_TYPES = ['swipe', 'regenerate'];

export function isSupersedingRender(renderType) {
    return SUPERSEDING_RENDER_TYPES.includes(String(renderType || ''));
}

/**
 * What a completed store leaves behind, so the next event can undo it.
 *
 * chatLength is recorded because MESSAGE_DELETED reports the chat's length *after* the
 * deletion and nothing else - no id, no content (script.js:1609) - so a length is the
 * only thing there is to compare against.
 */
export function buildStoredTurn({
    chatId = null,
    characterId = null,
    createdIds = [],
    chatLength = 0,
    recentMessagesCount = 0,
} = {}) {
    const ids = (createdIds || []).filter(Boolean);
    if (!chatId || !ids.length) {
        return null;
    }
    return {
        chatId,
        characterId,
        memoryIds: ids,
        chatLength,
        recentMessagesCount,
    };
}

function isSameChat(storedTurn, chatId) {
    return Boolean(storedTurn) && storedTurn.chatId === chatId;
}

/**
 * A deletion invalidates the stored turn when the chat got shorter than it was when we
 * stored - the store window is the tail of the chat, so any deletion at the end is inside
 * it. A chat that is the same length or longer means the deletion happened somewhere this
 * turn's memories did not come from.
 */
export function shouldDiscardAfterDelete(storedTurn, { chatId = null, chatLengthAfterDelete = 0 } = {}) {
    if (!isSameChat(storedTurn, chatId)) {
        return false;
    }
    return Number(chatLengthAfterDelete) < Number(storedTurn.chatLength);
}

/**
 * An edit invalidates the stored turn when the edited message is one of the messages that
 * store actually sent - the last `recentMessagesCount` of them. Editing message 3 of a
 * fifty-message chat changes nothing we could fix from here, and pretending otherwise
 * would delete a perfectly good turn's memories.
 */
export function shouldDiscardAfterEdit(storedTurn, { chatId = null, editedMessageIndex = null } = {}) {
    if (!isSameChat(storedTurn, chatId)) {
        return false;
    }
    const index = Number(editedMessageIndex);
    if (!Number.isInteger(index) || index < 0) {
        return false;
    }
    const windowStart = Math.max(0, storedTurn.chatLength - storedTurn.recentMessagesCount);
    return index >= windowStart;
}
