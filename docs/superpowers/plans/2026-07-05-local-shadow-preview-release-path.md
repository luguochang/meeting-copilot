# Local Shadow Preview Release Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one truthful Local Shadow Preview release path that makes current product value visible while clearly showing ASR quality, real microphone, and LLM execution blockers.

**Architecture:** Keep the existing FastAPI/Web workbench and mainline runner as the single preview surface. Add a release-readiness summary over existing ASR quality, real mic readiness, and mainline trial evidence rather than adding another independent readiness wrapper.

**Tech Stack:** Python 3, FastAPI, pytest, browser smoke via `code/web_mvp/e2e/browser_smoke.mjs`, existing Web MVP static assets, existing ASR/readiness tools.

---

## File Structure

- Modify `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
  - Add one release-readiness summary endpoint derived from existing local reports and static current defaults.
- Modify `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
  - Add one concise release summary panel near the mainline controls.
- Modify `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
  - Fetch and render the release summary; keep existing mainline trial flows.
- Modify `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
  - Style release status without adding decorative cards inside cards.
- Modify `code/web_mvp/backend/tests/test_app.py`
  - Add endpoint and static asset tests.
- Modify `code/web_mvp/e2e/browser_smoke.mjs`
  - Verify the release status is visible and does not claim real-mic readiness.
- Modify `tools/mainline_usable_e2e_runner.py`
  - Include release readiness fields in the output only if this can be derived from existing data without new side effects.
- Update `docs/current-mainline-index.md`, `docs/decision-log.md`, and `docs/requirements-traceability-matrix.md`
  - Record implementation outcome and verification.

## Task 1: Release Summary API

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Test: `code/web_mvp/backend/tests/test_app.py`

- [ ] **Step 1: Write the failing API test**

Add a test that calls `GET /desktop/local-shadow-preview-release-readiness` and asserts the response is explicit:

```python
def test_local_shadow_preview_release_readiness_reports_truthful_status(client):
    response = client.get("/desktop/local-shadow-preview-release-readiness")
    assert response.status_code == 200
    payload = response.json()

    assert payload["release_tier"] == "local_shadow_preview"
    assert payload["demo_preview_ready"] is True
    assert payload["shadow_pilot_ready"] is False
    assert payload["production_mvp_ready"] is False
    assert payload["asr_quality_exit_status"] == "not_exited"
    assert payload["real_mic_readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert payload["llm_execution_status"] == "disabled_not_called"
    assert payload["formal_card_status"] == "not_created_in_current_mainline_preview"
    assert payload["allowed_claim"] == "local synthetic/replay/artifact Copilot preview"
    assert "real meeting ready" not in payload["allowed_claim"].lower()
    assert payload["safety_flags"]["safe_to_capture_microphone_now"] is False
    assert payload["safety_flags"]["safe_to_call_llm_now"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_local_shadow_preview_release_readiness_reports_truthful_status \
  -q -p no:cacheprovider
```

Expected: FAIL with `404` because the endpoint does not exist.

- [ ] **Step 3: Implement the endpoint**

In `create_app()`, add:

