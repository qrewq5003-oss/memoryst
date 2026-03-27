# Live ST Question-Guardrail Re-Check

Date: 2026-03-27

## Setup

- Runtime: real SillyTavern 1.15.0 at `http://127.0.0.1:8001`
- Memory Service: local backend at `http://127.0.0.1:8010`
- Extension mode:
  - `auditEnabled: true`
  - current-turn injection enabled
  - layered retrieval enabled
  - summary layer + refresh policy enabled
  - prompt budget defaults enabled
  - durable relationship formation + guardrails present
  - question-form durable relationship guardrail present
- Verification method:
  - live ST page driven through the real browser runtime
  - seed arc written through actual `CHARACTER_MESSAGE_RENDERED` hooks
  - retrieve stage captured through the live pre-generation hook
  - audit finalized through the real post-render hook in throwaway scopes

This was a focused re-check, not a new benchmark pass.

## Seed Scope

Main scope: `pr38-question-guardrail-main-1774590799`

Seed arc:

- trust / cooperation
- conflict
- distance / tension
- working together again
- one recent project-meeting decision
- one additional recent scene to allow rolling summary creation

Stored snapshot after seeding and summary creation:

- `summary=1`
- `stable relationship=3`
- `episodic=3`

Important sanity result:

- no question-form stable relation memories were stored
- the previously bad examples disappeared:
  - `Они хоть немного помирились после разговора?`
  - `Был момент, когда он её поддержал?`

## Scenarios

### 1. Broad relationship state

- User prompt: `Что у них сейчас вообще?`
- Expected behavior:
  - useful relationship context should reach the prompt
- Audit result:
  - returned: `summary=0 stable=0 episodic=0`
  - injected: `summary=0 stable=0 episodic=0`
  - prompt chars: `0`
  - trim: none
- Verdict: still weak

### 2. Attitude phrasing

- User prompt: `Как он теперь к ней относится?`
- Expected behavior:
  - broad relationship path should still work after the guardrail
- Audit result:
  - returned: `summary=0 stable=0 episodic=0`
  - injected: `summary=0 stable=0 episodic=0`
  - prompt chars: `0`
  - trim: none
- Verdict: still weak

### 3. Local control

- User prompt: `Что они решили про встречу по проекту?`
- Expected behavior:
  - useful episodic should survive
  - local control should no longer sink into summary-only because of junk stable memories
- Audit result:
  - returned: `summary=0 stable=0 episodic=1`
  - injected: `summary=0 stable=0 episodic=1`
  - prompt chars: `114`
  - trim: none
- Verdict: fixed
- Practical read:
  - local-scene path recovered the concrete meeting outcome
  - no stable junk interfered with the injected result

## Observations

- The question-form durable-relationship artifact is gone in the stored scope.
- The local control prompt improved in the intended direction.
- Current-turn injection and budget behavior remained normal.
- Broad relationship prompts did not recover in this focused re-check, even though the scope now had:
  - one rolling summary
  - three stable relationship memories

Practical read:

- the guardrail fix solved the store-quality artifact it targeted
- but broad relationship behavior in this seeded scope still looks brittle
- the stored summary itself remained noisy because it still included one question-like episodic memory:
  - `Что они решили по ближайшей рабочей встрече?`

## Did The Artifact Disappear?

Yes.

The focused live re-check did not produce new stable relationship memories that were just user-side questions. The exact previously bad pattern disappeared from the stored stable layer in the checked scope.

## What Improved

- question-form stable-memory artifact disappeared
- local control no longer fell back to useless summary-only in this run

## What Still Looks Weak

- broad relationship state prompt stayed empty
- attitude phrasing stayed empty
- summary quality is still noisy when episodic extraction keeps question-like scene lines

## Recommended Next Step

The next justified step is a narrow episodic/store guardrail pass for question-form local-scene lines, not a new retrieval-wide rewrite.

The practical target:

- stop question-form user prompts like `Что они решили по ближайшей рабочей встрече?` from surviving as episodic memories
- keep the real declarative scene outcome
- then rerun one broad relationship prompt and one local control prompt in the same live setup
