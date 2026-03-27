# Durable Relationship Policy

## Scope

The durable relationship formation layer is a narrow store/extractor policy for long-chat Russian relationship carry-over.

It exists so statements like:

- `Маркус снова доверяет Алисе в работе`
- `между ними всё ещё остаётся напряжение`
- `они снова работают вместе`
- `Маркус поддерживает Алису перед командой`

can become `relationship/stable` memories more reliably.

It is not a general relationship semantics parser.

## Allowed State Families

Current v1 families:

- `trust / distrust shift`
- `distance / caution / lingering tension`
- `repair / partial reconciliation`
- `support / protection / backing each other`
- `working together / renewed cooperation`

These families are intentionally bounded and load-bearing.

## Guardrails

- The layer is carry-over oriented, not scene oriented.
- One-off conflict bursts should stay `event/episodic`.
- One-off meetings and local help scenes should stay `event/episodic` unless there is explicit carry-over wording.
- Broad profile facts must not become `relationship/stable`.
- This layer complements rolling summary; it does not replace it.

## Episodic Blockers

The policy uses explicit blockers for scene-like phrasing such as:

- flare-up conflict wording
- one-off quarrel wording
- scene-local meeting/action wording

These blockers exist to stop relationship overcapture in the store path.

## Extension Policy

- Add new patterns only inside an existing family when there is a concrete failing scenario covered by tests or evals.
- Add a new family only with a dedicated long-chat scenario proving the current families are insufficient.
- Do not add patterns for one-off misses without regression coverage.

If a proposed rule cannot be defended through a repeatable store/eval miss, it should not be added.
