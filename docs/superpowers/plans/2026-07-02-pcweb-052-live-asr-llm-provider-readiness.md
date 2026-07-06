# PCWEB-052 Live ASR LLM Provider Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Live ASR endpoint that reports LLM provider readiness as blocked without reading credentials or calling any provider.

**Architecture:** Keep readiness as a local projection over the existing Live ASR audit record and executor projections. The backend reads the session audit record, counts request drafts/previews/disabled runs, returns future provider contract metadata, and keeps all config and credential status as not read.

**Tech Stack:** Python, FastAPI, pytest, local JSON repository.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests requiring:

- `GET /live/asr/sessions/{session_id}/llm-provider-readiness`
- response contains `readiness_status=not_ready`, `executor_mode=disabled`, `enabled_mode_status=blocked`, and `can_execute_llm=false`
- response exposes `provider_protocol=openai_compatible_chat_completions`
- response reports `provider_config_status=not_loaded`, `provider_config_source=not_read`, `credentials_status=not_read`, `base_url_status=not_configured`, and `model_status=not_configured`
- response keeps `llm_call_status=not_called`, `schema_status=not_generated`, `card_status=not_created`, and `cost_status=not_estimated`
- response counts request drafts, execution previews, and disabled runs
- reading `/events` before and after the endpoint returns the same event list
- an environment config path does not get read by this endpoint
- transcript-only sessions return `queue_status=empty`
- JSON persistence works across app instances
- missing session returns 404

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_readiness_endpoint_reports_not_ready_without_reading_config -q
```

Expected: fail with 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add route:

```python
@app.get("/live/asr/sessions/{session_id}/llm-provider-readiness")
def get_asr_live_session_llm_provider_readiness(session_id: str) -> dict[str, Any]:
    ...
```

Add `_llm_provider_readiness_from_record(record)` that reuses `_request_draft_events_from_record`, `_execution_previews_from_record`, and `_disabled_execution_runs_from_record`. It must not read files, environment secrets, local config, provider clients, or call LLM.

- [x] **Step 4: Verify endpoint tests**

Run:

```bash
python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_readiness_endpoint_reports_not_ready_without_reading_config \
  tests/test_app.py::test_asr_live_llm_provider_readiness_endpoint_returns_empty_queue_for_transcript_only_session \
  tests/test_app.py::test_asr_live_llm_provider_readiness_endpoint_reads_persisted_record_across_app_instances \
  tests/test_app.py::test_asr_live_llm_provider_readiness_endpoint_returns_404_for_missing_session \
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

- [x] **Step 1: Document PCWEB-052**

Record that provider readiness is local/read-only and explicitly not ready. It must not call LLM, read credentials, read local config, estimate tokens, generate schema results, create cards, or mutate audit records.

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
