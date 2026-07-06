# PCWEB-072 Live ASR Card Lifecycle Append Event Serializer Dry-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only append event serializer dry-run endpoint that converts existing lifecycle preview and append preflight output into canonical future persisted event objects while keeping all mutation disabled.

**Architecture:** Reuse PCWEB-066 append preflight as the source of truth for future event ids, idempotency keys, append order, and conflict status. Reuse PCWEB-065 preview events as the source of truth for event payloads, then return response-only serialized event objects without writing `/events`, opening a transaction, writing an idempotency store, reading secrets, or calling ASR/LLM.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest coverage in `code/web_mvp/backend/tests/test_app.py`, Markdown product docs under `docs/` and `code/web_mvp/README.md`.

---

### Task 1: Document PCWEB-072 Contract

**Files:**
- Create: `docs/superpowers/plans/2026-07-02-pcweb-072-live-asr-card-lifecycle-append-event-serializer-dry-run.md`
- Modify: `docs/pcweb-072-live-asr-card-lifecycle-append-event-serializer-dry-run-plan.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Keep the PCWEB-072 plan document current**

Document endpoint, request body, response shape, serialization rules, non-goals, local-only boundaries, tests, and verification plan.

- [x] **Step 2: Add traceability and acceptance**

Keep the `PCWEB-072` requirements traceability row and add `AC-PCWEB-065` after AC-PCWEB-064.

- [x] **Step 3: Add decision log entry**

Append `DEC-072` describing why canonical event serialization is required before enabled append, rejected alternatives, boundaries, and verification.

- [x] **Step 4: Update project docs and README**

Update README endpoint lists and Live ASR boundary text. Update project structure, roadmap, privacy/data-flow, and end-to-end checklist so PCWEB-072 is part of the documented local-only lifecycle chain.

### Task 2: Write Failing PCWEB-072 Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add allowed serializer dry-run test**

Add `test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_serializes_allowed_events_without_mutating_events`. It must create a no-revision Live ASR mock session, guard provider config/env/keychain/HTTP reads, record `/events` before/after, post to `/llm-card-lifecycle-append-event-serializer-dry-runs`, and assert:

- response status 200
- `append_event_serializer_mode=dry_run_only`
- `append_event_serializer_status=serialized`
- `append_event_serialization_status=would_serialize_if_enabled`
- `append_event_count=2`
- serialized event types are `llm_schema_result` and `suggestion_card`
- serialized event ids are `llm_schema_result:card_dry_run_001` and `suggestion_card:card_dry_run_001`
- each event has `id == event_id`
- `sequence` equals `would_append_sequence`
- `source=live_asr_stream`
- `trace_kind=live_event`
- top-level and payload idempotency keys match the append idempotency key
- payload includes request id, request draft event id, card id, and preview-derived fields
- mutation flags remain false/not_written/not_appended
- `/events` is unchanged and forbidden values do not appear in the response text

- [x] **Step 2: Add silenced serializer tests**

Add one schema-invalid test that uses a failed candidate response and expects `llm_schema_result` plus `suggestion_silenced` with `silence_reason=schema_validation_failed`.

Add one policy-blocked test that uses a revision-backed session and expects `llm_schema_result` plus `suggestion_silenced` with `silence_reason=card_creation_policy_blocked`.

- [x] **Step 3: Add conflict preservation test**

Persist an existing future lifecycle event or idempotency conflict before the request. Assert top-level `append_event_serialization_status=blocked_by_preflight`, the affected serialized event has `serialization_status=blocked_by_preflight`, and `/events` stays unchanged.

- [x] **Step 4: Add 404, persistence, and 422 tests**

Cover unknown request id, missing session, cross-app JSON persistence with unchanged session file bytes, and request shape errors for non-object body, missing/non-string/unsupported/empty/whitespace-padded mode, missing/non-string/blank request id, missing/non-object candidate response, and extra top-level fields.

- [x] **Step 5: Update README contract gate**

Extend `test_web_mvp_readme_documents_scripted_browser_e2e_gate` to require `PCWEB-072`, endpoint path, `append_event_serializer_status=serialized`, `event_append_status=not_appended`, and no event mutation wording.

- [x] **Step 6: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_event_serializer or readme_documents_scripted_browser_e2e_gate" -q
```

