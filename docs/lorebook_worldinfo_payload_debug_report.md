# Lorebook World-Info Payload Debug

## Setup

This pass compared two live paths in the real SillyTavern runtime:

- synthetic `WORLD_INFO_ACTIVATED` emission
- real installed world-info activation through ST `#world_info` selection plus `getWorldInfoPrompt(...)`

The goal was to explain why the bridge-specific layer stayed empty on the real installed path even though `worldInfoString` was non-empty.

## Compared Paths

### Synthetic / event-path

Observed payload shape:

- top-level payload: array
- first entry contained:
  - `uid`
  - `world`
  - `comment: [memory-anchor]`
  - `content`

Bridge result before fix:

- `anchorBlock` non-empty
- `anchorEntries` non-empty

### Real installed world-info path

Observed payload shape:

- top-level payload: array
- first entry also contained:
  - `uid`
  - `world`
  - `comment: [memory-anchor]`
  - `content`

So the real path was not losing the entry shape and was not dropping the marker before the bridge saw it.

## Where The Bridge Lost The Entry

The mismatch turned out to be in the helper, not in the ST payload.

For the real installed entry:

- the entry text was much longer than the synthetic test entry
- `buildLoreAnchorBlock(...)` treated that as over-budget
- the helper dropped the only selected anchor entirely with `reason = char_budget`

So the bridge-specific layer was losing the real entry at the final char-budget stage, not at payload parsing.

Short version:

- synthetic path worked because the synthetic anchor text was short
- real installed path failed because the real anchor text was longer and the helper dropped it instead of compacting it

## Narrow Fix

A narrow safe fix was possible and was applied.

Changed in the helper:

- if exactly one allowlisted lore anchor survives selection
- and the final block is too long
- the helper now truncates that one anchor text to fit `maxChars`
- instead of dropping the anchor entirely

Also fixed a small debug-quality issue:

- `uid: 0` no longer collapses into an empty string id

## Verification After Fix

Local checks:

- `tests/test_st_lore_anchor_bridge.mjs` passes
- `tests/test_st_integration_audit.mjs` passes
- syntax check for `lore-anchors.mjs` passes

Live re-probe on the real installed path:

- active selected world-info produced non-empty `worldInfoString`
- bridge now also produced non-empty `anchorBlock`
- `anchorEntries` contained the truncated selected anchor

So the real installed path now surfaces the bridge-specific layer instead of silently dropping it.

## Current Status

After this fix:

- synthetic/event-path still works
- real installed world-info activation now reaches the bridge-specific lore-anchor layer
- the remaining status question is no longer “why is the bridge empty?”
- it is back to the higher-level usefulness question

## Recommended Next Step

The next justified step is a short repeat usefulness pass on the real installed world-info path now that the bridge-specific block actually surfaces.

That pass should answer the practical product question:

- does the real installed bridge layer help enough to justify keeping it as a real optional workflow?
