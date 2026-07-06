# PCWEB-048 Live ASR LLM Request Draft Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local no-LLM `llm_request_draft_event` that previews how a future suggestion-card LLM request would be assembled.

**Architecture:** Extend `asr_live_events.py` derived events from a state/scheduler/candidate triad to a state/scheduler/candidate/request-draft quartet. The request draft is deterministic and local; report, API/SSE, and timeline consume it as audit data only.

**Tech Stack:** Python, FastAPI test client, vanilla JavaScript browser smoke, pytest.

---

### Task 1: Backend Event Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_live_events.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`

- [x] **Step 1: Write failing unit test**

Add a test requiring a `llm_request_draft_event` immediately after a Live ASR `suggestion_candidate_event` for an ActionItem candidate. Assert:

- event sequence is `transcript_final -> state_event -> scheduler_event -> suggestion_candidate_event -> llm_request_draft_event -> evaluation_summary`
- `request_id=asr_llm_request_draft_asr_suggestion_candidate_asr_action_event_asr_seg_action_001`
- `request_type=llm_suggestion_card_draft`
- `request_status=draft_only`
- `target_candidate_id=asr_suggestion_candidate_asr_action_event_asr_seg_action_001`
- `prompt_version=not-called`
- `model=not-called`
- `llm_call_status=not_called`
- `card_status=not_created`
- `schema_status=not_generated`
- candidate quality metadata is copied into the request draft

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_emits_llm_request_draft_after_suggestion_candidate -q
```

Expected: fail because `llm_request_draft_event` does not exist.

- [x] **Step 3: Implement request draft event**

Add `llm_request_draft_event` to `EVENT_ORDER`, extend local derived event grouping to a quartet, and add `_llm_request_draft_payload(candidate_payload, state_spec)`.

- [x] **Step 4: Verify backend unit tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py -q
```

### Task 2: API, Draft Review, and UI Surface

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_report.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [x] **Step 1: Write failing integration assertions**

Update tests to assert:

- API/SSE include `llm_request_draft_event`
- Live ASR candidate query endpoint remains candidate-only
- draft JSON includes `llm_request_drafts`
- draft Markdown includes `## LLM Request Drafts`
- frontend timeline summarizes request draft audit text
- browser smoke still sees zero suggestion cards, zero schema results, zero silenced events, and zero formal report fetches

- [x] **Step 2: Verify red where applicable**

Run focused `tests/test_app.py` assertions before implementation and confirm failure.

- [x] **Step 3: Implement collectors/renderers**

Collect `llm_request_draft_event` in `build_asr_live_draft_review`, render Markdown, add frontend event type and summary, and add browser smoke assertions.

- [x] **Step 4: Verify integration**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q

cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs
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

- [x] **Step 1: Document PCWEB-048**

Record that request drafts are local audit events only and still do not call LLM or generate cards.

- [x] **Step 2: Run gates**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [x] **Step 3: Clean and inspect**

Run cache cleanup, port checks, and sensitive scan.
