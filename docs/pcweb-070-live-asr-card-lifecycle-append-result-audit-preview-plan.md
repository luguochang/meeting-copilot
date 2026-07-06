# PCWEB-070 Live ASR Card Lifecycle Append Result Audit Preview Plan

## Context

PCWEB-069 defines the disabled transaction-run boundary for future card lifecycle persistence. It proves that the transaction entrypoint exists, but the system still has no explicit contract for the audit event that an enabled append would write after the repository transaction completes or is blocked.

PCWEB-070 adds the next local-only boundary: an append result audit event preview. It derives deterministic response-only audit event previews from PCWEB-069 transaction runs while keeping event mutation, repository transactions, idempotency-store writes, credentials and LLM calls disabled.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-previews
```

Request body:

```json
{
  "mode": "preview_only",
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "candidate_response": {
    "id": "card_dry_run_001"
  }
}
```

Only `mode=preview_only` is accepted.

## Response Shape

The response reuses PCWEB-069 transaction disabled-run output and adds:

```json
{
  "append_result_audit_mode": "preview_only",
  "append_result_audit_status": "previewed",
  "append_result_audit_event_status": "preview_only",
  "append_result_audit_event_count": 2,
  "append_result_audit_events": [
    {
      "audit_event_id": "asr_card_lifecycle_append_result_audit_preview_llm_schema_result_card_dry_run_001",
      "audit_event_type": "card_lifecycle_append_result",
      "audit_event_status": "preview_only",
      "audit_result_status": "skipped_transaction_disabled",
      "transaction_run_id": "asr_card_lifecycle_append_transaction_run_disabled_llm_schema_result_card_dry_run_001",
      "transaction_idempotency_key": "live_asr_card_lifecycle_append_transaction_run:disabled:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "repository_result_id": "asr_card_lifecycle_repository_dry_run_llm_schema_result_card_dry_run_001",
      "repository_result_status": "would_append_if_enabled",
      "event_type": "llm_schema_result",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "event_append_status": "not_appended",
      "idempotency_store_status": "not_written",
      "idempotency_store_write_status": "not_written",
      "repository_transaction_status": "disabled",
      "safe_to_write_audit_event": false,
      "safe_to_append_event": false
    }
  ],
  "audit_event_append_status": "not_appended",
  "safe_to_write_audit_events": false
}
```

## Allowed Results

When PCWEB-069 transaction runs are blocked only because the transaction path is disabled:

- `audit_result_status=skipped_transaction_disabled`.
- `skip_reason=repository_transaction_disabled`.

When PCWEB-066/068/069 preflight is blocked by an existing future event id or idempotency key:

- `audit_result_status=blocked_by_preflight`.
- `skip_reason=repository_preflight_blocked`.
- `repository_result_status=blocked_by_preflight`.
- `preflight_conflict_status` preserves `existing_event_id` or `existing_idempotency_key`.

## Audit Preview Rules

- PCWEB-070 reuses PCWEB-069 transaction disabled-run as the source of truth.
- `append_result_audit_event_count` equals `transaction_run_count`, `repository_append_count`, `append_run_count`, and `append_plan_count`.
- Each `append_result_audit_event` references exactly one transaction run and one repository result.
- `audit_event_id` is deterministic from percent-encoded event type and card id components.
- `audit_idempotency_key` is deterministic from percent-encoded tuple components: `live_asr_card_lifecycle_append_result_audit_preview:{session_id_token}:{request_id_token}:{event_type_token}:{card_id_token}`.
- `transaction_idempotency_key` remains the canonical durable identity for the future transaction write; `audit_idempotency_key` is only the preview identity for the future result audit event.
- Existing request linkage, evidence linkage, state linkage, segment linkage, append errors, validation errors, policy errors and sequence metadata must be preserved from PCWEB-069.
- `append_result_audit_event_status=preview_only`.
- `audit_event_append_status=not_appended`.
- `event_append_status=not_appended`.
- `idempotency_store_status=not_written`.
- `idempotency_store_write_status=not_written`.
- `safe_to_write_audit_event=false` on every preview item.
- `safe_to_write_audit_events=false`, `safe_to_append_events=false`, and `safe_to_create_card=false` on the response.

## Boundaries

- POST is append-result audit preview only.
- No event mutation or appended audit event.
- No repository transaction begin/commit/rollback.
- No idempotency store write.
- No real `card_lifecycle_append_result` audit event.
- No real `llm_schema_result` event.
- No real `suggestion_card`.
- No real `suggestion_silenced`.
- No formal report request.
- No real LLM response parser.
- No gateway, relay, or remote LLM call.
- No API key, authorization header, bearer token, provider config, base URL, `configs/local/`, keychain, secret adapter, or environment secret read.
- No provider config file existence/readability/size/mtime/hash/fingerprint check.
- No token estimate or budget charge.
- No enabled append-result audit mode.
- No retry worker, queue worker, background task, cursor, or pagination.

## Why This Matters

The product needs the full lifecycle to be inspectable before any real write is enabled: request draft -> schema result -> card or silenced lifecycle -> append repository result -> disabled transaction run -> append result audit event -> feedback and replay. PCWEB-070 makes the future append result audit event explicit without claiming that a transaction committed or that a lifecycle event was written.

This keeps the product's real-time AI advice chain valuable: operators will be able to explain why a card appeared, why it was silenced, or why a persistence attempt was skipped. It also prevents a future enabled path from silently writing cards without an auditable result trail.

## Tests

- Allowed append result audit preview returns `append_result_audit_status=previewed`, two preview audit events for `llm_schema_result` plus `suggestion_card`, and does not mutate `/events`.
- The endpoint must not read provider config files, environment secrets, keychain, or call LLM.
- Schema-invalid candidates return audit previews for `llm_schema_result` plus `suggestion_silenced`, preserving validation/policy errors.
- Policy-blocked candidates return audit previews for `llm_schema_result` plus `suggestion_silenced`.
- Existing future event id or idempotency key returns 200 with `repository_dry_run_status=blocked_by_preflight`, preserved `append_errors`, and audit previews marked `blocked_by_preflight`.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-070 and the append result audit preview boundary.

## Implementation Status

- Status: implemented in this TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-previews`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.
- TDD RED result: 9 endpoint tests failed with `404 Not Found` before implementation; README gate already passed after documentation.
- TDD GREEN result: focused PCWEB-070 plus README gate passed, 10 passed, 171 deselected, 2 warnings.

## Verification Plan

- TDD RED: focused PCWEB-070 tests must fail before implementation.
- Focused PCWEB-070 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan.
