import {
    DEFAULT_AUDIT_MAX_RECORDS,
    DEFAULT_AUDIT_PREVIEW_CHARS,
    DEFAULT_MAX_EPISODIC_ITEMS,
    DEFAULT_MAX_PROMPT_CHARS,
    DEFAULT_MAX_PROMPT_MEMORIES,
    DEFAULT_MAX_STABLE_ITEMS,
    DEFAULT_MAX_SUMMARY_ITEMS,
} from './audit.mjs?v=179df03';
import {
    DEFAULT_MAX_TRACKER_CHARS,
    DEFAULT_TRACKER_REMINDER_THRESHOLD,
} from './trackers.mjs?v=179df03';
import {
    DEFAULT_RETRIEVE_TIMEOUT_MS,
    DEFAULT_STORE_TIMEOUT_MS,
} from './http.mjs?v=179df03';
import {
    DEFAULT_PROMPT_DEPTH,
    DEFAULT_PROMPT_POSITION,
    DEFAULT_PROMPT_ROLE,
    DEFAULT_PROMPT_SCAN,
} from './injection.mjs?v=179df03';

// retrieveTimeoutMs and storeTimeoutMs are knobs rather than constants because they are
// the two that trade real things off against each other, and the right answer depends on
// the machine. retrieve blocks generation, so its budget is "how long is the user willing
// to stare at a chat that has not replied"; store waits on the backend's extraction LLM,
// so its budget has to clear app/config.py's SCENE_LLM_TIMEOUT or it aborts work that was
// about to succeed. Everything else the extension fetches is fire-and-forget and keeps a
// fixed default in http.mjs.
export const DEFAULT_CONNECTION_SETTINGS = {
    enabled: false,
    memoryServiceUrl: 'http://localhost:8001',
    apiKey: '',
    retrieveTimeoutMs: DEFAULT_RETRIEVE_TIMEOUT_MS,
    storeTimeoutMs: DEFAULT_STORE_TIMEOUT_MS,
};

export const DEFAULT_RETRIEVAL_SETTINGS = {
    retrieveLimit: 5,
    recentMessagesCount: 8,
};

// Defaults to a non-reasoning model, independent of whatever the "LLM Provider"
// panel's active model is (that one is shared with consolidation/manual tools,
// which may want a stronger reasoning model). Reasoning models (e.g. zai-org/glm-4.7,
// which the backend used to default to) were found to spend their token budget
// on hidden reasoning before ever emitting the scene-extraction JSON, producing
// empty/failed calls that silently fell back to the cruder regex extractor - see
// CLAUDE.md's scene-extraction-llm-failing investigation. An empty string here
// means "use whatever model the active LLM Provider is configured with" instead.
// deepseek/deepseek-v4-pro was verified live against the backend's NanoGPT
// catalog (3/3 clean scene-extraction calls, reasoning_tokens=0 in the API
// response, no reasoning_content field) before being set as the default - see
// CLAUDE.md's scene-extraction-llm-failing investigation for the verification.
export const DEFAULT_EXTRACTION_SETTINGS = {
    sceneExtractionModel: 'deepseek/deepseek-v4-pro',
};

// One set of placement knobs for all three blocks memoryst injects (memory, lore anchor,
// tracker) rather than four knobs each. Twelve controls would be a worse panel for a
// distinction nobody has asked for: they are one extension's contribution to the prompt
// and there is no reason for them to sit in different places.
export const DEFAULT_INJECTION_SETTINGS = {
    promptPosition: DEFAULT_PROMPT_POSITION,
    promptDepth: DEFAULT_PROMPT_DEPTH,
    promptRole: DEFAULT_PROMPT_ROLE,
    promptScan: DEFAULT_PROMPT_SCAN,
};

export const DEFAULT_PROMPT_BUDGET_SETTINGS = {
    maxPromptMemories: DEFAULT_MAX_PROMPT_MEMORIES,
    maxPromptChars: DEFAULT_MAX_PROMPT_CHARS,
    maxSummaryItems: DEFAULT_MAX_SUMMARY_ITEMS,
    maxStableItems: DEFAULT_MAX_STABLE_ITEMS,
    maxEpisodicItems: DEFAULT_MAX_EPISODIC_ITEMS,
};

