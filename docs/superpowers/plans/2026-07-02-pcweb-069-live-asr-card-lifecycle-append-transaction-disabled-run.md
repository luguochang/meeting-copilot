# PCWEB-069 Live ASR Card Lifecycle Append Transaction Disabled Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a disabled transaction-run endpoint for future Live ASR card lifecycle event persistence while keeping event mutation, repository transactions, idempotency writes, secrets, and LLM calls disabled.

**Architecture:** Reuse PCWEB-068 repository dry-run as the source of truth, then map each repository result into a skipped transaction run. The endpoint accepts only `mode=disabled`, reports the future transaction action shape, and never mutates `/events`.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest tests in `code/web_mvp/backend/tests/test_app.py`, Markdown docs under `docs/`.

---

### Task 1: Document The Transaction Disabled-Run Boundary

**Files:**
- Create: `docs/pcweb-069-live-asr-card-lifecycle-append-transaction-disabled-run-plan.md`
- Create: `docs/superpowers/plans/2026-07-02-pcweb-069-live-asr-card-lifecycle-append-transaction-disabled-run.md`
- Modify: `docs/decision-log.md`

- [x] **Step 1: Write the PCWEB-069 plan document**

Document endpoint, request body, response shape, disabled transaction rules, boundaries, tests, and verification plan. Keep no-cost/no-secret/no-event-mutation constraints explicit.

- [x] **Step 2: Write this implementation plan**

Save the plan with file paths, test strategy, implementation steps, and verification commands.

- [x] **Step 3: Add DEC-069**

Append a decision log entry with endpoint path, fields, rationale, alternatives, boundaries, verification method, and linked docs.

### Task 2: Add Failing Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add allowed disabled transaction run test**

Add `test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_returns_transaction_contract_without_mutating_events`. It must create a no-revision Live ASR mock session, guard provider config/env/keychain/HTTP reads, record `/events` before/after, post to `/llm-card-lifecycle-append-transaction-runs`, and assert transaction run fields, deterministic ids, no forbidden values, and no mutation.

- [x] **Step 2: Add schema-invalid and policy-blocked tests**

Both must return transaction runs for `llm_schema_result` plus `suggestion_silenced` while preserving validation/policy errors.

- [x] **Step 3: Add preflight conflict test**

Use a persisted record with existing future event id and idempotency key. Assert `repository_dry_run_status=blocked_by_preflight`, preserved `append_errors`, and transaction run statuses skipped with `skip_reason=repository_preflight_blocked`.

- [x] **Step 4: Add unknown/missing/persistence/shape tests**

Cover unknown request id 404, missing session 404, JSON persistence across app instances with unchanged session file bytes, and 422 shape errors including whitespace-padded `mode`.

- [x] **Step 5: Add transaction identifier collision test**

Use candidate ids `card:dry_run:001` and `card_dry_run_001` and assert `transaction_run_id` plus `transaction_idempotency_key` stay distinct using percent-encoded components.

- [x] **Step 6: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_transaction or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: PCWEB-069 endpoint tests fail with `404 Not Found` before implementation.

Result before implementation: 9 endpoint tests failed with `404 Not Found`, and README gate failed because PCWEB-069 was not documented.

### Task 3: Implement The Endpoint And Helpers

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add request validator**

Add `_validate_llm_card_lifecycle_append_transaction_run_payload`:

- body must be object,
- `mode` required and string,
- `mode` must be exactly `disabled`,
- `request_id` required and string,
- `candidate_response` required and object,
- no extra top-level fields.

Use `unsupported card lifecycle append transaction run mode: {mode}` for unsupported mode.

- [x] **Step 2: Add POST route**

