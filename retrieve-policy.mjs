/**
 * Whether a turn gets memory at all, and which block it gets.
 *
 * Two decisions that used to sit inline in main.mjs, which exports nothing and cannot be
 * imported by a test. Both are load-bearing and neither raises anything when wrong: the
 * first decides whether a turn is served at all and records the reason the audit will
 * later show, the second decides what text is actually injected.
 *
 * Extracted 2026-09-21, closing the remainder of roadmap item 3. The three functions that
 * item originally named - the budget build, the store-response handling and the injection
 * audit rebuild - turned out to be extracted already: main.mjs imports
 * buildBudgetedMemoryBlock, buildPromptInsertionAuditSection and evaluateTrackerToasts
 * and only marshals arguments into them. This is what was actually left.
 */

/**
 * Reasons a turn is not served, in the order they are checked.
 *
 * These strings reach the audit record as `notes`, and they are what a later
 * investigation reads to tell "the extension was off" from "the hook fired before the
 * user's message existed". Renaming one silently breaks that reading, so they are named
 * here rather than written inline at three separate return statements.
 */
export const SKIP_DISABLED = 'extension_disabled';
export const SKIP_NO_CHAT = 'missing_chat_context';
export const SKIP_NO_USER_MESSAGE = 'no_last_user_message';
export const SKIP_NO_MESSAGES = 'no_messages';

/**
 * Decide whether to call /memory/retrieve for this turn.
 *
 * Returns `{ proceed: true }` or `{ proceed: false, reason }`. Order matters and is not
 * cosmetic: a disabled extension must not report a missing chat, because that reads as a
 * fault when it is a setting.
 */
export function shouldRetrieve({ enabled, chatContext, userInput }) {
    if (!enabled) {
        return { proceed: false, reason: SKIP_DISABLED };
    }
    if (!chatContext || !chatContext.chatId) {
        return { proceed: false, reason: SKIP_NO_CHAT };
    }
    if (!userInput) {
        return { proceed: false, reason: SKIP_NO_USER_MESSAGE };
    }
    return { proceed: true };
}

/**
 * Pick the block to inject from what the client budgeted and what the backend sent.
 *
 * The client-side budget wins because it is the one that knows this user's prompt limits;
 * the backend's own `memory_block` is the fallback for a response the budgeter could not
 * use - an older backend, or items in a shape it does not recognise. Falling through to
 * '' rather than undefined matters: setExtensionPrompt stringifies, and "undefined" would
 * be injected into the prompt as that literal word.
 */
export function chooseMemoryBlock({ budgetedBlock, backendBlock }) {
    return budgetedBlock || backendBlock || '';
}

/**
 * Decide whether to call /memory/store for this turn.
 *
 * Shares the first two checks with shouldRetrieve, and that sharing is the point: both
 * paths used to spell `extension_disabled` and `missing_chat_context` inline, so renaming
 * one would have left the audit saying two different things about the same condition
 * depending on which half of the turn hit it.
 *
 * The third check differs on purpose. A retrieve needs the user's message to query with;
 * a store needs a scene, and a scene can be assistant messages alone.
 */
export function shouldStore({ enabled, chatContext, messageCount }) {
    if (!enabled) {
        return { proceed: false, reason: SKIP_DISABLED };
    }
    if (!chatContext || !chatContext.chatId) {
        return { proceed: false, reason: SKIP_NO_CHAT };
    }
    if (!messageCount) {
        return { proceed: false, reason: SKIP_NO_MESSAGES };
    }
    return { proceed: true };
}
