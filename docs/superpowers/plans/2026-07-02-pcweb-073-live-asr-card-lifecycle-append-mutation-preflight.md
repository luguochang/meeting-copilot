# PCWEB-073 Live ASR Card Lifecycle Append Mutation Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only, response-only append mutation preflight after PCWEB-072 that analyzes canonical serialized lifecycle events without mutating the Live ASR audit record.

**Architecture:** The new endpoint validates a `preflight_only` request, loads the Live ASR session, reuses PCWEB-072 append event serializer output as the source of truth, and projects each serialized event into a mutation preflight check. It keeps every mutation/write/safety flag disabled and documents that this is not an enabled repository commit.

**Tech Stack:** FastAPI backend, pytest, JSON session repository, local Web MVP docs.

---

### Task 1: Document PCWEB-073 Contract

**Files:**
- Create: `docs/pcweb-073-live-asr-card-lifecycle-append-mutation-preflight-plan.md`
- Create: `docs/superpowers/plans/2026-07-02-pcweb-073-live-asr-card-lifecycle-append-mutation-preflight.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Add the PCWEB-073 plan document**

Define endpoint `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-mutation-preflights`, request `mode=preflight_only`, response-only mutation preflight fields, and explicit non-goals: no event append, no repository transaction, no idempotency store write, no provider config/secret read, no LLM call.

- [x] **Step 2: Update traceability and acceptance docs**

Add `PCWEB-073` and `AC-PCWEB-066` rows requiring `append_mutation_preflight_status=analyzed`, `append_mutation_readiness_status=blocked_until_enabled|blocked_by_serializer_preflight`, response-only mutation checks, and no `/events` mutation.

- [x] **Step 3: Update README and boundary docs**

Add the endpoint to the README endpoint list and Live ASR boundary text. Update project structure, roadmap, privacy/data-flow, end-to-end checklist, and decision log so PCWEB-073 is part of the documented local-only lifecycle chain.

### Task 2: Write Failing PCWEB-073 Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add allowed lifecycle test**

Add `test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_analyzes_allowed_events_without_mutating_events`. It must create a no-revision Live ASR mock session, guard provider config/env/keychain/HTTP reads, record `/events` before/after, post to `/llm-card-lifecycle-append-mutation-preflights`, and assert:

- `append_event_serializer_status=serialized`
- `append_mutation_preflight_mode=preflight_only`
- `append_mutation_preflight_status=analyzed`
- `append_mutation_readiness_status=blocked_until_enabled`
- two checks for `llm_schema_result` and `suggestion_card`
- each check points to the serialized event id, preview event id, append idempotency key, sequence, and disabled write statuses
- top-level and per-check safe flags are false
- `/events` is unchanged
- forbidden guard values are absent from the response

- [x] **Step 2: Add silenced lifecycle tests**

Add schema-invalid and policy-blocked tests that assert mutation preflight checks cover `llm_schema_result` plus `suggestion_silenced`.

- [x] **Step 3: Add conflict test**

Add a persisted existing lifecycle event/idempotency conflict and assert `append_mutation_readiness_status=blocked_by_serializer_preflight`, with the affected check status `blocked_by_serializer_preflight`.

- [x] **Step 4: Add 404, persistence, and shape tests**

Cover unknown request id, missing session, persisted record byte stability across app instances, and request body shape errors.

- [x] **Step 5: Extend README gate**

Extend `test_web_mvp_readme_documents_scripted_browser_e2e_gate` to require `PCWEB-073`, endpoint path, `append_mutation_preflight_status=analyzed`, and `safe_to_mutate_events=false`.

- [x] **Step 6: Run RED**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_mutation_preflight or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: endpoint tests fail with `404 Not Found`; README gate passes after docs update.

Result: RED confirmed. The eight new PCWEB-073 endpoint tests failed with `404 Not Found`; README gate passed.

### Task 3: Implement PCWEB-073 Backend Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add request validator**

Add `_validate_llm_card_lifecycle_append_mutation_preflight_payload` by following the PCWEB-072 validator pattern:

- request body object only
- allowed fields exactly `mode`, `request_id`, `candidate_response`
- `mode` is required, string, not empty, and exactly `preflight_only`
- `request_id` is required, string, trimmed, and not empty
- `candidate_response` is required object
- unsupported mode detail is `unsupported card lifecycle append mutation preflight mode: {raw_mode}`

- [x] **Step 2: Add route**

Register:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-mutation-preflights
```

The route validates payload, loads the ASR live record, and returns `_llm_card_lifecycle_append_mutation_preflight_from_record(record, payload)`.

- [x] **Step 3: Add mutation preflight helper**

Implement `_llm_card_lifecycle_append_mutation_preflight_from_record` by calling `_llm_card_lifecycle_append_event_serializer_dry_run_from_record` with `mode=dry_run_only`, then mapping every `serialized_append_events` item through `_card_lifecycle_append_mutation_preflight_check_from_serialized_event`.

Top-level response must include:

- `append_mutation_preflight_mode=preflight_only`
- `append_mutation_preflight_status=analyzed`
- `append_mutation_readiness_status=blocked_until_enabled|blocked_by_serializer_preflight`
- `mutation_preflight_check_count`
- `mutation_preflight_checks`
- `repository_transaction_status=not_started`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_mutate_events=false`
- `safe_to_commit_transaction=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

- [x] **Step 4: Add per-check helper**

Each check must project serializer fields, set `mutation_preflight_check_status=blocked_until_enabled` for `serialization_status=would_serialize_if_enabled` and `blocked_by_serializer_preflight` otherwise, and keep all mutation/write/safe flags disabled.

- [x] **Step 5: Run focused GREEN**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "append_mutation_preflight or readme_documents_scripted_browser_e2e_gate" -q
```

Expected: all focused PCWEB-073 tests plus README gate pass.

Result: focused PCWEB-073 plus README gate passed, 9 passed, 201 deselected, 2 warnings.

### Task 4: Verify And Record Results

**Files:**
- Modify: `docs/pcweb-073-live-asr-card-lifecycle-append-mutation-preflight-plan.md`
- Modify: `docs/superpowers/plans/2026-07-02-pcweb-073-live-asr-card-lifecycle-append-mutation-preflight.md`

- [x] **Step 1: Run backend regression**

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Result: `248 passed, 2 warnings`.

- [x] **Step 2: Run quality gates**

```bash
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Result: `pc-web` passed with core `34 passed`, web backend `251 passed`, browser smoke passed. `all-local --no-browser` passed with ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, web backend `251 passed`.

- [x] **Step 3: Run cleanup and sensitive scan**

Remove `.pytest_cache` and `__pycache__`, verify no test ports are listening, and run the local sensitive marker scan excluding `configs/local/**`.

Result: cache scan printed no remaining `.pytest_cache` or `__pycache__`; ports `8767` and `9223` had no listeners; sensitive marker scan printed no matches.

- [x] **Step 4: Record verification results**

Update the PCWEB-073 plan and this implementation plan with RED/GREEN/regression/quality-gate results.
