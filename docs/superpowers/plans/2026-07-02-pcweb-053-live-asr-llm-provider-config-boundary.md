# PCWEB-053 Live ASR LLM Provider Config Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Live ASR endpoint that exposes the future LLM provider config secret/display boundary without reading credentials or calling any provider.

**Architecture:** Keep the boundary as a static policy projection over an existing Live ASR audit record. The backend reads only the session audit record, returns provider config field metadata, and keeps config, credentials, LLM calls, token estimates, schema generation, and card creation disabled.

**Tech Stack:** Python, FastAPI, pytest, local JSON repository.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests requiring:

- `GET /live/asr/sessions/{session_id}/llm-provider-config-boundary`
- response contains `boundary_status=template_only`, `provider_protocol=openai_compatible_chat_completions`, `config_load_status=not_loaded`, `config_source_status=not_read`, `credentials_status=not_read`, `llm_call_status=not_called`, and `safe_to_execute=false`
- response preserves `session_id`, `source`, and `trace_kind`
- response returns static field metadata for `base_url`, `api_key`, `model`, `timeout_seconds`, and `ca_bundle_path`
- `api_key` appears only as metadata with `classification=secret`, `display_policy=never_display`, and `response_value_policy=never_return_value`
- response has no top-level `api_key`, `authorization`, `bearer_token`, or `raw_config` values
- an environment config path does not get read by this endpoint
- reading `/events` before and after the endpoint returns the same event list
- transcript-only sessions return the same template-only policy
- JSON persistence works across app instances
- missing session returns 404

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_returns_template_without_reading_config -q
```

Expected: fail with 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add route:

```python
@app.get("/live/asr/sessions/{session_id}/llm-provider-config-boundary")
def get_asr_live_session_llm_provider_config_boundary(session_id: str) -> dict[str, Any]:
    ...
```

Add `_llm_provider_config_boundary_from_record(record)` that returns static provider config policy metadata. It must not read files, environment secrets, local config, provider clients, token estimators, or call LLM.

- [x] **Step 4: Verify endpoint tests**

Run:

```bash
python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_returns_template_without_reading_config \
  tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_returns_template_for_transcript_only_session \
  tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_reads_persisted_record_across_app_instances \
  tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_returns_404_for_missing_session \
  tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q
```

### Task 2: Documentation and Gates

**Files:**
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Document PCWEB-053**

Record that provider config boundary is local/read-only and explicitly template-only. It must not call LLM, read credentials, read local config, estimate tokens, generate schema results, create cards, or mutate audit records.

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
