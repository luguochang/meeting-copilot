# PCWEB-076 Live ASR Card Lifecycle Append Result Audit Event Persistence Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only, response-only append result audit event persistence preflight after PCWEB-075 that defines future audit event persistence behavior without writing audit events or enabling lifecycle mutation.

**Architecture:** The new endpoint validates a `preflight_only` request, loads the Live ASR session, reuses PCWEB-075 idempotency-store write preflight as its source of truth, and projects each future lifecycle event into an audit persistence preflight check. Fresh append is blocked until enabled but exposes future audit event identities from PCWEB-070; safe replay requires no persistence; partial replay, retry/replay conflicts, and upstream preflight blockers remain blocked.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest contract tests in `code/web_mvp/backend/tests/test_app.py`, local docs under `docs/`.

---

### Task 1: Document PCWEB-076 Contract

**Files:**
- Create: `docs/pcweb-076-live-asr-card-lifecycle-append-result-audit-event-persistence-preflight-plan.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Define the endpoint contract**

Document:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-event-persistence-preflights
```

with request fields `mode`, `request_id`, and `candidate_response`; `mode` must equal exactly `preflight_only`.

- [x] **Step 2: Define readiness statuses**

Document top-level `append_result_audit_event_persistence_readiness_status` values:

```text
blocked_until_enabled
safe_replay_existing_events
blocked_by_partial_replay
blocked_by_retry_replay_conflict
blocked_by_idempotency_store_write_preflight
```

Document per-check `append_result_audit_event_persistence_preflight_check_status` values:

```text
blocked_until_enabled
persistence_not_required_for_safe_replay
blocked_by_partial_replay
blocked_by_retry_replay_conflict
blocked_by_idempotency_store_write_preflight
```

- [x] **Step 3: Define write boundaries**

Document that PCWEB-076 must not mutate `/events`, write append result audit events, write idempotency store records, begin/commit/rollback repository transactions, create cards, read secrets/config, estimate cost, or call remote ASR/LLM.

### Task 2: Add Failing Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add fresh append test**

Add a test that posts `mode=preflight_only` to the new endpoint for a fresh session and expects `append_result_audit_event_persistence_readiness_status=blocked_until_enabled`, two checks, `future_append_result_audit_event_status=would_persist_if_enabled`, PCWEB-070 audit ids preserved, all safe flags false, and unchanged `/events`.

- [x] **Step 2: Add safe replay test**

Seed both matching lifecycle events with `_append_persisted_lifecycle_event`, call the endpoint, and expect `append_result_audit_event_persistence_readiness_status=safe_replay_existing_events`, per-check `persistence_not_required_for_safe_replay`, `future_append_result_audit_event_status=not_required_existing_replay`, persisted bytes unchanged, and `safe_to_persist_append_result_audit_event=false`.

- [x] **Step 3: Add partial replay and conflict tests**

Seed only one matching lifecycle event for partial replay and mismatched lifecycle events for conflict. Assert `blocked_by_partial_replay` and `blocked_by_retry_replay_conflict`, with no event mutation and no audit event append.

- [x] **Step 4: Add upstream blocker, silenced lifecycle, shape, and not-found tests**

Cover PCWEB-075 upstream blocker mapping to `blocked_by_idempotency_store_write_preflight`, schema-invalid and policy-blocked `suggestion_silenced` lifecycles, missing session, unknown request id, persisted read across app instances, 422 request shape errors, and README contract text.

- [x] **Step 5: Run RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "audit_event_persistence_preflight or readme_documents_scripted_browser_e2e_gate" -q
```

Result before implementation: the eleven new endpoint tests failed with `404 Not Found`; README gate passed.

### Task 3: Implement Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add route**

Add:

```python
@app.post("/live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-event-persistence-preflights")
```

and use the same missing-session behavior as PCWEB-075.

- [x] **Step 2: Add payload validator**

Validate only `mode`, `request_id`, and `candidate_response`; require `mode == "preflight_only"` without trimming; trim only `request_id`.

- [x] **Step 3: Add preflight helper**

Create `_llm_card_lifecycle_append_result_audit_event_persistence_preflight_from_record()` that calls PCWEB-075 helper and returns response-only audit event persistence preflight metadata.

- [x] **Step 4: Add per-check projection helper**

Create `_card_lifecycle_append_result_audit_event_persistence_preflight_check()` that maps idempotency-store write preflight statuses into audit persistence statuses, joins PCWEB-070 audit preview data by `future_event_id`, and keeps all write flags false.

### Task 4: Verify and Review

**Files:**
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- docs changed in Task 1

- [x] **Step 1: Focused tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "audit_event_persistence_preflight or readme_documents_scripted_browser_e2e_gate" -q
```

Result after implementation: `12 passed, 230 deselected, 2 warnings`.

- [x] **Step 2: Backend regression**

Run:

```bash
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Result: `280 passed, 2 warnings`.

- [x] **Step 3: Quality gates**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Result: `pc-web` passed with core `34 passed`, web backend `283 passed`, and browser smoke passed. `all-local --no-browser` passed with ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, and web backend `283 passed`.

- [x] **Step 4: Independent review and hygiene**

Run an independent read-only review, fix any critical or important issues, remove pytest/python caches, confirm no test ports are listening, and run the sensitive marker scan excluding `configs/local/**`.

Result after review fixes: independent review found no Critical issues and identified two Important hardening items. The implementation now carries PCWEB-070 provenance fields (`transaction_run_id`, `transaction_run_status`, `append_run_id`, `repository_result_id`, `repository_result_status`, `repository_idempotency_key`, `preflight_append_status`, `preflight_conflict_status`, `audit_repository_transaction_status`, `repository_write_status`, `transaction_write_status`) on every PCWEB-076 check; each check also exposes `safe_to_mutate_events=false` and `safe_to_append_events=false`. Branch tests now install no-config/no-secret/outbound-call guards and assert `/events` stability where a session exists. The README/docs gate now reads the PCWEB-076 plan, requirements traceability matrix, acceptance table, privacy/data-flow document, and roadmap.

## Self-Review

- Spec coverage: endpoint, request validation, fresh append, safe replay, partial replay, conflict, upstream blocker, no mutation, no audit write, persistence, and documentation gates are covered.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: field names match the PCWEB-076 plan and PCWEB-070 through PCWEB-075 naming style.
