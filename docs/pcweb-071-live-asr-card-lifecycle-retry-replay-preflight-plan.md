# PCWEB-071 Live ASR Card Lifecycle Retry Replay Preflight Plan

## Context

PCWEB-066 through PCWEB-070 define the local-only card lifecycle persistence chain:

```text
lifecycle preview -> append preflight -> disabled append run
  -> repository dry-run -> disabled transaction run
  -> append result audit preview
```

That chain can detect that a future event id or idempotency key already exists, but it still treats every existing event as a generic blocked conflict. Before any enabled append can write to `/events`, the product needs a stricter retry/replay preflight that can tell whether a repeated request is a safe replay of the same append or a real conflict that must stop the transaction.

PCWEB-071 adds that boundary without enabling mutation. It reuses PCWEB-070 append result audit preview as the source of truth for the future lifecycle items, then inspects the current Live ASR audit record to classify existing event/idempotency evidence.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights
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

Only `mode=preflight_only` is accepted.

## Response Shape

The response reuses PCWEB-070 append result audit preview output and adds:

```json
{
  "retry_replay_preflight_mode": "preflight_only",
  "retry_replay_preflight_status": "analyzed",
  "retry_replay_resolution_status": "no_existing_append",
  "retry_replay_check_count": 2,
  "retry_replay_checks": [
    {
      "retry_replay_check_id": "asr_card_lifecycle_retry_replay_preflight_llm_schema_result_card_dry_run_001",
      "retry_replay_check_status": "no_existing_append",
      "resolution_status": "no_existing_append",
      "event_type": "llm_schema_result",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "existing_event_match_status": "not_found",
      "existing_idempotency_match_status": "not_found",
      "safe_to_replay_event": false,
      "safe_to_append_event": false,
      "event_append_status": "not_appended",
      "idempotency_store_status": "not_written"
    }
  ],
  "safe_to_replay_existing_events": false,
  "safe_to_mutate_events": false,
  "safe_to_append_events": false,
  "event_append_status": "not_appended",
  "idempotency_store_status": "not_written"
}
```

## Resolution Semantics

Per-item `resolution_status` values:

- `no_existing_append`: no existing event id and no existing idempotency key were found for the future lifecycle event.
- `safe_replay_same_event`: the existing event id, event type, append idempotency key, card id, request id, request draft event id, and card payload identity match the future lifecycle event. If an event contains both top-level and payload idempotency keys, every key must match the expected append idempotency key. For `suggestion_card`, the nested `payload.card.id` must also match. This is a future-safe replay signal only; it still does not allow mutation in PCWEB-071.
- `blocked_mismatched_replay`: the future event id already exists but its event type, append idempotency key, internal idempotency-key copies, card identity, request linkage, request draft linkage, or nested card identity does not match the current request.
- `blocked_existing_idempotency_key`: the future append idempotency key already exists on a different event, appears more than once, or appears on an idempotency marker without the matching lifecycle event.
- `blocked_partial_replay`: some lifecycle items are safe replays while others are missing. A future enabled transaction must not silently append the missing tail without an explicit recovery strategy.

Top-level `retry_replay_resolution_status` values:

- `no_existing_append`: all lifecycle items have no existing append evidence.
- `safe_to_replay`: all lifecycle items are `safe_replay_same_event`.
- `blocked_by_partial_replay`: at least one item is `safe_replay_same_event` and at least one item is `no_existing_append`, with no other conflicts.
- `blocked_by_conflict`: any item is `blocked_mismatched_replay` or `blocked_existing_idempotency_key`.

`safe_to_replay_existing_events=true` only when every item is `safe_replay_same_event`. `safe_to_append_events=false` and `safe_to_mutate_events=false` are always false in PCWEB-071.

## Rules

- PCWEB-071 reuses PCWEB-070 append result audit preview as the source of truth.
- It may inspect the current Live ASR audit record events to classify event id/idempotency evidence.
- It must not write `/events`.
- It must not write an idempotency store.
- It must not begin, commit, or roll back a repository transaction.
- It must not write append result audit events.
- It must not create `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` events.
- It must not estimate token cost.
- It must not call remote ASR, LLM, relay, or any HTTP gateway.
- It must not read provider config, API keys, authorization headers, bearer tokens, environment secrets, keychain, secret adapters, or `configs/local/`.
- It must not check provider config file existence, readability, size, mtime, hash, or fingerprint.
- Response identities should use percent-encoded tuple components where collision resistance matters.
- Existing generated append idempotency keys are not secrets; they may be returned for local auditability.

## Why This Matters

Enabled lifecycle append will need idempotent retry behavior. If a request is repeated after a successful write, the system should be able to identify the already-applied events and return a safe replay result instead of duplicating cards. If another event or idempotency marker occupies the same identity, the system must stop before appending anything.

PCWEB-071 is a value-preserving boundary: it moves the product closer to reliable real-time card persistence while still avoiding the risky step of actually mutating the audit log. This protects the product from becoming a fragile "transcribe then summarize" tool by making the future advice lifecycle explainable, idempotent, and recoverable.

## Tests

- No existing append evidence returns `retry_replay_resolution_status=no_existing_append`, two checks, and no `/events` mutation.
- Existing matching lifecycle events return `safe_to_replay` with every item `safe_replay_same_event`.
- Existing event id with mismatched idempotency, event metadata, request linkage, request draft linkage, or card identity returns `blocked_by_conflict` and item `blocked_mismatched_replay`.
- Existing event id with conflicting top-level vs payload idempotency keys returns `blocked_by_conflict` and item `blocked_mismatched_replay`.
- Duplicate idempotency evidence, including a marker with the same key alongside an otherwise matching lifecycle event, returns `blocked_by_conflict` and item `blocked_existing_idempotency_key`.
- Existing idempotency marker without the matching lifecycle event returns `blocked_by_conflict` and item `blocked_existing_idempotency_key`.
- Partial replay returns `blocked_by_partial_replay`.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-071 and the retry/replay preflight boundary.

## Implementation Status

- Status: implemented in this TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.
- TDD RED result: 9 endpoint tests failed with `404 Not Found` before implementation; README gate passed after documentation was added.
- First GREEN result: focused PCWEB-071 plus README gate passed, 10 passed, 180 deselected, 2 warnings.
- Review hardening: a code review found that safe replay was too narrow and duplicate idempotency evidence was not detected. PCWEB-071 was tightened so safe replay requires semantic request/card identity matches and any duplicate append idempotency evidence blocks replay.
- Final focused result after hardening: focused PCWEB-071 plus README gate passed, 13 passed, 180 deselected, 2 warnings.
- Final backend regression result: `tests/test_app.py tests/test_live_events.py` passed, 231 passed, 2 warnings.
- Final `pc-web` quality gate result: core 34 passed, web backend 234 passed, browser smoke passed.
- Final `all-local --no-browser` quality gate result: ASR runtime 65 passed, ASR bakeoff 18 passed, core 34 passed, web backend 234 passed.

## Verification Plan

- TDD RED: focused PCWEB-071 tests must fail before implementation.
- Focused PCWEB-071 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan from the operator checklist.
