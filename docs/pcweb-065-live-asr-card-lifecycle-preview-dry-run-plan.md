# PCWEB-065 Live ASR Card Lifecycle Preview Dry-Run Plan

## Goal

Add a local card lifecycle preview dry-run endpoint for future Live ASR LLM suggestion-card execution results.

PCWEB-063 answers whether a caller-provided `candidate_response` satisfies the local `SuggestionCardV1` schema dry-run. PCWEB-064 answers whether that candidate is eligible for future card creation. PCWEB-065 answers the next lifecycle question without writing anything: if this exact candidate went through a future enabled lifecycle, which audit events would be appended, and would the user-facing outcome be a `suggestion_card` or a `suggestion_silenced` record?

This keeps the product moving toward real-time, auditable meeting advice while preserving the current no-cost, no-secret, no-event-mutation boundary.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-preview-dry-runs
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
    "trigger_reason": "dry-run lifecycle sample",
    "trigger_source": "llm_card_lifecycle_preview_dry_run",
    "final_segment_at_ms": 3500,
    "state_event_at_ms": 3500,
    "card_created_at_ms": 3700,
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

Response shape when the future lifecycle would create a card:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "lifecycle_preview_mode": "dry_run_only",
  "lifecycle_preview_status": "previewed",
  "schema_validation_status": "dry_run_passed",
  "card_creation_policy_status": "dry_run_allowed",
  "future_lifecycle_status": "would_create_card",
  "schema_result_status": "preview_only",
  "card_status": "preview_only",
  "silenced_status": "not_previewed",
  "llm_call_status": "not_called",
  "credentials_status": "not_read",
  "config_source_status": "not_read",
  "cost_status": "not_estimated",
  "safe_to_append_events": false,
  "safe_to_create_card": false,
  "would_append_event_types_if_enabled": ["llm_schema_result", "suggestion_card"],
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
  "request_draft_sequence": 6,
  "target_candidate_id": "asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "target_type": "DecisionCandidate",
  "target_id": "asr_decision_asr_seg_001",
  "target_state_ref": "DecisionCandidate:asr_decision_asr_seg_001",
  "gap_rule_id": "release.rollback.owner.required",
  "source_event_ids": ["asr_state_event_asr_seg_001"],
  "evidence_span_ids": ["asr_ev_asr_seg_001"],
  "segment_batch": ["asr_seg_001"],
  "validation_errors": [],
  "policy_errors": [],
  "preview_events": [
    {
      "event_type": "llm_schema_result",
      "preview_only": true,
      "would_append_if_enabled": true,
      "event_id": "preview:llm_schema_result:card_dry_run_001",
      "at_ms": 3700,
      "payload": {
        "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
        "card_id": "card_dry_run_001",
        "schema_result": "valid",
        "show_or_silence_decision": "show",
        "usage": {"total_tokens": 0},
        "latency_ms": 200
      }
    },
    {
      "event_type": "suggestion_card",
      "preview_only": true,
      "would_append_if_enabled": true,
      "event_id": "preview:suggestion_card:card_dry_run_001",
      "at_ms": 3700,
      "payload": {
        "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
        "card_id": "card_dry_run_001",
        "card": {
          "id": "card_dry_run_001",
          "type": "owner_gap",
          "title": "确认回滚负责人",
          "suggested_question": "这次发布的回滚负责人是谁？",
          "status": "new"
        }
      }
    }
  ],
  "block_reasons": [
    "card_lifecycle_preview_dry_run_only",
    "event_mutation_disabled"
  ],
  "next_required_decisions": [
    "real_llm_response_parser",
    "llm_schema_result_event_persistence",
    "suggestion_card_repository_lifecycle",
    "suggestion_silenced_repository_lifecycle",
    "feedback_idempotency"
  ]
}
```

When schema validation or policy blocks card creation, the endpoint still returns 200 and previews a future `suggestion_silenced` lifecycle instead of a user-facing card:

```json
{
  "future_lifecycle_status": "would_silence_candidate",
  "schema_result_status": "preview_only",
  "card_status": "not_created",
  "silenced_status": "preview_only",
  "would_append_event_types_if_enabled": ["llm_schema_result", "suggestion_silenced"],
  "policy_errors": [
    {
      "field": "evidence_span_ids",
      "code": "stale_evidence",
      "message": "card_dry_run_001 references stale evidence_span_id: asr_ev_asr_seg_001"
    }
  ],
  "preview_events": [
    {
      "event_type": "llm_schema_result",
      "preview_only": true
    },
    {
      "event_type": "suggestion_silenced",
      "preview_only": true
    }
  ]
}
```

Request shape violations return 422 before validation. Missing session and unknown request id return 404.

## Lifecycle Rules

PCWEB-065 first reuses the PCWEB-063 schema validation subset and the PCWEB-064 card creation policy.

- If schema validation passes and card creation policy allows creation, preview `llm_schema_result` followed by `suggestion_card`.
- If schema validation fails, preview `llm_schema_result` followed by `suggestion_silenced` with `silence_reason=schema_validation_failed`.
- If schema validation passes but card creation policy blocks creation, preview `llm_schema_result` followed by `suggestion_silenced` with `silence_reason=card_creation_policy_blocked`.
- Preview event `at_ms` is deterministic and comes from `candidate_response.card_created_at_ms` when it is a strict JSON integer; otherwise it falls back to `0` only after schema validation has already reported the invalid timing field.
- `safe_to_append_events` and `safe_to_create_card` always remain false.
- `preview_events` are response objects only; they must never be appended to the Live ASR audit record.

## Boundaries

- POST is lifecycle preview dry-run only.
- No event mutation or appended lifecycle event.
- No real `llm_schema_result` event.
- No real `suggestion_card`.
- No real `suggestion_silenced`.
- No formal report request.
- No real LLM response parser.
- No gateway, relay, or remote LLM call.
- No API key, authorization header, bearer token, provider config, base URL, `configs/local/`, keychain, secret adapter, or environment secret read.
- No provider config file existence/readability/size/mtime/hash/fingerprint check.
- No token estimate or budget charge.
- No enabled executor mode.
- No retry, queue worker, background task, cursor, or pagination.

## Why This Matters

The user-facing product is not valuable unless a real-time suggestion can be traced from ASR evidence through state, LLM schema validation, policy gating, and finally a visible or silenced card lifecycle. PCWEB-065 makes that lifecycle auditable before the system is allowed to persist card events or spend money on LLM calls.

This is the last no-call boundary before a future enabled execution path can be designed responsibly.

## Tests

- Allowed lifecycle preview returns `future_lifecycle_status=would_create_card`, `would_append_event_types_if_enabled=[llm_schema_result,suggestion_card]`, two preview events, `safe_to_append_events=false`, `safe_to_create_card=false`, and does not mutate `/events`.
- The endpoint must not read provider config files, environment secrets, keychain, or call LLM.
- Schema-invalid candidates return 200 with `future_lifecycle_status=would_silence_candidate`, schema validation errors, `suggestion_silenced` preview, and no `suggestion_card` preview.
- Policy-blocked candidates return 200 with `future_lifecycle_status=would_silence_candidate`, deterministic policy errors, `suggestion_silenced` preview, and no `suggestion_card` preview.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances.
- Non-object body, missing/unsupported mode, non-string `mode/request_id`, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-065 and the lifecycle-preview-only boundary.

## Implementation Status

- Status: implemented as a local dry-run boundary.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-preview-dry-runs`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.
- Current verification scope: focused PCWEB-065 tests, README contract gate, backend regression, `pc-web` quality gate, and `all-local --no-browser` quality gate.

## Verification Evidence

Verified on 2026-07-02:

- TDD RED: focused PCWEB-065 tests failed with endpoint `404 Not Found` before implementation.
- Focused PCWEB-065 pytest: `7 passed`.
- README contract gate: `1 passed`.
- Backend regression: `174 passed`.
- `python3 tools/run_quality_gate.py --profile pc-web`: passed, including core pytest, web backend pytest, and browser smoke.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`: passed, including ASR runtime, ASR bakeoff, core, and web backend pytest.
