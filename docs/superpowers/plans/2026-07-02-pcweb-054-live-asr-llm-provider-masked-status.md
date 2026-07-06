# PCWEB-054 Live ASR LLM Provider Masked Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Live ASR endpoint that defines the future masked LLM provider status response without reading credentials or calling any provider.

**Architecture:** Keep masked status as a static policy/status projection over an existing Live ASR audit record. The backend reads only the session audit record, returns a template-only display envelope, and keeps config, credentials, LLM calls, token estimates, schema generation, and card creation disabled.

**Tech Stack:** Python, FastAPI, pytest, local JSON repository.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests requiring:

- `GET /live/asr/sessions/{session_id}/llm-provider-masked-status`
- response contains `status_kind=masked_provider_status`, `status_mode=template_only`, `provider_status=not_configured`, `config_load_status=not_loaded`, `config_source_status=not_read`, `credentials_status=not_read`, `llm_call_status=not_called`, and `safe_to_execute=false`
- response preserves `session_id`, `source`, and `trace_kind`
- response returns `display_values` with only `null` values
- response returns `display_value_status` with all non-secret fields `not_read` and `api_key=never_display`
- response returns `masked_value_policy` and `forbidden_status_signals`
- `api_key` appears only as a placeholder/policy/forbidden-signal name, never as a real or masked value
- an environment config path does not get read by this endpoint
- reading `/events` before and after the endpoint returns the same event list
- transcript-only sessions return the same template-only status
- JSON persistence works across app instances
- missing session returns 404

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_returns_template_without_reading_config -q
```

Expected: fail with 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add route:

```python
@app.get("/live/asr/sessions/{session_id}/llm-provider-masked-status")
def get_asr_live_session_llm_provider_masked_status(session_id: str) -> dict[str, Any]:
    ...
```

Add `_llm_provider_masked_status_from_record(record)` that returns static masked status policy metadata. It must not read files, environment secrets, local config, provider clients, token estimators, or call LLM.

- [x] **Step 4: Verify endpoint tests**

Run:

```bash
python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_returns_template_without_reading_config \
  tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_returns_template_for_transcript_only_session \
  tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_reads_persisted_record_across_app_instances \
  tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_returns_404_for_missing_session \
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

- [x] **Step 1: Document PCWEB-054**

Record that provider masked status is local/read-only and explicitly template-only. It must not call LLM, read credentials, read local config, estimate tokens, generate schema results, create cards, or mutate audit records.

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
