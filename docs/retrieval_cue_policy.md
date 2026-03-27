# Retrieval Cue Policy

## Scope

The Russian relationship cue layer is a narrow robustness channel for broad relationship and general-state phrasing such as:

- `Он всё ещё злится на неё?`
- `Они уже помирились или нет?`
- `Как он теперь к ней относится?`
- `Что у них сейчас вообще?`

It exists to reduce wording-sensitive misses for relationship summary/stable memories. It is not a general semantic retrieval layer.

## Allowed Cue Groups

Current v1 groups:

- `conflict`
- `repair`
- `trust`
- `distance`
- `together`
- `attitude`

These groups are intentionally load-bearing and limited. The cue layer should stay explainable and easy to audit.

## Guardrails

- The cue layer activates only for relationship/general-state query families.
- Main lexical/entity overlap remains the primary ranking signal.
- Cue overlap and relationship support bonus are auxiliary signals only.
- Local episodic relevance must still win when it is clearly the better match.
- Non-relationship factual/profile queries must not activate relationship mode spuriously.

## Extension Policy

- Add a new pattern only inside an existing group and only with eval-backed justification.
- Add a new cue group only with a dedicated long-chat/eval scenario showing why the existing groups are insufficient.
- Any cue-layer expansion should come with regression coverage in:
  - retrieval eval cases
  - targeted robustness tests

If a proposed cue change cannot be defended through a concrete failing scenario, it should not be added.
