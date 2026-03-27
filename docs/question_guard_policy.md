# Question Guard Policy

## Scope

The question-form guard is a narrow anti-artifact filter in the store/extractor path.

It exists only to stop raw user prompts from being persisted as low-value memories.

It is not:

- a semantic classifier
- a retrieval feature
- a generic prompt-understanding layer

## Allowed Families

Current v1 scope is limited to:

- relationship question prompts
- local-scene question prompts

No other question families are in scope by default.

## User-Role-Only Rule

The guard applies only to `role="user"`.

That rule is load-bearing:

- assistant text should not be filtered by these guards by default
- narration/system-like content should not be filtered by these guards by default

If a new case needs broader filtering, it should be treated as a separate policy decision, not as a silent extension of the current guard.

## What The Guard Does

- blocks question-form user prompts from being stored as `relationship/stable`
- blocks question-form user prompts from being stored as low-value `event/episodic`

## What The Guard Does Not Do

- it does not rank retrieval candidates
- it does not classify broad semantics for the rest of the pipeline
- it does not replace the existing relationship or local-scene heuristics

## Extension Policy

Expand this guard only when all three are true:

- there is a concrete live/runtime artifact
- there is a regression test for that artifact
- there is a narrow written justification for the new guarded family

If a proposed extension does not meet those conditions, it should not be added.
