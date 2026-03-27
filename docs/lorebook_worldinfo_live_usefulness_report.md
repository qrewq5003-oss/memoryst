# Lorebook World-Info Live Usefulness Check

## Setup

This pass used the real live path:

- SillyTavern
- installed World Info / Lorebook data
- current lore anchor bridge
- current Memory Service backend
- `Chat Completion -> NanoGPT`
- model: `zai-org/glm-4.7`

This was a narrow practical check, not a benchmark.

## Real Marked Entry Used

For this pass I created one temporary real world-info file in the installed ST `worlds/` directory and loaded it through the normal `#world_info` selection path.

The entry used one supported bridge marker and one compact canon fact:

- key: `Код-41`
- canon fact: `Код-41` is the internal code name for a family-linked matter
- expected effect: when `Код-41` appears, they switch to a formal tone so colleagues do not infer the family connection

The temporary file was removed after the check.

## Activation Path

Confirmed in the live runtime:

- the real installed world-info entry appeared in the ST world-info selector after reload
- selecting it through the normal ST UI path produced non-empty `worldInfoString`
- the active entry was therefore reaching the normal ST prompt composition path

But the bridge-specific result was weaker:

- `worldInfoString` was non-empty on active runs
- the lore-anchor bridge snapshot stayed empty:
  - `anchor_block = ""`
  - `anchor_entry_count = 0`

So this pass confirms real world-info activation, but it does not confirm successful bridge-side ephemeral anchor injection on that path.

## Scenarios

### 1. Canon / background prompt

- Prompt: `Почему они резко переходят на официальный тон, когда на планёрке всплывает Код-41?`
- Memory block: none

Observed:

- Without active world-info, the answer improvised a plausible but generic corporate-security explanation.
- With active real world-info, the answer became clearly aligned with the intended canon fact: hidden family connection plus deliberate formal tone.

Verdict: `useful`

### 2. World-info next to existing regular memory

- Prompt: `Почему они резко переходят на официальный тон, когда на планёрке всплывает Код-41?`
- Memory block: the same canon fact already present as stable memory

Observed:

- Without world-info, the answer still drifted toward a generic security / reputation explanation.
- With active world-info, the answer became exact and concise, but mostly repeated the same canon fact already present in memory.
- This looked more like grounding reinforcement than a distinctly new win.

Verdict: `mixed`

### 3. Local scene control

- Prompt: `Что они решили по ближайшей рабочей встрече?`
- Seed: recent meeting outcome plus one background mention of `Код-41`
- Memory block: fresh episodic meeting outcome

Observed:

- Without active world-info, the answer stayed on the meeting decision.
- With active world-info, the answer still stayed on the meeting decision.
- The background canon did not displace the local-scene answer.

Verdict: `neutral`

## Prompt Hygiene

Observed in this pass:

- no `[Lore Anchor]` surface leakage
- no instruction-like phrasing in replies
- no obvious local-scene degradation

But the key limitation is important:

- the installed world-info entry clearly entered ST prompt composition
- the separate bridge-side lore anchor block did not surface on this path

So any usefulness seen here is attributable to real world-info grounding, not confidently to the ephemeral anchor bridge itself.

## Recommendation

Practical conclusion:

- real marked world-info activation is useful for canon/background grounding
- real world-info activation does not appear harmful to local-scene behavior in this narrow pass
- but this pass does **not** prove practical usefulness of the bridge-specific ephemeral anchor layer on the real installed world-info path

So the most honest current reading is:

- world-info itself is useful here
- the bridge remains technically promising but not yet practically validated on the real installed activation path

## Next Step

The next justified step is not a bridge expansion.

It is a narrow debugging pass on the real installed world-info activation payload:

- why does real activated world-info produce non-empty `worldInfoString`
- but still leave the bridge-side `anchor_block` empty?

Until that is explained, the bridge should still be treated as mostly optional / experimental on real installed world-info entries.
