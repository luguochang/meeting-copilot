# PCWEB-042 Live ASR Draft Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add JSON and Markdown draft review endpoints for persisted Live ASR audit records without creating formal reports, suggestion cards, or LLM calls.

**Architecture:** Build a small `asr_live_report.py` module that derives a draft review from existing Live ASR event envelopes. `app.py` reads records from the ASR live repository and exposes `/draft` and `/draft.md`. The draft is explicitly marked non-formal.

**Tech Stack:** Python 3, FastAPI, pytest, local JSON/SSE event records.

---

### Task 1: Draft JSON Endpoint

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Create: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_report.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [ ] **Step 1: Write failing API test**

Add a test that creates a Live ASR session and requests `/live/asr/sessions/{id}/draft`. Assert:

```python
assert body["review_type"] == "asr_live_draft"
assert body["is_formal_report"] is False
assert body["llm_call_status"] == "not_called"
assert body["transcript_text"] == "先灰度 10%。先灰度 5%，不是 10%。谁负责回滚？"
assert [item["target_type"] for item in body["state_candidates"]] == [
    "DecisionCandidate",
    "DecisionCandidate",
    "OpenQuestion",
]
assert body["suggestion_cards"] == []
assert body["llm_schema_results"] == []
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_draft_review_json_summarizes_audit_record_without_llm -q
```

Expected: 404.

- [ ] **Step 3: Implement `asr_live_report.py`**

Create functions:

```python
def build_asr_live_draft_review(record: dict[str, Any]) -> dict[str, Any]:
    ...


def render_asr_live_draft_markdown(review: dict[str, Any]) -> str:
    ...
```

- [ ] **Step 4: Wire JSON endpoint**

In `app.py`, add `GET /live/asr/sessions/{session_id}/draft` that returns `build_asr_live_draft_review(record)`.

- [ ] **Step 5: Verify green**

Run the focused JSON test.

### Task 2: Draft Markdown Endpoint

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_report.py`

- [ ] **Step 1: Write failing Markdown test**

Add a test for `/live/asr/sessions/{id}/draft.md`. Assert response is text/markdown and includes:

```text
# Live ASR Draft Review: live_asr_draft_review
Draft only; not a formal gated meeting report.
## Transcript Draft
先灰度 5%，不是 10%。
## State Candidates
OpenQuestion
谁负责回滚？
## Scheduler Decisions
llm_candidate_queued
llm_candidate_skipped
not_called
```

- [ ] **Step 2: Verify red**

Run the focused Markdown test and expect 404.

- [ ] **Step 3: Wire Markdown endpoint**

In `app.py`, add `GET /live/asr/sessions/{session_id}/draft.md` returning `Response(..., media_type="text/markdown; charset=utf-8")`.

- [ ] **Step 4: Verify backend focused suite**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Expected: pass.

### Task 3: Docs and Gates

**Files:**
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`
- Modify: `code/web_mvp/README.md`

- [ ] **Step 1: Update docs**

Add `PCWEB-042`, `AC-PCWEB-035`, and `DEC-042`. Wording must say the draft is not a formal report and does not call LLM.

- [ ] **Step 2: Run gates**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [ ] **Step 3: Cleanup and safety checks**

Clean Python caches, check ports 8767/9223, and run the sensitive scan excluding `configs/local/**`.

