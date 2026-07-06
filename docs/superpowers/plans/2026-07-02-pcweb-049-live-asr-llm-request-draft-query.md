# PCWEB-049 Live ASR LLM Request Draft Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Live ASR endpoint that returns existing no-LLM `llm_request_draft_event` records.

**Architecture:** Mirror the existing `/suggestion-candidates` endpoint with a narrower projection over `llm_request_draft_event`. The helper returns original event metadata and payload only; it does not mutate audit records, execute requests, create schema results, or create suggestion cards.

**Tech Stack:** Python, FastAPI, pytest, local JSON repository.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests requiring:

- `GET /live/asr/sessions/{session_id}/llm-request-drafts`
- response contains `request_draft_count`
- `request_drafts` contains only `llm_request_draft_event`
- original `sequence`, `event_id`, `event_type`, `at_ms`, and payload are preserved
- transcript-only sessions return an empty list
- JSON persistence works across app instances
- missing session returns 404

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_request_drafts_endpoint_returns_only_request_draft_queue -q
```

Expected: fail with 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add `_request_draft_events_from_record(record)` and route:

```python
@app.get("/live/asr/sessions/{session_id}/llm-request-drafts")
def get_asr_live_session_llm_request_drafts(session_id: str) -> dict[str, Any]:
    ...
```

The helper must filter only `event_type == "llm_request_draft_event"`.

- [x] **Step 4: Verify endpoint tests**

Run:

```bash
python3 -m pytest tests/test_app.py::test_asr_live_llm_request_drafts_endpoint_returns_only_request_draft_queue \
  tests/test_app.py::test_asr_live_llm_request_drafts_endpoint_returns_empty_queue_for_transcript_only_session \
  tests/test_app.py::test_asr_live_llm_request_drafts_endpoint_reads_persisted_record_across_app_instances \
  tests/test_app.py::test_asr_live_llm_request_drafts_endpoint_returns_404_for_missing_session -q
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

- [x] **Step 1: Document PCWEB-049**

Record that request draft query is read-only and does not call LLM or create cards/schema results.

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
