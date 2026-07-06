# PCWEB-050 Live ASR LLM Execution Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, no-call Live ASR endpoint that turns existing `llm_request_draft_event` records into deterministic LLM execution previews.

**Architecture:** Keep the execution preview as a local projection over persisted Live ASR audit records. The backend reads the existing audit record, filters request drafts, derives preview envelopes with idempotency and schema-target metadata, and returns them without mutating events or calling any provider.

**Tech Stack:** Python, FastAPI, pytest, local JSON repository.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests requiring:

- `GET /live/asr/sessions/{session_id}/llm-execution-previews`
- response contains `execution_preview_count`
- `execution_previews` contains one item per `llm_request_draft_event`
- each preview links to `request_id`, `request_draft_event_id`, `request_draft_sequence`, target candidate/state, gap rule, source event ids, evidence ids, and segment batch
- every preview is local/no-call: `execution_status=preview_only`, `llm_call_status=not_called`, `schema_status=not_generated`, `card_status=not_created`, `cost_status=not_estimated`
- reading the endpoint does not mutate `/live/asr/sessions/{session_id}/events`
- transcript-only sessions return an empty list
- JSON persistence works across app instances
- missing session returns 404

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_execution_previews_endpoint_returns_preview_queue_without_calling_llm -q
```

Expected: fail with 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add `_execution_previews_from_record(record)` and route:

```python
@app.get("/live/asr/sessions/{session_id}/llm-execution-previews")
def get_asr_live_session_llm_execution_previews(session_id: str) -> dict[str, Any]:
    ...
```

The helper must filter only `event_type == "llm_request_draft_event"` and derive preview payloads from the existing draft payload. It must not append events, write repository state, call providers, estimate tokens, or create cards.

- [x] **Step 4: Verify endpoint tests**

Run:

```bash
python3 -m pytest tests/test_app.py::test_asr_live_llm_execution_previews_endpoint_returns_preview_queue_without_calling_llm \
  tests/test_app.py::test_asr_live_llm_execution_previews_endpoint_returns_empty_queue_for_transcript_only_session \
  tests/test_app.py::test_asr_live_llm_execution_previews_endpoint_reads_persisted_record_across_app_instances \
  tests/test_app.py::test_asr_live_llm_execution_previews_endpoint_returns_404_for_missing_session -q
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

- [x] **Step 1: Document PCWEB-050**

Record that execution previews are local read-only projections and do not call LLM, read credentials, estimate tokens, generate schema results, create cards, or mutate audit records.

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
