# PCWEB-055 Live ASR LLM Provider Config Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local Live ASR provider config validation endpoint that validates an explicit request body without reading real config, reading secrets, calling LLM, or mutating events.

**Architecture:** Keep the endpoint session-scoped and aligned with PCWEB-052/053/054. It reads only the Live ASR audit record for session/source/trace metadata, validates only the submitted JSON body, returns safe derived display fields, and keeps execution disabled with `safe_to_execute=false`.

**Tech Stack:** Python, FastAPI, pytest, local JSON repository.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests for:

- `POST /live/asr/sessions/{session_id}/llm-provider-config-validation`
- valid request body response with `validation_status=valid`
- `validation_mode=request_body_only`
- `config_file_status=not_read`
- `credentials_status=provided_but_not_returned`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_execute=false`
- safe display values only: base URL origin, model, timeout, CA basename, and `api_key=null`
- no submitted secret in success responses
- no sentinel config/env secret in success responses
- no `/events` mutation
- transcript-only session support
- JSON persistence across app instances
- missing session 404
- 422 for missing field, extra field, unsupported protocol, invalid URL, empty secret/model, out-of-range timeout, absolute CA path, and traversal CA path, without leaking submitted secrets

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_validates_request_body_without_reading_config -q
```

Expected: fail with 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add:

```python
@app.post("/live/asr/sessions/{session_id}/llm-provider-config-validation")
def validate_asr_live_session_llm_provider_config(
    session_id: str,
    payload: Any = Body(...),
) -> dict[str, Any]:
    ...
```

Add helpers:

- `_validate_llm_provider_config_payload(payload)`
- `_llm_provider_config_validation_from_record(record, payload)`

The implementation must use generic 422 messages and must not echo the submitted `api_key`, `authorization`, `bearer_token`, `raw_config`, top-level non-object body input, or URL userinfo credential value.

- [x] **Step 4: Verify endpoint tests**

Run:

```bash
python3 -m pytest \
  tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_validates_request_body_without_reading_config \
  tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_accepts_transcript_only_session \
  tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_reads_persisted_record_across_app_instances \
  tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_returns_404_for_missing_session \
  tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_rejects_invalid_request_without_leaking_secret \
  tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_rejects_non_object_body_without_leaking_secret -q
```

Expected: all pass.

### Task 2: Documentation and Gates

**Files:**
- Create: `docs/pcweb-055-live-asr-llm-provider-config-validation-plan.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Document PCWEB-055**

Record that provider config validation is request-body-only and explicitly not a config loader, secret manager, enabled executor, cost estimator, schema/card engine, or paid LLM call.

- [x] **Step 2: Run focused verification**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

- [x] **Step 3: Run gates**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [x] **Step 4: Clean and inspect**

Remove pytest/cache artifacts, check ports `8767` and `9223`, and run the sensitive scan.
