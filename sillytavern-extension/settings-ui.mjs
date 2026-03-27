import {
    LONG_CHAT_RECOMMENDED_BASELINE,
    applyRecommendedBaselineSettings,
} from './settings.mjs';

export const SETTINGS_UI_HOST_SELECTORS = [
    '#extensions_settings2',
    '#extensions_settings',
    '#extensionsMenu',
];

export const SETTINGS_UI_FIELDS = [
    {
        group: 'Connection',
        description: 'Base extension enablement and backend access.',
        fields: [
            {
                key: 'enabled',
                label: 'Enable Memory Service',
                help: 'Turn live retrieve/store integration on for the current SillyTavern session.',
                type: 'checkbox',
            },
            {
                key: 'memoryServiceUrl',
                label: 'Memory Service URL',
                help: 'Base URL for the backend API.',
                type: 'text',
                placeholder: 'http://localhost:8000',
            },
            {
                key: 'apiKey',
                label: 'API Key',
                help: 'Optional X-API-Key header for protected backends.',
                type: 'password',
                placeholder: 'Optional',
            },
        ],
    },
    {
        group: 'Retrieval',
        description: 'How much context is sent and how many candidates are requested.',
        fields: [
            {
                key: 'retrieveLimit',
                label: 'Retrieve Limit',
                help: 'Maximum memories requested from backend retrieval.',
                type: 'number',
                min: 1,
            },
            {
                key: 'recentMessagesCount',
                label: 'Recent Messages Count',
                help: 'How many recent chat messages are sent to store extraction.',
                type: 'number',
                min: 1,
            },
        ],
    },
    {
        group: 'Prompt Injection Budget',
        description: 'How much memory survives into the current-turn prompt.',
        fields: [
            {
                key: 'maxPromptMemories',
                label: 'Max Prompt Memories',
                help: 'Maximum injected memory items after budget trimming.',
                type: 'number',
                min: 1,
            },
            {
                key: 'maxPromptChars',
                label: 'Max Prompt Chars',
                help: 'Maximum injected memory block size in characters.',
                type: 'number',
                min: 64,
            },
            {
                key: 'maxSummaryItems',
                label: 'Max Summary Items',
                help: 'Cap for rolling summary items kept in the injected prompt.',
                type: 'number',
                min: 0,
            },
            {
                key: 'maxStableItems',
                label: 'Max Stable Items',
                help: 'Cap for profile and relationship carry-over memories.',
                type: 'number',
                min: 0,
            },
            {
                key: 'maxEpisodicItems',
                label: 'Max Episodic Items',
                help: 'Cap for fresh scene memories in the injected prompt.',
                type: 'number',
                min: 0,
            },
        ],
    },
    {
        group: 'Audit',
        description: 'Opt-in debugging for retrieve/store and prompt injection behavior.',
        fields: [
            {
                key: 'auditEnabled',
                label: 'Enable Audit',
                help: 'Store recent integration audit records in extension settings.',
                type: 'checkbox',
            },
            {
                key: 'auditMaxRecords',
                label: 'Audit Max Records',
                help: 'Keep only the most recent audit records.',
                type: 'number',
                min: 1,
            },
            {
                key: 'auditPreviewChars',
                label: 'Audit Preview Chars',
                help: 'Preview length for message and memory block snippets in audit records.',
                type: 'number',
                min: 40,
            },
        ],
    },
];

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
}

function getFieldValue(settings, field) {
    const value = settings?.[field.key];
    if (field.type === 'checkbox') {
        return Boolean(value);
    }
    return value ?? '';
}