```python
@app.get("/desktop/local-shadow-preview-release-readiness")
def desktop_local_shadow_preview_release_readiness() -> dict[str, Any]:
    return {
        "release_tier": "local_shadow_preview",
        "demo_preview_ready": True,
        "shadow_pilot_ready": False,
        "production_mvp_ready": False,
        "asr_quality_exit_status": "not_exited",
        "asr_quality_decision_status": "blocked_by_funasr_smoke_assembly_input_guard",
        "real_mic_readiness_status": "blocked_not_ready_for_user_real_mic_shadow_test",
        "llm_execution_status": "disabled_not_called",
        "formal_card_status": "not_created_in_current_mainline_preview",
        "formal_report_status": "preview_only_not_real_meeting_go_evidence",
        "allowed_claim": "local synthetic/replay/artifact Copilot preview",
        "forbidden_claims": [
            "real meeting ready",
            "production ASR ready",
            "production MVP ready",
            "background microphone capture ready",
        ],
        "release_blockers": [
            "asr_quality_exit_not_passed",
            "real_mic_shadow_test_blocked",
            "desktop_real_audio_capture_not_enabled",
            "llm_execution_disabled",
            "formal_cards_not_created_in_current_mainline_preview",
        ],
        "next_valid_actions": [
            "p0_local_shadow_preview_truthful_packaging",
            "p1_asr_quality_exit_or_pivot",
            "p2_user_authorized_shadow_pilot_after_p1",
        ],
        "safety_flags": {
            "safe_to_capture_microphone_now": False,
            "safe_to_capture_system_audio_now": False,
            "safe_to_call_remote_asr_now": False,
            "safe_to_call_llm_now": False,
            "safe_to_read_configs_local_now": False,
        },
    }
```

- [ ] **Step 4: Run the focused test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_local_shadow_preview_release_readiness_reports_truthful_status \
  -q -p no:cacheprovider
```

Expected: PASS.

## Task 2: Workbench Release Summary Panel

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- Test: `code/web_mvp/backend/tests/test_app.py`

- [ ] **Step 1: Write the failing static asset test**

Extend `test_workbench_static_assets_are_served` or add a focused static test:

```python
def test_workbench_static_assets_include_local_shadow_preview_release_summary(client):
    html = client.get("/").text
    js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    assert "local-shadow-preview-release-panel" in html
    assert "loadLocalShadowPreviewReleaseReadiness" in js
    assert "/desktop/local-shadow-preview-release-readiness" in js
    assert "local-shadow-preview-release" in css
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_include_local_shadow_preview_release_summary \
  -q -p no:cacheprovider
```

Expected: FAIL because the panel and renderer do not exist.

- [ ] **Step 3: Add the HTML panel**

Add one release summary section near mainline controls:

```html
<section class="panel local-shadow-preview-release" id="local-shadow-preview-release-panel">
  <div class="panel-heading">
    <div>
      <h2>Local Shadow Preview</h2>
      <p>Preview ready; real meeting release still gated.</p>
    </div>
    <button id="local-shadow-preview-release-refresh" type="button">刷新状态</button>
  </div>
  <div id="local-shadow-preview-release-content" class="release-summary-grid"></div>
</section>
```

- [ ] **Step 4: Add the JavaScript loader and renderer**

Add:

```javascript
async function loadLocalShadowPreviewReleaseReadiness() {
  const content = document.getElementById("local-shadow-preview-release-content");
  if (!content) return;
  content.textContent = "Loading release status...";
  const payload = await fetchJson("/desktop/local-shadow-preview-release-readiness");
  renderLocalShadowPreviewReleaseReadiness(payload);
}

function renderLocalShadowPreviewReleaseReadiness(payload) {
  const content = document.getElementById("local-shadow-preview-release-content");
  if (!content) return;
  const blockers = payload.release_blockers || [];
  content.innerHTML = `
    <div class="release-summary-item">
      <span class="label">Preview</span>
      <strong>${payload.demo_preview_ready ? "Ready" : "Blocked"}</strong>
    </div>
    <div class="release-summary-item blocked">
      <span class="label">Shadow Pilot</span>
      <strong>${payload.shadow_pilot_ready ? "Ready" : "Blocked"}</strong>
    </div>
    <div class="release-summary-item blocked">
      <span class="label">Production MVP</span>
      <strong>${payload.production_mvp_ready ? "Ready" : "Blocked"}</strong>
    </div>
    <div class="release-summary-item">
      <span class="label">ASR</span>
      <strong>${payload.asr_quality_exit_status}</strong>
    </div>
    <div class="release-summary-item">
      <span class="label">Real Mic</span>
      <strong>${payload.real_mic_readiness_status}</strong>
    </div>
    <div class="release-summary-item">
      <span class="label">LLM</span>
      <strong>${payload.llm_execution_status}</strong>
    </div>
    <div class="release-summary-wide">
      <span class="label">Allowed Claim</span>
      <p>${payload.allowed_claim}</p>
    </div>
    <div class="release-summary-wide">
      <span class="label">Blockers</span>
      <ul>${blockers.map((item) => `<li>${item}</li>`).join("")}</ul>
    </div>
  `;
}
```

Wire startup and refresh:

```javascript
document
  .getElementById("local-shadow-preview-release-refresh")
  ?.addEventListener("click", loadLocalShadowPreviewReleaseReadiness);

