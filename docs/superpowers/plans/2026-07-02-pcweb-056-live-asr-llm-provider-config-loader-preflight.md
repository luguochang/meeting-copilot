# PCWEB-056 Live ASR LLM Provider Config Loader Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local Live ASR provider config loader preflight endpoint that validates future loader request shape without reading config, reading secrets, calling LLM, or mutating events.

**Architecture:** Keep preflight session-scoped and aligned with PCWEB-052 through PCWEB-055. It reads only the Live ASR audit record for session/source/trace metadata, validates only the submitted JSON body, returns path-display metadata only, and keeps config loading/execution disabled.

**Tech Stack:** Python, FastAPI, pytest, local JSON repository.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests for:

- `POST /live/asr/sessions/{session_id}/llm-provider-config-loader-preflight`
- valid preflight response with `preflight_status=accepted`
- `preflight_mode=metadata_only`
- `config_file_status=not_read`
- `config_existence_status=not_checked`
- `credentials_status=not_read`
- `llm_call_status=not_called`
- `safe_to_load_config=false`
- path display kept null, with no raw path, basename, parent basename, or path-derived label
- no raw submitted path in response
- no sentinel config/env secret in response
- no `/events` mutation
- transcript-only session support
- JSON persistence across app instances
- missing session 404
- 422 for non-object body, missing config path, extra fields, unsupported mode/protocol, empty URL/file URL/control-character/traversal paths, unsupported or duplicate requested fields, and authorization flags that allow secret reads or LLM calls

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_returns_contract_without_reading_config -q
```

Expected: fail with 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add:

```python
@app.post("/live/asr/sessions/{session_id}/llm-provider-config-loader-preflight")
def preflight_asr_live_session_llm_provider_config_loader(
    session_id: str,
    payload: Any = Body(...),
) -> dict[str, Any]:
    ...
```

Add helpers:

- `_validate_llm_provider_config_loader_preflight_payload(payload)`
- `_llm_provider_config_loader_preflight_from_record(record, payload)`

The implementation must use generic 422 messages and must not echo submitted `config_path`, `api_key`, `authorization`, `bearer_token`, or `raw_config` values.

- [x] **Step 4: Verify endpoint tests**

Run:

```bash
python3 -m pytest \
  tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_returns_contract_without_reading_config \
  tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_accepts_transcript_only_session \
  tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_reads_persisted_record_across_app_instances \
  tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_returns_404_for_missing_session \
  tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_rejects_invalid_request_without_leaking_path_or_secret -q
```

Expected: all pass.

### Task 2: Documentation and Gates

**Files:**
- Create: `docs/pcweb-056-live-asr-llm-provider-config-loader-preflight-plan.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Document PCWEB-056**

Record that provider config loader preflight is metadata-only and explicitly not a config reader, secret manager, enabled executor, cost estimator, schema/card engine, or paid LLM call.

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