Add:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-runs
```

The route validates payload, loads the ASR live record, and returns `_llm_card_lifecycle_append_transaction_disabled_run_from_record(record, payload)`.

- [x] **Step 3: Add transaction disabled-run helper**

Implement `_llm_card_lifecycle_append_transaction_disabled_run_from_record` by calling `_llm_card_lifecycle_append_repository_dry_run_from_record` with `mode=dry_run_only`, then mapping `repository_results` into `transaction_runs`.

The response should inherit PCWEB-068 fields and add/override:

- `transaction_run_mode=disabled`
- `transaction_run_status=skipped`
- `repository_transaction_status=disabled`
- `idempotency_store_write_status=not_written`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `safe_to_commit_transaction=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`
- `transaction_run_count`
- `transaction_runs`
- `block_reasons=["repository_transaction_disabled","idempotency_store_write_disabled","event_mutation_disabled"]`
- `next_required_decisions=["repository_transaction_commit_contract","idempotency_store_write_contract","append_result_audit_event","retry_and_replay_conflict_resolution","enabled_card_lifecycle_mutation"]`

- [x] **Step 4: Add transaction run helper**

For each repository result, return:

- `transaction_run_id`
- `transaction_run_status=skipped`
- `skip_reason=repository_transaction_disabled|repository_preflight_blocked`
- `repository_result_id`
- `repository_result_status`
- `event_type`
- `future_event_id`
- `preview_event_id`
- `append_run_id`
- `idempotency_key`
- `repository_idempotency_key`
- `transaction_idempotency_key`
- `preflight_append_status`
- `preflight_conflict_status`
- `would_append_sequence`
- `would_append_after_sequence`
- `repository_write_status`
- `transaction_write_status=disabled`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_commit_transaction=false`
- `safe_to_append_event=false`

Use `repository_preflight_blocked` when `repository_result_status=blocked_by_preflight`; otherwise use `repository_transaction_disabled`.

- [x] **Step 5: Verify GREEN focused**

Run the focused command from Task 2. Expected: all selected tests pass.

Result: focused PCWEB-069 plus README gate passed, 10 passed, 162 deselected, 2 warnings.

### Task 4: Update Product Docs

**Files:**
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Add PCWEB-069 traceability**

Add a row after PCWEB-068 describing the transaction disabled-run endpoint, required fields, no-mutation constraints, 404/422/persistence behavior, and test/doc locations.

- [x] **Step 2: Add acceptance entry**

Add `AC-PCWEB-062` for the transaction disabled-run boundary.

- [x] **Step 3: Update README endpoint lists and gate text**

Add the endpoint to the README endpoint list and explain that it only accepts `mode=disabled` and returns skipped transaction run envelopes.

- [x] **Step 4: Update architecture/checklist/privacy/roadmap docs**

Record that PCWEB-069 is still local, does not read provider config/secrets, does not call LLM, does not start a repository transaction, does not write idempotency store, and does not mutate `/events`.

- [x] **Step 5: Verify README gate**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "readme_documents_scripted_browser_e2e_gate" -q
```

Expected: pass.

Result: included in focused PCWEB-069 run, 10 passed.

### Task 5: Run Verification And Review

**Files:**
- No source edits expected unless verification finds issues.

- [x] **Step 1: Run backend regression**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Expected: pass.

Result: 210 passed, 2 warnings.

- [x] **Step 2: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: both pass.

Result:
- `pc-web`: core 34 passed, web backend 213 passed, browser smoke passed.
- `all-local --no-browser`: ASR runtime 65 passed, ASR bakeoff 18 passed, core 34 passed, web backend 213 passed.

- [x] **Step 3: Clean generated caches**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -exec rm -rf {} +
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -print
```

Expected: second command prints nothing.

Result: no output after cleanup.

- [x] **Step 4: Check test ports**

```bash
lsof -nP -iTCP:8767 -sTCP:LISTEN || true
lsof -nP -iTCP:9223 -sTCP:LISTEN || true
```

Expected: no listeners from test runs.

Result: no output for ports 8767 or 9223.

- [x] **Step 5: Run sensitive marker scan**

Run the existing local sensitive marker scan from the handoff notes. Expected: no output.

Result: no output.

- [x] **Step 6: Request code review**

Ask a read-only review agent to check PCWEB-069 for:

- no mutation,
- no secret/config reads,
- deterministic ids,
- correct repository dry-run / disabled-run / preflight inheritance,
- blocked preflight behavior,
- tests and docs aligned.

Fix any Critical or Important issues before reporting back.

Result: read-only review found no Critical or Important issues. Minor follow-ups were applied: the PCWEB-069 implementation status was updated, `transaction_run_id` was documented as response/envelope-local, `transaction_idempotency_key` was documented as the future durable identity, and the preflight conflict test now asserts `repository_result_status=blocked_by_preflight` for each transaction run.
