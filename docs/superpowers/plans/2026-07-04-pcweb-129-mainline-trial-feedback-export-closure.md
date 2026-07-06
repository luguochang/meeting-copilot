# PCWEB-129 Mainline Trial Feedback And Export Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the current mainline trial product flow from Live ASR suggestion candidates to local feedback and Markdown/JSON export preview while keeping synthetic evidence marked as not-Go.

**Execution status:** Implemented and browser-smoke verified on 2026-07-04. Detailed result is recorded in `docs/pcweb-129-mainline-trial-feedback-export-closure-plan.md`, `docs/decision-log.md`, `docs/current-mainline-index.md`, `docs/current-plan-and-validation-report-2026-07-04.md`, and `docs/requirements-traceability-matrix.md`.

**Architecture:** Add a Web MVP endpoint that reads an existing `mainline_asr_blocked_trial` Live ASR session, derives a DRV-033-compatible candidate report from local events, applies deterministic feedback through existing DRV-038 tooling, and returns DRV-036 export previews. Add a workbench button and panel that render the closure and place the Markdown preview in the existing report area.

**Tech Stack:** FastAPI, existing JSON repositories, vanilla HTML/CSS/JS, pytest, Chrome CDP browser smoke.

---

### Task 1: Backend API Red Test

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [ ] **Step 1: Add failing test**

Add test near the existing `test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker`:

```python
def test_desktop_mainline_trial_feedback_export_closure_creates_preview_not_go_evidence():
    client = TestClient(create_app())
    created = client.post(
        "/desktop/mainline-asr-blocked-trial/sessions",
        json={"session_id": "mainline_trial_feedback_export_closure_review"},
    )
    assert created.status_code == 201

    response = client.post(
        "/desktop/mainline-trial-feedback-export-closures",
        json={"session_id": "mainline_trial_feedback_export_closure_review"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["pcweb_id"] == "PCWEB-129"
    assert body["closure_id"] == "mainline_trial_feedback_export_closure"
    assert body["closure_status"] == "mainline_trial_feedback_export_preview_created"
    assert body["source_trial_id"] == "mainline_asr_blocked_trial"
    assert body["candidate_report_validation_status"] == "passed"
    assert body["feedback_ingestion_status"] == "shadow_report_feedback_ingested_preview_only"
    assert body["export_readiness_status"] == "draft_export_preview_only"
    assert body["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    assert body["final_decision"]["decision"] == "inconclusive_requires_more_shadow_tests"
    assert body["feedback_entry_count"] == 2
    assert body["timeline_counts"]["candidate_cards"] >= 2
    assert "Draft only; not real mic validation." in body["markdown_export_preview"]
    assert "确认决策是否包含 owner" in body["markdown_export_preview"]
    assert body["safe_to_access_microphone_now"] is False
    assert body["safe_to_call_remote_asr_now"] is False
    assert body["safe_to_call_llm_now"] is False
```

- [ ] **Step 2: Run red test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_trial_feedback_export_closure_creates_preview_not_go_evidence \
  -q -p no:cacheprovider
```

Expected: fail with `404` because the endpoint does not exist.

### Task 2: Backend Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [ ] **Step 1: Add request model**

Add:

```python
class CreateMainlineTrialFeedbackExportClosureRequest(BaseModel):
    session_id: str = Field(min_length=1)
    feedback_entries: list[dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 2: Add route**

Add:

```python
@app.post("/desktop/mainline-trial-feedback-export-closures", status_code=201)
def create_mainline_trial_feedback_export_closure(
    payload: CreateMainlineTrialFeedbackExportClosureRequest,
) -> dict[str, Any]:
    try:
        record = asr_live_repo.get(payload.session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"ASR live session not found: {payload.session_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    report = _mainline_trial_feedback_export_closure_from_record(
        record,
        feedback_entries=payload.feedback_entries,
    )
    if str(report.get("closure_status", "")).startswith("blocked_"):
        raise HTTPException(status_code=422, detail=report)
    return report
```

- [ ] **Step 3: Add helpers**

Implement helpers in `app.py`:

```python
def _mainline_trial_feedback_export_closure_from_record(
    record: dict[str, Any],
    *,
    feedback_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    ...
```

The helper must:
- reject records whose `ingest_mode` is not `mainline_asr_blocked_trial`
- build candidate report sections from Live ASR events
- validate through `real_mic_shadow_test_report_schema.validate_candidate_report`
- load `_load_shadow_report_feedback_ingestion_module()`
- apply provided feedback entries or deterministic defaults
- return DRV-038/DRV-036 statuses and previews
- set all safety flags false

- [ ] **Step 4: Run green test**

Run the focused backend test. Expected: pass.

### Task 3: Static UI Red/Green

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`

- [ ] **Step 1: Add static assertions**

Assert:

```python
assert 'id="mainline-feedback-export-closure-button"' in html.text
assert 'id="mainline-closure-panel"' in html.text
assert "loadMainlineTrialFeedbackExportClosure" in script.text
assert "/desktop/mainline-trial-feedback-export-closures" in script.text
assert "renderMainlineTrialFeedbackExportClosure" in script.text
assert "mainline-closure-panel" in styles.text
```

- [ ] **Step 2: Run static test**

Expected: fail before UI changes.

- [ ] **Step 3: Implement HTML/CSS/JS**

Add toolbar button `闭环预览`, main panel `主线闭环`, JS function to call the endpoint, render status metrics, and put `markdown_export_preview` in `report-panel`.

- [ ] **Step 4: Run static test**

Expected: pass.

### Task 4: Browser Smoke

**Files:**
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [ ] **Step 1: Extend mainline section**

After the existing mainline ASR blocked trial checks, click `mainline-feedback-export-closure-button` and assert:

```javascript
closureText.includes("mainline_trial_feedback_export_closure")
closureText.includes("draft_export_preview_only")
closureText.includes("not_go_evidence_replay_or_feedback_missing")
reportText.includes("Draft only; not real mic validation.")
```

- [ ] **Step 2: Run browser smoke**

Run:

```bash
node code/web_mvp/e2e/browser_smoke.mjs
```

Expected: `status=ok`.

### Task 5: Docs And Verification

**Files:**
- Modify: `docs/decision-log.md`
- Modify: `docs/current-mainline-index.md`
- Modify: `docs/current-plan-and-validation-report-2026-07-04.md`
- Modify: `docs/requirements-traceability-matrix.md`

- [ ] **Step 1: Document DEC-205 / PCWEB-129**

Record the decision, files changed, verification, and safety boundary.

- [ ] **Step 2: Run full verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py -q -p no:cacheprovider
node code/web_mvp/e2e/browser_smoke.mjs
Run the current local sensitive-scan command without persisting private hostnames, token patterns, user audio filenames, or user-specific cache paths in this plan.
```

Expected:
- pytest: all tests pass
- browser smoke: `status=ok`
- sensitive scan: no matches