// maxTrackerChars is a single budget shared by all four of a character's trackers, not
// one budget each - four independent caps would let a fully-populated chat triple the
// injected prompt without any single tracker looking oversized. There is deliberately no
// "max tracker items": the set of tracker types is fixed at four, so only volume matters.
// lastTrackerToastAt is runtime state, not a knob: it remembers the counter value at
// which each tracker last nagged, so a reload doesn't restart the nagging.
export const DEFAULT_TRACKER_SETTINGS = {
    trackerInjectionEnabled: true,
    // The main path for a solo chat: the tracker of the character you are talking to goes in
    // every turn, instead of waiting for a lorebook entry about them to activate. The lorebook
    // route still runs and is still what pulls in a secondary character (by @memory-tracker
    // marker or by name) - it is simply no longer the only way the main character gets in.
    trackerAlwaysInjectCurrentCharacter: true,
    maxTrackerChars: DEFAULT_MAX_TRACKER_CHARS,
    trackerReminderThreshold: DEFAULT_TRACKER_REMINDER_THRESHOLD,
};

export const DEFAULT_AUDIT_SETTINGS = {
    auditEnabled: false,
    auditMaxRecords: DEFAULT_AUDIT_MAX_RECORDS,
    auditPreviewChars: DEFAULT_AUDIT_PREVIEW_CHARS,
};

export const LONG_CHAT_RECOMMENDED_BASELINE = {
    ...DEFAULT_RETRIEVAL_SETTINGS,
    ...DEFAULT_PROMPT_BUDGET_SETTINGS,
};

export const DEFAULT_SETTINGS_GROUPS = {
    connection: { ...DEFAULT_CONNECTION_SETTINGS },
    retrieval: { ...DEFAULT_RETRIEVAL_SETTINGS },
    extraction: { ...DEFAULT_EXTRACTION_SETTINGS },
    injection: { ...DEFAULT_INJECTION_SETTINGS },
    promptBudget: { ...DEFAULT_PROMPT_BUDGET_SETTINGS },
    trackers: { ...DEFAULT_TRACKER_SETTINGS },
    audit: { ...DEFAULT_AUDIT_SETTINGS },
};

export const DEFAULT_SETTINGS = {
    ...DEFAULT_CONNECTION_SETTINGS,
    ...DEFAULT_RETRIEVAL_SETTINGS,
    ...DEFAULT_EXTRACTION_SETTINGS,
    ...DEFAULT_INJECTION_SETTINGS,
    ...DEFAULT_PROMPT_BUDGET_SETTINGS,
    ...DEFAULT_TRACKER_SETTINGS,
    ...DEFAULT_AUDIT_SETTINGS,
    lastTrackerToastAt: {},
    recentAudits: [],
};

