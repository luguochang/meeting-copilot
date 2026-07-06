# PCWEB-077 Live ASR Card Lifecycle Readiness Summary Plan

## Context

PCWEB-065 through PCWEB-076 define the disabled local card lifecycle chain for a future Live ASR suggestion card: preview, append preflight, disabled append run, repository dry-run, disabled transaction run, append result audit preview, retry/replay preflight, serializer dry-run, mutation preflight, transaction commit preflight, idempotency-store write preflight, and append result audit event persistence preflight.

The chain is intentionally verbose because each phase needs precise write and replay boundaries before real persistence is enabled. That creates a product gap: the PC workbench needs a compact, user-facing answer to "can this candidate become a card yet, and if not, why?" without forcing the UI to understand every low-level preflight field.

PCWEB-077 adds a response-only readiness summary endpoint. It reuses PCWEB-076 as the source of truth, preserves upstream status traceability, and projects the 12 lifecycle phases into a stable summary for the future Live ASR card panel. It does not enable any real write path.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries
```

Request body:

```json
{
  "mode": "summary_only",
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "candidate_response": {
    "id": "card_dry_run_001"
  }
}
```

Only `mode=summary_only` is accepted. `mode` is not trimmed; whitespace-padded mode is rejected as unsupported. `request_id` is trimmed. No extra top-level fields are permitted.

## Response Shape

The response projects scoped source trace from PCWEB-076 and adds a UI-facing summary. It does not copy or spread the full PCWEB-076 response.

```json
{
  "card_lifecycle_readiness_summary_mode": "summary_only",
  "card_lifecycle_readiness_summary_status": "summarized",
  "card_lifecycle_overall_readiness_status": "blocked_until_enabled",
  "source_preflight_kind": "append_result_audit_event_persistence_preflight",
  "source_preflight_endpoint": "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-event-persistence-preflights",
  "source_preflight_mode": "preflight_only",
  "source_preflight_status": "analyzed",
  "source_readiness_status": "blocked_until_enabled",
  "source_check_count": 2,
  "card_lifecycle_summary_phase_count": 12,
  "card_lifecycle_summary_phases": [
    {
      "phase_id": "append_result_audit_event_persistence_preflight",
      "phase_status": "blocked_until_enabled",
      "phase_mode": "preflight_only",
      "phase_kind": "preflight",
      "write_boundary_status": "preflight_only",
      "item_count": 2,
      "safe_to_write": false,
      "source_status_field": "append_result_audit_event_persistence_readiness_status",
      "source_status_value": "blocked_until_enabled"
    }
  ],
  "card_lifecycle_next_required_decisions": [
    "enabled_append_result_audit_event_persistence",
    "enabled_idempotency_store_write",
    "enabled_repository_transaction_commit",
    "enabled_retry_replay_resolution_policy",
    "enabled_card_lifecycle_mutation"
  ],
  "card_lifecycle_block_reasons": [
    "append_result_audit_event_persistence_preflight_only",
    "append_result_audit_event_persistence_disabled",
    "idempotency_store_write_disabled",
    "repository_transaction_commit_disabled",
    "event_mutation_disabled"
  ],
  "card_lifecycle_safe_to_create_card": false,
  "card_lifecycle_safe_to_append_events": false,
  "card_lifecycle_safe_to_mutate_events": false,
  "card_lifecycle_safe_to_begin_transaction": false,
  "card_lifecycle_safe_to_commit_transaction": false,
  "card_lifecycle_safe_to_write_idempotency_store": false,
  "card_lifecycle_safe_to_persist_append_result_audit_event": false,
  "card_lifecycle_safe_to_execute_llm": false
}
```

The 12 phases are:

1. `card_lifecycle_preview`
2. `append_preflight`
3. `append_disabled_run`
4. `append_repository_dry_run`
5. `append_transaction_disabled_run`
6. `append_result_audit_preview`
7. `retry_replay_preflight`
8. `append_event_serializer_dry_run`
9. `append_mutation_preflight`
10. `append_transaction_commit_preflight`
11. `append_idempotency_store_write_preflight`
12. `append_result_audit_event_persistence_preflight`

## Readiness Rules

Top-level `card_lifecycle_overall_readiness_status` mirrors PCWEB-076 `append_result_audit_event_persistence_readiness_status`:

- `blocked_until_enabled`: the candidate is a fresh append candidate, but all write capabilities remain disabled.
- `safe_replay_existing_events`: the required lifecycle events already exist and match retry/replay identity. The UI may present this as an idempotent replay summary, but no new writes are permitted.
- `blocked_by_partial_replay`: only part of the required lifecycle evidence exists. The UI must surface partial replay as the primary blocker.
- `blocked_by_retry_replay_conflict`: retry/replay identity checks found mismatched or duplicate evidence.
- `blocked_by_idempotency_store_write_preflight`: an upstream transaction, mutation, serializer, or idempotency-store preflight blocks readiness before audit event persistence.

`card_lifecycle_next_required_decisions` is a unique ordered list derived from the PCWEB-076 source response, with summary-specific replay decisions added only when needed. Fresh append must include at least:

- `enabled_append_result_audit_event_persistence`
- `enabled_idempotency_store_write`
- `enabled_repository_transaction_commit`
- `enabled_retry_replay_resolution_policy`
- `enabled_card_lifecycle_mutation`

Safe replay must state that idempotency-store write and append result audit event persistence are not required for the replay path.

## Preflight Rules

- PCWEB-077 reuses PCWEB-076 as the source of truth.
- It must not recompute lifecycle preview, append preflight, retry/replay, serializer, mutation, transaction, idempotency, or audit persistence decisions outside the existing helper chain.
- It must not collapse source fields into ambiguous names. Phase summaries must keep `source_status_field` and `source_status_value` so a UI can click through to the detailed source response.
- It must preserve source traceability through scoped source fields such as `source_preflight_status`, `source_readiness_status`, `source_check_count`, `source_status_field`, and `source_status_value`.
- It must not copy unscoped upstream `safe_to_*` fields wholesale into the summary response. In particular, PCWEB-071 safe replay evidence must remain a replay/source status, not an executable permission.
- It must keep every exposed `card_lifecycle_safe_to_*` summary flag false until a future enabled mode is explicitly designed.

## Non-Goals

- No enabled card lifecycle mutation.
- No card store.
- No event append repository.
- No idempotency store write.
- No repository transaction begin, commit, rollback, compensation, lock, or lease.
- No append result audit event persistence.
- No real LLM execution.
- No provider config, API key, authorization header, bearer token, keychain, secret adapter, or `configs/local/` read.
- No desktop audio capture or ASR provider call.

## Boundaries

- PCWEB-077 must not write `/events`.
- It must not write a Live ASR audit record, append result audit event, idempotency store, idempotency marker, lifecycle event, card, or summary artifact.
- It must not begin, commit, or roll back a repository transaction.
- It must not create real `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` events.
- It must not estimate token cost.
- It must not call remote ASR, LLM, relay, or any HTTP gateway.
- It must not read provider config, API keys, authorization headers, bearer tokens, environment secrets, keychain, secret adapters, or `configs/local/`.
- It must not check provider config file existence, readability, size, mtime, hash, or fingerprint.
- `safe_replay_existing_events` means no new lifecycle, idempotency, or audit persistence writes are required for that replay; it does not mean a new card can be created.

## Why This Matters

The product value is not another low-level preflight endpoint. The value is a real-time assistant that can tell the user and the UI, while the meeting is ongoing, whether a candidate suggestion is ready, blocked, replayed, or unsafe. PCWEB-077 creates that product-facing seam while preserving the no-cost, no-secret, no-write safety boundary. It turns the existing deep lifecycle chain into a clear control-surface contract for the PC workbench.

## Tests

- Fresh allowed lifecycle returns `card_lifecycle_overall_readiness_status=blocked_until_enabled`, 12 summary phases, required next decisions, disabled safe flags, and unchanged `/events`.
- Complete matching replay returns `safe_replay_existing_events`, marks replay no-write policy in block reasons or decisions, keeps persistence and idempotency writes unsafe, does not expose any true `safe_to_*` action flag, preserves persisted bytes, and does not append events.
- Partial replay returns `blocked_by_partial_replay`, surfaces a partial replay blocker, and does not write missing tail events or audit evidence.
- Retry/replay conflict returns `blocked_by_retry_replay_conflict`.
- Upstream PCWEB-076 blocker maps to `blocked_by_idempotency_store_write_preflight` while preserving source readiness fields.
- Missing session returns 404.
- Unknown request id returns 404 through the PCWEB-076 source path.
- JSON persistence works across app instances and persisted session file bytes remain unchanged.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-077 and the readiness summary boundary.

## Implementation Status

- Status: implemented and focused-test verified in the PCWEB-077 TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.

## Verification Plan

- TDD RED: focused PCWEB-077 tests must fail before implementation.
- Focused PCWEB-077 pytest plus README/docs contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Independent read-only review, then final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan from the operator checklist.

Verification result on 2026-07-02:

- RED: the eight new PCWEB-077 endpoint tests failed with `404 Not Found` before implementation; README/docs gate passed after PCWEB-077 endpoint documentation was added.
- GREEN: focused PCWEB-077 plus README/docs gate passed with `9 passed, 241 deselected, 2 warnings`.
- PCWEB-077 now calls PCWEB-076 as the source of truth, returns scoped source trace and 12 phase summaries, and avoids copying unscoped upstream `safe_to_*` fields into the UI summary.
- Backend regression: `288 passed, 2 warnings`.
- Quality gate `pc-web`: core `34 passed`, web backend `291 passed`, browser smoke passed.
- Quality gate `all-local --no-browser`: ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, web backend `291 passed`.
- Independent read-only review found no Critical or Important issues. One Minor documentation wording issue was fixed so the plan now says PCWEB-077 projects scoped source trace from PCWEB-076 rather than preserving the full PCWEB-076 response.
