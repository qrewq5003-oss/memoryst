/**
 * Reconnaissance only: what the World Info scan is handed, and who got there first.
 *
 * Suppressing a lorebook entry from memoryst is feasible - `WORLDINFO_ENTRIES_LOADED`
 * fires before selection, hands over the live arrays, and core honours `entry.disable`
 * (`world-info.js:4801`). `WORLD_INFO_ACTIVATED`, which this extension already listens
 * to, is too late: the prompt string is assembled a line before it is emitted.
 *
 * But two other extensions installed here subscribe to the same event, and one of them -
 * SillyTavern-LorebookOrdering - already sets `disable` on entries that miss its token
 * budget. Handler order follows subscription order, which follows extension load order,
 * which is written down nowhere. Mutating these arrays blind would be a silent race.
 *
 * So this module only reads. Nothing here writes to an entry, and a test asserts it: the
 * point of the pass is to learn the real order and cost before touching anything.
 */

/** Memory Books stamps its own entries, so they are identifiable without guessing. */
function isMemoryBooksEntry(entry) {
    return Boolean(entry && (entry.stmemorybooks || entry.STMB_chatId));
}

function entryChars(entry) {
    return String(entry?.content ?? entry?.entry ?? entry?.text ?? '').length;
}

function summarizeBucket(entries) {
    const list = Array.isArray(entries) ? entries : [];
    let chars = 0;
    let disabled = 0;
    let memoryBooks = 0;
    let memoryBooksChars = 0;
    for (const entry of list) {
        chars += entryChars(entry);
        // `== true` on purpose: core compares the same way, and an entry carrying the
        // string "true" or 1 would otherwise be counted as live here and skipped there.
        if (entry?.disable == true) {
            disabled += 1;
        }
        if (isMemoryBooksEntry(entry)) {
            memoryBooks += 1;
            memoryBooksChars += entryChars(entry);
        }
    }
    return { count: list.length, chars, disabled, memory_books: memoryBooks, memory_books_chars: memoryBooksChars };
}

/**
 * Summarise one `WORLDINFO_ENTRIES_LOADED` payload.
 *
 * `already_disabled` is the number that matters: anything above zero means another
 * handler ran before this one and suppressed something, which answers the load-order
 * question that a code read cannot.
 */
export function summarizeEntriesLoaded(payload = {}) {
    const buckets = {
        global: summarizeBucket(payload.globalLore),
        character: summarizeBucket(payload.characterLore),
        chat: summarizeBucket(payload.chatLore),
        persona: summarizeBucket(payload.personaLore),
    };
    const total = { count: 0, chars: 0, disabled: 0, memory_books: 0, memory_books_chars: 0 };
    for (const bucket of Object.values(buckets)) {
        for (const key of Object.keys(total)) {
            total[key] += bucket[key];
        }
    }
    return {
        buckets,
        total_count: total.count,
        total_chars: total.chars,
        already_disabled: total.disabled,
        memory_books_count: total.memory_books,
        memory_books_chars: total.memory_books_chars,
        // What memoryst would be taking over if the second path is ever built: the
        // Memory Books digests for this chat, which are keyword-triggered today.
        suppressible_chars: total.memory_books_chars,
    };
}
