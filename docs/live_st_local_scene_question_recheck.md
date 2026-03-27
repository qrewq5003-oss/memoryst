# Live ST Local-Scene Question Guardrail Re-Check

Date: 2026-03-27

## Setup

- Runtime: real SillyTavern 1.15.0 at `http://127.0.0.1:8001`
- Memory Service: local backend at `http://127.0.0.1:8010`
- Extension mode:
  - `auditEnabled: true`
  - current-turn injection enabled
  - layered retrieval enabled
  - rolling summary + refresh policy enabled
  - prompt budget defaults enabled
  - durable relationship formation + guardrails present
  - question-form relationship guard present
  - question-form local-scene guard present

This was a focused re-check, not a new benchmark pass.

## Seed Scope

Main scope: `pr40-local-scene-recheck-1774592791`

Seed arc included:

- trust / cooperation
- conflict
- partial repair
- working together again
- support moment
- one recent project-meeting decision
- additional recent scene lines

The seed intentionally still contained user-side question phrasing like:

- `Они хоть немного помирились после разговора?`
- `Что они решили по ближайшей рабочей встрече?`
- `Был момент, когда он её поддержал?`

The goal was to confirm that these prompts no longer survive as stored memories.

## Stored-Memory Sanity

Stored snapshot after the live seed:

- `summary=0`
- `stable relationship=4`
- `episodic=2`

Surviving memories:

- stable:
  - `В начале поездки Маркус снова доверяет Алисе...`
  - `Им всё равно пришлось снова работать вместе.`
  - `После тяжёлого разговора они частично помирились, но Маркус всё ещё держит дистанцию.`
  - `Вечером они договорились не возвращаться к старой ссоре до конца поездки.`
- episodic:
  - `После провала на съёмке Маркус сорвался на Алису...`
  - `Сегодня утром они решили перенести ближайшую рабочую встречу...`

Central sanity result:

- no question-form user prompt survived as `relationship/stable`
- no question-form user prompt survived as `event/episodic`

Examples that did not appear in the stored scope:

- `Они хоть немного помирились после разговора?`
- `Был момент, когда он её поддержал?`
- `Что они решили по ближайшей рабочей встрече?`

Manual rolling summary trigger on this scope still returned `skipped_not_enough_inputs`.

Practical read:

- the new local-scene question guard removed the noisy episodic line
- but this also left only two surviving episodic memories in the checked scope
- so this pass did not produce a fresh rolling summary

## Checked Scenarios

Live ST retrieve calls were confirmed in the real runtime by backend `POST /memory/retrieve` traffic.

For exact composition, the snapshot below uses the live-seeded scope plus the same retrieval service and ST budget helper that the extension uses. This was more reliable than the page-level audit helper in this focused pass, which kept surfacing stale records.

### 1. Broad relationship state

- Prompt: `Что у них сейчас вообще?`
- Expected:
  - broad path should not be empty
  - no question-form junk should appear
- Retrieved:
  - `summary=0 stable=3 episodic=1`
- Injected after ST budget:
  - `summary=0 stable=2 episodic=1`
  - trimmed: `stable=1`
  - prompt chars: `322`
- Injected mix:
  - conflict episodic
  - `Вечером они договорились не возвращаться к старой ссоре...`
  - `В начале поездки Маркус снова доверяет Алисе...`
- Verdict: improved

### 2. Attitude / state

- Prompt: `Как он теперь к ней относится?`
- Expected:
  - broad relationship path should still work
  - no question-form junk should dominate
- Retrieved:
  - `summary=0 stable=3 episodic=1`
- Injected after ST budget:
  - `summary=0 stable=2 episodic=1`
  - trimmed: `stable=1`
  - prompt chars: `322`
- Injected mix:
  - conflict episodic
  - `Вечером они договорились не возвращаться к старой ссоре...`
  - `В начале поездки Маркус снова доверяет Алисе...`
- Verdict: improved

### 3. Local control

- Prompt: `Что они решили по ближайшей рабочей встрече?`
- Expected:
  - concrete meeting outcome should survive
  - question-form local-scene prompt itself should not be stored
- Retrieved:
  - `summary=0 stable=3 episodic=2`
- Injected after ST budget:
  - `summary=0 stable=2 episodic=1`
  - trimmed: `stable=1 episodic=1`
  - prompt chars: `322`
- Injected mix:
  - conflict episodic
  - two stable relationship lines
- Practical problem:
  - the concrete meeting-outcome episodic was retrieved
  - but it was trimmed out by the final budgeted composition
- Verdict: still weak

## Observations

- The question-form episodic artifact is gone at the store layer.
- The broad relationship path is no longer empty on this live-seeded scope.
- The surviving broad mix is still skewed:
  - no fresh summary
  - conflict episodic still ranks first
  - stable relationship context helps, but the overall composition is not yet ideal
- Local control is the remaining problem in this scope:
  - the useful meeting-outcome episodic still exists
  - but final injected composition keeps the conflict episodic instead

## Did The Episodic Artifact Disappear?

Yes.

The checked live scope did not contain new low-value question-form memories in either:

- `relationship/stable`
- `event/episodic`

That was the main target of this guardrail, and it held in the real runtime seed.

## What Improved

- question-form local-scene user prompts no longer polluted the episodic pool
- broad relationship prompts were not empty on the checked scope
- summary path was no longer polluted by the specific question-form episodic artifact from the previous re-check

## What Still Looks Weak

- the scope did not produce enough surviving episodic memories to build a fresh rolling summary
- broad prompts still leaned on a conflict episodic line plus two stable lines
- local control still lost the concrete meeting outcome at final budget selection time

## Recommended Next Step

The next justified step is not another wide retrieval pass.

The clearest follow-up is a narrow selection pass for episodic conflict dominance inside mixed long-arc scopes:

- keep the stored-memory guardrails as they are
- improve which episodic survives when local-scene and broad relationship signals compete
- rerun one broad relationship prompt and one local control prompt on the same kind of live scope
