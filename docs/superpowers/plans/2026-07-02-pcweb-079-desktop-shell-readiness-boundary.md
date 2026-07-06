# PCWEB-079 Desktop Shell Readiness Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only desktop-shell readiness boundary and Web workbench panel that show why the product is not yet allowed to start desktop capture, workers, installers, or paid providers.

**Architecture:** Keep the backend as a thin local FastAPI adapter with a deterministic template-only readiness envelope. The frontend loads the endpoint once at startup and renders a compact panel. No native permission checks, audio capture, config reads, worker spawns, or remote calls are introduced.

**Tech Stack:** FastAPI, static HTML/CSS/JS, pytest, headless Chrome CDP smoke.

---

### Task 1: RED API, Static, Browser, And Docs Expectations

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [ ] **Step 1: Add failing API expectations**

Add `test_desktop_shell_readiness_reports_disabled_preflight_boundary` to `test_app.py`:

```python
def test_desktop_shell_readiness_reports_disabled_preflight_boundary():
    client = TestClient(create_app())

    response = client.get("/desktop/shell-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["desktop_readiness_mode"] == "preflight_only"
    assert payload["desktop_readiness_status"] == "blocked_before_desktop_shell"
    assert payload["desktop_shell_status"] == "not_started"
    assert payload["target_platform_status"] == "macos_first_windows_deferred"
    assert payload["audio_capture_status"] == "not_connected"
    assert payload["microphone_permission_status"] == "not_requested"
    assert payload["system_audio_permission_status"] == "not_requested"
    assert payload["asr_worker_status"] == "not_started"
    assert payload["llm_provider_status"] == "not_connected"
    assert payload["desktop_readiness_phase_count"] == 8
    assert len(payload["desktop_readiness_phases"]) == 8
    assert payload["desktop_safe_to_capture_audio"] is False
    assert payload["desktop_safe_to_request_permissions"] is False
    assert payload["desktop_safe_to_start_asr_worker"] is False
    assert payload["desktop_safe_to_call_remote_asr"] is False
    assert payload["desktop_safe_to_call_llm"] is False
    assert payload["desktop_safe_to_write_audio_chunks"] is False
```

- [ ] **Step 2: Add failing no-side-effect expectations**

Add `test_desktop_shell_readiness_does_not_probe_audio_or_read_secrets` using the existing no-config/no-secret guard helper. Assert the response text does not contain the sentinel and still returns false safety flags.

- [ ] **Step 3: Add failing static asset expectations**

Extend `test_workbench_index_serves_state_first_ui_shell` and `test_workbench_static_assets_are_served`:

```python
assert 'id="desktop-readiness-panel"' in response.text
assert "loadDesktopShellReadiness" in script.text
assert "renderDesktopShellReadiness" in script.text
assert "/desktop/shell-readiness" in script.text
assert "desktop_readiness_phases" in script.text
```

- [ ] **Step 4: Add failing browser smoke expectations**

After the initial workbench load, assert:

```javascript
await waitForCdpExpression(
  page,
  "document.getElementById('desktop-readiness-panel')?.textContent?.includes('blocked_before_desktop_shell')",
);
```

In the initial `apiReview` object, capture desktop readiness text and phase count. Assert it includes `8 phases`, `not_connected`, `not_requested`, `not_started`, and `desktop_safe_to_capture_audio=false`.

- [ ] **Step 5: Add failing docs gate expectations**

Extend the existing README/docs gate to require PCWEB-079 and `/desktop/shell-readiness` in README, plan, traceability, acceptance, privacy, project structure, roadmap, and decision log.

- [ ] **Step 6: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "desktop_shell_readiness or workbench_index_serves_state_first_ui_shell or workbench_static_assets_are_served or scripted_browser_e2e_gate_exists_and_checks_critical_ui_paths or web_mvp_readme_documents_scripted_browser_e2e_gate" -q
```

Expected: failures for missing endpoint, DOM, script functions, browser smoke text, and docs references.

### Task 2: Implement Backend Readiness Boundary

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [ ] **Step 1: Add route**

Add near `/health`:

```python
    @app.get("/desktop/shell-readiness")
    def desktop_shell_readiness() -> dict[str, Any]:
        return _desktop_shell_readiness()
