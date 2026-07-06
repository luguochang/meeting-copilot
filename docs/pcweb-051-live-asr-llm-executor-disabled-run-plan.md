# PCWEB-051 Live ASR LLM Executor Disabled Run Plan

## Goal

Add an explicit local LLM executor entrypoint for Live ASR sessions, but keep it disabled by default.

The disabled run turns existing execution previews into deterministic skipped run records. It proves the product has a clear handoff from realtime ASR state to future LLM execution without reading credentials, calling the relay, estimating cost, generating schema output, or creating suggestion cards.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-execution-runs
```

Request body:

```json
{
  "mode": "disabled"
}
```

`mode` is required. Empty request bodies are rejected instead of silently defaulting to disabled mode, so future enabled execution cannot be hidden behind an implicit default. Extra request fields are rejected; credentials or provider options must not be accepted through this disabled endpoint.

Response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "executor_mode": "disabled",
  "run_count": 5,
  "runs": [
    {
      "run_id": "asr_llm_execution_run_disabled_asr_llm_execution_preview_asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "run_status": "skipped",
      "skip_reason": "llm_executor_disabled",
      "execution_id": "asr_llm_execution_preview_asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "execution_status": "preview_only",
      "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
      "request_draft_sequence": 6,
      "request_type": "llm_suggestion_card_draft",
      "target_candidate_id": "asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "target_type": "DecisionCandidate",
      "target_id": "asr_decision_asr_seg_001",
      "gap_rule_id": "release.rollback.owner.required",
      "idempotency_key": "live_asr_execution_run:disabled:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "provider": "not_configured",
      "model": "not_called",
      "llm_call_status": "not_called",
      "schema_name": "SuggestionCardV1",
      "schema_status": "not_generated",
      "card_status": "not_created",
      "cost_status": "not_estimated",
      "source_event_ids": ["asr_state_event_asr_seg_001"],
      "evidence_span_ids": ["asr_ev_asr_seg_001"],
      "segment_batch": ["asr_seg_001"],
      "candidate_confidence": 0.9,
      "candidate_confidence_level": "high",
      "candidate_degradation_reasons": [],
      "input_summary": "DecisionCandidate asr_decision_asr_seg_001 from asr_seg_001 using asr_ev_asr_seg_001",
      "suggested_prompt": "确认决策是否包含 owner、回滚条件和监控口径。"
    }
  ]
}
```

## Boundaries

- POST is an executor entrypoint, but PCWEB-051 only supports `mode=disabled`.
- Disabled runs are local projections over execution previews.
- No event mutation or appended run event.
- No API key, `configs/local/`, gateway, relay, or remote LLM call.
- No real provider/model selection.
- No token estimate or budget charge.
- No schema validation.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No retry, queue worker, background task, cursor, or pagination in PCWEB-051.

## Why This Matters

PCWEB-050 shows what would be executed. PCWEB-051 adds the action boundary that a later real executor will replace, while still returning a clear skipped decision when execution is disabled.

This keeps the roadmap honest: the product is no longer only “ASR text plus request previews”, but the real paid-call switch remains explicitly off, auditable, and testable. Future work can add `mode=enabled` only after provider config, secret handling, schema validation, cost tracking, and card lifecycle are separately designed and tested.

## Tests

- Disabled run returns one skipped run per existing execution preview.
- Each run preserves request, draft, candidate, state, gap rule, EvidenceSpan, segment, quality, prompt, and idempotency linkage.
- Every run has `run_status=skipped`, `skip_reason=llm_executor_disabled`, `not_called`, `not_generated`, `not_created`, and `not_estimated`.
- Calling the endpoint does not mutate the Live ASR event stream or append events.
- Transcript-only Live ASR sessions return an empty run list.
- JSON persisted records can be read across app instances.
- Missing sessions return 404.
- Unsupported modes return 422.
- Missing `mode` returns 422.
- Empty request body and extra request fields return 422.