export function normalizeExtensionSettings(rawSettings = {}) {
    const connection = {
        ...DEFAULT_CONNECTION_SETTINGS,
        ...(rawSettings.connection || {}),
        memoryServiceUrl: (rawSettings.connection || {}).memoryServiceUrl || DEFAULT_CONNECTION_SETTINGS.memoryServiceUrl,
    };
    const retrieval = {
        ...DEFAULT_RETRIEVAL_SETTINGS,
        ...(rawSettings.retrieval || {}),
    };
    const extraction = {
        ...DEFAULT_EXTRACTION_SETTINGS,
        ...(rawSettings.extraction || {}),
    };
    const injection = {
        ...DEFAULT_INJECTION_SETTINGS,
        ...(rawSettings.injection || {}),
    };
    const promptBudget = {
        ...DEFAULT_PROMPT_BUDGET_SETTINGS,
        ...(rawSettings.promptBudget || {}),
    };
    const trackers = {
        ...DEFAULT_TRACKER_SETTINGS,
        ...(rawSettings.trackers || {}),
    };
    const audit = {
        ...DEFAULT_AUDIT_SETTINGS,
        ...(rawSettings.audit || {}),
    };

    return {
        ...DEFAULT_SETTINGS,
        ...connection,
        ...retrieval,
        ...extraction,
        ...injection,
        ...promptBudget,
        ...trackers,
        ...audit,
        enabled: rawSettings.enabled ?? connection.enabled,
        // Legacy flat memoryServiceUrl (pre-`connection.*` schema) is intentionally
        // ignored here even if still present on disk - connection.* is the only
        // source of truth, so a stale flat value can never silently win again.
        memoryServiceUrl: connection.memoryServiceUrl,
        // `||`, not `??`, for the text fields below. `??` only falls through on
        // null/undefined, so a legacy flat key left on disk as an empty string counted
        // as "configured" and beat the nested value - an empty apiKey or model name
        // silently winning over a real one. memoryServiceUrl was already guarded this
        // way; its siblings were not, which made the same stale-settings.json that has
        // bitten before behave differently field by field.
        apiKey: rawSettings.apiKey || connection.apiKey,
        retrieveTimeoutMs: rawSettings.retrieveTimeoutMs ?? connection.retrieveTimeoutMs,
        storeTimeoutMs: rawSettings.storeTimeoutMs ?? connection.storeTimeoutMs,
        retrieveLimit: rawSettings.retrieveLimit ?? retrieval.retrieveLimit,
        recentMessagesCount: rawSettings.recentMessagesCount ?? retrieval.recentMessagesCount,
        sceneExtractionModel: rawSettings.sceneExtractionModel || extraction.sceneExtractionModel,
        promptPosition: rawSettings.promptPosition ?? injection.promptPosition,
        promptDepth: rawSettings.promptDepth ?? injection.promptDepth,
        promptRole: rawSettings.promptRole ?? injection.promptRole,
        promptScan: rawSettings.promptScan ?? injection.promptScan,
        maxPromptMemories: rawSettings.maxPromptMemories ?? promptBudget.maxPromptMemories,
        maxPromptChars: rawSettings.maxPromptChars ?? promptBudget.maxPromptChars,
        maxSummaryItems: rawSettings.maxSummaryItems ?? promptBudget.maxSummaryItems,
        maxStableItems: rawSettings.maxStableItems ?? promptBudget.maxStableItems,
        maxEpisodicItems: rawSettings.maxEpisodicItems ?? promptBudget.maxEpisodicItems,
        trackerInjectionEnabled: rawSettings.trackerInjectionEnabled ?? trackers.trackerInjectionEnabled,
        trackerAlwaysInjectCurrentCharacter:
            rawSettings.trackerAlwaysInjectCurrentCharacter ?? trackers.trackerAlwaysInjectCurrentCharacter,
        maxTrackerChars: rawSettings.maxTrackerChars ?? trackers.maxTrackerChars,
        trackerReminderThreshold: rawSettings.trackerReminderThreshold ?? trackers.trackerReminderThreshold,
        lastTrackerToastAt: isPlainObject(rawSettings.lastTrackerToastAt)
            ? { ...rawSettings.lastTrackerToastAt }
            : { ...(isPlainObject(trackers.lastTrackerToastAt) ? trackers.lastTrackerToastAt : {}) },
        auditEnabled: rawSettings.auditEnabled ?? audit.auditEnabled,
        auditMaxRecords: rawSettings.auditMaxRecords ?? audit.auditMaxRecords,
        auditPreviewChars: rawSettings.auditPreviewChars ?? audit.auditPreviewChars,
        recentAudits: Array.isArray(rawSettings.recentAudits) ? rawSettings.recentAudits : [],
    };
}

