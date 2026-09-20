/**
 * Where memoryst's blocks land in the prompt, and in what shape.
 *
 * This was hardcoded, and one of the hardcoded arguments was wrong. `setExtensionPrompt`
 * ends with `role: Number(role ?? extension_prompt_roles.SYSTEM)` (script.js), and the
 * extension passed the *string* `'system'`. `Number('system')` is `NaN`, so the stored
 * role was NaN rather than the enum's 0. It happened to be harmless: at `IN_PROMPT` the
 * role is only read through `getPromptRole`, whose `default:` returns `'system'` anyway.
 * The moment anyone moved the block to `IN_CHAT` it would have become a real bug, and
 * moving the block is exactly what the settings below now allow.
 *
 * So every value here is a number, and `resolveInjectionSettings` is what guarantees it.
 *
 * The enum values are duplicated from SillyTavern rather than imported, because this
 * module has to stay loadable without SillyTavern - that is what makes it testable. They
 * are part of ST's public API and have been stable for years; main.mjs additionally
 * compares them against the real enums at startup and warns if ST ever moves them.
 */

// script.js: extension_prompt_types
export const PROMPT_POSITION_IN_PROMPT = 0;
export const PROMPT_POSITION_IN_CHAT = 1;
export const PROMPT_POSITION_BEFORE_PROMPT = 2;

// script.js: extension_prompt_roles
export const PROMPT_ROLE_SYSTEM = 0;
export const PROMPT_ROLE_USER = 1;
export const PROMPT_ROLE_ASSISTANT = 2;

// script.js: MAX_INJECTION_DEPTH
export const MAX_INJECTION_DEPTH = 10000;

export const PROMPT_POSITION_OPTIONS = [
    { value: PROMPT_POSITION_IN_PROMPT, label: 'In prompt — after the character card' },
    { value: PROMPT_POSITION_IN_CHAT, label: 'In chat — at the depth below' },
    { value: PROMPT_POSITION_BEFORE_PROMPT, label: 'Before prompt — above everything' },
];

export const PROMPT_ROLE_OPTIONS = [
    { value: PROMPT_ROLE_SYSTEM, label: 'System' },
    { value: PROMPT_ROLE_USER, label: 'User' },
    { value: PROMPT_ROLE_ASSISTANT, label: 'Assistant' },
];

// The defaults are what the extension did when these were hardcoded, so upgrading
// changes nothing until someone deliberately moves the block. IN_PROMPT is also what
// SillyTavern's own Summarize, Vector Storage and qvink_memory default to.
export const DEFAULT_PROMPT_POSITION = PROMPT_POSITION_IN_PROMPT;
export const DEFAULT_PROMPT_DEPTH = 2;
export const DEFAULT_PROMPT_ROLE = PROMPT_ROLE_SYSTEM;
// Scanning has always been on here, and it is the one place memoryst differs from the
// others on purpose: an injected memory can trigger a lorebook entry.
export const DEFAULT_PROMPT_SCAN = true;

const VALID_POSITIONS = new Set(PROMPT_POSITION_OPTIONS.map(option => option.value));
const VALID_ROLES = new Set(PROMPT_ROLE_OPTIONS.map(option => option.value));

function toNumber(value, fallback, valid) {
    const parsed = Number(value);
    return Number.isInteger(parsed) && valid.has(parsed) ? parsed : fallback;
}

export function resolveInjectionDepth(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
        return DEFAULT_PROMPT_DEPTH;
    }
    // ST clamps at MAX_INJECTION_DEPTH; a negative depth is not a position it understands.
    return Math.min(MAX_INJECTION_DEPTH, Math.max(0, Math.round(parsed)));
}

/**
 * The four arguments `setExtensionPrompt` takes after the key and the value.
 *
 * Always returns numbers for position/depth/role and a boolean for scan, whatever shape
 * the stored settings are in - a settings.json hand-edited to `"promptRole": "system"`
 * has to come back as 0, not as the NaN that started all this.
 */
export function resolveInjectionSettings(settings = {}) {
    return {
        position: toNumber(settings.promptPosition, DEFAULT_PROMPT_POSITION, VALID_POSITIONS),
        depth: resolveInjectionDepth(settings.promptDepth),
        scan: settings.promptScan ?? DEFAULT_PROMPT_SCAN,
        role: toNumber(settings.promptRole, DEFAULT_PROMPT_ROLE, VALID_ROLES),
    };
}

/**
 * Compare our copies of SillyTavern's enums against the real ones.
 *
 * Returns the mismatches, so main.mjs can warn rather than silently inject at a position
 * that means something else now. Takes the enums as plain objects and never imports
 * them, so the check itself stays testable.
 */
export function findEnumDrift(promptTypes = null, promptRoles = null) {
    const expected = [
        ['extension_prompt_types.IN_PROMPT', promptTypes?.IN_PROMPT, PROMPT_POSITION_IN_PROMPT],
        ['extension_prompt_types.IN_CHAT', promptTypes?.IN_CHAT, PROMPT_POSITION_IN_CHAT],
        ['extension_prompt_types.BEFORE_PROMPT', promptTypes?.BEFORE_PROMPT, PROMPT_POSITION_BEFORE_PROMPT],
        ['extension_prompt_roles.SYSTEM', promptRoles?.SYSTEM, PROMPT_ROLE_SYSTEM],
        ['extension_prompt_roles.USER', promptRoles?.USER, PROMPT_ROLE_USER],
        ['extension_prompt_roles.ASSISTANT', promptRoles?.ASSISTANT, PROMPT_ROLE_ASSISTANT],
    ];

    return expected
        // An absent enum means this SillyTavern does not export it, which is not drift -
        // there is simply nothing to compare against, and warning would be noise.
        .filter(([, actual]) => actual !== undefined && actual !== null)
        .filter(([, actual, ours]) => actual !== ours)
        .map(([name, actual, ours]) => `${name}: SillyTavern says ${actual}, memoryst assumes ${ours}`);
}
