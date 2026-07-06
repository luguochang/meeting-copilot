# PCWEB-128 Mainline ASR-Blocked Trial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PC workbench entry that runs the product Live ASR flow while explicitly showing the DEC-201 ASR quality blocker and keeping real microphone capture disabled.

**Architecture:** Reuse the existing Live ASR session repository, SSE stream, realistic synthetic events, and Shadow MVP panel. Add a small backend endpoint that creates a local synthetic Live ASR session and returns a DEC-201 quality summary; add one toolbar button and renderer so the UI makes the mainline state visible instead of looking like another generic simulation.

**Tech Stack:** FastAPI, Pydantic, existing Web MVP static JS/CSS, pytest, Playwright browser smoke.

---

### Task 1: Backend Mainline Trial Endpoint

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Write failing API test**

Add `test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker`.

Expected assertions:

```python
response = client.post(
    "/desktop/mainline-asr-blocked-trial/sessions",
    json={"session_id": "mainline_asr_blocked_trial_review"},
)
assert response.status_code == 201
body = response.json()
assert body["trial_id"] == "mainline_asr_blocked_trial"
assert body["trial_status"] == "mainline_trial_session_created"
assert body["asr_quality_exit_status"] == "not_exited"
assert body["asr_quality_decision_status"] == "blocked_by_funasr_smoke_assembly_input_guard"
assert body["user_can_start_real_mic_shadow_test_now"] is False
assert body["safe_to_capture_microphone_now"] is False
assert body["safe_to_call_remote_asr_now"] is False
assert body["safe_to_call_llm_now"] is False
assert body["blocked_asr_candidates"][0]["candidate_id"] == "chunk10_hotword"
assert body["blocked_asr_candidates"][1]["candidate_id"] == "chunk20_hotword"
```

Also fetch `/live/asr/sessions/mainline_asr_blocked_trial_review/events` and assert it contains `transcript_final`, `state_event`, `llm_request_draft_event`, and `evaluation_summary`.

- [x] **Step 2: Run test to verify RED**

Run:

```bash
cd code/web_mvp/backend
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py::test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker -q -p no:cacheprovider
```

Expected: FAIL with 404 because the endpoint does not exist.

- [x] **Step 3: Implement endpoint and response helper**

Add request model `CreateMainlineAsrBlockedTrialSessionRequest`, route `POST /desktop/mainline-asr-blocked-trial/sessions`, and helper `_mainline_asr_blocked_trial_response(...)`. Reuse `_long_shadow_realistic_meeting_simulation_events()` for the Live ASR event stream and `build_asr_live_events(..., provider="local_mock_asr", is_mock=True)`.

Return these stable fields:

```python
{
    "trial_id": "mainline_asr_blocked_trial",
    "trial_status": "mainline_trial_session_created",
    "session_id": session_id,
    "provider": "local_mock_asr",
    "execution_boundary": "synthetic_live_events_only_no_mic_no_audio_file_no_remote_calls",
    "mainline_decision_id": "DEC-201",
    "asr_quality_exit_status": "not_exited",
    "asr_quality_decision_status": "blocked_by_funasr_smoke_assembly_input_guard",
    "selected_product_route": "pc_product_flow_with_asr_quality_blocked_visible",
    "recommended_next_action": "continue_pc_product_flow_keep_real_mic_blocked",
    "product_replay_summary": {
        "funasr_engineering_preview_created_count": 3,
        "funasr_engineering_scenario_count": 4,
        "mock_engineering_preview_created_count": 4,
        "negative_control_fake_candidate_count": 0,
        "failed_funasr_scenario_id": "incident-review-001",
    },
    "blocked_asr_candidates": [...],
    "live_events": live_events,
    safety flags all false,
}
```

- [x] **Step 4: Run focused API test to verify GREEN**

Run the same focused pytest command. Expected: PASS.

