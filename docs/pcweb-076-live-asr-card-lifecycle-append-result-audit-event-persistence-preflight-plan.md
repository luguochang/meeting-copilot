# PCWEB-076 Live ASR Card Lifecycle Append Result Audit Event Persistence Preflight Plan

## Context

PCWEB-070 previews future `card_lifecycle_append_result` audit events, PCWEB-071 through PCWEB-075 then analyze retry/replay, serialization, mutation, transaction commit, and idempotency-store write readiness without enabling writes. The remaining durable boundary before a future enabled lifecycle append is append result audit event persistence: the system must know when it would persist audit evidence for an append result, when safe replay requires no new audit event, and when persistence must remain blocked.

PCWEB-076 adds a response-only audit event persistence preflight. It reuses PCWEB-075 as the source of truth, preserves PCWEB-070 audit event preview identities, and projects one audit persistence check per future lifecycle event. It never writes a Live ASR audit record, append result audit event, repository transaction, idempotency store, lifecycle event, card, or marker.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-event-persistence-preflights
```

Request body:

```json
{
  "mode": "preflight_only",
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "candidate_response": {
    "id": "card_dry_run_001"
  }
}
```

Only `mode=preflight_only` is accepted. `mode` is not trimmed; whitespace-padded mode is rejected as unsupported. `request_id` is trimmed.

## Response Shape

The response preserves PCWEB-075 idempotency-store write preflight output, including upstream transaction, mutation, retry/replay, serializer, and audit preview provenance, then adds:

```json
{
  "append_result_audit_event_persistence_preflight_mode": "preflight_only",
  "append_result_audit_event_persistence_preflight_status": "analyzed",
  "append_result_audit_event_persistence_readiness_status": "blocked_until_enabled",
  "append_result_audit_event_persistence_preflight_check_count": 2,
  "append_result_audit_event_persistence_preflight_checks": [
    {
      "append_result_audit_event_persistence_preflight_check_id": "asr_card_lifecycle_append_result_audit_event_persistence_preflight_llm_schema_result_card_dry_run_001",
      "append_result_audit_event_persistence_preflight_check_status": "blocked_until_enabled",
      "future_append_result_audit_event_id": "asr_card_lifecycle_append_result_audit_preview_llm_schema_result_card_dry_run_001",
      "future_append_result_audit_event_type": "card_lifecycle_append_result",
      "future_append_result_audit_event_status": "would_persist_if_enabled",
      "append_result_audit_event_persistence_reason": "fresh_append_requires_append_result_audit_event",
      "audit_event_id": "asr_card_lifecycle_append_result_audit_preview_llm_schema_result_card_dry_run_001",
      "audit_idempotency_key": "live_asr_card_lifecycle_append_result_audit_preview:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "append_result_audit_event_status": "preview_only",
      "audit_result_status": "skipped_transaction_disabled",
      "transaction_run_id": "asr_card_lifecycle_append_transaction_run_llm_schema_result_card_dry_run_001",
      "transaction_run_status": "skipped",
      "append_run_id": "asr_card_lifecycle_append_run_llm_schema_result_card_dry_run_001",
      "repository_result_id": "asr_card_lifecycle_append_repository_result_llm_schema_result_card_dry_run_001",
      "repository_result_status": "would_append_if_enabled",
      "repository_idempotency_key": "live_asr_card_lifecycle_append_repository:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "preflight_append_status": "would_append_once_if_enabled",
      "preflight_conflict_status": "none",
      "audit_repository_transaction_status": "disabled",
      "repository_write_status": "dry_run_only",
      "transaction_write_status": "disabled",
      "idempotency_store_write_preflight_check_id": "asr_card_lifecycle_append_idempotency_store_write_preflight_llm_schema_result_card_dry_run_001",
      "idempotency_store_write_preflight_check_status": "blocked_until_enabled",
      "idempotency_store_write_readiness_status": "blocked_until_enabled",
      "future_idempotency_record_status": "would_write_if_enabled",
      "transaction_commit_readiness_status": "blocked_until_enabled",
      "transaction_commit_preflight_check_id": "asr_card_lifecycle_append_transaction_commit_preflight_llm_schema_result_card_dry_run_001",
      "transaction_commit_preflight_check_status": "blocked_until_enabled",
      "mutation_preflight_check_id": "asr_card_lifecycle_append_mutation_preflight_llm_schema_result_card_dry_run_001",
      "mutation_preflight_check_status": "blocked_until_enabled",
      "retry_replay_check_id": "asr_card_lifecycle_retry_replay_preflight_llm_schema_result_card_dry_run_001",
      "retry_replay_resolution_status": "no_existing_append",
      "serializer_result_id": "asr_card_lifecycle_append_event_serializer_llm_schema_result_card_dry_run_001",
      "serialization_status": "would_serialize_if_enabled",
      "event_type": "llm_schema_result",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "serialized_event_id": "llm_schema_result:card_dry_run_001",
      "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
      "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "transaction_idempotency_key": "live_asr_card_lifecycle_append_transaction_run:disabled:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "would_append_sequence": 31,
      "would_append_after_sequence": 30,
      "append_status": "would_append_once_if_enabled",
      "conflict_status": "none",
      "repository_transaction_status": "not_started",
      "repository_transaction_commit_status": "not_committed",
      "repository_transaction_rollback_status": "not_started",
      "event_append_status": "not_appended",
      "audit_event_append_status": "not_appended",
      "idempotency_store_status": "not_written",
      "idempotency_store_write_status": "not_written",
      "safe_to_persist_append_result_audit_event": false,
      "safe_to_write_audit_event": false,
      "safe_to_write_audit_events": false,
      "safe_to_append_event": false,
      "safe_to_write_idempotency_store": false,
      "safe_to_begin_transaction": false,
      "safe_to_commit_transaction": false,
      "safe_to_rollback_transaction": false,
      "safe_to_mutate_events": false,
      "safe_to_append_events": false,
      "safe_to_create_card": false
    }
  ],
  "audit_event_append_status": "not_appended",
  "event_append_status": "not_appended",
  "idempotency_store_status": "not_written",
  "idempotency_store_write_status": "not_written",
  "repository_transaction_status": "not_started",
  "repository_transaction_commit_status": "not_committed",
  "repository_transaction_rollback_status": "not_started",
  "safe_to_persist_append_result_audit_event": false,
  "safe_to_write_audit_events": false,
  "safe_to_write_idempotency_store": false,
  "safe_to_begin_transaction": false,
  "safe_to_commit_transaction": false,
  "safe_to_rollback_transaction": false,
  "safe_to_mutate_events": false,
  "safe_to_append_events": false,
  "safe_to_create_card": false
}
```

## Readiness Rules

Top-level `append_result_audit_event_persistence_readiness_status` values:

- `blocked_until_enabled`: no replay/conflict/mutation/store blocker is detected, so future fresh append would need deterministic append result audit event persistence, but persistence is disabled.
- `safe_replay_existing_events`: all lifecycle events already exist and match replay identity rules. No new append result audit event should be persisted for this request.
- `blocked_by_partial_replay`: some lifecycle events already exist while others are missing. The preflight must not persist audit events for the missing tail because that would create misleading audit evidence.
- `blocked_by_retry_replay_conflict`: retry/replay found mismatched event identity, mismatched metadata, existing idempotency marker without a matching event, or duplicate idempotency evidence.
- `blocked_by_idempotency_store_write_preflight`: PCWEB-075 is blocked by transaction/mutation/serializer/idempotency-store preflight and did not produce a fresher replay-specific blocker.

Per-check `append_result_audit_event_persistence_preflight_check_status` values:

- `blocked_until_enabled`
- `persistence_not_required_for_safe_replay`
- `blocked_by_partial_replay`
- `blocked_by_retry_replay_conflict`
- `blocked_by_idempotency_store_write_preflight`

Per-check `future_append_result_audit_event_status` values:

- `would_persist_if_enabled`
- `not_required_existing_replay`
- `blocked`

## Preflight Rules

- PCWEB-076 reuses PCWEB-075 idempotency-store write preflight as the source of truth.
- It must not recompute lifecycle previews, audit previews, retry/replay checks, serializer output, mutation checks, commit readiness, idempotency-store write readiness, idempotency keys, transaction idempotency keys, audit idempotency keys, or sequence placement outside existing helpers.
- It must join PCWEB-075 idempotency-store checks to PCWEB-070 audit previews by `future_event_id` and preserve the preview `audit_event_id` and `audit_idempotency_key`.
- Each persistence check must remain self-contained for audit review by carrying PCWEB-070 provenance: `transaction_run_id`, `transaction_run_status`, `append_run_id`, `repository_result_id`, `repository_result_status`, `repository_idempotency_key`, `preflight_append_status`, `preflight_conflict_status`, `audit_repository_transaction_status`, `repository_write_status`, and `transaction_write_status`.
- `audit_repository_transaction_status` is the PCWEB-070 audit preview source value. The existing per-check `repository_transaction_status=not_started` remains the PCWEB-076 boundary value and means this endpoint did not start a new repository transaction.
- Fresh no-existing append returns `blocked_until_enabled`. Each check uses `future_append_result_audit_event_status=would_persist_if_enabled`.
- Complete safe replay returns `safe_replay_existing_events`. Each check uses `append_result_audit_event_persistence_preflight_check_status=persistence_not_required_for_safe_replay` and `future_append_result_audit_event_status=not_required_existing_replay`.
- Partial replay returns `blocked_by_partial_replay`; it must not persist audit events for missing tail lifecycle events.
- Retry/replay conflict returns `blocked_by_retry_replay_conflict`.
- Mutation/serializer/transaction/idempotency-store blockers return `blocked_by_idempotency_store_write_preflight`.
- PCWEB-076 never marks audit event persistence, audit event append, idempotency-store write, transaction begin/commit/rollback, event append, mutation, or card creation safe.

## Non-Goals

- No enabled audit event persistence mode.
- No append result audit event repository.
- No audit event write, update, delete, compare-and-swap, lock, or lease.
- No repository transaction begin, commit, rollback, compensation, or lock acquisition.
- No idempotency store write.
- No lifecycle event append.
- No card store or formal report request.
- No remote ASR or LLM call.
- No provider config or secret read.

## Boundaries

- PCWEB-076 must not write `/events`.
- It must not write a Live ASR audit record, append result audit event, idempotency store, idempotency marker, or lifecycle event.
- It must not begin, commit, or roll back a repository transaction.
- It must not create real `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` events.
- It must not estimate token cost.
- It must not call remote ASR, LLM, relay, or any HTTP gateway.
- It must not read provider config, API keys, authorization headers, bearer tokens, environment secrets, keychain, secret adapters, or `configs/local/`.
- It must not check provider config file existence, readability, size, mtime, hash, or fingerprint.
- `safe_replay_existing_events` means no new audit event persistence is needed; it does not mean a new audit event, lifecycle event, transaction, or idempotency record can be written.

## Why This Matters

The product value depends on durable, explainable real-time suggestions. A future enabled append must be able to tell users and developers why a schema result/card/silenced lifecycle write was attempted, skipped, replayed, or blocked. Persisting audit evidence too early can make partial writes look intentional; failing to persist it on the enabled path can make recovery and debugging opaque. PCWEB-076 locks the audit persistence semantics before any real write path is opened.

## Tests

- Fresh allowed lifecycle returns audit event persistence checks for `llm_schema_result` plus `suggestion_card`, with `append_result_audit_event_persistence_readiness_status=blocked_until_enabled`, `future_append_result_audit_event_status=would_persist_if_enabled`, PCWEB-070 audit ids/provenance preserved, all disabled write flags false, and unchanged `/events`.
- Complete matching replay returns `append_result_audit_event_persistence_readiness_status=safe_replay_existing_events`, per-check `persistence_not_required_for_safe_replay`, `future_append_result_audit_event_status=not_required_existing_replay`, persisted bytes unchanged, and no audit event append.
- Partial replay returns `append_result_audit_event_persistence_readiness_status=blocked_by_partial_replay`, blocks missing tail audit event persistence, and does not append or write.
- Retry/replay conflicts return `append_result_audit_event_persistence_readiness_status=blocked_by_retry_replay_conflict`.
- Mutation/serializer/transaction/idempotency blockers return `blocked_by_idempotency_store_write_preflight` while preserving the PCWEB-075 source readiness.
- Schema-invalid and policy-blocked lifecycles still cover `llm_schema_result` plus `suggestion_silenced`.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-076 and the append result audit event persistence preflight boundary.

## Implementation Status

- Status: implemented and focused-test verified in the PCWEB-076 TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-event-persistence-preflights`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.

