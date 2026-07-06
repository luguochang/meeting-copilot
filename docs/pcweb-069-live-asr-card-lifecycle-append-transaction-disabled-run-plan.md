# PCWEB-069 Live ASR Card Lifecycle Append Transaction Disabled Run Plan

## Goal

Add a local disabled transaction-run boundary for future Live ASR card lifecycle event persistence.

PCWEB-068 defines the repository append dry-run contract without writing anything. PCWEB-069 adds the next action boundary: callers can attempt the future repository transaction entrypoint, but the server returns skipped transaction run envelopes and still does not mutate `/events`, start a repository transaction, or write an idempotency store.

This keeps the product moving toward auditable real-time advice while preserving the current zero-cost, no-secret, no-event-mutation boundary.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-runs
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
    "trigger_reason": "transaction disabled-run sample",
    "trigger_source": "llm_card_lifecycle_append_transaction_disabled_run",
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

Response shape for a conflict-free disabled transaction run:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "transaction_run_mode": "disabled",
  "transaction_run_status": "skipped",
  "repository_dry_run_status": "would_append_if_enabled",
  "append_run_status": "skipped",
  "append_preflight_status": "allowed",
  "future_lifecycle_status": "would_create_card",
  "repository_transaction_status": "disabled",
  "idempotency_store_write_status": "not_written",
  "event_append_status": "not_appended",
  "idempotency_store_status": "not_written",
  "safe_to_commit_transaction": false,
  "safe_to_append_events": false,
  "safe_to_create_card": false,
  "transaction_run_count": 2,
  "transaction_runs": [
    {
      "transaction_run_id": "asr_card_lifecycle_append_transaction_run_disabled_llm_schema_result_card_dry_run_001",
      "transaction_run_status": "skipped",
      "skip_reason": "repository_transaction_disabled",
      "repository_result_id": "asr_card_lifecycle_repository_dry_run_llm_schema_result_card_dry_run_001",
      "repository_result_status": "would_append_if_enabled",
      "event_type": "llm_schema_result",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
      "append_run_id": "asr_card_lifecycle_append_run_disabled_llm_schema_result_card_dry_run_001",
      "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "repository_idempotency_key": "live_asr_card_lifecycle_repository_dry_run:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "transaction_idempotency_key": "live_asr_card_lifecycle_append_transaction_run:disabled:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "preflight_append_status": "would_append_once_if_enabled",
      "preflight_conflict_status": "none",
      "would_append_sequence": 8,
      "would_append_after_sequence": 7,
      "repository_write_status": "dry_run_only",
      "transaction_write_status": "disabled",
      "event_append_status": "not_appended",
      "idempotency_store_status": "not_written",
      "idempotency_store_write_status": "not_written",
      "safe_to_commit_transaction": false,
      "safe_to_append_event": false
    }
  ],
  "block_reasons": [
    "repository_transaction_disabled",
    "idempotency_store_write_disabled",
    "event_mutation_disabled"
  ],
  "next_required_decisions": [
    "repository_transaction_commit_contract",
    "idempotency_store_write_contract",
    "append_result_audit_event",
    "retry_and_replay_conflict_resolution",
    "enabled_card_lifecycle_mutation"
  ]
}
```

If PCWEB-066 preflight is blocked by an existing future event id or idempotency key, PCWEB-069 still returns 200 but sets `repository_dry_run_status=blocked_by_preflight`, preserves `append_preflight_status=blocked` and `append_errors`, and marks affected transaction run items with `skip_reason=repository_preflight_blocked`. It still does not write to `/events`, start a repository transaction, or write an idempotency store.

Schema-invalid or policy-blocked candidates can still produce skipped transaction runs for `llm_schema_result` plus `suggestion_silenced`; the disabled transaction boundary describes the future persistence action, not whether a formal card should be shown.

Request shape violations return 422 before validation. Missing session and unknown request id return 404.

## Transaction Disabled-Run Rules

PCWEB-069 reuses PCWEB-068 repository dry-run, which itself reuses PCWEB-067 disabled append run and PCWEB-066 append preflight.

- `mode` must be exactly `disabled`.
- `transaction_run_status=skipped` on the response.
- `transaction_run_count` equals `repository_append_count`, `append_run_count`, and `append_plan_count`.
- Each `transaction_run` references exactly one repository result and one skipped append run.
- `transaction_run_id` is deterministic from percent-encoded event type and card id components, and is scoped as a response/envelope-local display id while the transaction path remains disabled.
- `transaction_idempotency_key` is deterministic from percent-encoded tuple components: `live_asr_card_lifecycle_append_transaction_run:disabled:{session_id_token}:{request_id_token}:{event_type_token}:{card_id_token}`.
- `transaction_idempotency_key` is the canonical durable identity for future transaction persistence, replay, conflict detection, and idempotency-store writes.
- Existing `append_errors`, `validation_errors`, `policy_errors`, request linkage, evidence linkage, state linkage, segment linkage and sequence metadata must be preserved from the repository dry-run.
- `repository_transaction_status=disabled`.
- `transaction_write_status=disabled`.
- `event_append_status=not_appended`.
- `idempotency_store_status=not_written`.
- `idempotency_store_write_status=not_written`.
- `safe_to_commit_transaction=false`.
- `safe_to_append_event=false` on every transaction run.
- `safe_to_append_events=false` and `safe_to_create_card=false` on the response.

## Boundaries

- POST is transaction-run action boundary only, but the action is disabled.
- No event mutation or appended lifecycle event.
- No repository transaction begin/commit/rollback.
- No idempotency store write.
- No append result audit event.
- No real `llm_schema_result` event.
- No real `suggestion_card`.
- No real `suggestion_silenced`.
- No formal report request.
- No real LLM response parser.
- No gateway, relay, or remote LLM call.
- No API key, authorization header, bearer token, provider config, base URL, `configs/local/`, keychain, secret adapter, or environment secret read.
- No provider config file existence/readability/size/mtime/hash/fingerprint check.
- No token estimate or budget charge.
- No enabled transaction mode.
- No retry worker, queue worker, background task, cursor, or pagination.

## Why This Matters

The product needs a reliable audit chain before real-time AI advice can be trusted: request draft -> schema result -> card or silenced lifecycle -> append repository result -> disabled transaction run -> feedback and replay. PCWEB-069 makes the repository transaction action boundary explicit without opening the mutation path too early.

The repository dry-run says “this is what would be written.” The disabled transaction run says “this is the action envelope that would commit those writes, and this is why it still did not commit.”

## Tests

- Allowed disabled transaction run returns `transaction_run_status=skipped`, two deterministic transaction run items for `llm_schema_result` plus `suggestion_card`, and does not mutate `/events`.
- The endpoint must not read provider config files, environment secrets, keychain, or call LLM.
- Schema-invalid candidates return transaction runs for `llm_schema_result` plus `suggestion_silenced`, preserving validation/policy errors.
- Policy-blocked candidates return transaction runs for `llm_schema_result` plus `suggestion_silenced`.
- Existing future event id or idempotency key returns 200 with `repository_dry_run_status=blocked_by_preflight`, preserved `append_errors`, and transaction runs skipped with `repository_preflight_blocked`.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-069 and the disabled transaction boundary.

## Implementation Status

- Status: implemented in this TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-runs`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.
- Review follow-up: documented that `transaction_run_id` is response/envelope-local and `transaction_idempotency_key` is the durable identity before real transaction writes are enabled.

## Verification Plan

- TDD RED: focused PCWEB-069 tests must fail before implementation.
- Focused PCWEB-069 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan.
