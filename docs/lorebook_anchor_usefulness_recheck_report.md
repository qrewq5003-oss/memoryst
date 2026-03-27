# Lorebook Anchor Usefulness Re-check

## Setup

This re-check repeated the live usefulness pass after the real installed world-info path fix from PR 46.

Runtime:

- SillyTavern live page
- Chat Completion -> NanoGPT
- model: `zai-org/glm-4.7`
- current Memory Service extension with the real-path lore-anchor fix

To isolate the bridge-specific effect from ordinary ST world info, the pass used two temporary installed world-info files with the same canon payload:

- `plain`: same world-info content, no marker, so `worldInfoString` was active but `anchorBlock` stayed empty
- `marked`: same content plus `[memory-anchor]` and a compact `@memory-anchor:` line, so both `worldInfoString` and bridge-specific `anchorBlock` were active

The temporary installed files were removed after the pass.

## Real Marked Entry

Shared canon payload:

- Marcus and Alice once swore not to hand the Northern Expedition archive to the Council
- this explains their formal tone and hidden coordination

Bridge-specific compact anchor on the marked variant:

- `Severny dogovor derzhit Markusa i Alisu v formalnom tone, no zastavlyaet ikh skryto koordinirovatsya protiv Soveta.`

On active marked runs the real installed path now behaved as expected:

- `worldInfoString != ""`
- `anchorBlock != ""`
- `anchor_entry_count = 1`

## Scenarios

### 1. Canon / background prompt

Prompt:

- `Что означает Северный договор между Маркусом и Алисой и почему из-за него они держатся так формально?`

Observation:

- `plain` already helped because ordinary world info was active
- `marked` produced a tighter answer centered on the two load-bearing ideas:
  - formal tone
  - hidden coordination against the Council
- `plain` drifted into extra speculative detail more often

Verdict:

- `useful`

### 2. Prompt next to already useful regular memory

Prompt:

- `Если учитывать Северный договор, как сейчас выглядит их рабочая динамика?`

Regular memory block:

- `summary`: restored working trust after conflict
- `stable`: they cover for each other in front of the team

Observation:

- `plain` and `marked` were both usable
- `marked` reinforced the formal-facade / covert-coordination angle a bit more clearly
- but the difference was small
- both variants became verbose and partially redundant with the already useful memory block

Verdict:

- `mixed`

### 3. Local-scene control

Prompt:

- `Учитывая Северный договор, что они решили по ближайшей рабочей встрече?`

Regular memory block:

- `episodic`: they moved the archive discussion to the morning and planned to check copies without the Council

Observation:

- both `plain` and `marked` answered with broad strategy/background instead of preserving the concrete meeting decision
- `marked` was slightly worse because it leaned even harder into Council-facing strategy language
- this is not prompt-surface leakage, but it is practical scene interference

Verdict:

- `harmful`

## Prompt Hygiene

Observed during all marked runs:

- no `[Lore Anchor]` label leaked into surface text
- no instruction-like artifacting
- no obvious formatter corruption

The problem was relevance weighting in the final model response, not prompt-surface formatting.

## Summary

The bridge-specific lore anchor now clearly surfaces on the real installed world-info path.

Practical usefulness after the fix is mixed:

- good for compact canon/background grounding
- small and noisy gain when ordinary memory is already sufficient
- harmful for local-scene prompts where the anchor competes with episodic detail

## Recommendation

The lorebook anchor bridge now looks like a real but narrow optional capability, not just a technical bridge.

Current practical stance:

- useful for canon/background grounding
- not a universal prompt improvement layer
- should not be treated as safe-by-default for local-scene questions

## Next Step

Do not expand marker scope or store lore in memory.

The most justified next step is a narrow experiment-only gating pass:

- suppress or de-prioritize lore anchors when the prompt is clearly local-scene / episodic-detail oriented
- then repeat one canon prompt and one local-scene prompt in the same live setup