## Verification Plan

- TDD RED: focused PCWEB-076 tests must fail before implementation.
- Focused PCWEB-076 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Independent read-only review, then final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan from the operator checklist.

Verification result on 2026-07-02:

- RED: the eleven new PCWEB-076 endpoint tests failed with `404 Not Found` before implementation; README gate passed.
- Focused PCWEB-076 plus README gate after README gate hardening: `12 passed, 230 deselected, 2 warnings`.
- Backend regression: `280 passed, 2 warnings`.
- Quality gate `pc-web`: core `34 passed`, web backend `283 passed`, browser smoke passed.
- Quality gate `all-local --no-browser`: ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, web backend `283 passed`.

Review hardening result on 2026-07-02:

- RED: after adding provenance and docs-gate assertions, focused tests first failed on missing `transaction_run_id`, then on missing docs coverage. Both failures were expected and confirmed the new tests were exercising the reviewed gaps.
- GREEN: PCWEB-076 now carries PCWEB-070 provenance on every persistence check, including `transaction_run_id`, `transaction_run_status`, `append_run_id`, `repository_result_id`, `repository_result_status`, `repository_idempotency_key`, `preflight_append_status`, `preflight_conflict_status`, `audit_repository_transaction_status`, `repository_write_status`, and `transaction_write_status`.
- GREEN: PCWEB-076 branch tests now install no-config/no-secret/outbound-call guards and assert `/events` stability wherever the route has an existing session.
- GREEN: README/docs gate now covers the PCWEB-076 plan, requirements traceability matrix, acceptance table, privacy/data-flow document, and roadmap.
- Independent read-only review after hardening found no Critical, Important, or Minor issues.