### Task 2: Frontend Button And Summary Panel

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`

- [x] **Step 1: Write failing static asset test**

Extend the existing frontend static test to assert:

```python
assert "mainline-asr-blocked-trial-button" in html.text
assert "loadMainlineAsrBlockedTrial" in script.text
assert "/desktop/mainline-asr-blocked-trial/sessions" in script.text
assert "renderMainlineAsrBlockedTrial" in script.text
assert "mainline_asr_blocked_trial" in script.text
assert "continue_pc_product_flow_keep_real_mic_blocked" in script.text
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
cd code/web_mvp/backend
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py::test_frontend_static_assets_wire_expected_workbench_behavior -q -p no:cacheprovider
```

Expected: FAIL because the button and JS function are missing.

- [x] **Step 3: Implement UI wiring**

Add toolbar button text `主线试运行`. In `app.js`, bind `mainlineAsrBlockedTrialButton`, add `loadMainlineAsrBlockedTrial()`, and render the endpoint response into the existing `mac-local-shadow-mvp-panel` with `renderMainlineAsrBlockedTrial(summary)`.

The renderer must show:

- `mainline_asr_blocked_trial`
- `DEC-201`
- `not_exited`
- `blocked_by_funasr_smoke_assembly_input_guard`
- `continue_pc_product_flow_keep_real_mic_blocked`
- `chunk10_hotword`
- `chunk20_hotword`
- `incident-review-001`

- [x] **Step 4: Run focused static test to verify GREEN**

Run the same focused pytest command. Expected: PASS.

### Task 3: Browser Smoke And Documentation

**Files:**
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`
- Modify: `docs/decision-log.md`
- Modify: `docs/current-mainline-index.md`
- Modify: `docs/current-plan-and-validation-report-2026-07-04.md`
- Modify: `docs/requirements-traceability-matrix.md`

- [x] **Step 1: Add browser smoke assertions**

Click `mainline-asr-blocked-trial-button`, wait for `/desktop/mainline-asr-blocked-trial/sessions`, wait for Live ASR SSE close, and assert the panel includes `mainline_asr_blocked_trial`, `DEC-201`, `not_exited`, `blocked_by_funasr_smoke_assembly_input_guard`, `chunk10_hotword`, `chunk20_hotword`, and `incident-review-001`.

- [x] **Step 2: Run focused browser smoke**

Run:

```bash
node code/web_mvp/e2e/browser_smoke.mjs
```

Expected: PASS with checked list including `mainline ASR blocked trial`.

- [x] **Step 3: Update docs**

Record PCWEB-128 in decision log, current mainline index, current validation report, and RTM. State that it advances PC product flow visibility but does not unlock real microphone capture.

- [x] **Step 4: Run regression**

Run:

```bash
cd code/web_mvp/backend
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py -q -p no:cacheprovider

node code/web_mvp/e2e/browser_smoke.mjs
```

Expected: PASS.

### Result

Implemented PCWEB-128:

- `POST /desktop/mainline-asr-blocked-trial/sessions` creates a local synthetic Live ASR session and returns DEC-201 ASR quality blocked metadata.
- Web workbench toolbar now has `主线试运行`.
- Clicking it opens Live ASR mode, streams the long technical meeting timeline, and renders `DEC-201`, `not_exited`, `blocked_by_funasr_smoke_assembly_input_guard`, `chunk10_hotword`, `chunk20_hotword`, and `incident-review-001`.
- The feature keeps real microphone, user audio, remote ASR, remote LLM, model download, public audio download, and Tauri/Cargo execution blocked.

Verification completed so far:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_app.py::test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker \
  tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
Result: 2 passed, 2 warnings
```

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok, checked includes "mainline ASR blocked trial"
```

Full regression:

```text
cd code/web_mvp/backend
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py -q -p no:cacheprovider
Result: 288 passed, 2 warnings
```

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok, checked includes "mainline ASR blocked trial"
```

Sensitive scan:

```text
rg -n "<secret-token-pattern>|<gateway-domain-pattern>|<private-audio-path-pattern>|<local-model-cache-pattern>" ...
Result: no matches
```
