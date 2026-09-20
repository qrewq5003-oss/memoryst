/**
 * Every request this extension makes, with a deadline on it.
 *
 * Why this exists: `retrieve` is issued from MESSAGE_SENT, and SillyTavern's
 * `eventSource.emit()` awaits its listeners - Generate() does not assemble the prompt
 * until this handler returns. A bare `fetch()` has no timeout at all, so a backend that
 * accepts the TCP connection and then never answers (a wedged uvicorn, a SQLite write
 * lock, Termux frozen by Android's doze) stops the generation forever, with no error
 * anywhere the user can see it. The chat simply never replies.
 *
 * That failure mode is the whole reason for this module: memoryst being unavailable must
 * degrade to "this turn gets no injected memory", never to "SillyTavern is broken".
 *
 * Sizing the deadlines is not one number. A request that blocks generation and a request
 * that waits on an extraction LLM want opposite values, so the two hot-path budgets are
 * settings (see settings.mjs) and the fire-and-forget ones are the constants below.
 */

// Blocks generation - the user is watching a chat that has not replied yet. Short.
export const DEFAULT_RETRIEVE_TIMEOUT_MS = 8000;

// Runs after the reply is already rendered, so nothing is blocked on it, and the backend
// spends most of it inside the scene-extraction LLM: app/config.py sizes that call at
// SCENE_LLM_TIMEOUT=90s. A client deadline under that would abort calls that were about
// to succeed and report an error for a store that the server went on to complete anyway.
export const DEFAULT_STORE_TIMEOUT_MS = 120000;

// Handshake, tracker cache warm, audit mirror. All fire-and-forget: failing one costs a
// banner, a tracker block, or an audit row - never the turn.
export const DEFAULT_VERSION_TIMEOUT_MS = 5000;
export const DEFAULT_TRACKERS_TIMEOUT_MS = 10000;
export const DEFAULT_AUDIT_TIMEOUT_MS = 5000;

// Operator actions from the settings panel. Backfill walks a whole chat file through
// extraction server-side, so it is the one request here that is legitimately slow.
export const DEFAULT_MODELS_TIMEOUT_MS = 15000;
export const DEFAULT_BACKFILL_TIMEOUT_MS = 600000;
export const DEFAULT_DELETE_CHAT_TIMEOUT_MS = 30000;

export const MIN_TIMEOUT_MS = 500;
export const MAX_TIMEOUT_MS = 600000;

/**
 * Clamp a configured timeout into something a request can actually run under.
 *
 * A user who types 0 into the box means "no limit" to themselves and "abort instantly"
 * to AbortController, which is the one reading that must not win. Anything unparseable
 * falls back to the caller's default rather than to zero.
 */
export function resolveTimeoutMs(value, fallback) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed <= 0) {
        return fallback;
    }
    return Math.min(MAX_TIMEOUT_MS, Math.max(MIN_TIMEOUT_MS, Math.round(parsed)));
}

export function isTimeoutError(error) {
    return Boolean(error && error.name === 'MemoryServiceTimeout');
}

function buildTimeoutError(timeoutMs) {
    // Message shape matches the extension's other transport errors (`store_http_500`,
    // `trackers_http_404`) so the audit record reads the same way whichever one fired.
    const error = new Error(`fetch_timeout_${timeoutMs}ms`);
    error.name = 'MemoryServiceTimeout';
    error.timeoutMs = timeoutMs;
    return error;
}

/**
 * `fetch` with a deadline.
 *
 * Returns whatever fetch returns. Throws the underlying error on a network failure, and a
 * `MemoryServiceTimeout` error once `timeoutMs` elapses - callers already treat any throw
 * from here as "no answer this turn", so a timeout needs no special handling to be safe,
 * only a distinguishable message for the audit.
 *
 * @param {string} url
 * @param {object} [options] - passed through to fetch
 * @param {object} [deps]
 * @param {number} [deps.timeoutMs]
 * @param {Function} [deps.fetchImpl] - injected in tests; defaults to global fetch
 * @param {Function} [deps.AbortControllerImpl] - injected in tests
 */
export async function fetchWithTimeout(url, options = {}, {
    timeoutMs = DEFAULT_RETRIEVE_TIMEOUT_MS,
    fetchImpl = typeof fetch !== 'undefined' ? fetch : undefined,
    AbortControllerImpl = typeof AbortController !== 'undefined' ? AbortController : undefined,
} = {}) {
    if (typeof fetchImpl !== 'function') {
        throw new Error('fetch_unavailable');
    }

    // No AbortController (an ancient webview, a stripped test env) means no deadline is
    // possible. Making the request anyway is strictly better than refusing to talk to the
    // backend at all - it is exactly the behaviour this extension had before.
    if (typeof AbortControllerImpl !== 'function') {
        return fetchImpl(url, options);
    }

    const controller = new AbortControllerImpl();
    let timedOut = false;
    const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeoutMs);

    try {
        return await fetchImpl(url, { ...options, signal: controller.signal });
    } catch (error) {
        // The abort surfaces as a DOMException whose message varies by browser, so the
        // flag decides, not the message.
        throw timedOut ? buildTimeoutError(timeoutMs) : error;
    } finally {
        // Without this a slow-but-successful request leaves a pending timer that fires an
        // abort at a controller nobody is listening to any more - harmless, but it also
        // keeps a timer alive per turn, which on a long chat is not nothing.
        clearTimeout(timer);
    }
}
