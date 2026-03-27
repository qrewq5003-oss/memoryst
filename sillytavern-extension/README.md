# Memory Service Extension for SillyTavern

External memory service integration for long-term context in roleplay chats.

## Installation

1. Copy this folder to your SillyTavern extensions directory:
   ```
   <SillyTavern>/extensions/memory-service/
   ```

2. Enable the extension in SillyTavern:
   - Open SillyTavern
   - Go to Extensions menu (puzzle piece icon)
   - Find "Memory Service" and enable it

3. Configure settings in `settings.mjs` defaults or through SillyTavern `extension_settings`.

**Note:** Settings UI is not implemented in v1. The extension now keeps ST-facing settings grouped logically in storage:
- `connection`
- `retrieval`
- `promptBudget`
- `audit`

## How It Works

**Current pattern:** retrieve runs before generation and affects the current reply. Store still runs after render for the completed exchange.

### Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Current turn                                                │
│ 1. User sends message                                       │
│ 2. Pre-generation hook fires                                │
│ 3. Extension calls /memory/retrieve                         │
│ 4. Extension sets memory_block for CURRENT generation       │
│ 5. Assistant generates response (WITH memory injection)     │
│ 6. CHARACTER_MESSAGE_RENDERED fires                         │
│ 7. Extension calls /memory/store                            │
└─────────────────────────────────────────────────────────────┘
```

### Key Points

- **Retrieve timing:** before generation on a pre-generation hook
- **Memory application:** the retrieved `memory_block` is intended for the **current** generation
- **Store timing:** after `CHARACTER_MESSAGE_RENDERED`, so the completed exchange can be extracted safely

## Settings Groups

The runtime still uses simple flat fields internally, but persisted settings are grouped so the extension is easier to reason about in real long-chat use.

### Connection

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `false` | Enable or disable the extension |
| `memoryServiceUrl` | `http://localhost:8000` | Memory Service endpoint |
| `apiKey` | `''` | API key sent as `X-API-Key` |

### Retrieval

| Setting | Default | Description |
|---------|---------|-------------|
| `retrieveLimit` | `5` | Maximum memories requested from backend retrieval |
| `recentMessagesCount` | `8` | Recent chat messages sent to store extraction |

### Prompt Injection Budget

| Setting | Default | Description |
|---------|---------|-------------|
| `maxPromptMemories` | `4` | Maximum memory entries injected into the prompt |
| `maxPromptChars` | `520` | Maximum injected memory block size in characters |
| `maxSummaryItems` | `1` | Maximum rolling summary items kept |
| `maxStableItems` | `2` | Maximum stable/profile/relationship items kept |
| `maxEpisodicItems` | `1` | Maximum episodic items kept |

### Audit

| Setting | Default | Description |
|---------|---------|-------------|
| `auditEnabled` | `false` | Enable per-interaction integration audit |
| `auditMaxRecords` | `20` | Keep only the latest N audit records |
| `auditPreviewChars` | `240` | Preview length for messages and memory blocks |

To change defaults, edit `sillytavern-extension/settings.mjs`. Existing older flat settings still load correctly, but the extension now serializes grouped settings for cleaner storage.

### Recommended long Russian chat defaults

Recommended baseline:

- `retrieveLimit: 5`
- `recentMessagesCount: 8`
- `maxPromptMemories: 4`
- `maxPromptChars: 520`
- `maxSummaryItems: 1`
- `maxStableItems: 2`
- `maxEpisodicItems: 1`

This is a safe starting point for long Russian RP chats:
- one rolling summary usually survives
- two stable slots preserve durable relationship/profile context
- only one episodic slot reaches prompt injection by default
- prompt budget stays compact enough not to crowd out the main prompt

Knobs most worth tuning first:
- `retrieveLimit`
- `maxPromptChars`
- `maxStableItems`
- `maxEpisodicItems`

## Integration Audit Mode

For Russian long-chat debugging, enable:

```js
auditEnabled: true
```

Each rendered interaction then stores one recent audit record in:

```js
extension_settings['memory-service'].recentAudits
```

You can also inspect it in browser devtools:

```js
memoryServiceAudit.getRecentAudits()
memoryServiceAudit.printRecentAudits()
memoryServiceAudit.clearRecentAudits()
```

Each audit record includes:

- `timestamp`
- `chat_id`, `character_id`
- `store_called`, `retrieve_called`
- store message previews and store summary
- retrieve query, recent message previews, returned item count
- retrieved vs injected item counts by layer
- `memory_block` preview, length, item count
- prompt budget settings, actual chars, trimmed item count, trimming reasons
- retrieve stage and prompt injection stage
- `applied_to_current_turn: true/false`
- prompt insertion method/timing (`setExtensionPrompt`, `current_generation_pre_prompt`)
- notes for missing steps such as `no_last_user_message`, `empty_memory_block`, `prompt_insertion_not_observed`

This is intentionally opt-in and meant for local debugging, not always-on telemetry.

### Manual verification in SillyTavern

1. Enable `auditEnabled: true` in `index.js`.
2. Open a Russian chat with existing stored memories.
3. Send a user message that should clearly retrieve one of them.
4. Before or immediately after the reply, inspect:

```js
memoryServiceAudit.getRecentAudits()[0]
```

Expected signals:

- `retrieve_called: true`
- `retrieve_stage: 'pre_generation'`
- `prompt_injection_stage: 'pre_generation'`
- `applied_to_current_turn: true`
- non-empty `prompt_insertion.memory_block_preview`
- sensible `prompt_insertion.injected_summary_count / injected_stable_count / injected_episodic_count`
- non-zero `trimmed_item_count` only when the retrieved set actually exceeded prompt budget

If current-turn injection fails, the audit record should make that visible via `notes`.

## Requirements

- Memory Service running and accessible
- SillyTavern with extension support

## API Compatibility

This extension uses the following SillyTavern APIs:

**From `../../extensions.js`:**
- `getContext()` - Get current chat context
- `extension_settings` - Settings storage per extension

**From `../../../script.js`:**
- `eventSource` - Event system
- `event_types` - Event type constants (CHARACTER_MESSAGE_RENDERED, CHAT_CHANGED)
- `saveSettingsDebounced` - Debounced settings save function
- `setExtensionPrompt` - Function to set prompt for generation

## Troubleshooting

1. **Extension not working:**
   - Check Memory Service is running: `curl http://localhost:8000/health`
   - Verify URL in settings
   - Check browser console for errors

2. **No memories being stored:**
   - Ensure extension is enabled in SillyTavern
   - Check that chat has started (character selected)

3. **Memory block not appearing:**
   - Check that memories exist in the database
   - Verify retrieval is finding relevant items

## License

Same as Memory Service project.
