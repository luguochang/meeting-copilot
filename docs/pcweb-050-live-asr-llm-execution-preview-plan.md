# PCWEB-050 Live ASR LLM Execution Preview Plan

## Goal

Add a local, read-only execution preview for Live ASR LLM request drafts.

The preview converts existing no-LLM `llm_request_draft_event` records into deterministic future executor envelopes. It does not call an LLM gateway, read model credentials, choose a real provider, estimate tokens, validate schema, create suggestion cards, mutate request state, or append events.

## Endpoint

```http
GET /live/asr/sessions/{session_id}/llm-execution-previews
```

Response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "execution_preview_count": 5,
  "execution_previews": [
    {
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
      "prompt_version": "suggestion-card-execution-preview.v1",
      "provider": "not_configured",
      "model": "not_called",
      "llm_call_status": "not_called",
      "schema_name": "SuggestionCardV1",
      "schema_status": "not_generated",
      "card_status": "not_created",
      "cost_status": "not_estimated",
      "idempotency_key": "live_asr_execution_preview:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
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

- Read only.
- No gateway, relay, or remote LLM call.
- No API key or `configs/local/` read.
- No real provider/model selection.
- No token estimate.
- No schema validation.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No event mutation or appended preview event.
- No ranking, dedupe, retry, status transition, or pagination in PCWEB-050.

## Why This Matters

PCWEB-048 made request assembly visible as `llm_request_draft_event`. PCWEB-049 exposed those request drafts through a stable query endpoint. The next low-cost step is a deterministic execution preview that shows how a later real executor will identify the draft, preserve idempotency, carry evidence linkage, select a schema target, and remain auditable before any paid call is enabled.

This keeps the product moving toward real-time suggestions without prematurely spending on LLM calls or mixing dry-run requests with formal cards.

## Tests

- Execution preview query returns one preview per `llm_request_draft_event`.
- It preserves linkage to request id, request draft event id, source state events, evidence ids, segment batch, target candidate, target state, and gap rule.
- It marks every preview as `preview_only`, `not_called`, `not_generated`, `not_created`, and `not_estimated`.
- It returns empty previews for transcript-only Live ASR sessions.
- It reads JSON persisted records across app instances.
- It returns 404 for missing Live ASR sessions.
- Reading previews must not mutate the Live ASR event stream or append a new event.
- Existing candidate and request-draft query endpoints remain narrow and unchanged.
