# PCWEB-075 Live ASR Card Lifecycle Append Idempotency Store Write Preflight Plan

## Context

PCWEB-074 proves that future lifecycle event mutation and retry/replay evidence can be projected into a transaction commit preflight while every write path remains disabled. The next risk is the idempotency store: a future enabled append must write deterministic idempotency records for fresh lifecycle events, but it must not write new idempotency records for safe replay, partial replay, conflict, or mutation-blocked cases.

PCWEB-075 adds a response-only idempotency store write contract preflight. It reuses PCWEB-074 as the source of truth, then reports which future idempotency records would be needed if writes were enabled, which replay cases require no write, and which cases remain blocked. It never writes an idempotency store.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-idempotency-store-write-preflights
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

The response preserves PCWEB-074 transaction commit preflight output, including mutation and retry/replay provenance, then adds:

```json
{
  "idempotency_store_write_preflight_mode": "preflight_only",
  "idempotency_store_write_preflight_status": "analyzed",
  "idempotency_store_write_readiness_status": "blocked_until_enabled",
  "idempotency_store_write_preflight_check_count": 2,
  "idempotency_store_write_preflight_checks": [
    {
      "idempotency_store_write_preflight_check_id": "asr_card_lifecycle_append_idempotency_store_write_preflight_llm_schema_result_card_dry_run_001",
      "idempotency_store_write_preflight_check_status": "blocked_until_enabled",
      "future_idempotency_record_id": "asr_card_lifecycle_append_idempotency_record_llm_schema_result_card_dry_run_001",
      "future_idempotency_record_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "future_idempotency_record_status": "would_write_if_enabled",
      "idempotency_store_write_reason": "fresh_append_requires_idempotency_record",
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
      "safe_to_begin_transaction": false,
      "safe_to_commit_transaction": false,
      "safe_to_rollback_transaction": false,
      "safe_to_append_event": false,
      "safe_to_write_idempotency_store": false,
      "safe_to_write_audit_event": false,
      "safe_to_create_card": false
    }
  ],
  "idempotency_store_status": "not_written",
  "idempotency_store_write_status": "not_written",
  "repository_transaction_status": "not_started",
  "repository_transaction_commit_status": "not_committed",
  "repository_transaction_rollback_status": "not_started",
  "event_append_status": "not_appended",
  "audit_event_append_status": "not_appended",
  "safe_to_write_idempotency_store": false,
  "safe_to_begin_transaction": false,
  "safe_to_commit_transaction": false,
  "safe_to_rollback_transaction": false,
  "safe_to_mutate_events": false,
  "safe_to_append_events": false,
  "safe_to_write_audit_events": false,
  "safe_to_create_card": false
}
```

## Readiness Rules

Top-level `idempotency_store_write_readiness_status` values:

- `blocked_until_enabled`: no replay/conflict/mutation blocker is detected, so future fresh append would need deterministic idempotency store records, but the store write path is disabled.
- `safe_replay_existing_events`: all lifecycle events already exist and match PCWEB-071 replay identity rules. The future idempotency store write is not required, and no new store record may be written.
- `blocked_by_partial_replay`: some lifecycle events already exist while others are missing. The preflight must not allow writing missing idempotency records or filling the tail.
- `blocked_by_retry_replay_conflict`: retry/replay found mismatched event identity, mismatched metadata, existing idempotency marker without a matching event, or duplicate idempotency evidence.
- `blocked_by_transaction_commit_preflight`: PCWEB-074 is blocked by mutation/serializer preflight or any transaction commit preflight check that is not a fresh blocked-until-enabled or safe-replay state.

Per-check `idempotency_store_write_preflight_check_status` values:

- `blocked_until_enabled`
- `write_not_required_for_safe_replay`
- `blocked_by_partial_replay`
- `blocked_by_retry_replay_conflict`
- `blocked_by_transaction_commit_preflight`

Per-check `future_idempotency_record_status` values:

- `would_write_if_enabled`
- `not_required_existing_replay`
- `blocked`

## Preflight Rules

