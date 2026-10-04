/**
 * The processed boundary: the highest chat message memoryst is known to have processed.
 *
 * Why this exists: a store sends only the last `recentMessagesCount` messages and forgets
 * them. One failed turn heals itself - the next store's window covers it - but anything
 * longer is lost for good. On 2026-10-02 the backend was down for 20 turns and nothing
 * of that evening was stored; a failing extraction LLM loses turns the same way, with the
 * backend answering 200 throughout.
 *
 * Borrowed from SillyTavern Memory Books, which keeps a per-chat "highest processed
 * message" and starts its next memory from there. Here the ordinary store stays as it
 * was - same window, same speed - and only a gap behind the window triggers catch-up,
 * which goes through /memory/backfill: it already splits into scenes and skips facts that
 * are already stored, so overlapping a range is safe.
 *
 * The boundary lives in SillyTavern's chat metadata, which is saved in the chat file:
 * it survives a reload, and the chat id stored beside it keeps a branch (whose metadata
 * is copied from its parent) from taking the parent's boundary as its own.
 *
 * Pure, so it can be tested without SillyTavern; main.mjs reads and writes the metadata.
 */

export const METADATA_KEY = 'memoryst';

// Messages per catch-up request. Backfill runs one extraction call per scene of 8, so
// this is five calls - minutes, not seconds, and a longer gap simply continues on the
// next successful turn. Kept under the 600s backfill deadline in http.mjs.
export const CATCH_UP_MAX_MESSAGES = 40;

/** The boundary for this chat, or null if unknown (new chat, older extension, branch). */
export function readBoundary(chatMetadata, chatId) {
    const entry = chatMetadata?.[METADATA_KEY];
    if (!entry || entry.chatId !== chatId) {
        return null;
    }
    const value = Number(entry.processedThrough);
    return Number.isInteger(value) && value >= -1 ? value : null;
}

export function writeBoundary(chatMetadata, chatId, processedThrough) {
    if (!chatMetadata) {
        return;
    }
    chatMetadata[METADATA_KEY] = { ...(chatMetadata[METADATA_KEY] || {}), chatId, processedThrough };
}

/**
 * The boundary after an ordinary store of the last `windowSize` messages.
 *
 * Moves only on success, and only when nothing unprocessed sits between the boundary and
 * the window - otherwise catch-up owns that gap and moves it. An unknown boundary starts
 * at the end: a chat from before this existed was stored turn by turn already, and
 * re-reading all of it on the first turn would be expensive and mostly duplicates.
 */
export function boundaryAfterStore({ boundary, chatLength, windowSize, ok }) {
    const last = chatLength - 1;
    if (!ok) {
        return boundary === null ? null : Math.min(boundary, last);
    }
    if (boundary === null) {
        return last;
    }
    const windowStart = Math.max(0, chatLength - windowSize);
    if (Math.min(boundary, last) >= windowStart - 1) {
        return last;
    }
    return boundary;
}

/**
 * The range to catch up, inclusive, or null if there is no gap.
 *
 * Starts right after the boundary and runs to the end of the chat - overlapping the
 * window the store just covered is deliberate: backfill skips what is already stored,
 * and it means one successful catch-up closes the gap completely.
 */
export function planCatchUp({ boundary, chatLength, windowSize, maxMessages = CATCH_UP_MAX_MESSAGES }) {
    if (boundary === null || chatLength <= 0) {
        return null;
    }
    const windowStart = Math.max(0, chatLength - windowSize);
    if (boundary >= windowStart - 1) {
        return null;
    }
    const start = boundary + 1;
    const end = Math.min(chatLength - 1, start + maxMessages - 1);
    return { start, end };
}

/** The boundary after a catch-up of `range`. A failed scene keeps the whole range open. */
export function boundaryAfterCatchUp({ boundary, range, ok }) {
    if (!ok || boundary === null) {
        return boundary;
    }
    return Math.max(boundary, range.end);
}
