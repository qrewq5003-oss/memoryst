import {
    LONG_CHAT_RECOMMENDED_BASELINE,
    applyRecommendedBaselineSettings,
} from './settings.mjs';

// 'ok' and 'unknown' (no fetch attempted yet) stay silent; only a real failure warns.
export const TRACKER_WARNING_STATUSES = ['unsupported', 'error'];

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
                placeholder: 'http://localhost:8001',
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
        group: 'Trackers',
        description: 'Character trackers injected when a lorebook entry for that character fires. They do not compete with retrieved memories for the prompt budget above.',
        // Renders the tracker-availability banner right above this group's fields, so a
        // backend that can't serve trackers says so where the trackers are configured.
        banner: 'trackers',
        fields: [
            {
                key: 'trackerInjectionEnabled',
                label: 'Inject Trackers',
                help: 'Add the character\'s stored trackers to the prompt when their lorebook entry activates.',
                type: 'checkbox',
            },
            {
                key: 'maxTrackerChars',
                label: 'Max Tracker Chars',
                help: 'Total size cap for all four trackers of one character, not a cap per tracker.',
                type: 'number',
                min: 100,
            },
            {
                key: 'trackerReminderThreshold',
                label: 'Tracker Reminder Threshold',
                help: 'Show a reminder toast once a tracker is this many messages behind the chat.',
                type: 'number',
                min: 5,
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

/**
 * Build <option> markup for the Scene Extraction Model <select>, the same way
 * the backend's own web UI populates consolidate-model/scene-model selects
 * from GET /memory/models (see _scripts.html's loadModels()). Pure/no DOM so
 * it's directly unit-testable without a fetch or document.
 *
 * Always keeps `selectedValue` selectable even if it isn't in `models` (e.g.
 * the catalog fetch failed, or the model was retired from the backend's
 * list) - otherwise pressing Confirm without touching the dropdown could
 * silently overwrite a working saved value with the blank default.
 */
export function buildModelOptionsMarkup(models = [], selectedValue = '') {
    const options = [{ value: '', label: '(use LLM Provider panel default)' }];
    const seen = new Set();

    if (selectedValue) {
        options.push({ value: selectedValue, label: `${selectedValue} (current)` });
        seen.add(selectedValue);
    }

    for (const model of models) {
        if (seen.has(model)) continue;
        seen.add(model);
        options.push({ value: model, label: model });
    }

    return options
        .map(opt => `<option value="${escapeHtml(opt.value)}"${opt.value === selectedValue ? ' selected' : ''}>${escapeHtml(opt.label)}</option>`)
        .join('');
}

/**
 * Fetch GET {memoryServiceUrl}/memory/models and populate `select` with the
 * full catalog, keeping `selectedValue` selected. Mirrors the backend web
 * UI's loadModels() but scoped to a single select, called eagerly when the
 * settings panel mounts (see renderSettingsUi) - not gated behind a button
 * click, since the model list should be ready by the time the user opens the
 * dropdown.
 *
 * fetchImpl is injectable for tests; defaults to the global fetch so runtime
 * callers don't need to pass it.
 */
export async function loadSceneExtractionModelOptions({
    select,
    resultEl = null,
    memoryServiceUrl,
    apiKey,
    selectedValue = '',
    fetchImpl = typeof fetch !== 'undefined' ? fetch : undefined,
}) {
    if (!select || typeof fetchImpl !== 'function' || !memoryServiceUrl) {
        return false;
    }

    try {
        const headers = {};
        if (apiKey) headers['X-API-Key'] = apiKey;

        const resp = await fetchImpl(`${memoryServiceUrl}/memory/models`, { headers });
        if (!resp.ok) {
            if (resultEl) resultEl.textContent = `Could not load model list: ${resp.status}`;
            return false;
        }

        const data = await resp.json();
        const models = Array.isArray(data.models) ? data.models : [];
        select.innerHTML = buildModelOptionsMarkup(models, selectedValue);
        if (resultEl) resultEl.textContent = `Loaded ${models.length} models.`;
        return true;
    } catch (e) {
        if (resultEl) resultEl.textContent = `Could not load model list: ${e.message}`;
        return false;
    }
}

/**
 * Build the version-compatibility warning banner. Returns an empty string when
 * there is nothing to warn about (no check yet, or `warn` is false), so it can
 * be inlined unconditionally into the panel markup.
 *
 * @param {object|null} compatibility - result of compareVersions(), or null
 */
/**
 * The shared warning-banner shape (yellow, left-barred) used for every "the backend and
 * this extension don't line up" condition. `statusAttr` keeps each banner's own
 * data-* hook so tests and CSS can tell them apart.
 */
export function buildWarningBannerMarkup({
    title,
    message,
    details = [],
    statusAttr = 'data-compat-status',
    status = '',
} = {}) {
    const detailLine = details.length
        ? `<small class="memory-service-compat-detail">${escapeHtml(details.join(' · '))}</small>`
        : '';

    return `
        <div class="memory-service-compat-banner" role="alert" ${statusAttr}="${escapeHtml(status)}">
            <strong>⚠ ${escapeHtml(title)}</strong>
            <span>${escapeHtml(message)}</span>
            ${detailLine}
        </div>
    `;
}

export function buildCompatibilityBannerMarkup(compatibility = null) {
    if (!compatibility || !compatibility.warn) {
        return '';
    }

    const details = [];
    if (typeof compatibility.extensionProtocol === 'number') {
        details.push(`extension protocol v${compatibility.extensionProtocol}`);
    }
    if (typeof compatibility.backendProtocol === 'number') {
        details.push(`backend protocol v${compatibility.backendProtocol}`);
    }
    if (compatibility.backendServiceVersion) {
        details.push(`backend ${compatibility.backendServiceVersion}`);
    }
    if (compatibility.backendGitCommit) {
        details.push(`commit ${compatibility.backendGitCommit}`);
    }

    return buildWarningBannerMarkup({
        title: 'Memory Service version mismatch',
        message: compatibility.message,
        details,
        status: compatibility.status,
    });
}

/**
 * Warns inside the Trackers section when the last GET /memory/trackers failed. A backend
 * that predates trackers answers 404, and until now that only produced a console.warn -
 * so the trackers panel looked configured and functional while silently never injecting
 * anything.
 *
 * @param {object|null} trackerStatus - { status, message, detail } from index.js
 */
export function buildTrackerStatusBannerMarkup(trackerStatus = null) {
    if (!trackerStatus || !TRACKER_WARNING_STATUSES.includes(trackerStatus.status)) {
        return '';
    }

    return buildWarningBannerMarkup({
        title: 'Трекеры недоступны',
        message: trackerStatus.status === 'unsupported'
            ? 'Бэкенд не поддерживает эту функцию — трекеры не будут попадать в промпт. Обновите memoryst.'
            : 'Бэкенд не ответил на запрос трекеров — они не будут попадать в промпт.',
        details: trackerStatus.detail ? [trackerStatus.detail] : [],
        statusAttr: 'data-tracker-status',
        status: trackerStatus.status,
    });
}

export function buildSettingsUiMarkup(settings = {}, compatibility = null, trackerStatus = null) {
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

        const banner = section.banner === 'trackers'
            ? buildTrackerStatusBannerMarkup(trackerStatus)
            : '';

        return `
            <section class="memory-service-settings-group">
                <h4>${escapeHtml(section.group)}</h4>
                <p class="memory-service-settings-group-copy">${escapeHtml(section.description)}</p>
                ${banner}
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
                .memory-service-compat-banner {
                    display: flex;
                    flex-direction: column;
                    gap: 4px;
                    margin-top: 12px;
                    padding: 10px 12px;
                    border: 1px solid #e0a800;
                    border-left-width: 4px;
                    border-radius: 8px;
                    background: rgba(224, 168, 0, 0.12);
                    color: var(--SmartThemeBodyColor, inherit);
                }
                .memory-service-compat-detail {
                    opacity: 0.75;
                    font-family: monospace;
                    font-size: 0.85em;
                }
            </style>
            <h3>Memory Service</h3>
            <p class="memory-service-settings-intro">Native extension settings for current-turn retrieval, prompt budget, and audit controls.</p>
            ${buildCompatibilityBannerMarkup(compatibility)}
            <div class="memory-service-settings-baseline">
                <button type="button" id="memory-service-apply-baseline">Apply Recommended Baseline</button>
                <span class="memory-service-settings-baseline-copy">Long Russian chat baseline: ${escapeHtml(baselinePairs)}</span>
            </div>
            <div class="memory-service-settings-grid">${sections}</div>
            <section class="memory-service-settings-group">
                <h4>Scene Extraction</h4>
                <p class="memory-service-settings-group-copy">Which model the automatic /memory/store pipeline calls for LLM scene extraction - independent of the LLM Provider panel's active model (that one is shared with consolidation and manual tools). Prefer a non-reasoning model: reasoning models can spend their token budget on hidden reasoning before emitting the extraction JSON, causing empty/failed calls that silently fall back to a cruder regex extractor.</p>
                <label class="memory-service-setting-row">
                    <span class="memory-service-setting-copy">
                        <span class="memory-service-setting-label">Scene Extraction Model</span>
                        <small class="memory-service-setting-help">Loaded from the backend's /memory/models catalog. Pick a model, then press Confirm to save it - selecting from the list alone does not save.</small>
                    </span>
                    <span class="memory-service-setting-control">
                        <select id="memory-service-scene-extraction-model">${buildModelOptionsMarkup([], settings.sceneExtractionModel)}</select>
                    </span>
                </label>
                <div style="margin-top:8px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
                    <button type="button" id="memory-service-scene-extraction-save">Confirm</button>
                    <span id="memory-service-scene-extraction-result" style="font-size:0.9em;"></span>
                </div>
            </section>
            <section class="memory-service-settings-group">
                <h4>Backfill</h4>
                <p class="memory-service-settings-group-copy">Import existing chat history into memory.</p>
                <div style="margin-top:8px;">
                    <label style="font-weight:600;">Upload .jsonl file (SillyTavern chat format):</label><br>
                    <input type="file" id="memory-service-backfill-file" accept=".jsonl,.json" style="margin-top:4px;">
                </div>
                <div style="margin-top:8px;">
                    <label style="font-weight:600;">Or paste messages (one per line: "user: text" / "assistant: text"):</label>
                    <textarea id="memory-service-backfill-text" rows="4" style="width:100%;box-sizing:border-box;margin-top:4px;font-family:monospace;font-size:0.85em;" placeholder="user: Hello Alice&#10;assistant: Hi, nice to meet you!"></textarea>
                </div>
                <div style="margin-top:8px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
                    <button type="button" id="memory-service-backfill-btn">Backfill Current Chat</button>
                    <button type="button" id="memory-service-delete-chat-btn" style="color:#e74c3c;">Delete Chat Memories</button>
                    <span id="memory-service-backfill-status" style="font-size:0.9em;"></span>
                </div>
            </section>
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
    getChatContext,
    compatibility = null,
    trackerStatus = null,
    fetchImpl = typeof fetch !== 'undefined' ? fetch : undefined,
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

    panel.innerHTML = buildSettingsUiMarkup(settings, compatibility, trackerStatus);

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

    // Scene Extraction Model: a <select> populated from the backend's live
    // /memory/models catalog (like the backend web UI's consolidate-model/
    // scene-model selects), with an explicit Confirm button rather than
    // save-on-select - deliberately NOT part of the generic SETTINGS_UI_FIELDS
    // loop above, since it needs an async catalog fetch and its own save
    // gesture. Confirm reuses the exact same onSettingsChanged callback every
    // other field already uses (a flat `{...settings, [key]: value}` merge in
    // index.js, not a re-normalize) - so this can't reintroduce the earlier
    // bug where re-running normalizeExtensionSettings on an already-flat
    // runtime settings object silently reset memoryServiceUrl to its default.
    const sceneModelSelect = panel.querySelector('#memory-service-scene-extraction-model');
    const sceneModelSaveBtn = panel.querySelector('#memory-service-scene-extraction-save');
    const sceneModelResult = panel.querySelector('#memory-service-scene-extraction-result');

    if (sceneModelSaveBtn && sceneModelSelect && typeof sceneModelSaveBtn.addEventListener === 'function') {
        sceneModelSaveBtn.addEventListener('click', () => {
            onSettingsChanged('sceneExtractionModel', sceneModelSelect.value);
            if (sceneModelResult) {
                sceneModelResult.textContent = `Saved: ${sceneModelSelect.value || '(provider default)'}`;
            }
        });
    }

    if (sceneModelSelect) {
        loadSceneExtractionModelOptions({
            select: sceneModelSelect,
            resultEl: sceneModelResult,
            memoryServiceUrl: settings.memoryServiceUrl,
            apiKey: settings.apiKey,
            selectedValue: settings.sceneExtractionModel,
            fetchImpl,
        });
    }

    const backfillBtn = panel.querySelector('#memory-service-backfill-btn');
    const backfillText = panel.querySelector('#memory-service-backfill-text');
    const backfillFile = panel.querySelector('#memory-service-backfill-file');
    const backfillStatus = panel.querySelector('#memory-service-backfill-status');
    const deleteChatBtn = panel.querySelector('#memory-service-delete-chat-btn');

    function parseJsonl(raw) {
        const messages = [];
        for (const line of raw.split('\n')) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            try {
                const obj = JSON.parse(trimmed);
                // SillyTavern format: {name, mes, is_user}
                if (obj.mes && typeof obj.is_user === 'boolean') {
                    const role = obj.is_user ? 'user' : 'assistant';
                    const text = (obj.mes || '').trim();
                    if (text) messages.push({ role, text });
                }
                // Standard format: {role, content}
                else if (obj.role && obj.content) {
                    messages.push({ role: obj.role, text: obj.content });
                }
            } catch (e) {
                // not JSON, skip
            }
        }
        return messages;
    }

    function parseTextFormat(raw) {
        return raw.split('\n')
            .map(line => {
                const match = line.match(/^(user|assistant|system):\s*(.+)$/i);
                if (match) return { role: match[1].toLowerCase(), text: match[2].trim() };
                return null;
            })
            .filter(Boolean);
    }

    async function runBackfill(messages) {
        const ctx = typeof getChatContext === 'function' ? getChatContext() : {};
        const chatId = ctx.chatId || 'backfill';
        const charId = ctx.characterId || 'backfill';

        const url = `${settings.memoryServiceUrl}/memory/backfill`;
        const headers = { 'Content-Type': 'application/json' };
        if (settings.apiKey) headers['X-API-Key'] = settings.apiKey;

        const resp = await fetch(url, {
            method: 'POST',
            headers,
            body: JSON.stringify({ chat_id: chatId, character_id: charId, messages }),
        });

        if (resp.ok) {
            const r = await resp.json();
            backfillStatus.textContent = `Done: ${r.stored} stored, ${r.skipped} skipped, ${r.duplicates} duplicates (${r.processed} processed)`;
        } else {
            backfillStatus.textContent = `Error: ${resp.status} ${resp.statusText}`;
        }
    }

    if (backfillBtn) {
        backfillBtn.addEventListener('click', async () => {
            // Check file first
            if (backfillFile && backfillFile.files.length > 0) {
                backfillBtn.disabled = true;
                backfillStatus.textContent = 'Reading file...';
                try {
                    const raw = await backfillFile.files[0].text();
                    const messages = parseJsonl(raw);
                    if (messages.length === 0) {
                        backfillStatus.textContent = 'No valid messages in .jsonl file.';
                        backfillBtn.disabled = false;
                        return;
                    }
                    backfillStatus.textContent = `Processing ${messages.length} messages from file...`;
                    await runBackfill(messages);
                } catch (e) {
                    backfillStatus.textContent = `Error: ${e.message}`;
                } finally {
                    backfillBtn.disabled = false;
                }
                return;
            }

            // Fall back to text area
            const raw = backfillText?.value?.trim();
            if (!raw) {
                backfillStatus.textContent = 'Paste messages or select a .jsonl file.';
                return;
            }

            const messages = parseTextFormat(raw);
            if (messages.length === 0) {
                backfillStatus.textContent = 'No valid messages. Use format: "user: text"';
                return;
            }

            backfillBtn.disabled = true;
            backfillStatus.textContent = `Processing ${messages.length} messages...`;
            try {
                await runBackfill(messages);
            } catch (e) {
                backfillStatus.textContent = `Error: ${e.message}`;
            } finally {
                backfillBtn.disabled = false;
            }
        });
    }

    if (deleteChatBtn) {
        deleteChatBtn.addEventListener('click', async () => {
            const ctx = typeof getChatContext === 'function' ? getChatContext() : {};
            const chatId = ctx.chatId;
            if (!chatId) {
                backfillStatus.textContent = 'No chat selected.';
                return;
            }

            if (!confirm(`Delete ALL memories for chat "${chatId}"? This cannot be undone.`)) {
                return;
            }

            deleteChatBtn.disabled = true;
            backfillStatus.textContent = 'Deleting...';

            try {
                const url = `${settings.memoryServiceUrl}/memory/chat/${encodeURIComponent(chatId)}`;
                const headers = {};
                if (settings.apiKey) headers['X-API-Key'] = settings.apiKey;

                const resp = await fetch(url, { method: 'DELETE', headers });

                if (resp.ok) {
                    const r = await resp.json();
                    backfillStatus.textContent = `Deleted ${r.deleted} memories for this chat.`;
                } else {
                    backfillStatus.textContent = `Error: ${resp.status} ${resp.statusText}`;
                }
            } catch (e) {
                backfillStatus.textContent = `Error: ${e.message}`;
            } finally {
                deleteChatBtn.disabled = false;
            }
        });
    }

    return true;
}

export function mountSettingsUi({
    document,
    settings,
    onSettingsChanged,
    onApplyRecommendedBaseline,
    getChatContext,
    compatibility = null,
    trackerStatus = null,
    retries = 10,
    retryDelayMs = 500,
    scheduleRetry = null,
    fetchImpl = typeof fetch !== 'undefined' ? fetch : undefined,
}) {
    const rendered = renderSettingsUi({
        document,
        settings,
        onSettingsChanged,
        onApplyRecommendedBaseline,
        getChatContext,
        compatibility,
        trackerStatus,
        fetchImpl,
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
                getChatContext,
                compatibility,
                trackerStatus,
                retries: retries - 1,
                retryDelayMs,
                scheduleRetry,
                fetchImpl,
            });
        }, retryDelayMs);
    }

    return false;
}
