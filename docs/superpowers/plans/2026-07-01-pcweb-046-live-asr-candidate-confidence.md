# PCWEB-046 Live ASR Candidate Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add no-LLM confidence and degradation metadata to Live ASR `suggestion_candidate_event` payloads.

**Architecture:** Extend `asr_live_events.py` so candidate quality is computed locally from ASR confidence, evidence quote length, and state item completeness. Scheduler cooldown/budget remains visible through scheduler audit fields but does not reduce candidate quality. Existing API/SSE/draft paths should carry the payload through; frontend timeline summary should render the extra metadata without creating suggestion cards.

**Tech Stack:** Python, FastAPI test client, vanilla JavaScript, pytest, Node browser smoke.

---

### Task 1: Backend Candidate Quality Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_live_events.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`

- [x] **Step 1: Write failing tests**

Add tests requiring:

- an ActionItem candidate with ASR confidence `0.9`, owner, and deadline to emit `candidate_policy_version="asr-candidate-policy.v1"`, `confidence_source="local_deterministic_heuristic"`, `confidence=0.9`, `confidence_level="high"`, and `degradation_reasons=[]`
- a revision candidate with ASR confidence `0.72` and scheduler cooldown to emit `low_asr_confidence`, `confidence=0.7`, and `confidence_level="medium"` while keeping `decision_reason="cooldown"` separate from candidate degradation
- a candidate with missing ASR confidence to emit `missing_asr_confidence`, `confidence=0.75`, and `confidence_level="medium"`
- an ActionItem candidate missing owner/deadline metadata to include the corresponding degradation reason
- a Risk candidate without mitigation to include `risk_mitigation_missing`

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_scores_high_confidence_action_candidate tests/test_live_events.py::test_build_asr_live_events_degrades_low_confidence_skipped_candidate tests/test_live_events.py::test_build_asr_live_events_degrades_incomplete_action_candidate tests/test_live_events.py::test_build_asr_live_events_degrades_risk_without_mitigation -q
```

Expected: fail because candidate quality fields do not exist.

- [x] **Step 3: Implement local quality policy**

Add helper functions in `asr_live_events.py`:

- `_suggestion_candidate_quality(...)`
- `_candidate_degradation_reasons(...)`
- `_confidence_level(confidence)`

Pass `raw_event` confidence and evidence quote into `_suggestion_candidate_payload`. Keep all fields deterministic and local; do not include scheduler cooldown/budget in `degradation_reasons`.

- [x] **Step 4: Verify focused backend**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py -q
```

### Task 2: API, Draft, and Frontend Surface

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_report.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [x] **Step 1: Write failing integration assertions**

Update API/SSE and draft tests to assert:

- candidate payloads include `candidate_policy_version`
- draft JSON candidate entries include `confidence_level`
- draft Markdown includes `confidence`
- SSE text includes `asr-candidate-policy.v1`

Update frontend static/browser smoke assertions to require confidence metadata in timeline/report text while preserving:

- zero suggestion cards
- zero LLM schema events
- zero silenced events
- zero formal report fetches

- [x] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -q
```

Expected: fail until draft Markdown/frontend summary renders the new metadata.

- [x] **Step 3: Render candidate quality metadata**

Update draft Markdown candidate lines and frontend `eventSummary()` for `suggestion_candidate_event`.

- [x] **Step 4: Verify integration**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q

cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs
```

### Task 3: Documentation and Gate

**Files:**
- Modify: `docs/decision-log.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Document DEC-046 and traceability**

Record that PCWEB-046 adds local confidence/degradation metadata to candidate audit events only, with no paid calls and no formal suggestion card creation.

- [x] **Step 2: Run quality gates**

Run:

```bash
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