Expected before implementation: endpoint tests fail with `404 Not Found`, while the README gate passes once documentation is written.

Result before implementation: 9 endpoint tests failed with `404 Not Found`; README gate passed.

### Task 3: Implement PCWEB-072 Backend Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add payload validator**

Add `_validate_llm_card_lifecycle_append_event_serializer_dry_run_payload`:

- require object body
- allowed fields exactly `mode`, `request_id`, `candidate_response`
- require `mode` string exactly `dry_run_only`
- require `request_id` string and trim only request id
- require `candidate_response` object
- reject extras with deterministic 422 detail

- [x] **Step 2: Add route**

Add:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-event-serializer-dry-runs
```

The route validates payload, loads the ASR live record, and returns `_llm_card_lifecycle_append_event_serializer_dry_run_from_record(record, payload)`.

- [x] **Step 3: Add serializer response helper**

Implement `_llm_card_lifecycle_append_event_serializer_dry_run_from_record` by calling `_llm_card_lifecycle_append_preflight_dry_run_from_record` with `mode=dry_run_only`, then combining each append plan item with the matching preview event.

Top-level response fields must include:

- `append_event_serializer_mode=dry_run_only`
- `append_event_serializer_status=serialized`
- `append_event_serialization_status=would_serialize_if_enabled|blocked_by_preflight`
- `append_event_count`
- `serialized_append_events`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

- [x] **Step 4: Add serialized event helper**

Add a helper that returns one canonical event object per append plan item:

- `serializer_result_id`
- `serialization_status`
- `id` and `event_id`
- `event_type`
- `sequence`
- `at_ms`
- `source=live_asr_stream`
- `trace_kind=live_event`
- top-level `idempotency_key`
- `payload` copied from the preview event plus `idempotency_key`, `request_id`, `request_draft_event_id`, and `card_id`
- `preview_event_id`
- `future_event_id`
- `append_status`
- `conflict_status`
- `would_append_sequence`
- `would_append_after_sequence`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_append_event=false`
- `safe_to_create_card=false`

### Task 4: Verify and Document Results

**Files:**
- Modify docs from Task 1 as needed

- [x] **Step 1: Run focused tests**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_event_serializer or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: all PCWEB-072 focused tests plus README gate pass.

Result: focused PCWEB-072 plus README gate passed, 10 passed, 192 deselected, 2 warnings. Serializer-only focused tests also passed, 9 passed, 193 deselected, 2 warnings.

- [x] **Step 2: Run backend regression**

```bash
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Expected: app and live event backend tests pass.

Result: backend regression passed, 240 passed, 2 warnings.

- [x] **Step 3: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: quality gates pass without remote ASR/LLM calls or `configs/local/` reads.

Result: `pc-web` passed with core 34 passed, web backend 243 passed, and browser smoke passed. `all-local --no-browser` passed with ASR runtime 65 passed, ASR bakeoff 18 passed, core 34 passed, and web backend 243 passed.

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

Expected: no cache directories outside excluded venvs, no test ports listening, and no sensitive marker output.

Result: cleanup printed no remaining cache directories, ports `8767` and `9223` had no listeners, and the local sensitive marker scan printed no matches.

### Review Result

Independent read-only implementation review found no Critical, Important, or Minor findings. The reviewer confirmed strict `mode=dry_run_only`, request-id-only trimming, response-only behavior, preview-derived payload plus append idempotency key, preflight conflict preservation, safe-false/not-written/not-appended statuses, and README/traceability coverage.
