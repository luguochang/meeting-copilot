# PCWEB-064 Live ASR Card Creation Policy Dry-Run Plan

## Goal

Add a local card creation policy dry-run endpoint for future Live ASR LLM suggestion-card responses.

PCWEB-063 validates whether a caller-provided candidate response is shaped like a local `SuggestionCardV1`. PCWEB-064 answers the next question without creating anything: if schema validation passes, would this candidate be eligible for a future formal `suggestion_card`, or must it remain blocked/silenced/pending because its evidence, state, request draft, timing, schema result, or candidate quality does not satisfy the product policy?

This keeps the product moving toward real-time meeting advice while preserving the current no-cost, no-secret, no-event-mutation boundary.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-card-creation-policy-dry-runs
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
    "trigger_reason": "dry-run validation sample",
    "trigger_source": "llm_schema_validation_dry_run",
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

Response shape when the candidate would be eligible in a future enabled card lifecycle:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "policy_mode": "dry_run_only",
  "policy_status": "allowed",
  "card_creation_policy_status": "dry_run_allowed",
  "schema_validation_status": "dry_run_passed",
  "schema_result_status": "not_generated",
  "card_status": "not_created",
  "llm_call_status": "not_called",
  "credentials_status": "not_read",
  "config_source_status": "not_read",
  "cost_status": "not_estimated",
  "safe_to_create_card": false,
  "would_create_card_if_enabled": true,
  "would_silence_candidate_if_enabled": false,
  "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
  "request_draft_sequence": 6,
  "target_candidate_id": "asr_suggestion_candidate_asr_state_event_asr_seg_001",
  "target_type": "DecisionCandidate",
  "target_id": "asr_decision_asr_seg_001",
  "target_state_ref": "DecisionCandidate:asr_decision_asr_seg_001",
  "gap_rule_id": "release.rollback.owner.required",
  "source_event_ids": ["asr_state_event_asr_seg_001"],
  "evidence_span_ids": ["asr_ev_asr_seg_001"],
  "segment_batch": ["asr_seg_001"],
  "validation_errors": [],
  "policy_errors": [],
  "policy_check_count": 12,
  "candidate_response_preview": {
    "id": "card_dry_run_001",
    "type": "owner_gap",
    "schema_result": "valid",
    "show_or_silence_decision": "show",
    "status": "new"
  },
  "block_reasons": [
    "card_creation_policy_dry_run_only",
    "card_lifecycle_disabled"
  ],
  "next_required_decisions": [
    "real_llm_response_parser",
    "llm_schema_result_event_lifecycle",
    "suggestion_card_persistence",
    "suggestion_silenced_lifecycle",
    "feedback_idempotency"
  ]
}
```

When policy blocks creation, the endpoint still returns 200:

```json
{
  "policy_status": "blocked",
  "card_creation_policy_status": "dry_run_blocked",
  "would_create_card_if_enabled": false,
  "would_silence_candidate_if_enabled": true,
  "safe_to_create_card": false,
  "policy_errors": [
    {
      "field": "evidence_span_ids",
      "code": "request_linkage_mismatch",
      "message": "card_dry_run_001 evidence_span_ids must match request draft"
    }
  ]
}
```

Request shape violations return 422 before validation. Missing session and unknown request id return 404.

## Policy Rules

PCWEB-064 first runs the PCWEB-063 local schema validation dry-run subset. If schema validation fails, policy status is blocked and no card lifecycle is allowed.

If schema validation passes, PCWEB-064 checks the candidate against the existing Live ASR audit record and no-LLM request draft:

- `request_id` must match an existing `llm_request_draft_event.payload.request_id`.
- Candidate `gap_rule_id` must equal the request draft gap rule.
- Candidate `evidence_span_ids` must exactly equal request draft `evidence_span_ids`.
- Candidate `segment_batch` must exactly equal request draft `segment_batch`.
- Candidate `state_event_ids` must exactly equal request draft `source_event_ids`.
- Candidate `state_refs` must include the state ref derived from request draft `target_type` and `target_id`.
- Each referenced evidence span must exist in `transcript_final` or `transcript_revision` payload evidence and must be active for a strong card.
- Each referenced state event must exist and must target the candidate state ref.
- Each segment id in `segment_batch` must exist in final/revision transcript events.
- `final_segment_at_ms` must equal the maximum final/revision event time for the segment batch.
- `state_event_at_ms` must equal the maximum referenced state event time.
- `latency_ms` must still equal `card_created_at_ms - final_segment_at_ms`, and strong card latency must be within the 30 second real-time window.
- `schema_result=valid`, `show_or_silence_decision=show`, and `status=new` are required for `would_create_card_if_enabled=true`.
- Blocking schema results such as `failed|timeout|invalid`, non-show decisions, non-new statuses, or request-draft candidate degradation reasons block formal card creation and are treated as future silence/pending lifecycle work.

The policy deliberately evaluates candidate/request linkage that PCWEB-063 left out. This is the boundary between "card-shaped JSON" and "eligible to become a user-facing suggestion later".

## Boundaries

- POST is policy dry-run only.
- No event mutation or appended policy event.
- No `llm_schema_result` event.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No formal report request.
- No real LLM response parser.
- No gateway, relay, or remote LLM call.
- No API key, authorization header, bearer token, provider config, base URL, `configs/local/`, keychain, secret adapter, or environment secret read.
- No provider config file existence/readability/size/mtime/hash/fingerprint check.
- No token estimate or budget charge.
- No enabled executor mode.
- No retry, queue worker, background task, cursor, or pagination.
- `safe_to_create_card` remains false even when `would_create_card_if_enabled=true`, because this endpoint is not the enabled lifecycle.

## Why This Matters

The product value depends on timely, trustworthy suggestions, not merely producing valid JSON. A valid model-shaped card can still be unsafe or low value if it references the wrong evidence, stale ASR text, an unrelated state event, a delayed segment, or a degraded candidate.

PCWEB-064 makes those product rules explicit before any real LLM call or card persistence exists. It keeps implementation pressure on the real differentiator: evidence-grounded, timing-aware meeting advice.

## Tests

- Valid policy dry-run for an existing request draft returns `allowed/dry_run_allowed`, preserves request linkage, sets `would_create_card_if_enabled=true`, keeps `safe_to_create_card=false`, and does not mutate `/events`.
- The endpoint must not read provider config files, environment secrets, keychain, or call LLM.
- Schema-invalid candidates return 200 with `blocked/dry_run_blocked`, include schema validation errors, do not evaluate as card-eligible, and do not create cards/events.
- Request linkage mismatches for evidence, segment batch, state events, target state ref, and gap rule return deterministic policy errors.
- Missing/stale evidence, unknown state event, wrong state-event target, unknown segment, final time mismatch, state time mismatch, too-late strong card, blocking schema result, non-show decision/status, and candidate degradation reasons block creation.
- Unknown request id returns 404.
- Missing session returns 404.
- JSON persistence works across app instances.
- Non-object body, missing/unsupported mode, non-string `mode/request_id`, missing request id, missing candidate response, non-object candidate response, and extra top-level fields return 422.
- README/docs must mention PCWEB-064 and the policy-dry-run-only boundary.

## Implementation Status

- Status: implemented as a local dry-run boundary.
- Backend endpoint: `POST /live/asr/sessions/{session_id}/llm-card-creation-policy-dry-runs`.
- Test location: `code/web_mvp/backend/tests/test_app.py`.
- Implementation location: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`.
- Current verification scope: focused PCWEB-064 tests, README contract gate, backend regression, `pc-web` quality gate, and `all-local --no-browser` quality gate.

## Verification Evidence

Verified on 2026-07-02:

- Focused PCWEB-064 pytest: `9 passed`.
- README contract gate: `1 passed`.
- Backend regression: `167 passed`.
- `python3 tools/run_quality_gate.py --profile pc-web`: passed, including core pytest, web backend pytest, and browser smoke.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`: passed, including ASR runtime, ASR bakeoff, core, and web backend pytest.
- Read-only review found no Critical or Important issues; one Minor documentation drift in the allowed response example was fixed by adding `target_state_ref`.
- Cache cleanup, port checks for `8767`/`9223`, and sensitive marker scan completed with no remaining cache directories, no port listeners, and no sensitive marker hits outside excluded local config paths.
