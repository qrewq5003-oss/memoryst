# Lorebook Ephemeral Anchor Bridge

## Setup

This pass used the real SillyTavern runtime and the current Memory Service extension.

The bridge is intentionally narrow:

- source: Lorebook / World Info activation
- target: current-turn prompt only
- storage: none

The bridge does not write lorebook content into SQLite and does not feed it into summary or stable memory formation.

## Bridge Mechanism

The extension listens for `WORLD_INFO_ACTIVATED` and builds a separate ephemeral prompt block:

- regular retrieved memories still use the normal `memory-service` prompt key
- lore anchors use a separate transient prompt key
- both are system-role extension prompts for the current turn only

The lore anchor block is compact:

```text
[Lore Anchor]
- ...
```

The block is cleared on:

- next turn start
- message rendered
- chat change

So the bridge behaves like temporary canonical context, not stored memory.

## Curated Scope

Only explicitly allowlisted lore entries are eligible.

Current v1 allowlist markers:

- tag: `memory-anchor`
- comment marker: `[memory-anchor]`
- comment marker: `@memory-anchor`
- content marker line: `@memory-anchor`
- explicit compact anchor line: `@memory-anchor: ...`

If a marker is present but no explicit compact anchor text is provided, the bridge falls back to the entry content with the marker line removed.

This keeps the scope curated and prevents the extension from pulling the whole lorebook into the prompt.

## Guardrails

- lore anchors are injected only
- lore anchors are never sent to `/memory/store`
- lore anchors are never retrieved from `/memory/retrieve`
- lore anchors do not enter rolling summary inputs
- lore anchors are deduped against the current memory block
- non-allowlisted entries are ignored

## Live Verification

Portable repo-managed lorebook fixtures were not available in the live ST install, so the live check used the actual browser runtime event path with curated `WORLD_INFO_ACTIVATED` payloads.

Checked in the live runtime:

1. Allowlisted payload:
   - marker: `@memory-anchor`
   - result: `[Lore Anchor]` block appeared in the extension prompt state
2. Plain lore payload without marker:
   - result: no lore anchor block
3. Store pollution check:
   - emitted an allowlisted anchor with a unique verification phrase
   - triggered the live post-render store hook
   - checked SQLite directly afterward
   - result: unique lore phrase count stayed `0`

Practical result:

- the anchor reached the current-turn prompt path
- the anchor did not become a stored memory
- the bridge stayed separate from regular memory retrieval

## Does It Help

Yes, for curated canon/background facts that should guide only the current turn.

This is especially useful when:

- the lore fact is canonical and editor-curated
- it should help immediately
- it should not become user-history-derived memory

## Next Step

The next justified step is a small live pass with one or two real marked lorebook entries in the installed ST environment, now that the generic bridge path exists.

That would validate prompt usefulness on a real activated lorebook entry, not only on the event-path verification payload.
