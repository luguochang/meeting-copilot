# PCWEB-045 Live ASR Suggestion Candidate Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking; all task boxes below are marked complete after implementation and verification.

**Goal:** Add a local no-LLM `suggestion_candidate_event` to the Live ASR skeleton so state candidates can enter an auditable future-suggestion queue without creating suggestion cards.

**Architecture:** Extend the existing `asr_live_events.py` derived-event builder. Each local state candidate keeps its existing `state_event -> scheduler_event` pair and gains one adjacent `suggestion_candidate_event` containing a self-contained gap rule, prompt, evidence ids, source state event ids, and `llm_call_status=not_called`. Draft review and Web event stream consume this event as audit data only.

**Tech Stack:** Python, FastAPI test client, vanilla JavaScript browser smoke, pytest.

---

### Task 1: Backend Event Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_live_events.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`

- [x] **Step 1: Write failing unit test**

Add a test requiring a `suggestion_candidate_event` after a Live ASR `ActionItem` scheduler event. The candidate must include `candidate_id=asr_suggestion_candidate_asr_action_event_asr_seg_action_001`, `candidate_type=state_gap_review`, `target_type=ActionItem`, `gap_rule_id=action.owner.deadline.confirmation`, `llm_call_status=not_called`, `card_status=not_created`, and evidence ids.

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_emits_suggestion_candidate_after_action_scheduler -q
```

Expected: fail because `suggestion_candidate_event` is not emitted.

- [x] **Step 3: Implement local candidate event**

Add `suggestion_candidate_event` to `EVENT_ORDER` and emit it from `_local_state_scheduler_events()` after each scheduler event. Add a helper mapping `target_type` to local `gap_rule_id` and `suggested_prompt`. Keep `suggestion_card`, `llm_schema_result`, and `suggestion_silenced` absent.

- [x] **Step 4: Verify focused backend**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py tests/test_app.py -q
```

### Task 2: Draft Review and API Coverage

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_report.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Write failing draft tests**

Update draft JSON and Markdown tests to require `suggestion_candidates`, `## Suggestion Candidates`, `action.owner.deadline.confirmation`, `risk.rollback.validation`, and `not_created`.

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_draft_review_json_summarizes_audit_record_without_llm tests/test_app.py::test_asr_live_draft_review_markdown_is_marked_as_non_formal -q
```

Expected: fail because draft review does not collect or render candidates.

- [x] **Step 3: Implement draft collection/rendering**

Collect `suggestion_candidate_event` payloads into `review["suggestion_candidates"]`. Render a `## Suggestion Candidates` Markdown section. Keep `suggestion_cards=[]` and `llm_schema_results=[]`.

- [x] **Step 4: Verify draft tests**

Run the same two tests and confirm they pass.

### Task 3: Web UI Smoke Coverage

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add frontend static assertions**

Add test assertions that `app.js` knows `suggestion_candidate_event` and summarizes it in `eventSummary()`.

- [x] **Step 2: Implement event summary**

Add `suggestion_candidate_event` to `liveEventTypes` and return a compact summary containing `candidate_id`, `gap_rule_id`, `llm_call_status`, and `card_status`.

- [x] **Step 3: Extend browser smoke**

Assert the Live ASR event stream text includes `suggestion_candidate_event`, `action.owner.deadline.confirmation`, `risk.rollback.validation`, and `not_created`, while keeping suggestion card, schema, silenced, and formal report counts at zero.

### Task 4: Documentation and Decision Log

**Files:**
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Add PCWEB-045 / AC-PCWEB-038 / DEC-045**

Document that Live ASR now produces a no-LLM suggestion candidate queue. Explicitly state this is not suggestion-card generation and does not call LLM.

### Task 5: Verification

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py tests/test_app.py -q

cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs

cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Then clean caches, check ports `8767` and `9223`, and run the sensitive scan.
