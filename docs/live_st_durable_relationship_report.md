# Live SillyTavern Durable Relationship Verification

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
  - relationship robustness + guardrails present
  - local-scene precision + guardrails present
  - durable relationship formation + guardrails present
- Verification method:
  - live ST page driven through the real browser runtime
  - seed arc written through actual `CHARACTER_MESSAGE_RENDERED` hooks
  - retrieval executed through the live pre-generation hook
  - integration audit finalized through the real post-render hook

This was a practical runtime verification pass, not a benchmark.

## Seed Arc Design

Main live scope: `pr36-durable-main-1774589209`

Seeded relationship arc:

- trust and cooperation at the start of the trip
- one open conflict scene
- distance and lingering tension
- forced collaboration on montage
- partial repair after a hard conversation
- support/protection in front of the team
- recent project meeting decision
- recent reply after the hard conversation
- renewed working-together state
- one additional meeting-outcome scene to push summary creation

Overcapture control scope: `pr36-durable-overcapture-1774589209`

- one-off conflict scene
- one-off meeting scene
- one-off help scene without carry-over wording

Practical store snapshot after the main seed:

- `summary=1`
- `stable relationship=5`
- `episodic=3`

Manual rolling summary on the main scope:

- `action=created`
- `summarized_count=3`
- `new_input_count=3`

## Scenarios

### 1. Broad relationship state

- User prompt: `Что у них сейчас вообще?`
- Expected behavior:
  - broad relationship prompt should lift durable context, ideally `summary + stable`
- Audit result:
  - returned: `summary=1 stable=0 episodic=0`
  - injected: `summary=1 stable=0 episodic=0`
  - prompt chars: `135`
  - trim: none
- Verdict: partly worked
- Practical read:
  - the prompt is no longer empty
  - durable context reached the prompt, but only through summary

### 2. Attitude phrasing

- User prompt: `Как он теперь к ней относится?`
- Expected behavior:
  - summary and/or stable relationship memory should survive
- Audit result:
  - returned: `summary=1 stable=0 episodic=0`
  - injected: `summary=1 stable=0 episodic=0`
  - prompt chars: `135`
  - trim: none
- Verdict: partly worked
- Practical read:
  - still useful, but not yet a true `summary + stable` mix

### 3. Reconciliation status

- User prompt: `Он всё ещё злится на неё или они уже помирились?`
- Expected behavior:
  - repair/tension state should surface instead of a stale conflict fragment
- Audit result:
  - returned: `summary=1 stable=2 episodic=0`
  - injected: `summary=1 stable=2 episodic=0`
  - prompt chars: `286`
  - trim: none
- Verdict: worked
- Practical read:
  - this is the clearest live gain from the durable relationship formation pass
  - broad repair/tension phrasing now carries both summary and stable relationship context

### 4. Working together

- User prompt: `Они снова нормально работают вместе?`
- Expected behavior:
  - cooperation/support state should surface as durable context
- Audit result:
  - returned: `summary=1 stable=1 episodic=0`
  - injected: `summary=1 stable=1 episodic=0`
  - prompt chars: `249`
  - trim: none
- Verdict: worked
- Practical read:
  - live runtime now has a real `summary + stable` cooperation mix for this phrasing

### 5. Local control

- User prompt: `Что они решили про встречу по проекту?`
- Expected behavior:
  - fresh episodic should stay episodic-first
  - durable relationship formation must not swallow local-scene behavior
- Audit result:
  - returned: `summary=1 stable=0 episodic=0`
  - injected: `summary=1 stable=0 episodic=0`
  - prompt chars: `135`
  - trim: none
- Verdict: regressed
- Practical read:
  - on this heavy relationship scope, the local-scene question did not recover the concrete meeting outcome
  - the surviving injected memory was summary-only

## Scene Overcapture Check

Overcapture control scope outcome:

- no `relationship/stable` memories were formed
- retained memories stayed `event/episodic`
- one-off conflict and one-off meeting scenes did not leak into stable relationship carry-over

So there was no obvious scene overcapture from the new durable-formation gate itself.

However, a different low-value capture issue showed up in the main seeded scope:

- `Был момент, когда он её поддержал?`
- `Они хоть немного помирились после разговора?`

Both survived as `relationship/stable` memories even though they are user-side question phrasing, not good durable carry-over statements. This is not classic scene overcapture, but it is still a practical store-side quality issue.

## Comparison vs Previous Relationship Arc Run

- Improved versus the previous richer-arc run:
  - broad relationship retrieval is no longer summary-only in all important cases
  - reconciliation and working-together prompts now lift a real `summary + stable` mix in live ST
- Improved versus the earlier weak relationship finding:
  - broad prompts no longer collapse into empty or stale conflict-only retrieval
- Not improved on local control:
  - the heavy durable relationship context can now overshadow the specific project-meeting episodic question in this setup
- Summary refresh looks sufficient:
  - once the third episodic scene was added, summary creation behaved as expected
  - the bigger limitation is no longer refresh thresholding

## What Worked

- Durable relationship formation had a real live effect.
- Useful stable relationship context appeared beside summary for broad repair/cooperation prompts.
- Prompt budget stayed compact and never trimmed away useful durable context.
- Overcapture control did not produce false stable relationship memories from one-off scenes alone.

## What Still Looks Weak

- Broad relationship state and attitude prompts still fell back to summary-only in this run.
- Local-scene control regressed on the heavy relationship scope.
- The main seeded scope contained low-value stable relationship memories created from question-like user phrasing.

## Recommended Next Step

The next justified step is not summary v2 or another retrieval-wide rewrite.

The clearest follow-up is a narrow store/extractor guardrail pass:

- prevent question-form user prompts from being persisted as durable `relationship/stable` memories
- preserve the new live `summary + stable` gains for reconciliation and cooperation prompts
- then rerun this same live verification with one broad relationship prompt and one local control prompt
