# Live SillyTavern Verification Report

Date: 2026-03-27

## Setup

- Runtime: real SillyTavern 1.15.0 at `http://127.0.0.1:8001`
- Memory Service: local backend at `http://127.0.0.1:8010`
- Extension mode:
  - `auditEnabled: true`
  - current-turn injection enabled
  - grouped settings / prompt budget defaults enabled
- Verification method:
  - live ST page loaded in headless Chromium
  - scenarios executed through the real ST browser runtime and extension event hooks
  - audit records collected from `memoryServiceAudit.getRecentAudits()`

This was not treated as a benchmark. The goal was practical confidence in the live ST loop: retrieve timing, prompt injection, scope, and prompt composition.

## Small Fixes Found During Live Run

1. `sillytavern-extension/index.js`
   - Fixed third-party extension import paths so the extension actually initializes under ST's `third-party/<name>` layout.
2. `app/main.py`
   - Added CORS middleware so the browser-side ST extension can reach the Memory Service over localhost.

Without these two fixes the live runtime could not exercise the intended loop.

## Scenarios

### 1. Relationship arc

- User prompt: `Он всё ещё на неё злится или они уже помирились?`
- Expected behavior:
  - summary helps
  - stable relation context helps
  - fresh arc signal stays available
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=0 stable=1 episodic=0`
  - prompt chars: `121`
  - trim: none
- Outcome: partly worked
- Practical read:
  - current-turn injection worked
  - the runtime picked a stable relation line and inserted it
  - summary and episodic context did not clear threshold in this wording

### 2. Ongoing goal through noise

- User prompt: `Чего Алина сейчас хочет добиться с фильмом?`
- Expected behavior:
  - summary survives scene noise
  - stable goal context survives
  - noisy episodic details do not take over
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=1 stable=1 episodic=0`
  - prompt chars: `213`
  - trim: none
- Outcome: worked
- Practical read:
  - this is the clearest strong case from the live run
  - summary + durable stable fact reached the prompt together
  - noisy episodic memories stayed out

### 3. Local fresh scene

- User prompt: `Что они решили про встречу по проекту?`
- Expected behavior:
  - fresh episodic scene should matter immediately
  - summary should not crowd out the local turn
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=0 stable=0 episodic=1`
  - prompt chars: `122`
  - trim: none
- Outcome: worked
- Practical read:
  - the fresh episodic memory got through cleanly
  - summary/stable did not join this query
  - for local scene questions the current policy does not appear to over-favor summary

### 4. Vague Russian follow-up

- User prompt: `А что у них сейчас вообще?`
- Expected behavior:
  - recent messages help
  - summary + stable context stay available
  - one episodic reminder may still join
- Audit result:
  - `retrieve_called = true`
  - `applied_to_current_turn = true`
  - returned/injected: `summary=1 stable=1 episodic=1`
  - prompt chars: `289`
  - trim: none
- Outcome: worked
- Practical read:
  - this was the best long-chat continuity mix from the live run
  - the runtime kept the rolling summary, one stable relation fact, and one episodic reminder
  - this is the strongest evidence that the layered policy is paying off in ST, not only in isolated tests

## What Worked

- Current-turn retrieval/injection is real now.
  - In all four scenarios `retrieve_called = true` and `applied_to_current_turn = true`.
- The browser-side audit loop is practically useful.
  - It was enough to catch both the broken extension import path and the missing backend CORS.
- Long-chat summary/stable behavior is already useful on broad Russian prompts.
  - The goal scenario and the vague follow-up scenario were the strongest results.
- Local fresh scene behavior is not being drowned by summary.
  - The project-meeting scenario stayed episodic-first.

## What Still Looks Weak

- Russian relation phrasing is still brittle.
  - In the relationship-arc scenario the system injected only one stable line; the summary did not join.
- Summary participation is still wording-sensitive.
  - It works well on broad goal/follow-up prompts, but not every relation-style prompt pulls it in.
- These live scenarios did not stress the prompt budget hard enough to trigger trimming.
  - The observed injected prompt sizes stayed between `121` and `289` chars, below the default `520`.
- This report validates the live prompt path more than final model prose quality.
  - The verification used the real ST runtime and real extension hooks, but it was not a benchmark of model answer quality across providers.

## Recommended Next Step

The next justified step is not new UI work and not summary v2. The clearest next target is Russian retrieval robustness for relationship/general-state phrasing:

- improve Russian overlap handling so summary/stable relation memories survive more wording variation
- specifically reduce misses where summary should join a broad relationship query but falls below threshold
- then rerun this same live ST report flow to confirm the change in the real runtime
