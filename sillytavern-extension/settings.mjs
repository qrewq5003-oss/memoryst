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
    memoryServiceUrl: 'http://localhost:8000',
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
        memoryServiceUrl: rawSettings.memoryServiceUrl ?? connection.memoryServiceUrl,
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

export function serializeExtensionSettings(settings = DEFAULT_SETTINGS) {
    const normalized = normalizeExtensionSettings(settings);
    return {
        connection: {
            enabled: normalized.enabled,
            memoryServiceUrl: normalized.memoryServiceUrl,
            apiKey: normalized.apiKey,
        },
        retrieval: {
            retrieveLimit: normalized.retrieveLimit,
            recentMessagesCount: normalized.recentMessagesCount,
        },
        promptBudget: {
            maxPromptMemories: normalized.maxPromptMemories,
            maxPromptChars: normalized.maxPromptChars,
            maxSummaryItems: normalized.maxSummaryItems,
            maxStableItems: normalized.maxStableItems,
            maxEpisodicItems: normalized.maxEpisodicItems,
        },
        audit: {
            auditEnabled: normalized.auditEnabled,
            auditMaxRecords: normalized.auditMaxRecords,
            auditPreviewChars: normalized.auditPreviewChars,
        },
        recentAudits: normalized.recentAudits,
    };
}

export function applyRecommendedBaselineSettings(settings = DEFAULT_SETTINGS) {
    const normalized = normalizeExtensionSettings(settings);
    return {
        ...normalized,
        ...LONG_CHAT_RECOMMENDED_BASELINE,
    };
}
