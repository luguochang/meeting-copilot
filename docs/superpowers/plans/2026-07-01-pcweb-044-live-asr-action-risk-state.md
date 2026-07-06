# PCWEB-044 Live ASR Action/Risk State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend local Live ASR state extraction so `final` and `revision` transcript events can emit evidence-backed `ActionItem` and `Risk` state candidates in addition to `DecisionCandidate` and `OpenQuestion`.

**Architecture:** Keep the existing `asr_live_events.py` event builder and add small deterministic helper functions inside the local skeleton. The helpers produce self-contained state items consumed by the existing frontend state board, draft review, repository, and scheduler audit paths.

**Tech Stack:** Python, FastAPI test client, vanilla JavaScript browser smoke, pytest.

---

### Task 1: Backend Contract Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_live_events.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [ ] **Step 1: Add failing unit tests**

Add tests that require:

```python
ActionItem from "张三下周三补充兼容性测试用例。"
Risk from "如果错误率超过 0.1% 就回滚。"
```

Each state event must include a self-contained state item, evidence ids, `source=live_asr_stream`, and `state_origin=local_deterministic_asr_skeleton`.

- [ ] **Step 2: Verify red**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_extracts_action_item_state_candidate -q
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_extracts_risk_state_candidate -q
```

Expected: fail because the local extractor does not emit these target types yet.

### Task 2: Local Extractor Implementation

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`

- [ ] **Step 1: Add deterministic helpers**

Add helper functions for local action/risk detection and deterministic state specs. Keep the same state-event/scheduler-event structure.

- [ ] **Step 2: Verify green**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py -q
python3 -m pytest tests/test_app.py -q
```

### Task 3: Frontend/E2E Sample Coverage

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [ ] **Step 1: Extend local Live ASR sample**

Add final events for:

```text
如果错误率超过 0.1% 就回滚。
张三下周三补充兼容性测试用例。
```

- [ ] **Step 2: Extend browser smoke assertions**

Assert Live ASR state board includes:

```text
如果错误率超过 0.1%
张三下周三补充兼容性测试用例
```

Continue asserting zero suggestion cards, zero schema events, zero silenced events, and no formal report requests.

### Task 4: Documentation and Decision Log

**Files:**
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`
- Modify: `code/web_mvp/README.md`

- [ ] **Step 1: Add PCWEB-044 / AC-PCWEB-037 / DEC-044**

Document the deterministic ActionItem/Risk skeleton and its boundaries.

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

Then clean caches, check ports, and run the sensitive scan.
