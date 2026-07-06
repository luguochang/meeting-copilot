# PCWEB-070 Live ASR Card Lifecycle Append Result Audit Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a response-only append result audit preview endpoint for future Live ASR card lifecycle persistence while keeping event mutation, repository transactions, idempotency writes, secrets, and LLM calls disabled.

**Architecture:** Reuse PCWEB-069 transaction disabled-run as the source of truth, then map each transaction run into a preview-only append result audit event. The endpoint accepts only `mode=preview_only`, reports the future audit trail shape, and never mutates `/events`.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest coverage in `code/web_mvp/backend/tests/test_app.py`, Markdown product docs under `docs/` and `code/web_mvp/README.md`.

---

### Task 1: Document PCWEB-070 Contract

**Files:**
- Create: `docs/pcweb-070-live-asr-card-lifecycle-append-result-audit-preview-plan.md`
- Create: `docs/superpowers/plans/2026-07-02-pcweb-070-live-asr-card-lifecycle-append-result-audit-preview.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Write the PCWEB-070 plan document**

Document endpoint, request body, response shape, audit preview rules, boundaries, tests, and verification plan. Keep no-cost/no-secret/no-event-mutation constraints explicit.

- [x] **Step 2: Add traceability and acceptance rows**

Add `PCWEB-070` after PCWEB-069 in `docs/requirements-traceability-matrix.md` and `AC-PCWEB-063` after AC-PCWEB-062 in `docs/pc-local-web-mvp-acceptance.md`.

- [x] **Step 3: Add decision log entry**

Append `DEC-070` describing the append result audit preview endpoint, why it exists before enabled writes, rejected alternatives, boundaries, and verification.

- [x] **Step 4: Update project docs and README**

Update README endpoint lists and Live ASR boundary text. Update project structure, roadmap, privacy/data-flow, and end-to-end checklist so PCWEB-070 is part of the documented local-only lifecycle chain.

### Task 2: Write Failing PCWEB-070 Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add allowed audit preview test**

Add `test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_returns_audit_preview_without_mutating_events`. It must create a no-revision Live ASR mock session, guard provider config/env/keychain/HTTP reads, record `/events` before/after, post to `/llm-card-lifecycle-append-result-audit-previews`, and assert preview audit fields, deterministic ids, no forbidden values, and no mutation.

- [x] **Step 2: Add schema-invalid and policy-blocked tests**

Both tests must return audit previews for `llm_schema_result` plus `suggestion_silenced` while preserving validation/policy errors.

- [x] **Step 3: Add preflight conflict test**

Use a persisted record with existing future event id and idempotency key. Assert `repository_dry_run_status=blocked_by_preflight`, preserved `append_errors`, and audit previews with `audit_result_status=blocked_by_preflight`.

- [x] **Step 4: Add 404, persistence, and 422 tests**

Cover unknown request id, missing session, cross-app JSON persistence with unchanged session file bytes, and request shape errors.

- [x] **Step 5: Add audit identifier collision test**

Use candidate ids `card:dry_run:001` and `card_dry_run_001` and assert `audit_event_id` plus `audit_idempotency_key` stay distinct using percent-encoded components.

- [x] **Step 6: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_result_audit or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: PCWEB-070 endpoint tests fail with `404 Not Found` before implementation, and README gate fails until PCWEB-070 is documented.

Result before implementation: 9 endpoint tests failed with `404 Not Found`; README gate passed after PCWEB-070 documentation was added.

### Task 3: Implement PCWEB-070 Backend Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add payload validator**

Add `_validate_llm_card_lifecycle_append_result_audit_preview_payload`:

- require object body,
- allowed fields exactly `mode`, `request_id`, `candidate_response`,
- require `mode` string exactly `preview_only`,
- require `request_id` string and trim only request id,
- require `candidate_response` object,
- reject extras with deterministic 422 detail.

- [x] **Step 2: Add route**

Add:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-previews
```

The route validates payload, loads the ASR live record, and returns `_llm_card_lifecycle_append_result_audit_preview_from_record(record, payload)`.

