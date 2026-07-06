# PCWEB-048 Live ASR LLM Request Draft Plan

## Goal

Add a local, no-LLM `llm_request_draft_event` after each Live ASR `suggestion_candidate_event`.

This event explains how a future paid LLM request would be assembled, without sending the request, choosing a real model, generating schema output, or creating a suggestion card.

## Event Contract

Each state-derived Live ASR event group becomes:

1. `state_event`
2. `scheduler_event`
3. `suggestion_candidate_event`
4. `llm_request_draft_event`

`llm_request_draft_event.payload` contains:

- `request_id`: deterministic id derived from the candidate id.
- `request_type=llm_suggestion_card_draft`
- `request_status=draft_only`
- `target_candidate_id`
- `target_type`
- `target_id`
- `gap_rule_id`
- `prompt_version=not-called`
- `model=not-called`
- `llm_call_status=not_called`
- `card_status=not_created`
- `schema_status=not_generated`
- `suggested_prompt`
- `input_summary`
- `source_event_ids`
- `evidence_span_ids`
- `segment_batch`
- `candidate_confidence`
- `candidate_confidence_level`
- `candidate_degradation_reasons`
- `request_origin=local_deterministic_asr_request_draft`
- `source=live_asr_stream`

## Boundaries

- No remote ASR call.
- No LLM gateway or relay call.
- No model selection.
- No token usage estimate.
- No schema validation.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No formal `/report.md` request.
- No ranking, filtering, dedupe, or cross-segment merge.

## Why This Matters

PCWEB-047 lets future engines query candidate queue records. The next missing link is the shape of the request that would be sent to an LLM later. A local request draft makes that link visible and testable while preserving the current cost boundary.

This helps answer product and engineering questions before paid integration:

- Which candidate generated the request?
- Which prompt policy would be used?
- Which evidence and segment batch would go into context?
- Is the request blocked from real execution?
- Which quality metadata would be available before an LLM call?

## Draft Review and UI

The Live ASR draft review should collect request drafts into `llm_request_drafts` and render a `## LLM Request Drafts` Markdown section.

The Web timeline should summarize `llm_request_draft_event` as audit data only. It must not add suggestion cards, schema results, silenced records, feedback actions, or formal report requests.

## Tests

- Live ASR event sequence includes request draft after each suggestion candidate.
- Request draft payload links to the candidate id, source state event id, evidence id, segment batch, gap rule, prompt, and candidate quality metadata.
- Scheduler skipped/cooldown candidates still get draft events, but `request_status=draft_only` and `llm_call_status=not_called`.
- API/SSE expose `llm_request_draft_event`.
- Draft JSON/Markdown expose request drafts.
- Browser smoke shows request draft audit text while keeping cards/schema/silenced/formal report counts at zero.

