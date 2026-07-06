# PCWEB-051 Live ASR LLM Executor Disabled Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local POST executor entrypoint that returns disabled/skipped LLM execution runs derived from existing Live ASR execution previews.

**Architecture:** Keep the first executor endpoint as a no-call boundary. The backend accepts only `mode=disabled`, reads the existing Live ASR audit record, reuses the execution preview projection, and returns deterministic skipped run envelopes without mutating audit events or reading any provider configuration.

**Tech Stack:** Python, FastAPI, Pydantic, pytest, local JSON repository.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests requiring:

- `POST /live/asr/sessions/{session_id}/llm-execution-runs`
- request body must include `mode=disabled`
- request body rejects missing mode, empty body, and extra fields
- response contains `executor_mode=disabled`, `run_count`, and `runs`
- `runs` contains one skipped run per `llm_request_draft_event`
- each run links to `execution_id`, `request_id`, `request_draft_event_id`, `request_draft_sequence`, target candidate/state, gap rule, source event ids, evidence ids, and segment batch
- every run is local/no-call: `run_status=skipped`, `skip_reason=llm_executor_disabled`, `llm_call_status=not_called`, `schema_status=not_generated`, `card_status=not_created`, `cost_status=not_estimated`
- reading `/events` before and after the POST returns the same event list
- transcript-only sessions return an empty list
- JSON persistence works across app instances
- missing session returns 404
- unsupported mode returns 422

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_returns_skipped_runs_without_calling_llm -q
```

Expected: fail with 405 or 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add request model:

```python
class CreateLlmExecutionRunsRequest(BaseModel):
    mode: str
```

Add route:

```python
@app.post("/live/asr/sessions/{session_id}/llm-execution-runs")
def create_asr_live_session_llm_execution_runs(
    session_id: str,
    payload: CreateLlmExecutionRunsRequest,
) -> dict[str, Any]:
    if payload.mode != "disabled":
        raise HTTPException(
            status_code=422,
            detail="unsupported llm execution mode: <mode>",
        )
    ...
```

Add `_disabled_execution_runs_from_record(record)` that reuses `_execution_previews_from_record(record)` and maps previews to skipped run envelopes. It must not append events, write repository state, call providers, estimate tokens, create schema results, or create cards.

- [x] **Step 4: Verify endpoint tests**

Run:

```bash
python3 -m pytest tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_returns_skipped_runs_without_calling_llm \
  tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_returns_empty_runs_for_transcript_only_session \
  tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_reads_persisted_record_across_app_instances \
  tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_returns_404_for_missing_session \
  tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_rejects_unsupported_mode \
  tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_requires_explicit_mode \
  tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_rejects_empty_body \
  tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_rejects_extra_fields -q
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

- [x] **Step 1: Document PCWEB-051**

Record that the executor entrypoint exists but only in disabled mode. It returns skipped runs and does not call LLM, read credentials, estimate tokens, generate schema results, create cards, or mutate audit records.

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
