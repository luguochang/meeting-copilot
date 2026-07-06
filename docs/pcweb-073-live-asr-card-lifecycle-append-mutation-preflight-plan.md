# PCWEB-073 Live ASR Card Lifecycle Append Mutation Preflight Plan

## Context

PCWEB-065 through PCWEB-072 make the future Live ASR card lifecycle append path inspectable without mutating the audit record:

```text
lifecycle preview -> append preflight -> disabled append run
  -> repository dry-run -> disabled transaction run
  -> append result audit preview -> retry/replay preflight
  -> append event serializer dry-run
```

PCWEB-072 locks the canonical future event objects. The next persistence question is not "write them now"; it is whether those canonical objects are eligible to enter a future mutation transaction. PCWEB-073 adds a response-only append mutation preflight that analyzes PCWEB-072 serialized events and reports why mutation remains disabled.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-mutation-preflights
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

The response reuses PCWEB-072 append event serializer output and adds:

```json
{
  "append_mutation_preflight_mode": "preflight_only",
  "append_mutation_preflight_status": "analyzed",
  "append_mutation_readiness_status": "blocked_until_enabled",
  "mutation_preflight_check_count": 2,
  "mutation_preflight_checks": [
    {
      "mutation_preflight_check_id": "asr_card_lifecycle_append_mutation_preflight_llm_schema_result_card_dry_run_001",
      "mutation_preflight_check_status": "blocked_until_enabled",
      "serializer_result_id": "asr_card_lifecycle_append_event_serializer_llm_schema_result_card_dry_run_001",
      "serialization_status": "would_serialize_if_enabled",
      "event_type": "llm_schema_result",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "serialized_event_id": "llm_schema_result:card_dry_run_001",
      "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
      "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "would_append_sequence": 31,
      "would_append_after_sequence": 30,
      "append_status": "would_append_once_if_enabled",
      "conflict_status": "none",
      "event_append_status": "not_appended",
      "idempotency_store_status": "not_written",
      "idempotency_store_write_status": "not_written",
      "repository_transaction_status": "not_started",
      "safe_to_mutate_event": false,
      "safe_to_commit_transaction": false,
      "safe_to_append_event": false,
      "safe_to_create_card": false
    }
  ],
  "event_append_status": "not_appended",
  "idempotency_store_status": "not_written",
  "idempotency_store_write_status": "not_written",
  "repository_transaction_status": "not_started",
  "safe_to_mutate_events": false,
  "safe_to_commit_transaction": false,
  "safe_to_append_events": false,
  "safe_to_create_card": false
}
```

If any serialized event is blocked by PCWEB-072 serializer/preflight status, the top-level `append_mutation_readiness_status` is `blocked_by_serializer_preflight`, and the affected item has `mutation_preflight_check_status=blocked_by_serializer_preflight`.

## Mutation Preflight Rules

- PCWEB-073 reuses PCWEB-072 `serialized_append_events` as the source of truth.
- It must not recompute lifecycle preview events, append plan items, event payloads, idempotency keys, or sequences outside the PCWEB-072 helper.
- Each mutation preflight check is a thin projection of one serialized event.
- Allowed card lifecycle returns checks for `llm_schema_result` plus `suggestion_card`.
- Schema or policy blocked lifecycle returns checks for `llm_schema_result` plus `suggestion_silenced`.
- Serialized events with `serialization_status=would_serialize_if_enabled` become `mutation_preflight_check_status=blocked_until_enabled`.
- Serialized events with any other serialization status become `mutation_preflight_check_status=blocked_by_serializer_preflight`.
- PCWEB-073 never marks mutation safe. `safe_to_mutate_events=false`, `safe_to_commit_transaction=false`, `safe_to_append_events=false`, `safe_to_create_card=false`, and per-check `safe_to_mutate_event=false` are always false.

## Non-Goals

- No enabled append mode.
- No repository transaction begin, commit, rollback, or compensation.
- No idempotency store write.
- No event append.
- No append result audit event write.
- No retry queue mutation.
- No card store.
- No remote ASR or LLM call.
- No provider config or secret read.

## Boundaries

- PCWEB-073 must not write `/events`.
- It must not write an idempotency store.
- It must not begin, commit, or roll back a repository transaction.
- It must not create real `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` events.
- It must not estimate token cost.
- It must not call remote ASR, LLM, relay, or any HTTP gateway.
- It must not read provider config, API keys, authorization headers, bearer tokens, environment secrets, keychain, secret adapters, or `configs/local/`.
- It must not check provider config file existence, readability, size, mtime, hash, or fingerprint.
- `append_mutation_preflight_status=analyzed` means the response-only preflight ran; it does not mean mutation is enabled.

## Why This Matters

The product goal is real-time meeting assistance, not a transcript-only tool. Durable lifecycle events are needed for traceable suggestions, retries, user feedback, and post-meeting audit. But opening mutation too early risks corrupting the audit trail. PCWEB-073 narrows the remaining gap by proving the serialized events can be inspected as a future mutation unit while keeping the current no-cost, no-secret, no-mutation operating model intact.

## Tests

- Allowed lifecycle returns two mutation preflight checks for `llm_schema_result` plus `suggestion_card` without mutating `/events`.
- Schema-invalid lifecycle returns `llm_schema_result` plus `suggestion_silenced`.
- Policy-blocked lifecycle returns `llm_schema_result` plus `suggestion_silenced`.
- Serializer/preflight conflicts are preserved as `blocked_by_serializer_preflight`.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-073 and the append mutation preflight boundary.

## Implementation Status

- Status: implemented and focused-test verified in the PCWEB-073 TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-mutation-preflights`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.

## Verification Plan

- TDD RED: focused PCWEB-073 tests must fail before implementation.
- Focused PCWEB-073 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan from the operator checklist.

Verification result on 2026-07-02:

- RED: the eight new PCWEB-073 endpoint tests failed with `404 Not Found` before implementation; README gate passed.
- Focused PCWEB-073 plus README gate: `9 passed, 201 deselected, 2 warnings`.
- Backend regression: `248 passed, 2 warnings`.
- `pc-web` quality gate: core `34 passed`, web backend `251 passed`, browser smoke passed.
- `all-local --no-browser` quality gate: ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, web backend `251 passed`.
