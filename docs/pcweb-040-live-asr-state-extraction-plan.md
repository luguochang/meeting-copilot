# PCWEB-040 Live ASR State Extraction Plan

> Date: 2026-07-01  
> Scope: PC Web MVP, local Live ASR event skeleton only.

## Goal

Expand the Live ASR local state skeleton from one hard-coded decision trigger into a small deterministic state extraction contract that can emit both `DecisionCandidate` and `OpenQuestion` state events from `final` and `revision` transcript events.

## Why This Matters

`PCWEB-038` and `PCWEB-039` proved that a Live ASR transcript can create evidence-backed state events and no-LLM scheduler audit events. The remaining weakness is that the state extraction currently only recognizes text containing `灰度`, so the product can still look like a transcript tool with one narrow demo rule.

This increment makes the Live ASR skeleton closer to the product value chain:

1. ASR final/revision text appears.
2. EvidenceSpan is created from that transcript event.
3. Local state extraction creates a structured meeting state item.
4. Scheduler records whether this state change would enter the LLM queue.
5. UI renders state, evidence, transcript, and scheduler trace without calling remote services.

## Requirements

- `final` and `revision` events containing `灰度` continue to emit `DecisionCandidate`.
- `final` and `revision` events containing Chinese question indicators emit `OpenQuestion`.
- Question indicators for this skeleton are:
  - `谁`
  - `吗`
  - `怎么`
  - `是否`
  - `有没有`
  - `还没有确认`
  - `?`
  - `？`
- Each emitted state event must include:
  - `target_type`
  - `target_id`
  - `state_event_type=created`
  - `evidence_span_ids`
  - a self-contained `state_item`
- `DecisionCandidate.state_item` keeps the existing `statement` shape.
- `OpenQuestion.state_item` uses `question`.
- If one transcript event emits multiple state candidates, the public event order must keep each state and its scheduler audit adjacent:
  - `state_event`
  - `scheduler_event`
  - `state_event`
  - `scheduler_event`
- Each state item must include:
  - stable deterministic id
  - `evidence_span_ids`
  - `source=live_asr_stream`
  - `state_origin=local_deterministic_asr_skeleton`
- Each emitted state event must still be followed by a no-LLM `scheduler_event`.
- Scheduler payload remains the PCWEB-039 contract:
  - `llm_candidate_queued` or `llm_candidate_skipped`
  - `decision_reason`
  - `would_call_llm`
  - `llm_call_status=not_called`
  - cooldown and budget fields
  - `prompt_version=not-called`
  - `model=not-called`
- Frontend Live ASR sample must show the extracted open question in the state board.
- Live ASR must still produce zero suggestion cards, zero LLM schema events, zero silenced events, and no report fetch.

## Boundaries

This is not the production state engine. It is a deterministic local contract skeleton used to prove event shape, UI consumption, evidence linkage, and scheduler audit semantics.

Out of scope:

- real desktop audio capture
- real ASR provider quality proof
- remote ASR
- LLM calls
- suggestion-card generation
- schema validation for LLM output
- persisted scheduler logs
- semantic deduplication or state closure
- action/risk extraction

## Test Plan

Follow TDD:

1. Add backend failing test for `OpenQuestion` extraction from a `final` event.
2. Add backend failing test for same-segment multi-state ordering.
3. Add API/SSE test expectations for a Live ASR sample containing both decision and question state events.
4. Add API/SSE ordering test for a single utterance that emits both `DecisionCandidate` and `OpenQuestion`.
5. Add browser smoke expectations that Live ASR state board includes `谁负责回滚？`.
6. Implement minimal local extraction helpers.
7. Run focused backend tests.
8. Run browser smoke.
9. Run `pc-web` and `all-local --no-browser` quality gates.

## Files

- Modify `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`.
- Modify `code/web_mvp/backend/tests/test_live_events.py`.
- Modify `code/web_mvp/backend/tests/test_app.py`.
- Modify `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`.
- Modify `code/web_mvp/e2e/browser_smoke.mjs`.
- Update requirements, acceptance, roadmap, project structure, README, and decision log.
