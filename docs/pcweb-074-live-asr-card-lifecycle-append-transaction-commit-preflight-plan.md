# PCWEB-074 Live ASR Card Lifecycle Append Transaction Commit Preflight Plan

## Context

PCWEB-073 proves that PCWEB-072 canonical serialized lifecycle events can be inspected as future mutation candidates, while still refusing every real write. The next persistence risk is the repository transaction boundary: even if future events serialize correctly and mutation checks can be projected, an enabled path must not begin or commit a transaction until replay, idempotency, ordering, audit, and rollback semantics are explicit.

PCWEB-074 adds a response-only transaction commit preflight. It combines PCWEB-073 append mutation preflight checks with PCWEB-071 retry/replay interpretation, then reports whether the future commit unit is still blocked, safely explainable as replay, or blocked by conflict/partial replay. It never starts or commits a transaction.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-commit-preflights
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

Only `mode=preflight_only` is accepted. `mode` is not trimmed; whitespace-padded mode is rejected as unsupported.

## Response Shape

The response preserves PCWEB-073 mutation preflight output and PCWEB-071 retry/replay output, then adds:

```json
{
  "append_transaction_commit_preflight_mode": "preflight_only",
  "append_transaction_commit_preflight_status": "analyzed",
  "transaction_commit_readiness_status": "blocked_until_enabled",
  "transaction_commit_preflight_check_count": 2,
  "transaction_commit_preflight_checks": [
    {
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
  "safe_to_mutate_events": false,
  "safe_to_append_events": false,
  "safe_to_write_idempotency_store": false,
  "safe_to_write_audit_events": false,
  "safe_to_create_card": false
}
```

## Readiness Rules

Top-level `transaction_commit_readiness_status` values:

- `blocked_until_enabled`: no existing append/replay conflict is detected, but the commit path is disabled.
- `safe_replay_existing_events`: all future lifecycle events already exist and match request, draft, idempotency, event type, card id, and nested card identity. This still does not allow commit.
- `blocked_by_partial_replay`: some lifecycle events match as safe replay while others are missing. The preflight must not allow filling the missing tail.
- `blocked_by_retry_replay_conflict`: retry/replay found mismatched event identity, mismatched metadata, existing idempotency marker without a matching event, or duplicate idempotency evidence.
- `blocked_by_mutation_preflight`: mutation preflight is blocked and retry/replay did not classify the situation as safe replay, partial replay, or retry/replay conflict.

Per-check `transaction_commit_preflight_check_status` values:

- `blocked_until_enabled`
- `safe_replay_existing_event`
- `blocked_by_partial_replay`
- `blocked_by_retry_replay_conflict`
- `blocked_by_mutation_preflight`

## Commit Preflight Rules

- PCWEB-074 reuses PCWEB-073 mutation checks and PCWEB-071 retry/replay checks.
- It must not recompute lifecycle preview events, serialized event payloads, idempotency keys, replay checks, or sequence placement outside those helpers.
- Retry/replay interpretation takes precedence for existing events:
  - `safe_to_replay` becomes `safe_replay_existing_events`, but every write/commit safety flag remains false.
  - `blocked_by_partial_replay` remains blocked and must not be treated as an append opportunity.
  - `blocked_by_conflict` remains blocked even if one mutation check appears eligible.
- Fresh no-existing append returns `blocked_until_enabled`, not `ready_to_commit`.
- PCWEB-074 never marks begin, commit, rollback, event append, audit append, idempotency write, mutation, or card creation safe.

## Non-Goals

- No enabled commit mode.
- No repository transaction begin, commit, rollback, compensation, or lock acquisition.
- No idempotency store write.
- No event append.
- No append result audit event write.
- No retry queue mutation.
- No card store or formal report request.
- No remote ASR or LLM call.
- No provider config or secret read.

## Boundaries

- PCWEB-074 must not write `/events`.
- It must not write an idempotency store.
- It must not begin, commit, or roll back a repository transaction.
- It must not create real `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` events.
- It must not write `card_lifecycle_append_result` audit events.
- It must not estimate token cost.
- It must not call remote ASR, LLM, relay, or any HTTP gateway.
- It must not read provider config, API keys, authorization headers, bearer tokens, environment secrets, keychain, secret adapters, or `configs/local/`.
- It must not check provider config file existence, readability, size, mtime, hash, or fingerprint.
- `safe_replay_existing_events` means the already-persisted events are explainable as a replay; it does not mean a new commit is safe.

## Why This Matters

The product needs durable real-time suggestion lifecycle events for audit, feedback, retry, and post-meeting review. But a partially enabled commit path is more dangerous than no commit path: it can duplicate cards, write mismatched idempotency markers, or append half of a lifecycle unit. PCWEB-074 narrows that risk by making the future transaction commit contract inspectable before any mutation is enabled.

## Tests

- Fresh allowed lifecycle returns transaction commit preflight checks for `llm_schema_result` plus `suggestion_card`, with `transaction_commit_readiness_status=blocked_until_enabled`, without mutating `/events`.
- Complete matching replay returns `transaction_commit_readiness_status=safe_replay_existing_events`, keeps `safe_to_commit_transaction=false`, and does not append or rewrite events.
- Partial replay returns `transaction_commit_readiness_status=blocked_by_partial_replay`, with missing events marked `blocked_by_partial_replay`, and does not append the missing tail.
- Retry/replay conflicts return `transaction_commit_readiness_status=blocked_by_retry_replay_conflict`.
- Schema-invalid and policy-blocked lifecycles still cover `llm_schema_result` plus `suggestion_silenced`.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-074 and the transaction commit preflight boundary.

## Implementation Status

- Status: implemented and focused-test verified in the PCWEB-074 TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-commit-preflights`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.

## Verification Plan

- TDD RED: focused PCWEB-074 tests must fail before implementation.
- Focused PCWEB-074 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan from the operator checklist.

Verification result on 2026-07-02:

- RED: the ten new PCWEB-074 endpoint tests failed with `404 Not Found` before implementation; README gate passed.
- Focused PCWEB-074 plus README gate: `11 passed, 209 deselected, 2 warnings`.
- Backend regression: `258 passed, 2 warnings`.
