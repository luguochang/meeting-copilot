# PCWEB-067 Live ASR Card Lifecycle Append Disabled Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a disabled append-run endpoint for future Live ASR card lifecycle event persistence while keeping event mutation, idempotency writes, secrets, and LLM calls disabled.

**Architecture:** Reuse PCWEB-066 append preflight as the source of truth, then map each append plan item into a skipped run envelope. The endpoint is an action boundary but only accepts `mode=disabled`, so it validates shape, reports what would have happened, and does not mutate `/events`.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest tests in `code/web_mvp/backend/tests/test_app.py`, Markdown docs under `docs/`.

---

### Task 1: Document The Disabled Append-Run Boundary

**Files:**
- Create: `docs/pcweb-067-live-asr-card-lifecycle-append-disabled-run-plan.md`
- Create: `docs/superpowers/plans/2026-07-02-pcweb-067-live-asr-card-lifecycle-append-disabled-run.md`
- Modify: `docs/decision-log.md`

- [x] **Step 1: Write the PCWEB-067 plan document**

Document the endpoint, request body, response shape, disabled run rules, boundaries, why it matters, tests, and verification plan. Keep explicit no-cost/no-secret/no-event-mutation constraints.

- [x] **Step 2: Write this implementation plan**

Save a bite-sized implementation plan that an agent can execute without needing chat context.

- [x] **Step 3: Add DEC-067**

Append a decision log entry with the endpoint path, fields, rationale, alternatives, boundaries, verification method, and linked docs.

### Task 2: Add Failing Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add allowed disabled-run test**

Add a test named `test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_returns_skipped_runs_without_mutating_events` that:

- creates a Live ASR mock session without revision,
- guards provider config/env/keychain reads with monkeypatch sentinels,
- records `/events` before and after,
- posts to `/live/asr/sessions/{session_id}/llm-card-lifecycle-append-runs`,
- asserts `mode=disabled`, `append_run_status=skipped`, `append_preflight_status=allowed`, deterministic two-run output, no forbidden values in response, and no `/events` mutation.

- [x] **Step 2: Add silenced lifecycle tests**

Add tests for schema-invalid and policy-blocked candidates. Both should return skipped runs for `llm_schema_result` plus `suggestion_silenced` and preserve validation/policy errors.

- [x] **Step 3: Add conflict test**

Add a persisted-record test that inserts one existing future event id and one existing idempotency key. The endpoint should return `append_preflight_status=blocked`, preserve `append_errors`, and mark affected runs with `skip_reason=append_preflight_blocked`.

- [x] **Step 4: Add unknown/missing/persistence/shape tests**

Add tests for unknown request id 404, missing session 404, persistence across app instances, and 422 request shape errors.

- [x] **Step 5: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_runs or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: PCWEB-067 endpoint tests fail with 404 before implementation.

### Task 3: Implement The Endpoint And Helper

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add request validator**

Add a small validator for append-run payloads:

- body must be object,
- `mode` required and string,
- `mode` must be `disabled`,
- `request_id` required and string,
- `candidate_response` required and object,
- no extra top-level fields.

Reuse the same error wording style as `_validate_llm_schema_validation_dry_run_payload`, but use `unsupported card lifecycle append run mode: {mode}` for unsupported mode.

- [x] **Step 2: Add POST route**

Add:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-runs
```

The route validates payload, loads the ASR live record, and returns `_llm_card_lifecycle_append_disabled_run_from_record(record, payload)`.

- [x] **Step 3: Add disabled run helper**

Implement `_llm_card_lifecycle_append_disabled_run_from_record` by calling `_llm_card_lifecycle_append_preflight_dry_run_from_record`, then mapping `append_plan` into `append_runs`.

The response should inherit PCWEB-066 fields and override/add:

- `append_run_mode=disabled`
- `append_run_status=skipped`
- `append_run_count`
- `append_runs`
- `safe_to_append_events=false`
- `safe_to_create_card=false`
- `block_reasons=["append_run_disabled","event_mutation_disabled"]`
- `next_required_decisions=["event_append_repository_api","idempotency_store","enabled_card_lifecycle_mutation","retry_and_replay_conflict_resolution","append_result_audit_event"]`

- [x] **Step 4: Add run item helper**

For each append plan item, return:

- `run_id`
- `run_status=skipped`
- `skip_reason`
- `event_type`
- `future_event_id`
- `preview_event_id`
- `idempotency_key`
- `preflight_idempotency_key`
- `preflight_append_status`
- `preflight_conflict_status`
- `would_append_sequence`
- `would_append_after_sequence`
- `llm_call_status=not_called`
- `credentials_status=not_read`
- `cost_status=not_estimated`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `safe_to_append_event=false`

Use `append_preflight_blocked` when the plan item conflict status is not `none`; otherwise use `event_append_disabled`.

- [x] **Step 5: Verify GREEN focused**

Run the focused command from Task 2. Expected: all selected tests pass.

### Task 4: Update Product Docs

**Files:**
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Add PCWEB-067 traceability**

Add a row after PCWEB-066 describing the disabled append-run endpoint, required fields, no-mutation constraints, 404/422/persistence behavior, and test/doc locations.

- [x] **Step 2: Add acceptance entry**

Add `AC-PCWEB-060` for the disabled append-run boundary and include the endpoint in the non-PC-1 endpoint list.

- [x] **Step 3: Update README endpoint lists and gate text**

Add the endpoint to the README endpoint list and explain that it only accepts `mode=disabled` and returns skipped runs.

- [x] **Step 4: Update architecture/checklist/privacy/roadmap docs**

Record that PCWEB-067 is still local, does not read provider config/secrets, does not call LLM, and does not mutate `/events`.

- [x] **Step 5: Verify README gate**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "readme_documents_scripted_browser_e2e_gate" -q
```

Expected: pass.

### Task 5: Run Verification And Review

**Files:**
- No source edits expected unless verification finds issues.

- [x] **Step 1: Run backend regression**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Expected: pass.

- [x] **Step 2: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: both pass.

- [x] **Step 3: Clean generated caches**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -exec rm -rf {} +
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -print
```

Expected: second command prints nothing.

- [x] **Step 4: Check test ports**

```bash
lsof -nP -iTCP:8767 -sTCP:LISTEN || true
lsof -nP -iTCP:9223 -sTCP:LISTEN || true
```

Expected: no listeners from test runs.

- [x] **Step 5: Run sensitive marker scan**

Run the existing local sensitive marker scan from the handoff notes. Expected: no output.

- [ ] **Step 6: Request code review**

Ask a read-only review agent to check PCWEB-067 for:

- no mutation,
- no secret/config reads,
- deterministic ids,
- correct preflight inheritance,
- tests and docs aligned.

Fix any Critical or Important issues before reporting back.
