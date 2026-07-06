# PCWEB-077 Live ASR Card Lifecycle Readiness Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only, response-only readiness summary endpoint that compresses PCWEB-065 through PCWEB-076 lifecycle preflight output into a UI-facing readiness, blocker, phase, and next-decision summary.

**Architecture:** The endpoint validates `mode=summary_only`, loads the Live ASR session, calls the PCWEB-076 append result audit event persistence preflight helper as the source of truth, and returns a stable 12-phase summary without enabling any writes. Overall readiness mirrors PCWEB-076 readiness, source traceability is exposed through scoped source fields, and summary flags remain false for card creation, event mutation, transaction commit, idempotency write, audit persistence, and LLM execution.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest contract tests in `code/web_mvp/backend/tests/test_app.py`, local docs under `docs/`.

---

### Task 1: Document PCWEB-077 Contract

**Files:**
- Create: `docs/pcweb-077-live-asr-card-lifecycle-readiness-summary-plan.md`
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
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries
```

with request fields `mode`, `request_id`, and `candidate_response`; `mode` must equal exactly `summary_only`.

- [x] **Step 2: Define readiness and phase statuses**

Document top-level `card_lifecycle_overall_readiness_status` values:

```text
blocked_until_enabled
safe_replay_existing_events
blocked_by_partial_replay
blocked_by_retry_replay_conflict
blocked_by_idempotency_store_write_preflight
```

Document the 12 summary phases:

```text
card_lifecycle_preview
append_preflight
append_disabled_run
append_repository_dry_run
append_transaction_disabled_run
append_result_audit_preview
retry_replay_preflight
append_event_serializer_dry_run
append_mutation_preflight
append_transaction_commit_preflight
append_idempotency_store_write_preflight
append_result_audit_event_persistence_preflight
```

- [x] **Step 3: Define write boundaries**

Document that PCWEB-077 must not mutate `/events`, write append result audit events, write idempotency store records, begin/commit/rollback repository transactions, create cards, read secrets/config, estimate cost, or call remote ASR/LLM.

### Task 2: Add Failing Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add fresh append summary test**

Add a test that posts `mode=summary_only` to the new endpoint for a fresh session and expects:

```python
assert body["card_lifecycle_readiness_summary_status"] == "summarized"
assert body["card_lifecycle_overall_readiness_status"] == "blocked_until_enabled"
assert body["card_lifecycle_summary_phase_count"] == 12
assert body["card_lifecycle_safe_to_create_card"] is False
assert body["card_lifecycle_safe_to_execute_llm"] is False
```

Also assert required next decisions, phase ids, source status fields, unchanged `/events`, and no forbidden config/secret markers.

- [x] **Step 2: Add safe replay summary test**

Seed both matching lifecycle events with `_append_persisted_lifecycle_event`, call the endpoint, and expect:

```python
assert body["card_lifecycle_overall_readiness_status"] == "safe_replay_existing_events"
assert "safe_replay_existing_events_requires_no_new_writes" in body["card_lifecycle_block_reasons"]
assert body["card_lifecycle_safe_to_persist_append_result_audit_event"] is False
assert body["card_lifecycle_safe_to_write_idempotency_store"] is False
```

Also assert no true unscoped `safe_to_*` action flag leaks into the summary response, and persisted bytes and `/events` are unchanged.

- [x] **Step 3: Add blocker summary tests**

Add partial replay, retry/replay conflict, and upstream PCWEB-076 blocker tests. Assert the summary maps to:

```text
blocked_by_partial_replay
blocked_by_retry_replay_conflict
blocked_by_idempotency_store_write_preflight
```

and surfaces the corresponding first blocker in `card_lifecycle_block_reasons`.

- [x] **Step 4: Add shape, not-found, persistence, and docs tests**

Cover missing session, unknown request id, JSON persistence across app instances, request shape 422 errors, and README/docs gate text for PCWEB-077.

- [x] **Step 5: Run RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "card_lifecycle_readiness_summary or readme_documents_scripted_browser_e2e_gate" -q
```

Result before implementation: the eight new endpoint tests failed with `404 Not Found`; README/docs gate passed after PCWEB-077 endpoint documentation was added.

### Task 3: Implement Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add route**

Add:

```python
@app.post("/live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries")
```

and use the same missing-session behavior as PCWEB-076.

- [x] **Step 2: Add payload validator**

Validate only `mode`, `request_id`, and `candidate_response`; require `mode == "summary_only"` without trimming; trim only `request_id`.

- [x] **Step 3: Add summary helper**

Create `_llm_card_lifecycle_readiness_summary_from_record()` that calls PCWEB-076 helper with `mode=preflight_only` and returns scoped source fields plus summary fields. Do not spread the full PCWEB-076 response into the summary because safe replay source evidence could otherwise look like an enabled UI action.

- [x] **Step 4: Add phase projection helpers**

Create helpers that project the PCWEB-076 source response into 12 phases with `phase_id`, `phase_status`, `phase_mode`, `phase_kind`, `write_boundary_status`, `item_count`, `safe_to_write=false`, `source_status_field`, and `source_status_value`.

### Task 4: Verify and Review

**Files:**
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- docs changed in Task 1

- [x] **Step 1: Focused tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "card_lifecycle_readiness_summary or readme_documents_scripted_browser_e2e_gate" -q
```

Result after implementation: `9 passed, 241 deselected, 2 warnings`.

- [x] **Step 2: Backend regression**

Run:

```bash
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Result: `288 passed, 2 warnings`.

- [x] **Step 3: Quality gates**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Result: `pc-web` passed with core `34 passed`, web backend `291 passed`, and browser smoke passed. `all-local --no-browser` passed with ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, and web backend `291 passed`.

- [x] **Step 4: Independent review and hygiene**

Run an independent read-only review, fix any critical or important issues, remove pytest/python caches, confirm no test ports are listening, and run the sensitive marker scan excluding `configs/local/**`.

Result: independent read-only review found no Critical or Important issues. One Minor documentation wording issue was fixed. Final hygiene removed pytest/python caches, found no listeners on ports `8767` or `9223`, and the sensitive marker scan excluding `configs/local/**` returned no output.

## Self-Review

- Spec coverage: endpoint, request validation, fresh append, safe replay, partial replay, conflict, upstream blocker, no mutation, no secret read, phase summary, next decisions, and documentation gates are covered by tasks.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: field names match the PCWEB-077 plan and PCWEB-065 through PCWEB-076 naming style.
