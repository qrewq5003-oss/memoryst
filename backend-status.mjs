/**
 * Whether the backend is answering, and when that is worth telling the user.
 *
 * Why this exists: on 2026-10-02 a whole evening of play went by with memoryst down -
 * every one of 20 turns failed retrieve and store with `Failed to fetch`, and nothing
 * stored. The extension knew on every turn and said so only to the browser console,
 * which nobody reads while playing. The backend had not come back after a phone reboot,
 * which on this device is the ordinary way for it to be down.
 *
 * So the rule is: say it once when the backend goes away, and once when it comes back -
 * never on every turn. The recovery notice also counts the turns that were not stored,
 * because those are recoverable (Backfill with nothing selected re-reads the open chat,
 * and already stored facts come back as duplicates) only if someone knows they are missing.
 *
 * Kept free of SillyTavern so it can be tested; main.mjs only feeds it outcomes and
 * shows what it returns.
 */

/**
 * What one request's ending says about the backend.
 *
 *   'ok'          - it answered (2xx)
 *   'rejected'    - it answered with a 4xx: alive, and the problem is the request
 *   'down'        - no answer at all, or a 5xx
 *   'timeout'     - our own deadline fired. Not counted as down: the backend may be
 *                   busy inside an extraction call and will very likely finish the
 *                   store anyway (see http.mjs). A frozen backend still shows up, as
 *                   the handshake and every retrieve failing on their own
 *
 * @param {object} params
 * @param {number} [params.httpStatus] - set when a response arrived
 * @param {unknown} [params.error] - set when the request threw
 */
export function classifyOutcome({ httpStatus, error } = {}) {
    if (error) {
        return error.name === 'MemoryServiceTimeout' ? 'timeout' : 'down';
    }
    if (typeof httpStatus !== 'number') {
        return 'down';
    }
    if (httpStatus >= 500) {
        return 'down';
    }
    if (httpStatus >= 400) {
        return 'rejected';
    }
    return 'ok';
}

export const BACKEND_DOWN_MESSAGE =
    'memoryst не отвечает — ходы не попадают в память. Проверьте, запущен ли сервер.';

export function backendBackMessage(missedStores) {
    if (!missedStores) {
        return 'memoryst снова на связи.';
    }
    return `memoryst снова на связи. Не сохранено ходов: ${missedStores} — `
        + 'их допишет кнопка Backfill Current Chat в панели memoryst.';
}

/**
 * @returns {{
 *   record: (kind: 'retrieve'|'store'|'handshake', outcome: string) =>
 *       null | { level: 'error'|'success', message: string },
 *   snapshot: () => { state: 'unknown'|'up'|'down', missedStores: number },
 * }}
 */
export function createBackendStatus() {
    // 'unknown' until the first answer: a page loaded while the backend is down must
    // still warn, and a page loaded while it is up must not announce a recovery.
    let state = 'unknown';
    let missedStores = 0;

    function record(kind, outcome) {
        if (outcome === 'timeout') {
            return null;
        }

        if (outcome === 'down') {
            if (kind === 'store') {
                missedStores += 1;
            }
            if (state === 'down') {
                return null;
            }
            state = 'down';
            return { level: 'error', message: BACKEND_DOWN_MESSAGE };
        }

        // 'ok' or 'rejected': either way something answered.
        const wasDown = state === 'down';
        state = 'up';
        if (!wasDown) {
            return null;
        }
        const missed = missedStores;
        missedStores = 0;
        return { level: 'success', message: backendBackMessage(missed) };
    }

    return {
        record,
        snapshot: () => ({ state, missedStores }),
    };
}
