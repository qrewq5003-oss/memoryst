# Local Scene Policy

## Scope

The local-scene layer is a narrow Russian episodic precision channel for queries such as:

- `Что они решили про встречу по проекту?`
- `Что она сказала ему вчера после разговора?`
- `На что они договорились утром?`
- `Что произошло на встрече с Леной?`

Its job is limited:

- prefer a concrete episodic scene outcome over a low-value query echo
- keep local episodic retrieval useful inside the layered policy
- avoid turning episodic selection into a generic event-semantics system

It is not a general semantic retrieval layer.

## Allowed Intent Families

Current v1 intent families:

- `decision / agreement`
- `saying / reply / statement`
- `meeting outcome`
- `recent concrete scene outcome`

These families are intentionally small and load-bearing.

## Guardrails

- The layer activates only for local-scene query families.
- Main lexical/entity overlap remains the primary ranking signal.
- `episodic_specificity_bonus` is a narrow support signal, not a universal episodic boost.
- `episodic_low_value_penalty` is an anti-noise signal for query-echo style lines, not a general episodic penalty.
- Broad relationship/general-state queries must not activate local-scene mode spuriously.
- Generic factual/profile queries must not receive local-scene bonuses or penalties.
- Useful summary/stable context should still survive when layered retrieval expects a mixed composition.

## Extension Policy

- Add a new pattern only inside an existing intent family and only with eval-backed justification.
- Add a new intent family only with a dedicated regression/eval scenario showing why the current families are insufficient.
- Do not add patterns for one-off phrasing misses without a repeatable practical failure case.

Any local-scene policy expansion should come with:

- a targeted local-scene precision test
- a retrieval eval case when the change affects practical ranking behavior

If the change cannot be defended through a concrete repeated miss, it should not be added.