function isPlainObject(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

// Builds the on-disk shape directly from the flat runtime `settings` object
// rather than round-tripping through normalizeExtensionSettings(): that second
// normalize pass would see no `.connection` sub-object on a flat runtime
// settings value and fall back to defaults, silently discarding whatever the
// user just set. Reading the flat fields directly here also guarantees the
// legacy top-level memoryServiceUrl (and siblings) never gets written back
// out - each save fully replaces extension_settings['memory-service'], so a
// stale flat key from an old settings.json can't survive past the next save.
export function serializeExtensionSettings(settings = DEFAULT_SETTINGS) {
    return {
        connection: {
            enabled: settings.enabled ?? DEFAULT_CONNECTION_SETTINGS.enabled,
            memoryServiceUrl: settings.memoryServiceUrl || DEFAULT_CONNECTION_SETTINGS.memoryServiceUrl,
            apiKey: settings.apiKey || DEFAULT_CONNECTION_SETTINGS.apiKey,
            retrieveTimeoutMs: settings.retrieveTimeoutMs ?? DEFAULT_CONNECTION_SETTINGS.retrieveTimeoutMs,
            storeTimeoutMs: settings.storeTimeoutMs ?? DEFAULT_CONNECTION_SETTINGS.storeTimeoutMs,
        },
        retrieval: {
            retrieveLimit: settings.retrieveLimit ?? DEFAULT_RETRIEVAL_SETTINGS.retrieveLimit,
            recentMessagesCount: settings.recentMessagesCount ?? DEFAULT_RETRIEVAL_SETTINGS.recentMessagesCount,
        },
        extraction: {
            sceneExtractionModel: settings.sceneExtractionModel || DEFAULT_EXTRACTION_SETTINGS.sceneExtractionModel,
        },
        injection: {
            promptPosition: settings.promptPosition ?? DEFAULT_INJECTION_SETTINGS.promptPosition,
            promptDepth: settings.promptDepth ?? DEFAULT_INJECTION_SETTINGS.promptDepth,
            promptRole: settings.promptRole ?? DEFAULT_INJECTION_SETTINGS.promptRole,
            promptScan: settings.promptScan ?? DEFAULT_INJECTION_SETTINGS.promptScan,
        },
        promptBudget: {
            maxPromptMemories: settings.maxPromptMemories ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxPromptMemories,
            maxPromptChars: settings.maxPromptChars ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxPromptChars,
            maxSummaryItems: settings.maxSummaryItems ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxSummaryItems,
            maxStableItems: settings.maxStableItems ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxStableItems,
            maxEpisodicItems: settings.maxEpisodicItems ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxEpisodicItems,
        },
        trackers: {
            trackerInjectionEnabled: settings.trackerInjectionEnabled ?? DEFAULT_TRACKER_SETTINGS.trackerInjectionEnabled,
            trackerAlwaysInjectCurrentCharacter:
                settings.trackerAlwaysInjectCurrentCharacter
                ?? DEFAULT_TRACKER_SETTINGS.trackerAlwaysInjectCurrentCharacter,
            maxTrackerChars: settings.maxTrackerChars ?? DEFAULT_TRACKER_SETTINGS.maxTrackerChars,
            trackerReminderThreshold: settings.trackerReminderThreshold ?? DEFAULT_TRACKER_SETTINGS.trackerReminderThreshold,
            lastTrackerToastAt: isPlainObject(settings.lastTrackerToastAt) ? settings.lastTrackerToastAt : {},
        },
        audit: {
            auditEnabled: settings.auditEnabled ?? DEFAULT_AUDIT_SETTINGS.auditEnabled,
            auditMaxRecords: settings.auditMaxRecords ?? DEFAULT_AUDIT_SETTINGS.auditMaxRecords,
            auditPreviewChars: settings.auditPreviewChars ?? DEFAULT_AUDIT_SETTINGS.auditPreviewChars,
        },
        recentAudits: Array.isArray(settings.recentAudits) ? settings.recentAudits : [],
    };
}

export function applyRecommendedBaselineSettings(settings = DEFAULT_SETTINGS) {
    // Callers always pass the already-normalized flat runtime settings object
    // (it has no `.connection` sub-object), so re-normalizing here would read
    // `rawSettings.connection` as empty and silently reset connection.* to
    // defaults - discarding the user's configured memoryServiceUrl/apiKey.
    // Just layer the baseline retrieval/promptBudget overrides on top instead.
    return {
        ...settings,
        ...LONG_CHAT_RECOMMENDED_BASELINE,
    };
}
