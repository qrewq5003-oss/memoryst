# Live SillyTavern Relationship Arc Verification

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
  - relationship robustness fixes + guardrails present
  - local-scene precision fixes + guardrails present
- Verification method:
  - live ST page driven through the real browser runtime
  - seed arc written through actual ST `CHARACTER_MESSAGE_RENDERED` hooks
  - broad prompts executed through actual pre-generation retrieval
  - audit captured from `memoryServiceAudit.getRecentAudits()`

This was still a practical runtime check, not a benchmark.

## Seeded Arc Design

The seed chat used one `chat_id/character_id` scope and deliberately walked through a broader relationship arc:

- initial cooperation and trust
- open quarrel after a production failure
- distance and caution
- forced collaboration to save the film
- partial repair after a hard conversation
- working together again on montage and festival planning
- two additional recent scenes where Marcus supported Alice and they aligned on the trip plan

Practical observation from the live store path:

- the runtime did not retain the whole arc as many distinct memories
- after the richer seed, the backend still converged to `3` surviving episodic memories in that scope
- manual rolling summary generation then created one summary over those `3` episodic memories

Manual summary lifecycle check on the same live scope:

- first run:
  - `action=created`
  - `summarized_count=3`
  - `new_input_count=3`
- after two more live relationship turns:
  - `action=skipped_not_enough_new_inputs`
  - `new_input_count=0`

Practical read:

- the refresh policy itself behaved consistently
- the real bottleneck was upstream: extra live turns updated the same surviving episodic memories instead of creating new summary inputs

## Scenarios

### 1. Relationship status

- User prompt: `Он всё ещё злится на неё или они уже помирились?`
- Expected behavior:
  - richer arc should let durable relationship context reach the prompt
- Audit result:
  - returned/injected: `summary=1 stable=0 episodic=0`
  - prompt chars: `138`
  - trim: none
- Verdict: worked
- Practical read:
  - compared with the early weak live run, the richer arc now gives a real durable relationship block instead of empty or unstable retrieval
  - the durable context came entirely from the summary layer

### 2. Broad state

- User prompt: `Что у них сейчас вообще?`
- Expected behavior:
  - broad relationship state should no longer collapse on a richer seeded arc
- Audit result:
  - returned/injected: `summary=1 stable=0 episodic=0`
  - prompt chars: `138`
  - trim: none
- Verdict: worked
- Practical read:
  - this is the clearest change versus the minimal-seed failure from v3
  - richer arc plus a live summary was enough for broad state retrieval to become useful

### 3. Attitude phrasing

- User prompt: `Как он теперь к ней относится?`
- Expected behavior:
  - attitude phrasing should pick up durable relationship context
- Audit result:
  - returned/injected: `summary=1 stable=0 episodic=0`
  - prompt chars: `138`
  - trim: none
- Verdict: worked
- Practical read:
  - broad attitude phrasing remained stable on the richer arc
  - again, the summary carried the load more than any separate stable relationship memory

### 4. Working together

- User prompt: `Они снова нормально работают вместе?`
- Expected behavior:
  - working-together phrasing should surface the repaired relationship state
- Audit result:
  - returned/injected: `summary=1 stable=0 episodic=0`
  - prompt chars: `138`
  - trim: none
- Verdict: worked
- Practical read:
  - the live runtime now handles this broad phrasing reliably on the richer arc
  - the result is compact and durable, but still summary-only

## Comparison vs Earlier Live Runs

- Improved vs the first weak relationship finding:
  - the relationship path no longer collapses when the chat has enough arc buildup
  - broad prompts now consistently inject a usable durable memory block
- Improved vs v3:
  - the weak broad-control result in the minimal local-scene setup was not a local-scene regression
  - richer arc changed live behavior materially
- Still limited versus v2:
  - v2 showed a richer `summary + stable + episodic` relationship mix
  - this richer-arc run produced a reliable summary, but not a comparable stable relationship layer beside it

## What Worked

- Richer live arc plus manual rolling summary generation was enough to make broad relationship prompts consistently useful.
- Current-turn injection stayed correct.
- Prompt budget did not interfere because the injected relationship block stayed compact.
- The broad relationship weakness from the earlier minimal-seed run looks explained, not mysterious.

## What Still Looks Weak

- Durable relationship context currently lives mostly as summary, not as `summary + stable` composition.
- Extra live turns tended to update the same surviving episodic memories instead of creating new summary inputs.
- That means the current refresh policy is not the main blocker in practice; the stronger limitation is durable relationship formation upstream of summary refresh.

## Recommended Next Step

The next justified step is not summary v2 yet. The clearest next move is a narrower pass on durable relationship formation:

- understand why richer relationship arcs converge to so few surviving episodic memories
- make stable relationship memory formation more reliable in realistic long Russian chats
- rerun this same broad relationship verification after that narrower durability pass
