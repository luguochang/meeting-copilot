# PCWEB-043 Live ASR Draft UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the non-formal Live ASR draft review in the Web workbench report panel after the local Live ASR stream finishes.

**Architecture:** Reuse the existing backend draft endpoint from PCWEB-042 and add only a thin frontend fetch/render path. The Live ASR path remains separate from the formal session report path so the UI cannot confuse a synthetic ASR audit draft with a gated meeting report.

**Tech Stack:** FastAPI static assets, vanilla JavaScript, Python pytest, Node CDP browser smoke.

---

### Task 1: Add Frontend Contract Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [ ] **Step 1: Write the failing static asset test**

Add assertions to `test_workbench_static_assets_are_served`:

```python
assert "loadLiveAsrDraft" in script.text
assert "/draft.md" in script.text
assert 'if (currentEventMode === "live_asr")' in script.text
```

- [ ] **Step 2: Run the static asset test to verify it fails**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_workbench_static_assets_are_served -q
```

Expected: fail because `loadLiveAsrDraft` and `/draft.md` are not in `app.js`.

- [ ] **Step 3: Write the failing browser smoke assertions**

In the Live ASR section, count `/draft.md` fetches and assert:

```javascript
assert(liveAsrReview.reportText.includes("Draft only; not a formal gated meeting report."), "expected live ASR draft warning");
assert(liveAsrReview.reportText.includes("## State Candidates"), "expected live ASR draft state candidates section");
assert(liveAsrReview.reportFetchCount === 0, `expected live ASR not to request report.md, got ${liveAsrReview.reportFetchCount}`);
assert(liveAsrReview.draftFetchCount === 1, `expected live ASR to request one draft.md, got ${liveAsrReview.draftFetchCount}`);
```

Then click `#export-report-button` while still in Live ASR mode and assert:

```javascript
assert(liveAsrRefreshReview.reportFetchCount === 0, `expected live ASR manual report refresh not to request report.md, got ${liveAsrRefreshReview.reportFetchCount}`);
assert(liveAsrRefreshReview.draftFetchCount === 2, `expected live ASR manual report refresh to request draft.md twice total, got ${liveAsrRefreshReview.draftFetchCount}`);
```

### Task 2: Implement Live ASR Draft UI Fetch

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`

- [ ] **Step 1: Add draft loader**

Add:

```javascript
async function loadLiveAsrDraft() {
  if (!currentSessionId) {
    return;
  }
  try {
    const response = await fetch(`/live/asr/sessions/${currentSessionId}/draft.md`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    document.getElementById("report-panel").textContent = await response.text();
  } catch (error) {
    showToast(error.message);
  }
}
```

- [ ] **Step 2: Wire terminal summary**

In `connectLiveEventStream()`, after closing on `evaluation_summary`, keep `live_mock` on `loadReport()` and add:

```javascript
if (currentEventMode === "live_asr") {
  loadLiveAsrDraft();
}
```

- [ ] **Step 3: Run focused frontend asset test**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_workbench_static_assets_are_served -q
```

Expected: pass.

- [ ] **Step 4: Guard stale async responses and pending sessions**

Capture the session id and mode before fetching report/draft text. Before writing `report-panel`, verify the captured values still match `currentSessionId` and `currentEventMode`. While Live ASR session creation is pending, clear `currentSessionId` and let `loadReport()` / `loadLiveAsrDraft()` return without requesting a previous session.

- [ ] **Step 5: Guard stale session creation and event stream loads**

Increment a session-load token whenever replay, Live Mock, or Live ASR starts creating a new session. After each async create returns, apply it only if the token and mode still match. `loadEventStream()` should also capture session id and mode and return no events without rendering if the active session/mode changed while the request was in flight.

- [ ] **Step 6: Cover JSON fallback**

When `window.EventSource` is missing, `connectLiveEventStream()` should call `loadEventStream()`, inspect the returned events for `evaluation_summary`, and run the same terminal side effect as SSE mode: Live Mock loads the formal fixture report, Live ASR loads the non-formal draft.

### Task 3: Update Documentation and Decision Log

**Files:**
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`
- Modify: `code/web_mvp/README.md`

- [ ] **Step 1: Add PCWEB-043 and AC-PCWEB-036**

Document that the workbench fetches `/live/asr/sessions/{id}/draft.md` after Live ASR terminal summary, keeps formal report fetch count at zero, and preserves no-LLM/no-card boundaries.

- [ ] **Step 2: Add DEC-043**

Record that Live ASR UI surfaces draft review as a non-formal panel output and explicitly refuses the formal report path until real state engine, scheduler, and gates exist.

### Task 4: Verify Gates

**Files:**
- No production edits.

- [ ] **Step 1: Run focused backend tests**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -q
```

- [ ] **Step 2: Run browser smoke**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs
```

- [ ] **Step 3: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [ ] **Step 4: Cleanup checks**

Remove `.pytest_cache` and `__pycache__` outside `.venv-*`, check ports `8767` and `9223`, and run the existing sensitive scan excluding `configs/local/**`.
