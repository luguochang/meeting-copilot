# PCWEB-047 Live ASR Candidate Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a read-only API endpoint that returns only Live ASR no-LLM `suggestion_candidate_event` audit records.

**Architecture:** Reuse the existing `asr_live_repo.get(session_id)` record path and project candidate events from the persisted event list. The endpoint preserves event sequence and payload exactly, and performs no ranking, filtering, card generation, schema validation, ASR calls, or LLM calls.

**Tech Stack:** FastAPI, pytest, existing JSON/in-memory ASR live repository.

---

### Task 1: Candidate Query API Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing API tests**

Add tests requiring `GET /live/asr/sessions/{session_id}/suggestion-candidates` to:

- return `session_id`, `source`, `trace_kind`, `candidate_count`, and `candidates`
- include only `suggestion_candidate_event` records
- preserve each candidate event `sequence`, `event_id`, `event_type`, `at_ms`, and full `payload`
- include PCWEB-046 quality metadata in payload
- preserve `llm_call_status=not_called` and `card_status=not_created`
- return an empty list for a valid Live ASR session with no local state candidate

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_suggestion_candidates_endpoint_returns_only_candidate_queue tests/test_app.py::test_asr_live_suggestion_candidates_endpoint_returns_empty_queue_for_transcript_only_session -q
```

Expected: fail with 404 because the route does not exist.

- [x] **Step 3: Implement endpoint**

Add a helper near the Live ASR routes in `app.py`:

```python
def _candidate_events_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": int(event.get("sequence", 0)),
            "event_id": str(event.get("id", "")),
            "event_type": str(event.get("event_type", "")),
            "at_ms": int(event.get("at_ms", 0)),
            "payload": dict(event.get("payload") or {}),
        }
        for event in record.get("events") or []
        if event.get("event_type") == "suggestion_candidate_event"
    ]
```

Wire `GET /live/asr/sessions/{session_id}/suggestion-candidates` to use the same error handling as `/events`.

- [x] **Step 4: Verify focused tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_suggestion_candidates_endpoint_returns_only_candidate_queue tests/test_app.py::test_asr_live_suggestion_candidates_endpoint_returns_empty_queue_for_transcript_only_session -q
```

### Task 2: Persistence and Error Boundary

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add persistence and error tests**

Add tests requiring:

- candidate query works across app instances when `data_dir` is set
- missing session returns `404` with `ASR live session not found`
- existing Live ASR create/delete persistence tests continue to cover unsafe session id validation; this endpoint should preserve the same repository error handling for route-reachable invalid ids

- [x] **Step 2: Verify focused tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -q
```

### Task 3: Documentation and Quality Gates

**Files:**
- Modify: `docs/decision-log.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Document PCWEB-047**

Record that candidate query is read-only and still does not call LLM, create cards, or rank candidates.

- [x] **Step 2: Run verification**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q

cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs

cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [x] **Step 3: Clean and inspect**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -name '__pycache__' -o -name '.pytest_cache' \) -exec rm -rf {} +
lsof -iTCP:8767 -sTCP:LISTEN || true
lsof -iTCP:9223 -sTCP:LISTEN || true
```
