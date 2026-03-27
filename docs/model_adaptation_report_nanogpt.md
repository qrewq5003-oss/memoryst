# NanoGPT Model Adaptation Pass

## Setup

This pass used the real SillyTavern runtime:

- `Chat Completion`
- provider: `NanoGPT`
- live browser page in SillyTavern
- current Memory Service pipeline unchanged

Compared NanoGPT models:

- `zai-org/glm-4.7`
- `moonshotai/kimi-k2.5`

This was not treated as a benchmark. The goal was practical confidence about how the models use the existing injected memory block.

## Memory Block Formats

### A. Baseline

Current production-style block with:

- `[Relevant Memory]`
- `[SUMMARY]`
- `[STABLE]`
- `[EPISODIC]`

### B. Natural

More natural Russian section labels:

- `Долгосрочный контекст`
- `Устойчивый контекст`
- `Свежая релевантная сцена`

### C. Guided

Baseline-style labels plus short usage instructions:

- use summary/stable for continuity
- use episodic for local scene
- do not let broad summary override local scene

## Scenarios

- Broad relationship: `Что у них сейчас вообще?`
- Local scene: `Что они решили по ближайшей рабочей встрече?`
- Vague follow-up: `А что насчёт этого?`
- Ongoing goal: `Чего она сейчас пытается добиться?`

All model and format combinations were run on the same scenario set through the live ST runtime.

## Observations

### GLM 4.7

Baseline was the most balanced overall.

- Broad relationship: good
  - baseline and guided both used summary + stable correctly
  - natural was still good, but slightly noisier and less natural in phrasing
- Local scene: good
  - baseline and natural preserved the concrete episodic outcome
  - guided over-compressed the answer and dropped the second detail about Lena
- Vague follow-up: mixed
  - baseline was the cleanest practical answer
  - natural and guided drifted into more scene-writing and extra dramatization
- Ongoing goal: good
  - all three formats worked
  - baseline stayed closest to the intended goal summary without extra filler

Practical reading: GLM 4.7 already handles the current labeled block well. Extra instructions did not clearly improve it and sometimes made the local answer more compressed or more theatrical.

### Kimi K2.5

Guided was the best overall format, with baseline close behind.

- Broad relationship: good
  - guided gave the clearest continuity read
  - baseline was also strong, but slightly more verbose
  - natural worked, but felt less disciplined
- Local scene: good
  - all three formats preserved the concrete episodic answer
  - guided was the most concise while still keeping the key detail
- Vague follow-up: mixed
  - baseline and guided both used the memory well
  - natural was weaker because it turned into a clarifying-question style response
- Ongoing goal: good
  - all three formats worked
  - guided produced the cleanest answer with the least extra narrative sprawl

Practical reading: Kimi K2.5 benefits more from a small explicit instruction layer. It already listens to the memory, but the guided format makes its use of continuity and local-scene cues more disciplined.

## Per-Scenario Summary

### Broad relationship

- GLM 4.7: `baseline` or `guided` both good
- Kimi K2.5: `guided` best

Both models used summary and stable context. Kimi reacted more positively to explicit usage instructions.

### Local scene

- GLM 4.7: `baseline` best
- Kimi K2.5: `guided` slightly best, but margin was small

Both models preserved the fresh episodic outcome. Guided formatting did not produce a meaningful win on GLM and slightly compressed detail.

### Vague follow-up

- GLM 4.7: `baseline` best
- Kimi K2.5: `baseline` or `guided`

This was the weakest scenario overall. Natural wording was the least reliable for both models because it encouraged more improvisational scene writing instead of grounded continuity use.

### Ongoing goal

- GLM 4.7: `baseline` best
- Kimi K2.5: `guided` best

This scenario was robust across all formats. The difference was mostly about discipline and extra narrative sprawl.

## Recommendation

For a first adaptation recommendation:

- keep one shared production format for now
- do not switch to a fully natural-language memory block
- if a model-specific experiment is needed later:
  - prefer `baseline` for `GLM 4.7`
  - prefer `guided` for `Kimi K2.5`

Why not switch immediately:

- the current baseline already works well on both models
- the gain from model-specific formatting is real but not large enough yet to justify production complexity
- the natural format was the least stable across the matrix

## What Still Looks Weak

- Vague follow-up remains the most sensitive scenario.
- Both models can drift into scene-writing if the prompt is underdetermined.
- Kimi K2.5 is more likely to elaborate beyond the exact memory payload.
- GLM 4.7 is more likely to compress away a secondary local-scene detail when the format includes extra instructions.

## Recommended Next Step

The next justified pass is not a formatter rewrite.

The best next step is a narrow ST-side adaptation check with real chat generation on the same two models:

- keep current baseline as production default
- optionally add an experiment-only formatter switch
- rerun a smaller live pass on:
  - one broad relationship scenario
  - one local-scene scenario
  - one vague follow-up scenario

That would show whether the small model-specific difference is still visible once the full chat prompt, not just the isolated injected memory block, is in play.
