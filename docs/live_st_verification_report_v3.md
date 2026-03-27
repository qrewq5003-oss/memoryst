# Live SillyTavern Verification Report v3

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
  - Russian relationship robustness fixes present
  - local-scene precision fixes and local-scene guardrails present
- Verification method:
  - real ST page loaded in headless Chrome
  - scenarios executed through the live browser runtime and extension event hooks
  - audit records collected from `memoryServiceAudit.getRecentAudits()`
  - each scenario used a two-turn flow:
    - seed turn to store the scene
    - follow-up turn to measure live retrieval/injection on the same `chat_id/character_id`

This was still a practical runtime verification pass, not a benchmark.

## Scenarios

### 1. Project meeting decision

- User prompt: `Что они решили про встречу по проекту?`
- Expected behavior:
  - surviving episodic should be a concrete decision line
  - query-echo or generic episodic should not win
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=0 stable=0 episodic=1`
  - prompt chars: `100`
  - trim: none
  - injected preview:
    - `[EPISODIC] Они решили перенести встречу по проекту на утро и позвать Лену позже.`
- Verdict: improved
- Practical read:
  - the live runtime now injected the concrete decision outcome line instead of a short query-like episodic fragment
  - budget policy did not cut the only useful local-scene memory

### 2. Detailed meeting outcome

- User prompt: `Что произошло на встрече с Леной?`
- Expected behavior:
  - detailed meeting outcome should beat a generic meeting mention
  - surviving episodic should stay concrete
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=0 stable=0 episodic=1`
  - prompt chars: `132`
  - trim: none
  - injected preview:
    - `[EPISODIC] На встрече с Леной договорились сдвинуть монтаж, поручить ей материалы и вернуться к бюджету вечером.`
- Verdict: improved
- Practical read:
  - this is the clearest positive result of the local-scene pass
  - the injected episodic memory is specific, outcome-heavy, and clearly more useful than a generic “была встреча” line

### 3. Recent saying / reply

- User prompt: `Что она сказала ему вчера после разговора?`
- Expected behavior:
  - fresh saying/reply scene should stay first
  - budget should keep it instead of trimming it away
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=0 stable=0 episodic=1`
  - prompt chars: `122`
  - trim: none
  - injected preview:
    - `[EPISODIC] Алиса сказала Маркусу, что не хочет снова ссориться и готова обсудить всё спокойно вечером.`
- Verdict: improved
- Practical read:
  - the live runtime kept the actual reply content, not a vague aftermath line
  - this is the right shape for local-scene continuity in Russian RP chats

### 4. Broad relationship control

- User prompt: `Что у них сейчас вообще?`
- Expected behavior:
  - relationship/general-state path should still work
  - local-scene changes should not break broad prompts
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = false`
  - returned/injected: `summary=0 stable=0 episodic=0`
  - prompt chars: `0`
  - trim: none
- Verdict: still weak
- Practical read:
  - on this minimal two-turn seed, the live runtime still did not surface a useful broad relationship memory block
  - this does not look like a regression caused by the local-scene pass
  - it looks closer to a data-shape issue: the seed exchange produced episodic memories, but not enough durable relationship/summary context for the broad control prompt

## Comparison vs v2

- Improved:
  - local-scene prompts now produced clearly concrete injected episodic memories
  - the earlier weak project-meeting behavior was replaced by a specific decision outcome line
  - detailed meeting and saying/reply prompts both kept high-value episodic content
- Unchanged in a good way:
  - current-turn injection remained real
  - prompt budget did not over-trim the single useful episodic item
  - no sign that local-scene logic started flooding broad prompts with generic episodic noise
- Still weak:
  - the broad relationship control prompt remained weak in this small live setup
  - this looks more like insufficient durable memory material in the seeded chat than a new local-scene regression

## What Improved

- Surviving local-scene episodic memories are more concrete in the live ST runtime.
- Query-echo style local episodic noise did not surface in the observed local-scene prompts.
- Budget policy stayed compatible with local-scene precision: no useful episodic item was trimmed away in these scenarios.

## What Still Looks Weak

- Broad relationship/general-state validation is still sensitive to how much durable context exists in the seeded live chat.
- This report confirms prompt composition in the live loop, but it is still not a large-scale answer quality benchmark.

## Recommended Next Step

The next justified step is not another local-scene heuristic expansion. The stronger next move is a small practical pass on live-seeded durable context quality:

- make it easier to build stable/summary-worthy relationship context from realistic multi-turn chats
- rerun the broad relationship control scenario with a richer seeded arc
- keep the current local-scene precision behavior unchanged unless a new live miss appears
