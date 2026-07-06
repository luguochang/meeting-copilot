# PCWEB-068 Live ASR Card Lifecycle Append Repository Dry-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repository append dry-run endpoint for future Live ASR card lifecycle event persistence while keeping event mutation, idempotency writes, secrets, and LLM calls disabled.

**Architecture:** Reuse PCWEB-067 disabled append run as the source of truth, then map each append plan/run pair into a repository dry-run result. The endpoint accepts only `mode=dry_run_only`, reports the future repository transaction shape, and never mutates `/events`.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest tests in `code/web_mvp/backend/tests/test_app.py`, Markdown docs under `docs/`.

---

### Task 1: Document The Repository Dry-Run Boundary

**Files:**
- Create: `docs/pcweb-068-live-asr-card-lifecycle-append-repository-dry-run-plan.md`
- Create: `docs/superpowers/plans/2026-07-02-pcweb-068-live-asr-card-lifecycle-append-repository-dry-run.md`
- Modify: `docs/decision-log.md`

- [x] **Step 1: Write the PCWEB-068 plan document**

Document endpoint, request body, response shape, dry-run rules, boundaries, tests, and verification plan. Keep no-cost/no-secret/no-event-mutation constraints explicit.

- [x] **Step 2: Write this implementation plan**

Save the plan with file paths, test strategy, implementation steps, and verification commands.

- [x] **Step 3: Add DEC-068**

Append a decision log entry with endpoint path, fields, rationale, alternatives, boundaries, verification method, and linked docs.

### Task 2: Add Failing Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add allowed repository dry-run test**

Add `test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_returns_repository_contract_without_mutating_events`. It must create a no-revision Live ASR mock session, guard provider config/env/keychain reads, record `/events` before/after, post to `/llm-card-lifecycle-append-repository-dry-runs`, and assert repository result fields, deterministic ids, no forbidden values, and no mutation.

- [x] **Step 2: Add schema-invalid and policy-blocked tests**

Both must return repository results for `llm_schema_result` plus `suggestion_silenced` while preserving validation/policy errors.

- [x] **Step 3: Add preflight conflict test**

Use a persisted record with existing future event id and idempotency key. Assert `repository_dry_run_status=blocked_by_preflight`, preserved `append_errors`, and blocked repository result statuses.

- [x] **Step 4: Add unknown/missing/persistence/shape tests**

Cover unknown request id 404, missing session 404, JSON persistence across app instances, and 422 shape errors including whitespace-padded `mode`.

- [x] **Step 5: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_repository or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: PCWEB-068 endpoint tests fail with `404 Not Found` before implementation.

Result before implementation: 8 endpoint tests failed with `404 Not Found`; existing README gate passed.

### Task 3: Implement The Endpoint And Helpers

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add request validator**

Add `_validate_llm_card_lifecycle_append_repository_dry_run_payload`:

- body must be object,
- `mode` required and string,
- `mode` must be exactly `dry_run_only`,
- `request_id` required and string,
- `candidate_response` required and object,
- no extra top-level fields.

Use `unsupported card lifecycle append repository dry-run mode: {mode}` for unsupported mode.

- [x] **Step 2: Add POST route**

