# Full Prompt Model Adaptation Check

## Setup

This pass used the real SillyTavern runtime:

- `Chat Completion`
- provider: `NanoGPT`
- live browser page in SillyTavern
- real ST full prompt composition
- current production pipeline unchanged

Compared models:

- `zai-org/glm-4.7`
- `moonshotai/kimi-k2.5`

Compared memory block formats:

- `baseline`
- `guided`

`natural` was intentionally excluded. The earlier adaptation pass already showed it was the weakest variant.

This was a full-prompt check, not an isolated memory-block check:

- live character/system context stayed in play
- live chat history stayed in play
- injected memory block was added as an ST extension prompt
- generation ran through the real ST chat path

## Scenarios

- Broad relationship: `Что у них сейчас вообще?`
- Local scene: `Что они решили по ближайшей рабочей встрече?`
- Vague follow-up: `А что насчёт этого?`
- Ongoing goal: `Чего она сейчас пытается добиться?`

Each run used a short seeded chat history plus a controlled injected memory block.

## Observations By Model

### GLM 4.7

Baseline stayed better overall.

- Broad relationship:
  - baseline: good
  - guided: same to slightly worse
  - both used summary and stable correctly
  - guided did not add practical value
- Local scene:
  - baseline: better
  - guided: worse
  - baseline preserved the concrete episodic outcome
  - guided leaked the label surface into the answer with `По памяти [EPISODIC]`
- Vague follow-up:
  - baseline: mixed
  - guided: mixed to worse
  - neither was great, but guided felt more instruction-shaped and less natural
- Ongoing goal:
  - baseline: slightly better
  - guided: same to slightly worse
  - both worked, but baseline stayed a bit more specific

Practical conclusion for GLM 4.7:

- the earlier baseline preference survives the full prompt check
- guided is not worth switching on for this model

### Kimi K2.5

The isolated-block guided advantage did not survive the full prompt check.

- Broad relationship:
  - baseline: good
  - guided: mixed
  - guided added more invented detail not grounded in the injected memory
- Local scene:
  - baseline: better
  - guided: worse
  - baseline preserved the core decision
  - guided invented new specifics like `в десять`, `без Лены`, `сырые цифры`
- Vague follow-up:
  - baseline: weak
  - guided: weak to mixed
  - both drifted, but guided was not a clear improvement
- Ongoing goal:
  - baseline: mixed
  - guided: weak
  - guided missed the intended goal almost entirely and jumped to unrelated details

Practical conclusion for Kimi K2.5:

- guided looked promising in the isolated memory-block pass
- inside the full ST prompt, that advantage became noisy and unreliable
- baseline ended up safer overall

## Baseline vs Guided

### What carried over from the earlier adaptation pass

- `GLM 4.7` still handles the current baseline format well.

### What did not carry over

- `Kimi K2.5` did not keep a clear guided-format advantage once the full ST prompt, character context, and chat history were all present.

### New practical finding

Guided formatting is more likely to create prompt-surface leakage or instruction-shaped behavior in the full prompt:

- explicit label leakage on `GLM 4.7`
- invented extra local-scene specifics on `Kimi K2.5`
- no consistent continuity gain large enough to justify formatter branching

## Production Decision

Recommended production decision:

- keep `baseline` as the universal production default

What is not justified yet:

- switching production to guided
- adding model-specific formatting in production

Why:

- the full-prompt check is the more important adaptation signal
- baseline stayed robust across both models
- guided did not show a stable enough win in the real chat path

## What Still Looks Weak

- Vague follow-up remains the weakest scenario for both models.
- `Kimi K2.5` still drifts more easily into invented scene detail once the full prompt becomes rich.
- `GLM 4.7` is steadier, but explicit guidance text can still leak into its surface reply.

## Recommended Next Step

The next justified pass is still not a production formatter rewrite.

The best next step is a narrower prompt-surface cleanup experiment, if needed:

- keep baseline in production
- if another adaptation pass is done, focus only on:
  - reducing surface leakage from explicit instructions
  - improving vague follow-up grounding
- do that as an experiment-only path first, not as a formatter switch
