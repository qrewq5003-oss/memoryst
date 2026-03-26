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

3. Configure settings in `index.js` (defaults):
   - `memoryServiceUrl`: Memory Service endpoint (default: `http://localhost:8000`)
   - `apiKey`: API key (if required by your backend)
   - `retrieveLimit`: Number of memory items to retrieve (default: 5)

**Note:** Settings UI is not implemented in v1. To change settings, edit `DEFAULT_SETTINGS` in `index.js` or use SillyTavern's extension settings storage if available.

## How It Works (V1 Pattern)

**IMPORTANT:** This extension uses a **POST-RENDER retrieve pattern**.

### Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Exchange N                                                  │
│ 1. User sends message                                       │
│ 2. Assistant generates response (WITHOUT memory injection)  │
│ 3. CHARACTER_MESSAGE_RENDERED event fires                   │
│ 4. Extension calls /memory/store                            │
│ 5. Extension calls /memory/retrieve                         │
│ 6. Extension sets memory_block for NEXT generation          │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ Exchange N+1                                                │
│ 1. User sends message                                       │
│ 2. Assistant generates response (WITH memory_block from N)  │
│ 3. ...                                                      │
└─────────────────────────────────────────────────────────────┘
```

### Key Points

- **Retrieve timing:** After `CHARACTER_MESSAGE_RENDERED` event (post-render)
- **Memory application:** The retrieved `memory_block` is used in the **NEXT** generation
- **This is expected behavior for v1** - not pre-generation injection

## Settings (in-code defaults)

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `false` | Enable/disable extension |
| `memoryServiceUrl` | `http://localhost:8000` | Memory Service endpoint |
| `apiKey` | `''` | API key (sent as X-API-Key header) |
| `retrieveLimit` | `5` | Number of memory items to retrieve |
| `recentMessagesCount` | `8` | Messages to send for extraction |
| `auditEnabled` | `false` | Enable per-interaction ST integration audit |
| `auditMaxRecords` | `20` | Keep only the latest N audit records |
| `auditPreviewChars` | `240` | Preview length for messages and memory blocks in audit |

To change settings, edit `DEFAULT_SETTINGS` in `index.js`.

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
- `memory_block` preview, length, item count
- prompt insertion method/timing (`setExtensionPrompt`, `next_generation_post_render`)
- notes for missing steps such as `no_last_user_message`, `empty_memory_block`, `prompt_insertion_not_observed`

This is intentionally opt-in and meant for local debugging, not always-on telemetry.

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
