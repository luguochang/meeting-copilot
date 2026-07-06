# PCWEB-071 Live ASR Card Lifecycle Retry Replay Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only retry/replay preflight endpoint that classifies whether future Live ASR card lifecycle append identities are fresh, safe replays, partial replays, or blocked conflicts while keeping all mutation disabled.

**Architecture:** Reuse PCWEB-070 append result audit preview as the source of truth for lifecycle items, then inspect current Live ASR audit record events to classify existing event id and idempotency-key evidence. The endpoint accepts only `mode=preflight_only`, returns response-only analysis, and never mutates `/events`.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest coverage in `code/web_mvp/backend/tests/test_app.py`, Markdown product docs under `docs/` and `code/web_mvp/README.md`.

---

### Task 1: Document PCWEB-071 Contract

**Files:**
- Create: `docs/pcweb-071-live-asr-card-lifecycle-retry-replay-preflight-plan.md`
- Create: `docs/superpowers/plans/2026-07-02-pcweb-071-live-asr-card-lifecycle-retry-replay-preflight.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Write the PCWEB-071 plan document**

Document endpoint, request body, response shape, retry/replay resolution semantics, local-only boundaries, tests, and verification plan.

- [x] **Step 2: Add traceability and acceptance rows**

Add `PCWEB-071` after PCWEB-070 in `docs/requirements-traceability-matrix.md` and `AC-PCWEB-064` after AC-PCWEB-063 in `docs/pc-local-web-mvp-acceptance.md`.

- [x] **Step 3: Add decision log entry**

Append `DEC-071` describing why retry/replay classification is required before enabled event append, rejected alternatives, boundaries, and verification.

- [x] **Step 4: Update project docs and README**

Update README endpoint lists and Live ASR boundary text. Update project structure, roadmap, privacy/data-flow, and end-to-end checklist so PCWEB-071 is part of the documented local-only lifecycle chain.

### Task 2: Write Failing PCWEB-071 Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add no-existing append test**

Add `test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_returns_no_existing_append_without_mutating_events`. It must create a no-revision Live ASR mock session, guard provider config/env/keychain/HTTP reads, record `/events` before/after, post to `/llm-card-lifecycle-retry-replay-preflights`, and assert `retry_replay_resolution_status=no_existing_append`, two checks, mutation flags false, no forbidden values, and no event mutation.

- [x] **Step 2: Add safe replay test**

Use a persisted record containing matching `llm_schema_result:card_dry_run_001` and `suggestion_card:card_dry_run_001` events with the expected append idempotency keys. Assert `retry_replay_resolution_status=safe_to_replay`, `safe_to_replay_existing_events=true`, and every item has `resolution_status=safe_replay_same_event`.

- [x] **Step 3: Add conflict classification tests**

Cover an existing future event id with mismatched idempotency as `blocked_mismatched_replay`, an idempotency marker without the matching lifecycle event as `blocked_existing_idempotency_key`, and a partial replay as `blocked_by_partial_replay`.

- [x] **Step 4: Add 404, persistence, and 422 tests**

Cover unknown request id, missing session, cross-app JSON persistence with unchanged session file bytes, and request shape errors.

- [x] **Step 5: Update README contract gate**

Extend `test_web_mvp_readme_documents_scripted_browser_e2e_gate` to require PCWEB-071, the endpoint path, `retry_replay_preflight_status=analyzed`, and no event mutation wording.

- [x] **Step 6: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "retry_replay or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: PCWEB-071 endpoint tests fail with `404 Not Found` before implementation, while README gate passes once documentation is written.

Result before implementation: 9 endpoint tests failed with `404 Not Found`; README gate passed after PCWEB-071 documentation was added.

### Task 3: Implement PCWEB-071 Backend Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add payload validator**

Add `_validate_llm_card_lifecycle_retry_replay_preflight_payload`:

- require object body,
- allowed fields exactly `mode`, `request_id`, `candidate_response`,
- require `mode` string exactly `preflight_only`,
- require `request_id` string and trim only request id,
- require `candidate_response` object,
- reject extras with deterministic 422 detail.

- [x] **Step 2: Add route**

Add:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights
```

The route validates payload, loads the ASR live record, and returns `_llm_card_lifecycle_retry_replay_preflight_from_record(record, payload)`.

- [x] **Step 3: Add retry/replay response helper**

Implement `_llm_card_lifecycle_retry_replay_preflight_from_record` by calling `_llm_card_lifecycle_append_result_audit_preview_from_record` with `mode=preview_only`, then mapping `append_result_audit_events` into retry/replay checks.

Top-level response fields must include:

- `retry_replay_preflight_mode=preflight_only`
- `retry_replay_preflight_status=analyzed`
- `retry_replay_resolution_status=no_existing_append|safe_to_replay|blocked_by_partial_replay|blocked_by_conflict`
- `safe_to_replay_existing_events`
- `safe_to_mutate_events=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `retry_replay_check_count`
- `retry_replay_checks`

- [x] **Step 4: Add event/idempotency classifier helpers**

Add helpers that build indexes over current record events by event id and by top-level or payload `idempotency_key`. The idempotency index must be one-to-many so duplicate evidence is observable. A check is `safe_replay_same_event` only when the existing event id, event type, append idempotency key, request id, request draft event id, card id, and `suggestion_card` nested card id all match the future lifecycle event; partial replay, duplicate idempotency evidence, and mismatched evidence must remain blocked.

### Task 4: Verify and Document Results

**Files:**
- Modify docs from Task 1 as needed

- [x] **Step 1: Run focused tests**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "retry_replay or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: all PCWEB-071 focused tests plus README gate pass.

Initial result: focused PCWEB-071 plus README gate passed, 10 passed, 180 deselected, 2 warnings.

Review hardening result: added failing tests for same event/key with mismatched request/draft/card metadata, duplicate idempotency evidence, and conflicting top-level/payload idempotency keys inside one event; final focused PCWEB-071 plus README gate passed, 13 passed, 180 deselected, 2 warnings.

- [x] **Step 2: Run backend regression**

```bash
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Expected: app and live event backend tests pass.

Initial result: backend regression passed, 228 passed, 2 warnings.

Final result after review hardening: backend regression passed, 231 passed, 2 warnings.

- [x] **Step 3: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: quality gates pass without remote ASR/LLM calls or `configs/local/` reads.

Initial result: `pc-web` passed with core 34 passed, web backend 231 passed, and browser smoke passed. `all-local --no-browser` passed with ASR runtime 65 passed, ASR bakeoff 18 passed, core 34 passed, and web backend 231 passed.

Final result after review hardening: `pc-web` passed with core 34 passed, web backend 234 passed, and browser smoke passed. `all-local --no-browser` passed with ASR runtime 65 passed, ASR bakeoff 18 passed, core 34 passed, and web backend 234 passed.

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

Result: no cache directories printed, no `8767`/`9223` listeners printed, and sensitive marker scan printed no matches.