export function buildSettingsUiMarkup(settings = {}) {
    const sections = SETTINGS_UI_FIELDS.map(section => {
        const fields = section.fields.map(field => {
            const value = getFieldValue(settings, field);
            const inputHtml = field.type === 'checkbox'
                ? `<input data-memory-setting="${field.key}" type="checkbox" ${value ? 'checked' : ''}>`
                : `<input data-memory-setting="${field.key}" type="${field.type}" value="${escapeHtml(value)}"${field.placeholder ? ` placeholder="${escapeHtml(field.placeholder)}"` : ''}${typeof field.min === 'number' ? ` min="${field.min}"` : ''}>`;

            return `
                <label class="memory-service-setting-row">
                    <span class="memory-service-setting-copy">
                        <span class="memory-service-setting-label">${escapeHtml(field.label)}</span>
                        <small class="memory-service-setting-help">${escapeHtml(field.help)}</small>
                    </span>
                    <span class="memory-service-setting-control">${inputHtml}</span>
                </label>
            `;
        }).join('');

        return `
            <section class="memory-service-settings-group">
                <h4>${escapeHtml(section.group)}</h4>
                <p class="memory-service-settings-group-copy">${escapeHtml(section.description)}</p>
                ${fields}
            </section>
        `;
    }).join('');

    const baselinePairs = Object.entries(LONG_CHAT_RECOMMENDED_BASELINE)
        .map(([key, value]) => `${key}: ${value}`)
        .join(' | ');

    return `
        <div class="memory-service-settings">
            <style>
                #memory-service-settings-panel {
                    border: 1px solid var(--SmartThemeBorderColor, #666);
                    border-radius: 10px;
                    padding: 14px;
                    margin-top: 12px;
                    background: var(--SmartThemeBlurTintColor, rgba(0, 0, 0, 0.08));
                }
                #memory-service-settings-panel h3,
                #memory-service-settings-panel h4,
                #memory-service-settings-panel p {
                    margin: 0;
                }
                .memory-service-settings-intro {
                    margin-top: 6px;
                    color: var(--SmartThemeEmColor, inherit);
                }
                .memory-service-settings-baseline {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 10px;
                    align-items: center;
                    margin-top: 12px;
                    margin-bottom: 14px;
                }
                .memory-service-settings-baseline-copy {
                    font-size: 0.9em;
                    color: var(--SmartThemeQuoteColor, inherit);
                }
                .memory-service-settings-grid {
                    display: grid;
                    gap: 12px;
                }
                .memory-service-settings-group {
                    border: 1px solid var(--SmartThemeBorderColor, #666);
                    border-radius: 8px;
                    padding: 12px;
                }
                .memory-service-settings-group-copy {
                    margin-top: 4px;
                    margin-bottom: 10px;
                    font-size: 0.9em;
                    color: var(--SmartThemeQuoteColor, inherit);
                }
                .memory-service-setting-row {
                    display: grid;
                    grid-template-columns: minmax(0, 1fr) minmax(140px, 220px);
                    gap: 12px;
                    align-items: center;
                    margin-top: 10px;
                }
                .memory-service-setting-label {
                    display: block;
                    font-weight: 600;
                }
                .memory-service-setting-help {
                    display: block;
                    margin-top: 2px;
                    opacity: 0.8;
                }
                .memory-service-setting-control input {
                    width: 100%;
                    box-sizing: border-box;
                }
                .memory-service-setting-control input[type="checkbox"] {
                    width: auto;
                    transform: scale(1.15);
                }
                @media (max-width: 720px) {
                    .memory-service-setting-row {
                        grid-template-columns: 1fr;
                    }
                }
            </style>
            <h3>Memory Service</h3>
            <p class="memory-service-settings-intro">Native extension settings for current-turn retrieval, prompt budget, and audit controls.</p>
            <div class="memory-service-settings-baseline">
                <button type="button" id="memory-service-apply-baseline">Apply Recommended Baseline</button>
                <span class="memory-service-settings-baseline-copy">Long Russian chat baseline: ${escapeHtml(baselinePairs)}</span>
            </div>
            <div class="memory-service-settings-grid">${sections}</div>
        </div>
    `;
}

export function applySettingsChange(settings, fieldKey, nextValue) {
    return {
        ...settings,
        [fieldKey]: nextValue,
    };
}

function coerceFieldValue(field, input) {
    if (field.type === 'checkbox') {
        return Boolean(input.checked);
    }

    if (field.type === 'number') {
        const parsed = Number.parseInt(input.value, 10);
        if (!Number.isNaN(parsed)) {
            return typeof field.min === 'number' ? Math.max(field.min, parsed) : parsed;
        }
        return typeof field.min === 'number' ? field.min : 0;
    }

    return input.value;
}

export function findSettingsUiHost(documentRef) {
    if (!documentRef || typeof documentRef.querySelector !== 'function') {
        return null;
    }

    for (const selector of SETTINGS_UI_HOST_SELECTORS) {
        const host = documentRef.querySelector(selector);
        if (host) {
            return host;
        }
    }

    return null;
}

export function renderSettingsUi({
    document,
    settings,
    onSettingsChanged,
    onApplyRecommendedBaseline,
}) {
    const host = findSettingsUiHost(document);
    if (!host || typeof host.querySelector !== 'function') {
        return false;
    }

    let panel = host.querySelector('#memory-service-settings-panel');
    if (!panel) {
        panel = document.createElement('div');
        panel.id = 'memory-service-settings-panel';
        host.appendChild(panel);
    }

    panel.innerHTML = buildSettingsUiMarkup(settings);

    for (const section of SETTINGS_UI_FIELDS) {
        for (const field of section.fields) {
            const input = panel.querySelector(`[data-memory-setting="${field.key}"]`);
            if (!input || typeof input.addEventListener !== 'function') {
                continue;
            }

            const eventName = field.type === 'checkbox' ? 'change' : 'input';
            input.addEventListener(eventName, () => {
                const nextValue = coerceFieldValue(field, input);
                onSettingsChanged(field.key, nextValue);
            });
        }
    }

    const baselineButton = panel.querySelector('#memory-service-apply-baseline');
    if (baselineButton && typeof baselineButton.addEventListener === 'function') {
        baselineButton.addEventListener('click', () => {
            onApplyRecommendedBaseline(applyRecommendedBaselineSettings(settings));
        });
    }

    return true;
}

export function mountSettingsUi({
    document,
    settings,
    onSettingsChanged,
    onApplyRecommendedBaseline,
    retries = 10,
    retryDelayMs = 500,
    scheduleRetry = null,
}) {
    const rendered = renderSettingsUi({
        document,
        settings,
        onSettingsChanged,
        onApplyRecommendedBaseline,
    });

    if (rendered || retries <= 0) {
        return rendered;
    }

    const retry = typeof scheduleRetry === 'function'
        ? scheduleRetry
        : (fn, delay) => globalThis.setTimeout?.(fn, delay);

    if (typeof retry === 'function') {
        retry(() => {
            mountSettingsUi({
                document,
                settings,
                onSettingsChanged,
                onApplyRecommendedBaseline,
                retries: retries - 1,
                retryDelayMs,
                scheduleRetry,
            });
        }, retryDelayMs);
    }

    return false;
}
