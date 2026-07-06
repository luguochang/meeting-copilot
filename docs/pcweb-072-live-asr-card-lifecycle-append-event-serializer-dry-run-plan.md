# PCWEB-072 Live ASR Card Lifecycle Append Event Serializer Dry-Run Plan

## Context

PCWEB-065 through PCWEB-071 make the future Live ASR card lifecycle append path inspectable without mutating the audit record:

```text
lifecycle preview -> append preflight -> disabled append run
  -> repository dry-run -> disabled transaction run
  -> append result audit preview -> retry/replay preflight
```

The next persistence question is the exact event object that a future enabled repository append would write. PCWEB-072 adds a serializer dry-run that turns the preview and append plan into canonical future `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` event objects while keeping repository writes disabled.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-event-serializer-dry-runs
```

Request body:

```json
{
  "mode": "dry_run_only",
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "candidate_response": {
    "id": "card_dry_run_001"
  }
}
```

Only `mode=dry_run_only` is accepted.

## Response Shape

The response reuses PCWEB-066 append preflight output and adds:

```json
{
  "append_event_serializer_mode": "dry_run_only",
  "append_event_serializer_status": "serialized",
  "append_event_serialization_status": "would_serialize_if_enabled",
  "append_event_count": 2,
  "serialized_append_events": [
    {
      "serializer_result_id": "asr_card_lifecycle_append_event_serializer_llm_schema_result_card_dry_run_001",
      "serialization_status": "would_serialize_if_enabled",
      "event_id": "llm_schema_result:card_dry_run_001",
      "id": "llm_schema_result:card_dry_run_001",
      "event_type": "llm_schema_result",
      "sequence": 31,
      "at_ms": 3700,
      "source": "live_asr_stream",
      "trace_kind": "live_event",
      "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "payload": {
        "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
        "card_id": "card_dry_run_001",
        "schema_result": "valid",
        "show_or_silence_decision": "show",
        "usage": {"total_tokens": 0},
        "latency_ms": 200,
        "validation_errors": [],
        "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001"
      },
      "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "append_status": "would_append_once_if_enabled",
      "conflict_status": "none",
      "would_append_sequence": 31,
      "would_append_after_sequence": 30,
      "event_append_status": "not_appended",
      "idempotency_store_status": "not_written",
      "idempotency_store_write_status": "not_written",
      "safe_to_append_event": false,
      "safe_to_create_card": false
    }
  ],
  "event_append_status": "not_appended",
  "idempotency_store_status": "not_written",
  "idempotency_store_write_status": "not_written",
  "safe_to_append_events": false,
  "safe_to_create_card": false
}
```

## Serialization Rules

- PCWEB-072 reuses PCWEB-066 append preflight as the source of truth for future event ids, idempotency keys, append sequence, append status, and conflict status.
- It also reuses PCWEB-065 preview events as the source of truth for payload fields.
- Each serialized event must include:
  - `id` and `event_id` equal to the future event id.
  - `event_type`.
  - `sequence` equal to `would_append_sequence`.
  - `at_ms` from the preview event.
  - `source=live_asr_stream`.
  - `trace_kind=live_event`.
  - top-level `idempotency_key`.
  - `payload.idempotency_key` matching the top-level idempotency key.
  - `payload.request_id`, `payload.request_draft_event_id`, and `payload.card_id`.
  - event-specific payload fields from the lifecycle preview.
  - `preview_event_id` that points to an actual PCWEB-065 preview event.
  - `future_event_id` equal to `id` and `event_id`.
  - `append_status`, `conflict_status`, `would_append_sequence`, and `would_append_after_sequence` from the PCWEB-066 append plan.
  - top-level and per-event `idempotency_store_write_status=not_written`.
- Serialized event payloads must match the corresponding preview event payload exactly, except for injecting the append `idempotency_key`.
- Matching between append plan items and preview events is by `preview_event_id`/future event identity, not by assuming list order.
- Allowed card lifecycle creates `llm_schema_result` plus `suggestion_card`.
- Schema or policy blocked lifecycle creates `llm_schema_result` plus `suggestion_silenced`.
- Existing event/idempotency conflicts must be preserved as `serialization_status=blocked_by_preflight` on the affected serialized item and `append_event_serialization_status=blocked_by_preflight` at the top level.

## Non-Goals

- No enabled append mode.
- No repository transaction.
- No idempotency store write.
- No append result audit event write.
- No retry queue.
- No card store.
- No remote ASR or LLM call.
- No provider config or secret read.

## Boundaries

- PCWEB-072 must not write `/events`.
- It must not write an idempotency store.
- It must not begin, commit, or roll back a repository transaction.
- It must not create real `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` events.
- It must not estimate token cost.
- It must not call remote ASR, LLM, relay, or any HTTP gateway.
- It must not read provider config, API keys, authorization headers, bearer tokens, environment secrets, keychain, secret adapters, or `configs/local/`.
- It must not check provider config file existence, readability, size, mtime, hash, or fingerprint.
- `safe_to_append_events=false`, `safe_to_create_card=false`, and per-event `safe_to_append_event=false` are always false in PCWEB-072.

## Why This Matters

The product should not open mutation until the exact future persisted event shape is stable. Without a canonical serializer, a later enabled repository append could drift from preview/preflight contracts, produce inconsistent payload idempotency keys, or write events that cannot be safely replayed.

PCWEB-072 moves the product closer to durable card lifecycle persistence while keeping the current no-cost, no-secret, no-mutation operating model intact.

## Tests

- Allowed lifecycle serializes `llm_schema_result` plus `suggestion_card` canonical event objects without mutating `/events`.
- Schema-invalid lifecycle serializes `llm_schema_result` plus `suggestion_silenced`.
- Policy-blocked lifecycle serializes `llm_schema_result` plus `suggestion_silenced`.
- Existing preflight conflicts are preserved as blocked serialized events.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-072 and the append event serializer dry-run boundary.

## Implementation Status

- Status: implemented and verified in the PCWEB-072 TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-event-serializer-dry-runs`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.

## Verification Plan

- TDD RED: focused PCWEB-072 tests must fail before implementation.
- Focused PCWEB-072 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan from the operator checklist.

Verification result on 2026-07-02:

- RED: focused PCWEB-072 endpoint tests failed with `404 Not Found` before implementation; README gate passed.
- Focused PCWEB-072 plus README gate: `10 passed, 192 deselected, 2 warnings`.
- Focused serializer-only tests: `9 passed, 193 deselected, 2 warnings`.
- Backend regression: `240 passed, 2 warnings`.
- `pc-web` quality gate: core `34 passed`, web backend `243 passed`, browser smoke passed.
- `all-local --no-browser` quality gate: ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, web backend `243 passed`.