Add:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-repository-dry-runs
```

The route validates payload, loads the ASR live record, and returns `_llm_card_lifecycle_append_repository_dry_run_from_record(record, payload)`.

- [x] **Step 3: Add repository dry-run helper**

Implement `_llm_card_lifecycle_append_repository_dry_run_from_record` by calling `_llm_card_lifecycle_append_disabled_run_from_record` with `mode=disabled`, then mapping `append_plan` and `append_runs` into `repository_results`.

The response should inherit PCWEB-067 fields and add/override:

- `repository_dry_run_mode=dry_run_only`
- `repository_dry_run_status=would_append_if_enabled|blocked_by_preflight`
- `repository_append_count`
- `repository_results`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `safe_to_append_events=false`
- `safe_to_create_card=false`
- `block_reasons=["repository_append_dry_run_only","event_mutation_disabled"]`
- `next_required_decisions=["repository_append_transaction","idempotency_store_write_contract","append_result_audit_event","retry_and_replay_conflict_resolution","enabled_card_lifecycle_mutation"]`

- [x] **Step 4: Add repository result helper**

For each append plan item/run item pair, return:

- `repository_result_id`
- `repository_result_status`
- `event_type`
- `future_event_id`
- `preview_event_id`
- `preflight_append_status`
- `preflight_conflict_status`
- `would_append_sequence`
- `would_append_after_sequence`
- `idempotency_key`
- `repository_idempotency_key`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `repository_write_status=dry_run_only`
- `safe_to_append_event=false`

Use `blocked_by_preflight` when the plan item conflict status is not `none`; otherwise use `would_append_if_enabled`.

- [x] **Step 5: Verify GREEN focused**

Run the focused command from Task 2. Expected: all selected tests pass.

Result before documentation synchronization: 9 selected tests passed.

### Task 4: Update Product Docs

**Files:**
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Add PCWEB-068 traceability**

Add a row after PCWEB-067 describing the repository dry-run endpoint, required fields, no-mutation constraints, 404/422/persistence behavior, and test/doc locations.

- [x] **Step 2: Add acceptance entry**

Add `AC-PCWEB-061` for the repository append dry-run boundary.

- [x] **Step 3: Update README endpoint lists and gate text**

Add the endpoint to the README endpoint list and explain that it only accepts `mode=dry_run_only` and returns repository dry-run result envelopes.

- [x] **Step 4: Update architecture/checklist/privacy/roadmap docs**

Record that PCWEB-068 is still local, does not read provider config/secrets, does not call LLM, does not write idempotency store, and does not mutate `/events`.

- [x] **Step 5: Verify README gate**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "readme_documents_scripted_browser_e2e_gate" -q
```

Expected: pass.

Result before review fixes: focused PCWEB-068 plus README gate passed, 9 passed, 153 deselected, 2 warnings.

Review-fix result: focused PCWEB-068 plus README gate passed, 10 passed, 153 deselected, 2 warnings. Added collision-resistant repository component token coverage, persisted session byte no-mutation coverage, broader no-secret-read guards, and README PCWEB-068 assertions.

### Task 5: Run Verification And Review

**Files:**
- No source edits expected unless verification finds issues.

- [x] **Step 1: Run backend regression**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Expected: pass.

Result before review fixes: 200 passed, 2 warnings.

Final result after review fixes: 201 passed, 2 warnings.

- [x] **Step 2: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: both pass.

Result:
- `pc-web`: core 34 passed, web backend 203 passed, browser smoke passed.
- `all-local --no-browser`: ASR runtime 65 passed, ASR bakeoff 18 passed, core 34 passed, web backend 203 passed.

Final result after review fixes:
- `pc-web`: core 34 passed, web backend 204 passed, browser smoke passed.
- `all-local --no-browser`: ASR runtime 65 passed, ASR bakeoff 18 passed, core 34 passed, web backend 204 passed.

- [x] **Step 3: Clean generated caches**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -exec rm -rf {} +
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -print
```

Expected: second command prints nothing.

Result before review fixes: no output after cleanup.

Final result after review fixes: no output after cleanup.

- [x] **Step 4: Check test ports**

```bash
lsof -nP -iTCP:8767 -sTCP:LISTEN || true
lsof -nP -iTCP:9223 -sTCP:LISTEN || true
```

Expected: no listeners from test runs.

Result before review fixes: no output for ports 8767 or 9223.

Final result after review fixes: no output for ports 8767 or 9223.

- [x] **Step 5: Run sensitive marker scan**

Run the existing local sensitive marker scan from the handoff notes. Expected: no output.

Result before review fixes: no output.

Final result after review fixes: no output.

- [x] **Step 6: Request code review**

Ask a read-only review agent to check PCWEB-068 for:

- no mutation,
- no secret/config reads,
- deterministic ids,
- correct disabled-run/preflight inheritance,
- blocked preflight behavior,
- tests and docs aligned.

Fix any Critical or Important issues before reporting back.

Review result:
- Critical: none.
- Important: repository identifier/idempotency collision risk; no-secret-read guard too narrow.
- Minor: README gate did not assert PCWEB-068; persisted record no-mutation was not byte-level.

Fixes applied:
- Repository result ids and repository idempotency keys now use percent-encoded components.
- Tests now cover delimiter-bearing card id versus underscore-bearing card id, byte-identical persisted record, broadened no-secret-read guard coverage across PCWEB-068 branches, and README PCWEB-068 gate assertions.