- PCWEB-075 reuses PCWEB-074 transaction commit preflight as the source of truth.
- It must not recompute lifecycle preview events, serialized event payloads, mutation checks, retry/replay checks, commit readiness, idempotency keys, transaction idempotency keys, or sequence placement outside existing helpers.
- Fresh no-existing append returns `blocked_until_enabled`. Each check uses `future_idempotency_record_status=would_write_if_enabled`.
- Complete safe replay returns `safe_replay_existing_events`. Each check uses `idempotency_store_write_preflight_check_status=write_not_required_for_safe_replay` and `future_idempotency_record_status=not_required_existing_replay`.
- Partial replay returns `blocked_by_partial_replay`; it must not mark missing tail idempotency records as writable.
- Retry/replay conflict returns `blocked_by_retry_replay_conflict`.
- Mutation/serializer/transaction preflight blockers return `blocked_by_transaction_commit_preflight`.
- PCWEB-075 never marks idempotency store write, transaction begin/commit/rollback, event append, audit append, mutation, or card creation safe.

## Non-Goals

- No enabled idempotency-store write mode.
- No idempotency store persistence backend.
- No idempotency record write, update, delete, lock, compare-and-swap, or lease.
- No repository transaction begin, commit, rollback, compensation, or lock acquisition.
- No event append.
- No append result audit event write.
- No retry queue mutation.
- No card store or formal report request.
- No remote ASR or LLM call.
- No provider config or secret read.

## Boundaries

- PCWEB-075 must not write `/events`.
- It must not write an idempotency store or any idempotency marker event.
- It must not begin, commit, or roll back a repository transaction.
- It must not create real `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` events.
- It must not write `card_lifecycle_append_result` audit events.
- It must not estimate token cost.
- It must not call remote ASR, LLM, relay, or any HTTP gateway.
- It must not read provider config, API keys, authorization headers, bearer tokens, environment secrets, keychain, secret adapters, or `configs/local/`.
- It must not check provider config file existence, readability, size, mtime, hash, or fingerprint.
- `safe_replay_existing_events` means no new idempotency store write is needed; it does not mean a new transaction, event append, or idempotency write is safe.

## Why This Matters

The product value depends on real-time suggestions being durable without duplicates. If the future enabled path writes lifecycle events but fails to write idempotency records, retries can duplicate cards. If it writes idempotency records for replay/conflict cases, it can mask corruption or create false evidence. PCWEB-075 makes the future idempotency store behavior inspectable before any real store write is enabled.

## Tests

- Fresh allowed lifecycle returns idempotency store write preflight checks for `llm_schema_result` plus `suggestion_card`, with `idempotency_store_write_readiness_status=blocked_until_enabled`, `future_idempotency_record_status=would_write_if_enabled`, and unchanged `/events`.
- Complete matching replay returns `idempotency_store_write_readiness_status=safe_replay_existing_events`, per-check `write_not_required_for_safe_replay`, persisted bytes unchanged, and no idempotency store write.
- Partial replay returns `idempotency_store_write_readiness_status=blocked_by_partial_replay`, blocks missing tail idempotency records, and does not append or write.
- Retry/replay conflicts return `idempotency_store_write_readiness_status=blocked_by_retry_replay_conflict`.
- Schema-invalid and policy-blocked lifecycles still cover `llm_schema_result` plus `suggestion_silenced`.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-075 and the idempotency store write preflight boundary.

## Implementation Status

- Status: implemented and focused-test verified in the PCWEB-075 TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-idempotency-store-write-preflights`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.

## Verification Plan

- TDD RED: focused PCWEB-075 tests must fail before implementation.
- Focused PCWEB-075 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan from the operator checklist.

Verification result on 2026-07-02:

- RED: the ten new PCWEB-075 endpoint tests failed with `404 Not Found` before implementation; README gate passed.
- Focused PCWEB-075 plus README gate: `12 passed, 219 deselected, 2 warnings`.
- Backend regression: `269 passed, 2 warnings`.
- Quality gate `pc-web`: core `34 passed`, web backend `272 passed`, browser smoke passed.
- Quality gate `all-local --no-browser`: ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, web backend `272 passed`.
- Post-review fix: added a commit-preflight-blocker regression test and corrected per-check readiness provenance so `transaction_commit_readiness_status` remains the PCWEB-074 source value while `idempotency_store_write_readiness_status` carries the PCWEB-075 mapped value.
