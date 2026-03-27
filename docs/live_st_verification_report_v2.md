# Live SillyTavern Verification Report v2

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
  - Russian relationship robustness fixes from PR 30 present
  - relationship cue guardrails from the follow-up PR present
- Verification method:
  - live ST page loaded in headless Chrome
  - scenarios executed through the real ST browser runtime and extension event hooks
  - audit records collected from `memoryServiceAudit.getRecentAudits()`

This was still not treated as a benchmark. The goal was practical confidence in the live ST loop after the Russian relationship retrieval fixes.

## Scenarios

### 1. Relationship status wording

- User prompt: `Он всё ещё на неё злится или они уже помирились?`
- Expected behavior:
  - summary and stable relationship context should survive threshold
  - old conflict should not dominate alone
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=1 stable=1 episodic=1`
  - prompt chars: `305`
  - trim: none
- Verdict: improved
- Practical read:
  - this was the main weak spot in the first live run
  - after the retrieval robustness pass the live runtime now keeps a full three-layer mix
  - the memory block includes both the rolling summary and the stable relationship line instead of only one stable fragment

### 2. Relationship attitude wording

- User prompt: `Как он теперь к ней относится?`
- Expected behavior:
  - broad attitude phrasing should still surface summary + stable relationship context
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=1 stable=1 episodic=1`
  - prompt chars: `305`
  - trim: none
- Verdict: improved
- Practical read:
  - this wording family was part of the targeted Russian robustness work
  - the live runtime handled it cleanly and kept the same useful relationship composition as the status phrasing

### 3. Broad relationship state

- User prompt: `Что у них сейчас вообще?`
- Expected behavior:
  - summary + stable should still reach the prompt
  - budget policy should keep the composition compact
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned: `summary=1 stable=1 episodic=2`
  - injected: `summary=1 stable=1 episodic=1`
  - prompt chars: `289`
  - trim: `layer_cap:episodic`
- Verdict: unchanged to slightly improved
- Practical read:
  - broad vague phrasing stayed strong after the relationship fixes
  - the live runtime now also showed budget pressure more clearly: extra episodic noise was trimmed while the summary + stable core survived

### 4. Working-together phrasing

- User prompt: `Они снова нормально работают вместе?`
- Expected behavior:
  - together/repair/trust cues should keep summary context from dropping out
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned: `summary=1 stable=1 episodic=2`
  - injected: `summary=1 stable=1 episodic=1`
  - prompt chars: `305`
  - trim: `layer_cap:episodic`
- Verdict: improved
- Practical read:
  - this wording family was not covered in the first live report
  - the live runtime now keeps the same sensible long-chat core as the broad relationship-state scenario
  - the budget policy trims overflow, but not the summary/stable backbone

### 5. Local fresh scene

- User prompt: `Что они решили про встречу по проекту?`
- Expected behavior:
  - fresh episodic scene should stay first
  - relationship robustness should not crowd out the local turn
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned: `summary=0 stable=0 episodic=2`
  - injected: `summary=0 stable=0 episodic=1`
  - prompt chars: `69`
  - trim: `layer_cap:episodic`
- Verdict: unchanged on composition, still slightly weak on specificity
- Practical read:
  - local scene behavior did not regress into summary-heavy retrieval
  - the injected block stayed episodic-only, which is the right direction
  - but the surviving episodic line was compact and not obviously as rich as the best stored local-scene memory, so there is still some retrieval/store hygiene weakness around scene-specific precision

## Comparison vs Previous Live Run

- Improved:
  - relationship/general-state phrasing is materially better in the live runtime now
  - the first report had `summary=0 stable=1 episodic=0` for the relationship-arc wording; this run produced `summary=1 stable=1 episodic=1`
  - attitude phrasing also worked with the same full relationship composition
- Unchanged but still good:
  - current-turn injection remained real in every scenario
  - broad vague Russian follow-up still produced a strong layered mix
  - summary did not start crowding out local episodic retrieval
- Newly visible:
  - budget trimming now showed up under richer candidate sets
  - the trim behavior matched the intended policy: episodic overflow was cut first, while summary + stable stayed intact
- No obvious regression found:
  - no scenario fell back to delayed-only injection
  - no relationship scenario showed runaway cue overfitting that suppressed all episodic context

## What Improved

- Russian relationship status wording is no longer the main live bottleneck.
- Broad attitude phrasing now works in the real ST runtime, not only in isolated evals.
- Working-together phrasing now keeps the intended long-chat core in the prompt.
- Prompt budget behavior looked sensible under relationship-heavy retrieval pressure.

## What Still Looks Weak

- Local scene precision is still not perfect.
  - The retrieval stayed episodic-first, which is correct, but the surviving injected episodic line was shorter and less concrete than ideal.
- This validation still covers the memory loop more than final model prose quality.
  - It confirms what reached the prompt in the real runtime, not a broad provider-agnostic answer quality benchmark.

## Recommended Next Step

The next justified step is not summary v2 and not a larger cue expansion. The clearest next target is a narrower quality pass on local-scene precision:

- reduce low-value or query-echo episodic memories surviving into retrieval
- keep the current relationship robustness gains intact
- rerun this same live ST verification flow after that narrower cleanup