loadLocalShadowPreviewReleaseReadiness();
```

- [ ] **Step 5: Add CSS**

Add:

```css
.local-shadow-preview-release {
  border-color: var(--border-strong);
}

.release-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}

.release-summary-item,
.release-summary-wide {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  background: var(--surface-subtle);
}

.release-summary-item.blocked strong {
  color: var(--danger);
}

.release-summary-wide {
  grid-column: 1 / -1;
}

.release-summary-wide ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
```

- [ ] **Step 6: Run the focused static test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_include_local_shadow_preview_release_summary \
  -q -p no:cacheprovider
```

Expected: PASS.

## Task 3: Browser Smoke Coverage

**Files:**
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [ ] **Step 1: Add browser assertions**

Add checks after the workbench loads:

```javascript
await waitForText("Local Shadow Preview");
await waitForText("Preview");
await waitForText("Shadow Pilot");
await waitForText("Production MVP");
await waitForText("not_exited");
await waitForText("blocked_not_ready_for_user_real_mic_shadow_test");
checked.push("local shadow preview release readiness");
```

- [ ] **Step 2: Run browser smoke**

Run:

```bash
node code/web_mvp/e2e/browser_smoke.mjs
```

Expected: exit 0 and `checked` includes `local shadow preview release readiness`.

## Task 4: Mainline Runner Release Summary

**Files:**
- Modify: `tools/mainline_usable_e2e_runner.py`
- Test: `tests/test_mainline_usable_e2e_runner.py`

- [ ] **Step 1: Write the failing runner test**

Add a test that runs the runner on the existing approved artifact and checks a top-level release readiness summary:

```python
def test_runner_reports_local_shadow_preview_release_readiness(tmp_path):
    report = run_mainline_runner_for_test(
        session_id="local_shadow_preview_release_readiness_test",
        output_root=tmp_path,
        asr_quality_decision_path="artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json",
        asr_events_path="artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json",
        asr_events_provider="local_artifact_asr",
    )

    readiness = report["local_shadow_preview_release_readiness"]
    assert readiness["demo_preview_ready"] is True
    assert readiness["shadow_pilot_ready"] is False
    assert readiness["production_mvp_ready"] is False
    assert readiness["asr_quality_exit_status"] == "not_exited"
    assert readiness["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_mainline_usable_e2e_runner.py::test_runner_reports_local_shadow_preview_release_readiness \
  -q -p no:cacheprovider
```

Expected: FAIL because the summary is missing.

- [ ] **Step 3: Implement the runner summary**

Add a pure helper:

```python
def _local_shadow_preview_release_readiness(report: dict[str, Any]) -> dict[str, Any]:
    closure = report.get("closure") or {}
    asr_quality = report.get("asr_quality") or {}
    mainline_trial = report.get("mainline_trial") or {}
    return {
        "demo_preview_ready": report.get("overall_status") == "mainline_product_chain_exercised_with_expected_blockers",
        "shadow_pilot_ready": False,
        "production_mvp_ready": False,
        "asr_quality_exit_status": asr_quality.get("quality_exit_status", "unknown"),
        "real_mic_readiness_status": mainline_trial.get(
            "real_mic_shadow_readiness_status",
            "blocked_not_ready_for_user_real_mic_shadow_test",
        ),
        "llm_execution_status": "disabled_not_called",
        "go_evidence_status": closure.get("go_evidence_status", "not_go_evidence"),
    }
```

