import {
    DEFAULT_AUDIT_MAX_RECORDS,
    DEFAULT_AUDIT_PREVIEW_CHARS,
    DEFAULT_MAX_EPISODIC_ITEMS,
    DEFAULT_MAX_PROMPT_CHARS,
    DEFAULT_MAX_PROMPT_MEMORIES,
    DEFAULT_MAX_STABLE_ITEMS,
    DEFAULT_MAX_SUMMARY_ITEMS,
} from './audit.mjs';

export const DEFAULT_CONNECTION_SETTINGS = {
    enabled: false,
    memoryServiceUrl: 'http://localhost:8001',
    apiKey: '',
};

export const DEFAULT_RETRIEVAL_SETTINGS = {
    retrieveLimit: 5,
    recentMessagesCount: 8,
};

export const DEFAULT_PROMPT_BUDGET_SETTINGS = {
    maxPromptMemories: DEFAULT_MAX_PROMPT_MEMORIES,
    maxPromptChars: DEFAULT_MAX_PROMPT_CHARS,
    maxSummaryItems: DEFAULT_MAX_SUMMARY_ITEMS,
    maxStableItems: DEFAULT_MAX_STABLE_ITEMS,
    maxEpisodicItems: DEFAULT_MAX_EPISODIC_ITEMS,
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
    promptBudget: { ...DEFAULT_PROMPT_BUDGET_SETTINGS },
    audit: { ...DEFAULT_AUDIT_SETTINGS },
};

export const DEFAULT_SETTINGS = {
    ...DEFAULT_CONNECTION_SETTINGS,
    ...DEFAULT_RETRIEVAL_SETTINGS,
    ...DEFAULT_PROMPT_BUDGET_SETTINGS,
    ...DEFAULT_AUDIT_SETTINGS,
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
    const promptBudget = {
        ...DEFAULT_PROMPT_BUDGET_SETTINGS,
        ...(rawSettings.promptBudget || {}),
    };
    const audit = {
        ...DEFAULT_AUDIT_SETTINGS,
        ...(rawSettings.audit || {}),
    };

    return {
        ...DEFAULT_SETTINGS,
        ...connection,
        ...retrieval,
        ...promptBudget,
        ...audit,
        enabled: rawSettings.enabled ?? connection.enabled,
        // Legacy flat memoryServiceUrl (pre-`connection.*` schema) is intentionally
        // ignored here even if still present on disk - connection.* is the only
        // source of truth, so a stale flat value can never silently win again.
        memoryServiceUrl: connection.memoryServiceUrl,
        apiKey: rawSettings.apiKey ?? connection.apiKey,
        retrieveLimit: rawSettings.retrieveLimit ?? retrieval.retrieveLimit,
        recentMessagesCount: rawSettings.recentMessagesCount ?? retrieval.recentMessagesCount,
        maxPromptMemories: rawSettings.maxPromptMemories ?? promptBudget.maxPromptMemories,
        maxPromptChars: rawSettings.maxPromptChars ?? promptBudget.maxPromptChars,
        maxSummaryItems: rawSettings.maxSummaryItems ?? promptBudget.maxSummaryItems,
        maxStableItems: rawSettings.maxStableItems ?? promptBudget.maxStableItems,
        maxEpisodicItems: rawSettings.maxEpisodicItems ?? promptBudget.maxEpisodicItems,
        auditEnabled: rawSettings.auditEnabled ?? audit.auditEnabled,
        auditMaxRecords: rawSettings.auditMaxRecords ?? audit.auditMaxRecords,
        auditPreviewChars: rawSettings.auditPreviewChars ?? audit.auditPreviewChars,
        recentAudits: Array.isArray(rawSettings.recentAudits) ? rawSettings.recentAudits : [],
    };
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
            apiKey: settings.apiKey ?? DEFAULT_CONNECTION_SETTINGS.apiKey,
        },
        retrieval: {
            retrieveLimit: settings.retrieveLimit ?? DEFAULT_RETRIEVAL_SETTINGS.retrieveLimit,
            recentMessagesCount: settings.recentMessagesCount ?? DEFAULT_RETRIEVAL_SETTINGS.recentMessagesCount,
        },
        promptBudget: {
            maxPromptMemories: settings.maxPromptMemories ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxPromptMemories,
            maxPromptChars: settings.maxPromptChars ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxPromptChars,
            maxSummaryItems: settings.maxSummaryItems ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxSummaryItems,
            maxStableItems: settings.maxStableItems ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxStableItems,
            maxEpisodicItems: settings.maxEpisodicItems ?? DEFAULT_PROMPT_BUDGET_SETTINGS.maxEpisodicItems,
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