```

- [ ] **Step 2: Add deterministic helper**

Add `_desktop_shell_readiness()` near other response helpers:

```python
def _desktop_shell_readiness() -> dict[str, Any]:
    phases = [
        {"phase_id": "desktop_shell_runtime", "phase_status": "blocked", "phase_mode": "not_started", "item_count": 1, "safe_to_proceed": False, "source_status_field": "desktop_shell_status", "source_status_value": "not_started"},
        {"phase_id": "target_platform", "phase_status": "planned", "phase_mode": "macos_first", "item_count": 2, "safe_to_proceed": False, "source_status_field": "target_platform_status", "source_status_value": "macos_first_windows_deferred"},
        {"phase_id": "microphone_permission", "phase_status": "blocked", "phase_mode": "not_requested", "item_count": 1, "safe_to_proceed": False, "source_status_field": "microphone_permission_status", "source_status_value": "not_requested"},
        {"phase_id": "system_audio_permission", "phase_status": "blocked", "phase_mode": "not_requested", "item_count": 1, "safe_to_proceed": False, "source_status_field": "system_audio_permission_status", "source_status_value": "not_requested"},
        {"phase_id": "audio_source_separation", "phase_status": "blocked", "phase_mode": "not_connected", "item_count": 2, "safe_to_proceed": False, "source_status_field": "audio_capture_status", "source_status_value": "not_connected"},
        {"phase_id": "asr_worker_lifecycle", "phase_status": "blocked", "phase_mode": "not_started", "item_count": 1, "safe_to_proceed": False, "source_status_field": "asr_worker_status", "source_status_value": "not_started"},
        {"phase_id": "local_data_lifecycle", "phase_status": "needs_decision", "phase_mode": "policy_only", "item_count": 1, "safe_to_proceed": False, "source_status_field": "local_data_dir_status", "source_status_value": "not_created"},
        {"phase_id": "packaging_distribution", "phase_status": "blocked", "phase_mode": "not_started", "item_count": 3, "safe_to_proceed": False, "source_status_field": "packaging_status", "source_status_value": "not_started"},
    ]
    return {
        "desktop_readiness_mode": "preflight_only",
        "desktop_readiness_status": "blocked_before_desktop_shell",
        "desktop_shell_status": "not_started",
        "target_platform_status": "macos_first_windows_deferred",
        "audio_capture_status": "not_connected",
        "microphone_permission_status": "not_requested",
        "system_audio_permission_status": "not_requested",
        "asr_worker_status": "not_started",
        "llm_provider_status": "not_connected",
        "local_data_dir_status": "not_created",
        "packaging_status": "not_started",
        "desktop_readiness_phase_count": len(phases),
        "desktop_readiness_phases": phases,
        "desktop_readiness_blockers": [
            "desktop_shell_not_selected",
            "audio_capture_not_connected",
            "permissions_not_requested",
            "asr_worker_not_started",
            "packaging_not_started",
        ],
        "desktop_readiness_next_decisions": [
            "choose_desktop_shell_runtime",
            "define_macos_audio_permission_ux",
            "define_mic_and_system_audio_source_separation",
            "define_asr_worker_lifecycle_and_resource_limits",
            "define_local_data_directory_and_retention_policy",
            "define_packaging_signing_notarization_path",
        ],
        "desktop_safe_to_capture_audio": False,
        "desktop_safe_to_request_permissions": False,
        "desktop_safe_to_start_asr_worker": False,
        "desktop_safe_to_call_remote_asr": False,
        "desktop_safe_to_call_llm": False,
        "desktop_safe_to_write_audio_chunks": False,
    }
```

- [ ] **Step 3: Verify API GREEN**

Run the focused endpoint tests.

### Task 3: Implement Frontend Panel

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`

- [ ] **Step 1: Add panel shell**

Add a `desktop-readiness-panel` section after the summary band and before the state board.

- [ ] **Step 2: Load readiness at startup**

Call `loadDesktopShellReadiness()` from `DOMContentLoaded` before `loadFixtures()`.

- [ ] **Step 3: Add loader and renderer**

Use `requestJson("/desktop/shell-readiness")` and render status metrics, false flags, blockers, next decisions, and eight phase rows. Use distinct classes such as `.desktop-readiness-panel`, `.desktop-readiness-summary`, and `.desktop-phase`.

- [ ] **Step 4: Verify static GREEN**

Run the focused static asset tests.

### Task 4: Update Browser Smoke And Docs

**Files:**
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`
- Modify: `code/web_mvp/README.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`

- [ ] **Step 1: Browser smoke assertions**

Assert desktop readiness panel text and eight phases during the initial workbench review.

- [ ] **Step 2: Docs updates**

Document PCWEB-079 as a no-capture/no-permission/no-worker/no-paid-call desktop-entry readiness boundary.

- [ ] **Step 3: Verify docs/browser GREEN**

Run browser smoke and focused docs tests.

### Task 5: Regression, Review, And Hygiene

**Files:**
- Review: all modified files.

- [ ] **Step 1: Run backend regression**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

- [ ] **Step 2: Run PC Web quality gate**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
```

- [ ] **Step 3: Request read-only review**

Ask a reviewer to check PCWEB-079 for hidden audio/config/secret access, misleading readiness semantics, frontend regressions, and docs consistency.

- [ ] **Step 4: Hygiene**

Remove test caches under `code/core` and `code/web_mvp`; check ports `8767` and `9223`; run the local sensitive-marker scan without committing marker values to documentation.
