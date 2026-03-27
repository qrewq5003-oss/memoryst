# Lorebook Ephemeral Anchor Usefulness Check

## Setup

This pass used the real live path:

- SillyTavern
- Chat Completion
- NanoGPT
- model: `zai-org/glm-4.7`
- current lorebook ephemeral anchor bridge
- current Memory Service backend

This was a short practical usefulness check, not a benchmark.

## Scenarios

### 1. Canon/background anchor helps

- Prompt: `Почему они при всех делают вид, что едва знакомы?`
- Seed: only office-formality context in chat history
- Regular memory block: none
- Lore anchor goal: add the hidden-family/background fact only for the current turn

Observation:

- Without anchor, the answer already improvised a plausible office-distance explanation.
- With anchor active, the answer became more scene-heavy, but not clearly more canon-grounded.
- In this focused pass the runtime collector did not surface a non-empty lore-anchor block snapshot, so the improvement cannot be claimed confidently.

Verdict: `weak`

### 2. Anchor next to existing useful memory

- Prompt: `Почему они при всех делают вид, что едва знакомы?`
- Seed: same as above
- Regular memory block: already contains the same hidden-family / formal-distance fact as stable memory
- Lore anchor goal: check whether the extra anchor becomes redundant or noisy

Observation:

- Baseline answer already used the stable-memory explanation well enough.
- Anchor-active answer stayed coherent, but did not become clearly better.
- No obvious duplication spiral or prompt-surface leakage appeared.

Verdict: `mixed`

### 3. Local scene control

- Prompt: `Что они решили по ближайшей рабочей встрече?`
- Seed: recent project-meeting scene plus cautious working-together background
- Regular memory block: episodic meeting outcome + stable cautious-cooperation context
- Lore anchor goal: unrelated canonical background should not override the local scene

Observation:

- Without anchor, the answer stayed on the meeting outcome.
- With anchor active, the answer still stayed on the meeting outcome.
- The lore anchor did not pull the model away from the local-scene answer.

Verdict: `useful`

## Prompt Hygiene

Observed in this pass:

- no `[Lore Anchor]` label leaked into surface text
- no instruction-like artifacts appeared
- no obvious duplication burst with regular memory

But also:

- the collector snapshot showed an empty anchor block in these runs
- because of that, this pass is enough to say “no obvious harm”, but not enough to claim strong grounding benefit

## Overall Result

Practical conclusion from this short pass:

- the bridge still looks safe as an optional capability
- clear usefulness was not demonstrated yet on the canon/background scenario
- duplication harm was not observed
- local-scene behavior was not harmed

So the bridge should stay available, but not yet treated as a proven high-impact workflow.

## Next Step

The next justified step is a narrower live pass with a real marked lorebook entry in the installed ST world-info data, not only the runtime event-path activation.

That would answer the remaining practical question more cleanly:

- does a real activated lore entry materially improve canonical grounding in the full prompt path?
