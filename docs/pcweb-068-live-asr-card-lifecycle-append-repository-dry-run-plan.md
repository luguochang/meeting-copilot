# PCWEB-068 Live ASR Card Lifecycle Append Repository Dry-Run Plan

## Goal

Add a local repository-append contract dry-run for future Live ASR card lifecycle event persistence.

PCWEB-067 provides a disabled action endpoint: callers can attempt the lifecycle append boundary, but the server returns skipped runs and does not mutate `/events`. PCWEB-068 answers the next repository question without writing anything: if an enabled append repository existed later, what append result envelope would each lifecycle event produce, and which preflight state would block it?

This continues the path toward real-time auditable meeting advice while preserving the current zero-cost, no-secret, no-event-mutation boundary.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-repository-dry-runs
```

Request body:

```json
{
  "mode": "dry_run_only",
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "candidate_response": {
    "id": "card_dry_run_001",
    "type": "owner_gap",
    "evidence_span_ids": ["asr_ev_asr_seg_001"],
    "state_refs": ["DecisionCandidate:asr_decision_asr_seg_001"],
    "state_event_ids": ["asr_state_event_asr_seg_001"],
    "gap_rule_id": "release.rollback.owner.required",
    "trigger_reason": "repository dry-run sample",
    "trigger_source": "llm_card_lifecycle_append_repository_dry_run",
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

Response shape for a conflict-free repository dry-run:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "repository_dry_run_mode": "dry_run_only",
  "repository_dry_run_status": "would_append_if_enabled",
  "append_run_status": "skipped",
  "append_preflight_status": "allowed",
  "future_lifecycle_status": "would_create_card",
  "safe_to_append_events": false,
  "safe_to_create_card": false,
  "event_append_status": "not_appended",
  "idempotency_store_status": "not_written",
  "repository_append_count": 2,
  "repository_results": [
    {
      "repository_result_id": "asr_card_lifecycle_repository_dry_run_llm_schema_result_card_dry_run_001",
      "repository_result_status": "would_append_if_enabled",
      "event_type": "llm_schema_result",
      "future_event_id": "llm_schema_result:card_dry_run_001",
      "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
      "preflight_append_status": "would_append_once_if_enabled",
      "preflight_conflict_status": "none",
      "would_append_sequence": 8,
      "would_append_after_sequence": 7,
      "idempotency_key": "live_asr_card_lifecycle_append:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "repository_idempotency_key": "live_asr_card_lifecycle_repository_dry_run:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:llm_schema_result:card_dry_run_001",
      "event_append_status": "not_appended",
      "idempotency_store_status": "not_written",
      "repository_write_status": "dry_run_only",
      "safe_to_append_event": false
    }
  ],
  "block_reasons": [
    "repository_append_dry_run_only",
    "event_mutation_disabled"
  ],
  "next_required_decisions": [
    "repository_append_transaction",
    "idempotency_store_write_contract",
    "append_result_audit_event",
    "retry_and_replay_conflict_resolution",
    "enabled_card_lifecycle_mutation"
  ]
}
```

If PCWEB-066 preflight is blocked by an existing future event id or idempotency key, PCWEB-068 still returns 200 but sets `repository_dry_run_status=blocked_by_preflight`, preserves `append_preflight_status=blocked` and `append_errors`, and marks affected repository result items as `repository_result_status=blocked_by_preflight`. It still does not write to `/events` or an idempotency store.

Schema-invalid or policy-blocked candidates can still produce a conflict-free repository dry-run for `llm_schema_result` plus `suggestion_silenced`; the dry-run only describes safe future repository append behavior, not whether a formal card should be shown.

Request shape violations return 422 before validation. Missing session and unknown request id return 404.

## Repository Dry-Run Rules

PCWEB-068 reuses PCWEB-067 disabled append run, which itself reuses PCWEB-066 append preflight.

- `mode` must be exactly `dry_run_only`.
- `repository_dry_run_status=would_append_if_enabled` only when append preflight is allowed.
- `repository_dry_run_status=blocked_by_preflight` when append preflight is blocked.
- `repository_append_count` equals `append_plan_count` and `append_run_count`.
- Each `repository_result` references exactly one append plan item and one skipped append run item.
- `repository_result_id` is deterministic from percent-encoded event type and card id components: `asr_card_lifecycle_repository_dry_run:{event_type_token}:{card_id_token}` expressed with underscores in the response id.
- `repository_idempotency_key` is deterministic from percent-encoded tuple components: `live_asr_card_lifecycle_repository_dry_run:{session_id_token}:{request_id_token}:{event_type_token}:{card_id_token}`.
- Percent-encoded component tokens preserve delimiter-bearing values such as `card:dry_run:001` distinctly from `card_dry_run_001`; this dry-run contract must not rely on punctuation-collapsing display tokens for future repository idempotency.
- Existing `append_errors`, `validation_errors`, `policy_errors`, request linkage, evidence linkage, state linkage, segment linkage and sequence metadata must be preserved from preflight.
- `event_append_status=not_appended`.
- `idempotency_store_status=not_written`.
- `repository_write_status=dry_run_only`.
- `safe_to_append_event=false` on every repository result.
- `safe_to_append_events=false` and `safe_to_create_card=false` on the response.

## Boundaries

- POST is repository append dry-run only.
- No event mutation or appended lifecycle event.
- No repository transaction.
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
- No enabled append mode.
- No retry worker, queue worker, background task, cursor, or pagination.

## Why This Matters

The product needs a reliable audit chain before real-time AI advice can be trusted: request draft -> schema result -> card or silenced lifecycle -> append repository result -> feedback and replay. PCWEB-068 keeps the repository boundary visible and testable without opening the mutation path too early.

The disabled run endpoint says “the action boundary exists but is disabled.” The repository dry-run says “this is the repository transaction shape we will eventually need, and this is why it still did not write.”

## Tests

- Allowed repository dry-run returns `repository_dry_run_status=would_append_if_enabled`, two deterministic repository result items for `llm_schema_result` plus `suggestion_card`, and does not mutate `/events`.
- The endpoint must not read provider config files, environment secrets, keychain, or call LLM.
- Schema-invalid candidates return repository results for `llm_schema_result` plus `suggestion_silenced`, preserving validation/policy errors.
- Policy-blocked candidates return repository results for `llm_schema_result` plus `suggestion_silenced`.
- Existing future event id or idempotency key returns 200 with `repository_dry_run_status=blocked_by_preflight`, preserved `append_errors`, and blocked repository results for affected items.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances.
- Non-object body, missing mode, non-string `mode/request_id`, unsupported mode, whitespace-padded mode, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-068 and the repository append dry-run boundary.
- Delimiter-bearing card ids must not collide with underscore-bearing card ids in `repository_result_id` or `repository_idempotency_key`.
- JSON-persisted Live ASR session files must remain byte-identical before and after the repository dry-run POST.
- No-secret-read guards cover every PCWEB-068 branch, arbitrary `configs/local/**` paths, direct secret environment reads, keychain adapters, provider config loaders, and outbound LLM/HTTP seams.

## Implementation Status

- Status: implemented in this TDD increment.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-repository-dry-runs`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.
- Documentation updated in README, traceability, acceptance, project structure, privacy/data flow, implementation roadmap, and end-to-end checklist.
- Review hardening added collision-resistant repository component tokens, README gate assertions, persisted-record byte no-mutation coverage, and broader no-secret-read guards.

## Verification Plan

- TDD RED: focused PCWEB-068 tests must fail before implementation.
- Focused PCWEB-068 pytest plus README contract gate.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- Quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Quality gate: `python3 tools/run_quality_gate.py --profile all-local --no-browser`.
- Final hygiene: remove `.pytest_cache` and `__pycache__`, confirm no app test ports are listening, and run the local sensitive marker scan.

## Verification Evidence

- RED before implementation: `python3 -m pytest tests/test_app.py -k "append_repository or readme_documents_scripted_browser_e2e_gate" -q` returned 8 endpoint failures with `404 Not Found` and 1 existing README gate pass.
- GREEN after implementation: `python3 -m pytest tests/test_app.py -k "append_repository or readme_documents_scripted_browser_e2e_gate" -q` returned 9 passed before documentation synchronization.
- Review-fix RED: delimiter-bearing card id and underscore-bearing card id produced colliding repository result ids before component encoding.
- Review-fix GREEN: focused PCWEB-068 plus README gate returned 10 passed after component encoding, broader no-secret-read guards, persisted-record byte no-mutation coverage, and README assertions.
- Final verification after documentation synchronization is tracked in `docs/superpowers/plans/2026-07-02-pcweb-068-live-asr-card-lifecycle-append-repository-dry-run.md`.
