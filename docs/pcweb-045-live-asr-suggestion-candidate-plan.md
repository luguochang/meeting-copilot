# PCWEB-045 Live ASR Suggestion Candidate Queue Plan

> Date: 2026-07-01  
> Scope: PC Web MVP, local Live ASR suggestion-candidate audit only.

## Goal

Add a local, no-LLM `suggestion_candidate_event` after Live ASR state scheduling so the system can explain which state gaps would become real-time suggestions later, without creating formal suggestion cards or calling the LLM gateway.

## Product Rationale

PCWEB-044 proves Live ASR can drive four evidence-backed state lanes. The next value step is to show that those states can enter a suggestion decision queue. This keeps the product from stopping at "transcript + state extraction" while still respecting the current cost and quality boundary.

## Requirements

- Emit `suggestion_candidate_event` only for Live ASR local state candidates.
- Place each candidate after the corresponding `scheduler_event`, preserving local adjacency:
  - `state_event`
  - `scheduler_event`
  - `suggestion_candidate_event`
- Candidate payload must be self-contained and evidence-backed:
  - `candidate_id`
  - `candidate_type`
  - `target_type`
  - `target_id`
  - `gap_rule_id`
  - `suggested_prompt`
  - `trigger_reason`
  - `decision_reason`
  - `source_event_ids`
  - `evidence_span_ids`
  - `segment_batch`
  - `llm_call_status=not_called`
  - `card_status=not_created`
  - `source=live_asr_stream`
  - `candidate_origin=local_deterministic_asr_skeleton`
- Initial candidate rules:
  - `DecisionCandidate` -> `release.rollback.owner.required`
  - `OpenQuestion` -> `open.question.followup`
  - `Risk` -> `risk.rollback.validation`
  - `ActionItem` -> `action.owner.deadline.confirmation`
- Draft review JSON must include `suggestion_candidates`.
- Draft Markdown must include a `## Suggestion Candidates` section.
- Web event stream must summarize `suggestion_candidate_event`.
- Browser smoke must verify Live ASR displays candidate events while preserving:
  - zero `suggestion_card` events
  - zero `llm_schema_result` events
  - zero `suggestion_silenced` events
  - zero formal `/sessions/{id}/report.md` requests

## Boundaries

- No LLM calls.
- No formal suggestion cards.
- No schema validation.
- No card feedback actions.
- No paid ASR or paid LLM usage.
- No formal gated report.
- No claim that candidate ranking, deduplication, or production prompt policy is complete.

## Why This Is Separate From Scheduler

The scheduler audit says whether a state change would be eligible for an LLM call under cooldown/budget. The suggestion candidate says what product gap would be reviewed if the LLM layer were enabled. Keeping both events separate lets the UI and draft explain the live reasoning path without implying that a card was created.
