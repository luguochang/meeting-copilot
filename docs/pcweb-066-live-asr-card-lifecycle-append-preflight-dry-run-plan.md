# PCWEB-066 Live ASR Card Lifecycle Append Preflight Dry-Run Plan

## Goal

Add a local append preflight dry-run endpoint for the future Live ASR LLM card lifecycle.

PCWEB-065 previews which lifecycle events a caller-provided `candidate_response` would produce. PCWEB-066 answers the next persistence safety question without writing anything: if those preview events were converted into real Live ASR audit events later, would their deterministic event ids, idempotency keys, and append sequence placement be safe?

This keeps the product moving toward real-time, auditable meeting advice while preserving the current no-cost, no-secret, no-event-mutation boundary.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-preflight-dry-runs
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
    "trigger_reason": "dry-run append preflight sample",
    "trigger_source": "llm_card_lifecycle_append_preflight_dry_run",
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

Response shape when the future append plan is conflict-free:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "append_preflight_mode": "dry_run_only",
  "append_preflight_status": "allowed",
  "lifecycle_preview_status": "previewed",
  "future_lifecycle_status": "would_create_card",
  "schema_validation_status": "dry_run_passed",
  "card_creation_policy_status": "dry_run_allowed",
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
  "existing_event_count": 7,
  "last_existing_sequence": 7,
  "append_plan_count": 2,
  "append_errors": [],
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
  "append_plan": [
    {
      "event_type": "llm_schema_result",
      "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "would_append_sequence": 8,
      "would_append_after_sequence": 7,
      "at_ms": 3700,
      "append_status": "would_append_once_if_enabled",
      "conflict_status": "none",
      "preview_only": true,
      "would_append_if_enabled": true
    },
    {
      "event_type": "suggestion_card",
      "preview_event_id": "preview:suggestion_card:card_dry_run_001",
      "future_event_id": "suggestion_card:card_dry_run_001",
      "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:suggestion_card:card_dry_run_001",
      "would_append_sequence": 9,
      "would_append_after_sequence": 8,
      "at_ms": 3700,
      "append_status": "would_append_once_if_enabled",
      "conflict_status": "none",
      "preview_only": true,
      "would_append_if_enabled": true
    }
  ],
  "block_reasons": [
    "append_preflight_dry_run_only",
    "event_mutation_disabled"
  ],
  "next_required_decisions": [
    "event_append_repository_api",
    "idempotency_store",
    "enabled_card_lifecycle_mutation",
    "feedback_idempotency",
    "retry_and_replay_conflict_resolution"
  ]
}
```

When schema validation or card policy blocks card creation, append preflight can still be `allowed`: the safe future append plan would be `llm_schema_result` followed by `suggestion_silenced`.

When a future event id or idempotency key already exists in the current audit record, the endpoint returns 200 with `append_preflight_status=blocked`, deterministic `append_errors`, and `append_status=blocked_existing_event` or `blocked_existing_idempotency_key` on the affected plan item. It still does not mutate `/events`.

Request shape violations return 422 before validation. Missing session and unknown request id return 404.

## Preflight Rules

PCWEB-066 first reuses PCWEB-065 lifecycle preview.

- `existing_event_count` is the number of current Live ASR audit events.
- `last_existing_sequence` is the maximum current event `sequence`, or `0` for an empty record.
- `future_event_id` is derived from each preview event id by removing the `preview:` prefix.
- `idempotency_key` is deterministic: `live_asr_card_lifecycle_append:{session_id}:{request_id}:{event_type}:{card_id}`.
- `would_append_sequence` starts at `last_existing_sequence + 1` and preserves the preview event order.
- `would_append_after_sequence` is the previous existing or planned sequence.
- `append_preflight_status=allowed` only when every future event id and idempotency key is absent from the current audit record.
- Existing `payload.idempotency_key`, top-level `idempotency_key`, or matching `id` conflicts block the preflight.
- `safe_to_append_events` always remains false.
- `append_plan` is response-only; it must never be appended to the Live ASR audit record.

## Boundaries

- POST is append preflight dry-run only.
- No event mutation or appended lifecycle event.
- No idempotency store write.
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

The product should not jump from previewing suggestion-card lifecycle events straight into event persistence. Without deterministic ids, idempotency keys, and sequence placement, retrying a real LLM response could duplicate cards, reorder audit events, or make later user feedback ambiguous.

PCWEB-066 keeps that future persistence boundary explicit and testable before enabling any real mutation or paid execution path.

## Tests

- Allowed append preflight returns `append_preflight_status=allowed`, two ordered append plan items, deterministic future event ids/idempotency keys/sequences, `safe_to_append_events=false`, and does not mutate `/events`.
- The endpoint must not read provider config files, environment secrets, keychain, or call LLM.
- Schema-invalid candidates return 200 with allowed preflight for `llm_schema_result` + `suggestion_silenced`, while preserving validation/policy errors.
- Policy-blocked candidates return 200 with allowed preflight for `llm_schema_result` + `suggestion_silenced`.
- Existing future event id or existing idempotency key returns 200 with `append_preflight_status=blocked` and deterministic `append_errors`.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances.
- Non-object body, missing/unsupported mode, non-string `mode/request_id`, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-066 and the append-preflight-only boundary.

## Implementation Status

- Status: implemented as a local dry-run boundary.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-preflight-dry-runs`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.
- Current verification scope: focused PCWEB-066 tests, README contract gate, backend regression, `pc-web` quality gate, and `all-local --no-browser` quality gate.

## Verification Evidence

Verified on 2026-07-02:

- TDD RED: focused PCWEB-066 tests failed with endpoint `404 Not Found` before implementation.
- Focused PCWEB-066 pytest plus README contract gate: `10 passed`.
- Backend regression: `183 passed`.
- `python3 tools/run_quality_gate.py --profile pc-web`: passed, including core pytest, web backend pytest, and browser smoke.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`: passed, including ASR runtime, ASR bakeoff, core, and web backend pytest.
