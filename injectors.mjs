/**
 * Who else is filling this prompt.
 *
 * memoryst is never the only thing writing into a SillyTavern prompt. On this machine it
 * shares it with Vector Storage (`3_vectors`, `4_vectors_data_bank`), the built-in
 * Summarize (`1_memory`), CharMemory and STMemoryBooks' lorebook - four systems answering
 * "what happened earlier", none of them aware of the others.
 *
 * Working out the split used to mean parsing CharMemory's own accounting out of the first
 * line of a chat's `.jsonl` by hand. That was done twice, seven weeks apart, and the two
 * measurements disagreed completely - by the second one memoryst had been switched off
 * for five weeks and nobody had noticed, because nothing reported it.
 *
 * So the audit record now carries the whole split, every turn. It is the same argument as
 * the rest of memoryst's audit: a number nobody has to go and dig for is a number that
 * gets looked at.
 *
 * Reading only. Nothing here touches another extension's prompt - the point is to see the
 * competition, not to referee it.
 */

// Ours, so they are reported separately rather than counted as competition.
export const MEMORYST_PROMPT_KEYS = [
    'memory-service',
    'memory-service-lore-anchor',
    'memory-service-tracker',
];

// Keys worth naming in a report, with what they are. Anything else is still counted and
// listed - an unknown injector is exactly the thing this is for.
export const KNOWN_INJECTORS = {
    '1_memory': 'SillyTavern Summarize',
    '3_vectors': 'Vector Storage (chat)',
    '4_vectors_data_bank': 'Vector Storage (Data Bank)',
    'qvink_memory_long': 'qvink_memory (long term)',
    'qvink_memory_short': 'qvink_memory (short term)',
    'DEPTH_PROMPT': "character card's depth prompt",
    'PERSONA_DESCRIPTION': 'persona description',
};

export const DEFAULT_MAX_INJECTOR_ENTRIES = 12;

function promptLength(prompt) {
    if (typeof prompt === 'string') {
        return prompt.length;
    }
    return typeof prompt?.value === 'string' ? prompt.value.length : 0;
}

/**
 * Break SillyTavern's `extension_prompts` into ours, theirs, and the total.
 *
 * Empty prompts are skipped rather than listed: every extension that has ever injected
 * anything leaves its key behind with an empty value, so counting those would report a
 * dozen live competitors in a prompt none of them contributed to.
 */
export function summarizeForeignInjectors(extensionPrompts = {}, {
    ourKeys = MEMORYST_PROMPT_KEYS,
    maxEntries = DEFAULT_MAX_INJECTOR_ENTRIES,
} = {}) {
    const ours = new Set(ourKeys);
    const entries = [];
    let ownChars = 0;
    let foreignChars = 0;

    for (const [key, prompt] of Object.entries(extensionPrompts || {})) {
        const chars = promptLength(prompt);
        if (chars <= 0) {
            continue;
        }

        if (ours.has(key)) {
            ownChars += chars;
            continue;
        }

        foreignChars += chars;
        entries.push({
            key,
            label: KNOWN_INJECTORS[key] || null,
            chars,
            // Recorded because two blocks of the same size land very differently: one at
            // the top of the context and one at chat depth are not the same prompt.
            position: typeof prompt?.position === 'number' ? prompt.position : null,
            depth: typeof prompt?.depth === 'number' ? prompt.depth : null,
        });
    }

    entries.sort((left, right) => right.chars - left.chars);

    const total = ownChars + foreignChars;
    return {
        own_chars: ownChars,
        foreign_chars: foreignChars,
        total_chars: total,
        // The number the whole exercise is about: how much of the memory volume in this
        // prompt is actually memoryst's. It was measured at under 2% in August.
        own_share: total > 0 ? Number((ownChars / total).toFixed(4)) : 0,
        foreign_count: entries.length,
        // Capped so one pathological turn cannot bloat every audit record. The counts
        // above are computed over all of them, not just the ones listed.
        foreign: entries.slice(0, maxEntries),
        truncated: Math.max(0, entries.length - maxEntries),
    };
}

/**
 * How much World Info put in, from the entries the activation handler already receives.
 *
 * Lorebook text does not pass through `extension_prompts` at all, so without this the
 * largest constant contributor on some chats would be missing from the split - 2113
 * characters every single turn on the chat this was measured on.
 */
export function summarizeWorldInfo(entries = []) {
    const list = Array.isArray(entries) ? entries : [];
    let chars = 0;
    for (const entry of list) {
        const content = entry?.content ?? entry?.entry ?? entry?.text ?? '';
        chars += String(content).length;
    }
    return { entry_count: list.length, chars };
}
