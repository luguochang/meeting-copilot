# PCWEB-067 Live ASR Card Lifecycle Append Disabled Run Plan

## Goal

Add an explicit local action boundary for future Live ASR card lifecycle event append, while keeping event mutation disabled.

PCWEB-066 proves a caller-provided `candidate_response` can be converted into a deterministic append preflight plan. PCWEB-067 adds the next boundary: a POST endpoint that accepts the same candidate response but only supports `mode=disabled`, returns skipped append run envelopes derived from the preflight plan, and never appends lifecycle events.

This keeps the product on the path toward real-time, auditable meeting advice without introducing extra ASR/LLM/provider cost, secret reads, or durable event mutation.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-runs
```

Request body:

```json
{
  "mode": "disabled",
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "candidate_response": {
    "id": "card_dry_run_001",
    "type": "owner_gap",
    "evidence_span_ids": ["asr_ev_asr_seg_001"],
    "state_refs": ["DecisionCandidate:asr_decision_asr_seg_001"],
    "state_event_ids": ["asr_state_event_asr_seg_001"],
    "gap_rule_id": "release.rollback.owner.required",
    "trigger_reason": "disabled append run sample",
    "trigger_source": "llm_card_lifecycle_append_disabled_run",
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

Response shape for a disabled run whose preflight is conflict-free:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "append_run_mode": "disabled",
  "append_run_status": "skipped",
  "append_preflight_status": "allowed",
  "lifecycle_preview_status": "previewed",
  "future_lifecycle_status": "would_create_card",
  "schema_validation_status": "dry_run_passed",
  "card_creation_policy_status": "dry_run_allowed",
  "llm_call_status": "not_called",
  "credentials_status": "not_read",
  "config_source_status": "not_read",
  "cost_status": "not_estimated",
  "safe_to_append_events": false,
  "safe_to_create_card": false,
  "existing_event_count": 7,
  "last_existing_sequence": 7,
  "append_plan_count": 2,
  "append_run_count": 2,
  "append_errors": [],
  "would_append_event_types_if_enabled": ["llm_schema_result", "suggestion_card"],
  "append_runs": [
    {
      "run_id": "asr_card_lifecycle_append_run_disabled_llm_schema_result_card_dry_run_001",
      "run_status": "skipped",
      "skip_reason": "event_append_disabled",
      "event_type": "llm_schema_result",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
      "idempotency_key": "live_asr_card_lifecycle_append_run:disabled:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "preflight_idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "preflight_append_status": "would_append_once_if_enabled",
      "preflight_conflict_status": "none",
      "would_append_sequence": 8,
      "would_append_after_sequence": 7,
      "llm_call_status": "not_called",
      "credentials_status": "not_read",
      "cost_status": "not_estimated",
      "event_append_status": "not_appended",
      "idempotency_store_status": "not_written",
      "safe_to_append_event": false
    }
  ],
  "block_reasons": [
    "append_run_disabled",
    "event_mutation_disabled"
  ],
  "next_required_decisions": [
    "event_append_repository_api",
    "idempotency_store",
    "enabled_card_lifecycle_mutation",
    "retry_and_replay_conflict_resolution",
    "append_result_audit_event"
  ]
}
```

The endpoint returns a skipped run envelope for every preflight plan item, including schema-invalid or policy-blocked candidates where the future lifecycle would append `llm_schema_result` plus `suggestion_silenced`.

If PCWEB-066 preflight is blocked by an existing future event id or idempotency key, PCWEB-067 still returns 200, keeps `append_preflight_status=blocked`, includes the same `append_errors`, and marks affected append runs as `run_status=skipped` with `skip_reason=append_preflight_blocked`. Unaffected runs remain skipped because event append is disabled.

Request shape violations return 422 before validation. Missing session and unknown request id return 404.

## Disabled Run Rules

PCWEB-067 first reuses PCWEB-066 append preflight.

- `mode` must be exactly `disabled`.
- The response inherits request/draft/candidate/state/gap/evidence/segment linkage from PCWEB-066.
- `append_run_status` is always `skipped`.
- `append_run_count` equals `append_plan_count`.
- Each `append_run` references exactly one `append_plan` item.
- `run_id` is deterministic from the future event id. ASCII letters and digits are lowercased as-is, `_` is preserved, `:` is treated as the event/card separator and rendered as `_`, and other non-alphanumeric characters are rendered as their lowercase hexadecimal code between underscores to avoid simple punctuation collisions.
- `idempotency_key` is deterministic: `live_asr_card_lifecycle_append_run:disabled:{session_id}:{request_id}:{event_type}:{card_id}`.
- `preflight_idempotency_key` preserves the PCWEB-066 append plan idempotency key.
- `skip_reason=event_append_disabled` when the preflight item has no conflict.
- `skip_reason=append_preflight_blocked` when the preflight item is blocked.
- `event_append_status=not_appended`.
- `idempotency_store_status=not_written`.
- `safe_to_append_event=false` on every run.
- `safe_to_append_events=false` and `safe_to_create_card=false` on the response.

## Boundaries

- POST is a disabled append-run boundary only.
- No event mutation or appended lifecycle event.
- No idempotency store write.
- No real `llm_schema_result` event.
- No real `suggestion_card`.
- No real `suggestion_silenced`.
- No append result audit event.
- No formal report request.
- No real LLM response parser.
- No gateway, relay, or remote LLM call.
- No API key, authorization header, bearer token, provider config, base URL, `configs/local/`, keychain, secret adapter, or environment secret read.
- No provider config file existence/readability/size/mtime/hash/fingerprint check.
- No token estimate or budget charge.
- No enabled append mode.
- No retry, queue worker, background task, cursor, or pagination.

## Why This Matters

PCWEB-066 answers “could these lifecycle events be appended safely later?” PCWEB-067 answers “what does the action endpoint do while append is still disabled?” That distinction matters because the future desktop product will need a real executor-like boundary for card lifecycle persistence, but enabling mutation too early would risk duplicate cards, ambiguous feedback, and hard-to-debug audit log side effects.

The disabled run endpoint also keeps the API contract honest: callers must opt into an explicit action endpoint, yet the server still reports that no action was performed.

## Tests

- Allowed append run returns `append_run_mode=disabled`, `append_run_status=skipped`, `append_preflight_status=allowed`, deterministic skipped runs for `llm_schema_result` plus `suggestion_card`, `safe_to_append_events=false`, and does not mutate `/events`.
- The endpoint must not read provider config files, environment secrets, keychain, or call LLM.
- Schema-invalid candidates return skipped runs for `llm_schema_result` plus `suggestion_silenced`, preserving validation/policy errors.
- Policy-blocked candidates return skipped runs for `llm_schema_result` plus `suggestion_silenced`.
- Existing future event id or existing idempotency key returns 200 with `append_preflight_status=blocked`, preserved `append_errors`, and skipped runs that identify blocked preflight items.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-067 and the disabled append-run boundary.

## Implementation Status

- Status: implemented as a local disabled action boundary.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-runs`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.

## Verification Evidence

Verified on 2026-07-02:

- TDD RED: focused PCWEB-067 tests failed with endpoint `404 Not Found` before implementation.
- Focused PCWEB-067 pytest plus README contract gate: `9 passed`.
- Backend regression: `191 passed`.
- `python3 tools/run_quality_gate.py --profile pc-web`: passed, including core pytest, web backend pytest, and browser smoke.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`: passed, including ASR runtime, ASR bakeoff, core, and web backend pytest.
- Final hygiene: generated pytest/cache directories removed, app test ports not listening, and local sensitive marker scan returned no output.