Attach it before writing artifacts:

```python
report["local_shadow_preview_release_readiness"] = _local_shadow_preview_release_readiness(report)
```

- [ ] **Step 4: Run the focused test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_mainline_usable_e2e_runner.py::test_runner_reports_local_shadow_preview_release_readiness \
  -q -p no:cacheprovider
```

Expected: PASS.

## Task 5: Documentation Update

**Files:**
- Modify: `docs/current-mainline-index.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`

- [ ] **Step 1: Add DEC entry**

Append a decision entry:

```markdown
## DEC-218: Local Shadow Preview release path replaces readiness-wrapper mainline drift

日期：2026-07-05

状态：Accepted

背景：

DEC-217 established that the current project is Local Shadow Preview ready, not Shadow Pilot or Production MVP ready. The next implementation must make that truth visible in the product surface and runner output.

决策：

Add one Local Shadow Preview release readiness path in the Web workbench and mainline runner. It must report preview ready, ASR quality not exited, real microphone blocked, LLM disabled, and formal card/report not-Go status. It must not authorize microphone capture, remote ASR, LLM execution, or paid providers.

验证方式：

Focused API/static tests, browser smoke, and mainline runner test.
```

- [ ] **Step 2: Add RTM row**

Append:

```markdown
| `REQ-LOCAL-SHADOW-PREVIEW-RELEASE-001` | Workbench and mainline runner must expose one truthful Local Shadow Preview release readiness summary: preview ready, ASR not exited, real mic blocked, LLM disabled, formal card/report not-Go; no hidden microphone/remote/secret side effects | `GET /desktop/local-shadow-preview-release-readiness`; `tools/mainline_usable_e2e_runner.py`; `code/web_mvp/e2e/browser_smoke.mjs`; `docs/project-release-readiness-reset-2026-07-05.md`; `docs/decision-log.md#dec-218-local-shadow-preview-release-path-replaces-readiness-wrapper-mainline-drift` | Planned by DEC-217 reset |
```

- [ ] **Step 3: Update mainline index**

Add a short pointer to the reset doc and release path:

```markdown
2026-07-05 DEC-217 reset: Current status is Local Shadow Preview ready only; Shadow Pilot and Production MVP are blocked. Next implementation must expose a single truthful Local Shadow Preview release path and stop counting readiness/preflight/wrapper-only work as mainline progress unless it changes a release-decisive state.
```

- [ ] **Step 4: Verify docs mention the reset**

Run:

```bash
rg -n "DEC-217|DEC-218|Local Shadow Preview|project-release-readiness-reset-2026-07-05" docs/current-mainline-index.md docs/decision-log.md docs/requirements-traceability-matrix.md
```

Expected: matches in all three files.

## Task 6: Final Verification

**Files:**
- No code changes beyond prior tasks.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_local_shadow_preview_release_readiness_reports_truthful_status \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_include_local_shadow_preview_release_summary \
  tests/test_mainline_usable_e2e_runner.py::test_runner_reports_local_shadow_preview_release_readiness \
  -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 2: Run browser smoke**

Run:

```bash
node code/web_mvp/e2e/browser_smoke.mjs
```

Expected: exit 0 and `checked` includes `local shadow preview release readiness`.

- [ ] **Step 3: Run mainline runner**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id local_shadow_preview_release_readiness_20260705 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
```

Expected:

```text
overall_status=mainline_product_chain_exercised_with_expected_blockers
local_shadow_preview_release_readiness.demo_preview_ready=true
local_shadow_preview_release_readiness.shadow_pilot_ready=false
local_shadow_preview_release_readiness.production_mvp_ready=false
```

- [ ] **Step 4: Run sensitive scan**

Run:

```bash
rg -n "sk-[A-Za-z0-9]{20,}|codexai\\.club" .
```

Expected: no matches outside ignored/forbidden runtime roots.

