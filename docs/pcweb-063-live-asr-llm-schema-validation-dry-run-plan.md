# PCWEB-063 Live ASR LLM Schema Validation Dry-Run Plan

## Goal

Add a local schema validation dry-run endpoint for future Live ASR LLM suggestion-card responses.

PCWEB-060 made the future OpenAI-compatible request body visible, PCWEB-061 protected reflected prompt values, and PCWEB-062 exposed the outline-only `SuggestionCardV1` output contract. PCWEB-063 checks a caller-provided sample LLM response against that local contract without calling a model, reading provider config, creating a card, appending events, or generating a real `llm_schema_result`.

This gives the project an explicit, testable bridge between "future model response body" and "card lifecycle may be allowed later" while keeping all paid/credentialed execution disabled.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-schema-validation-dry-runs
```

Request body:

```json
{
  "mode": "dry_run_only",
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "candidate_response": {
    "id": "card_dry_run_001",
    "type": "owner_gap",
    "evidence_span_ids": ["asr_ev_asr_seg_001"],
    "state_refs": ["DecisionCandidate:asr_decision_asr_seg_001"],
    "state_event_ids": ["asr_state_event_asr_seg_001"],
    "gap_rule_id": "release.rollback.owner.required",
    "trigger_reason": "dry-run validation sample",
    "trigger_source": "llm_schema_validation_dry_run",
    "final_segment_at_ms": 3200,
    "state_event_at_ms": 3200,
    "card_created_at_ms": 3400,
    "latency_ms": 200,
    "prompt_version": "suggestion-card-execution-preview.v1",
    "model": "not_called",
    "usage": {"total_tokens": 0},
    "schema_result": "valid",
    "show_or_silence_decision": "show",
    "segment_batch": ["asr_seg_001"],
    "status": "new",
    "title": "确认回滚负责人",
    "suggested_question": "这次发布的回滚负责人是谁？"
  }
}
```

Response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "validation_mode": "dry_run_only",
  "validation_status": "passed",
  "schema_name": "SuggestionCardV1",
  "schema_validation_status": "dry_run_passed",
  "schema_result_status": "not_generated",
  "card_status": "not_created",
  "llm_call_status": "not_called",
  "credentials_status": "not_read",
  "config_source_status": "not_read",
  "cost_status": "not_estimated",
  "safe_to_create_card": false,
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
  "target_candidate_id": "asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "target_type": "DecisionCandidate",
  "target_id": "asr_decision_asr_seg_001",
  "gap_rule_id": "release.rollback.owner.required",
  "validation_errors": [],
  "validated_field_count": 21,
  "candidate_response_preview": {
    "id": "card_dry_run_001",
    "type": "owner_gap",
    "schema_result": "valid",
    "show_or_silence_decision": "show",
    "status": "new"
  },
  "block_reasons": [
    "schema_validation_dry_run_only",
    "llm_executor_disabled",
    "card_lifecycle_disabled"
  ],
  "next_required_decisions": [
    "enabled_executor_mode_contract",
    "real_llm_response_parser",
    "schema_validation_failure_lifecycle",
    "card_creation_policy",
    "token_cost_accounting"
  ]
}
```

When validation fails, the endpoint still returns 200 with:

```json
{
  "validation_status": "failed",
  "schema_validation_status": "dry_run_failed",
  "safe_to_create_card": false,
  "validation_errors": [
    {
      "field": "usage.total_tokens",
      "code": "missing_required_field",
      "message": "suggestion card card_dry_run_001 missing usage.total_tokens"
    }
  ]
}
```

Request shape violations return 422 before validation:

- Non-object body.
- Missing `mode`.
- Unsupported `mode`.
- Missing `request_id`.
- Missing `candidate_response`.
- Non-object `candidate_response`.
- Extra top-level request fields.

## Validation Rules

PCWEB-063 uses the local `SuggestionCardV1.from_dict()` contract and a small dry-run gate subset:

- Required fields must be present and non-empty where the local contract requires strings/lists.
- `usage` must be an object.
- `usage.total_tokens` must exist and be a non-negative JSON integer. Floats, booleans, and numeric strings are invalid even if Python could coerce them with `int()`.
- `schema_result` must be one of the existing schema-result gate values used by core gates.
- Strong cards may not pass with blocking schema results such as failed/timeout/invalid.
- `final_segment_at_ms`, `state_event_at_ms`, `card_created_at_ms`, and `latency_ms` must be non-negative JSON integers. Floats, booleans, and numeric strings are invalid.
- `state_event_at_ms >= final_segment_at_ms`.
- `card_created_at_ms >= state_event_at_ms`.
- `latency_ms == card_created_at_ms - final_segment_at_ms`.

This validation is intentionally local and deterministic. It is not a provider-specific JSON Schema validator and it does not parse a real chat-completions response envelope.

## Linkage Rules

- `request_id` must match an existing `llm_request_draft_event.payload.request_id` in the Live ASR audit record.
- The response includes request draft linkage: request draft event id/sequence, target candidate id, target type/id, gap rule, source event ids, evidence span ids, and segment batch.
- Validation does not require candidate response evidence/span fields to exactly equal the request draft linkage in PCWEB-063. Cross-link enforcement remains a later card-creation policy decision.

## Boundaries

- POST is validation-only and dry-run only.
- No event mutation or appended validation event.
- No `llm_schema_result` event.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No real LLM response parser.
- No gateway, relay, or remote LLM call.
- No API key, authorization header, bearer token, provider config, base URL, `configs/local/`, keychain, secret adapter, or environment secret read.
- No provider config file existence/readability/size/mtime/hash/fingerprint check.
- No token estimate or budget charge.
- No full JSON Schema generation.
- No enabled executor mode.
- No retry, queue worker, background task, cursor, or pagination.

## Why This Matters

Real-time meeting value depends on creating useful, safe suggestion cards from live context. The project cannot jump directly from request preview to paid LLM execution without a tested boundary for output validation and failure handling.

PCWEB-063 adds that boundary while keeping costs and credentials at zero. It answers: if a future model returned this card-shaped JSON, would our local contract accept it, and what failure category would be shown before any card lifecycle is allowed?

## Tests

- Valid dry-run response for an existing request draft returns `passed/dry_run_passed`, preserves request draft linkage, keeps `safe_to_create_card=false`, and does not mutate `/events`.
- Invalid dry-run response returns `failed/dry_run_failed` with deterministic field/code/message errors while still not creating schema results or cards.
- Missing request id in the session returns 404 without exposing provider/config data.
- Empty Live ASR sessions return 404 for unknown request id.
- Missing/unsupported mode, missing request id, missing candidate response, non-object candidate response, non-object body, and extra top-level fields return 422.
- `mode` and `request_id` must be strings; numeric, boolean, null, and object/array values return 422 instead of being coerced.
- `usage.total_tokens` and timing fields reject floats, booleans, numeric strings, negative values, invalid time ordering, and inconsistent latency.
- Blocking `schema_result` values are allowed only for non-strong card envelopes; they must not pass as strong suggestions.
- The endpoint must not read provider config files, environment secrets, keychain, or call LLM.
- README/docs must mention PCWEB-063 and the dry-run-only boundary.