- [x] **Step 3: Add audit preview response helper**

Implement `_llm_card_lifecycle_append_result_audit_preview_from_record` by calling `_llm_card_lifecycle_append_transaction_disabled_run_from_record` with `mode=disabled`, then mapping `transaction_runs` into `append_result_audit_events`.

Top-level response fields must include:

- `append_result_audit_mode=preview_only`
- `append_result_audit_status=previewed`
- `append_result_audit_event_status=preview_only`
- `audit_event_append_status=not_appended`
- `safe_to_write_audit_events=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`
- `append_result_audit_event_count`
- `append_result_audit_events`
- `block_reasons=["append_result_audit_preview_only","repository_transaction_disabled","idempotency_store_write_disabled","event_mutation_disabled"]`
- `next_required_decisions=["append_result_audit_event_persistence_contract","retry_and_replay_conflict_resolution","enabled_repository_transaction_commit","idempotency_store_write_contract","enabled_card_lifecycle_mutation"]`

- [x] **Step 4: Add audit preview item helper**

Implement `_card_lifecycle_append_result_audit_event_preview_from_transaction_run`.

Each item must include:

- `audit_event_id`
- `audit_event_type=card_lifecycle_append_result`
- `audit_event_status=preview_only`
- `audit_result_status=skipped_transaction_disabled|blocked_by_preflight`
- `transaction_run_id`
- `transaction_run_status`
- `transaction_idempotency_key`
- `repository_result_id`
- `repository_result_status`
- `repository_idempotency_key`
- `event_type`
- `future_event_id`
- `preview_event_id`
- `append_run_id`
- `idempotency_key`
- `audit_idempotency_key`
- `preflight_append_status`
- `preflight_conflict_status`
- `would_append_sequence`
- `would_append_after_sequence`
- `repository_transaction_status=disabled`
- `repository_write_status`
- `transaction_write_status=disabled`
- `event_append_status=not_appended`
- `audit_event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_write_audit_event=false`
- `safe_to_append_event=false`

Use `blocked_by_preflight` when `skip_reason=repository_preflight_blocked`; otherwise use `skipped_transaction_disabled`.

### Task 4: Verify and Document Results

**Files:**
- Modify: docs from Task 1 as needed

- [x] **Step 1: Run focused tests**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_result_audit or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: all PCWEB-070 focused tests plus README gate pass.

Result: focused PCWEB-070 plus README gate passed, 10 passed, 171 deselected, 2 warnings.

- [x] **Step 2: Run backend regression**

```bash
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Expected: app and live event backend tests pass.

Result: backend regression passed, 219 passed, 2 warnings.

- [x] **Step 3: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: quality gates pass.

Result: `pc-web` passed with core 34 passed, web backend 222 passed, and browser smoke passed. `all-local --no-browser` passed with ASR runtime 65 passed, ASR bakeoff 18 passed, core 34 passed, and web backend 222 passed.

- [x] **Step 4: Cleanup and sensitive scan**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -exec rm -rf {} +
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -print
lsof -nP -iTCP:8767 -sTCP:LISTEN || true
lsof -nP -iTCP:9223 -sTCP:LISTEN || true
# Run the local sensitive marker scan from the operator checklist without
# writing the sensitive literal patterns into repository documents.
```

Expected: no cache directories outside excluded venvs, no test ports listening, no sensitive marker output.

Result: no cache directories printed, no `8767`/`9223` listeners printed, and sensitive marker scan printed no matches after removing sensitive literal scan patterns from this plan document.

- [x] **Step 5: Request code review**

Ask a read-only review agent to check PCWEB-070 for:

- no mutation,
- no secret/config reads,
- deterministic ids,
- correct transaction/repository/preflight inheritance,
- blocked preflight behavior,
- tests and docs aligned.

Fix any Critical or Important issues before reporting back.

Result: external read-only review agent could not be started because the agent thread limit was reached. Performed local read-only diff review instead and fixed two Minor documentation consistency issues: the plan example `audit_event_id` now matches the implemented collision-resistant id shape, and the traceability matrix now marks PCWEB-070 as implemented.
