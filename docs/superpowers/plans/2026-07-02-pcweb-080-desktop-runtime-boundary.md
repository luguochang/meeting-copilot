# PCWEB-080 Desktop Runtime Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only runtime decision boundary and Web workbench panel that lock the Mac-first Tauri-first/Electron-fallback process model before creating a real desktop shell.

**Architecture:** Keep the backend as a deterministic FastAPI template boundary. Reuse the current static Web workbench and render a second desktop boundary panel at startup and reset. Do not add desktop dependencies, native probes, workers, permissions, model loading, provider config reads, local writes, or remote calls.

**Tech Stack:** FastAPI, static HTML/CSS/JS, pytest, headless Chrome CDP smoke.

**Implementation status: completed.** Checklist entries below are retained as the original executable TDD plan for traceability; current implementation is tracked in PCWEB-080 docs, tests, and decision log.

---

### Task 1: RED API, Static, Browser, And Docs Expectations

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [ ] **Step 1: Add failing API expectations**

Add `test_desktop_runtime_boundary_reports_decision_preflight` to `test_app.py`:

```python
def test_desktop_runtime_boundary_reports_decision_preflight():
    client = TestClient(create_app())

    response = client.get("/desktop/runtime-boundary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["desktop_runtime_mode"] == "decision_preflight_only"
    assert payload["desktop_runtime_boundary_status"] == "blocked_before_runtime_creation"
    assert payload["recommended_desktop_runtime"] == "tauri_first_electron_fallback"
    assert payload["desktop_runtime_decision_status"] == "recommended_not_created"
    assert payload["desktop_process_model_status"] == "planned_not_started"
    assert payload["ui_reuse_status"] == "web_mvp_static_assets_reusable"
    assert payload["core_isolation_status"] == "platform_independent"
    assert payload["native_bridge_status"] == "not_created"
    assert payload["asr_worker_process_model"] == "sidecar_worker_planned"
    assert payload["packaging_pipeline_status"] == "not_started"
    assert payload["macos_target_status"] == "apple_silicon_first"
    assert payload["windows_target_status"] == "deferred_adapter"
    assert payload["desktop_runtime_phase_count"] == 8
    assert len(payload["desktop_runtime_phases"]) == 8
    assert "desktop_runtime_not_created" in payload["desktop_runtime_blockers"]
    assert "create_tauri_shell_spike" in payload["desktop_runtime_next_decisions"]
    assert payload["desktop_runtime_safe_to_create_shell"] is False
    assert payload["desktop_runtime_safe_to_start_native_bridge"] is False
    assert payload["desktop_runtime_safe_to_spawn_worker"] is False
    assert payload["desktop_runtime_safe_to_package_installer"] is False
    assert payload["desktop_runtime_safe_to_request_permissions"] is False
    assert payload["desktop_runtime_safe_to_capture_audio"] is False
    assert payload["desktop_runtime_safe_to_call_remote_asr"] is False
    assert payload["desktop_runtime_safe_to_call_llm"] is False
```

- [ ] **Step 2: Add failing no-side-effect expectations**

Add `test_desktop_runtime_boundary_does_not_probe_audio_or_read_secrets`:

```python
def test_desktop_runtime_boundary_does_not_probe_audio_or_read_secrets(monkeypatch, tmp_path):
    leaked_markers = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "desktop_runtime_boundary",
    )
    client = TestClient(create_app())

    response = client.get("/desktop/runtime-boundary")

    assert response.status_code == 200
    response_text = response.text
    for marker in leaked_markers:
        assert marker not in response_text
    payload = response.json()
    assert payload["native_bridge_status"] == "not_created"
    assert payload["desktop_runtime_safe_to_create_shell"] is False
    assert payload["desktop_runtime_safe_to_capture_audio"] is False
```

- [ ] **Step 3: Add failing no-local-storage expectation**

Add `test_desktop_runtime_boundary_with_data_dir_does_not_create_local_storage`:

```python
def test_desktop_runtime_boundary_with_data_dir_does_not_create_local_storage(tmp_path):
    app = create_app(data_dir=tmp_path)

    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()

    client = TestClient(app)
    response = client.get("/desktop/runtime-boundary")

    assert response.status_code == 200
    assert response.json()["desktop_runtime_boundary_status"] == "blocked_before_runtime_creation"
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()
```

- [ ] **Step 4: Add failing static expectations**

Extend `test_workbench_index_serves_state_first_ui_shell`:

```python
assert 'id="desktop-runtime-boundary-panel"' in response.text
```

Extend `test_workbench_static_assets_are_served`:

```python
assert "loadDesktopRuntimeBoundary" in script.text
assert "renderDesktopRuntimeBoundary" in script.text
assert "/desktop/runtime-boundary" in script.text
assert "desktop_runtime_phases" in script.text
assert "desktop_runtime_safe_to_create_shell" in script.text
assert "desktop-runtime-boundary-panel" in styles.text
render_empty_body = script.text.split("function renderEmpty()", 1)[1].split(
    "async function setEventMode", 1
)[0]
assert "loadDesktopRuntimeBoundary();" in render_empty_body
```

- [ ] **Step 5: Add failing browser smoke expectations**

Before loading a fixture, add:

```javascript
await waitForHttp(`http://127.0.0.1:${port}/desktop/runtime-boundary`);
await waitForCdpExpression(
  page,
  "document.getElementById('desktop-runtime-boundary-panel')?.textContent?.includes('blocked_before_runtime_creation')",
);
```

In the `apiReview` object, collect:

```javascript
desktopRuntimeText: document.getElementById("desktop-runtime-boundary-panel")?.textContent || "",
desktopRuntimePhaseCount: document.querySelectorAll(".desktop-runtime-phase").length,
```

Assert:

```javascript
assert(apiReview.desktopRuntimeText.includes("blocked_before_runtime_creation"), "expected desktop runtime blocked status");
assert(apiReview.desktopRuntimeText.includes("tauri_first_electron_fallback"), "expected desktop runtime recommendation");
assert(apiReview.desktopRuntimeText.includes("8 phases"), "expected desktop runtime phase count");
assert(apiReview.desktopRuntimeText.includes("sidecar_worker_planned"), "expected desktop runtime worker model");
assert(apiReview.desktopRuntimeText.includes("desktop_runtime_safe_to_create_shell=false"), "expected runtime safe-to-create-shell flag false");
assert(apiReview.desktopRuntimePhaseCount === 8, `expected 8 desktop runtime phases, got ${apiReview.desktopRuntimePhaseCount}`);
```

- [ ] **Step 6: Add failing docs gate expectations**

Read `docs/pcweb-080-desktop-runtime-boundary-plan.md` and `docs/superpowers/plans/2026-07-02-pcweb-080-desktop-runtime-boundary.md` in the docs gate. Require `PCWEB-080` and `/desktop/runtime-boundary` in README, traceability, acceptance, privacy, project structure, roadmap, decision log, and both PCWEB-080 plan files.

- [ ] **Step 7: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "desktop_runtime_boundary or workbench_index_serves_state_first_ui_shell or workbench_static_assets_are_served or scripted_browser_e2e_gate_exists_and_checks_critical_ui_paths or web_mvp_readme_documents_scripted_browser_e2e_gate" -q
```

Expected: failures for missing endpoint, DOM, script functions, browser smoke text, and docs references.

### Task 2: Implement Backend Runtime Boundary

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [ ] **Step 1: Add route**

Add near `/desktop/shell-readiness`:

```python
    @app.get("/desktop/runtime-boundary")
    def desktop_runtime_boundary() -> dict[str, Any]:
        return _desktop_runtime_boundary()
```

- [ ] **Step 2: Add deterministic helper**

Add `_desktop_runtime_boundary()` near `_desktop_shell_readiness()`:

```python
def _desktop_runtime_boundary() -> dict[str, Any]:
    phases = [
        {"phase_id": "runtime_recommendation", "phase_status": "recommended", "phase_mode": "tauri_first", "item_count": 2, "safe_to_proceed": False, "source_status_field": "recommended_desktop_runtime", "source_status_value": "tauri_first_electron_fallback"},
        {"phase_id": "process_model", "phase_status": "planned", "phase_mode": "ui_bridge_worker_split", "item_count": 4, "safe_to_proceed": False, "source_status_field": "desktop_process_model_status", "source_status_value": "planned_not_started"},
        {"phase_id": "ui_reuse", "phase_status": "planned", "phase_mode": "static_assets", "item_count": 1, "safe_to_proceed": False, "source_status_field": "ui_reuse_status", "source_status_value": "web_mvp_static_assets_reusable"},
        {"phase_id": "core_isolation", "phase_status": "satisfied", "phase_mode": "platform_independent", "item_count": 1, "safe_to_proceed": False, "source_status_field": "core_isolation_status", "source_status_value": "platform_independent"},
        {"phase_id": "native_bridge", "phase_status": "blocked", "phase_mode": "not_created", "item_count": 1, "safe_to_proceed": False, "source_status_field": "native_bridge_status", "source_status_value": "not_created"},
        {"phase_id": "asr_sidecar_worker", "phase_status": "planned", "phase_mode": "sidecar_worker", "item_count": 1, "safe_to_proceed": False, "source_status_field": "asr_worker_process_model", "source_status_value": "sidecar_worker_planned"},
        {"phase_id": "platform_targets", "phase_status": "planned", "phase_mode": "macos_first_windows_deferred", "item_count": 2, "safe_to_proceed": False, "source_status_field": "macos_target_status", "source_status_value": "apple_silicon_first"},
        {"phase_id": "packaging_pipeline", "phase_status": "blocked", "phase_mode": "not_started", "item_count": 3, "safe_to_proceed": False, "source_status_field": "packaging_pipeline_status", "source_status_value": "not_started"},
    ]
    return {
        "desktop_runtime_mode": "decision_preflight_only",
        "desktop_runtime_boundary_status": "blocked_before_runtime_creation",
        "recommended_desktop_runtime": "tauri_first_electron_fallback",
        "desktop_runtime_decision_status": "recommended_not_created",
        "desktop_process_model_status": "planned_not_started",
        "ui_reuse_status": "web_mvp_static_assets_reusable",
        "core_isolation_status": "platform_independent",
        "native_bridge_status": "not_created",
        "asr_worker_process_model": "sidecar_worker_planned",
        "packaging_pipeline_status": "not_started",
        "macos_target_status": "apple_silicon_first",
        "windows_target_status": "deferred_adapter",
        "desktop_runtime_phase_count": len(phases),
        "desktop_runtime_phases": phases,
        "desktop_runtime_blockers": [
            "desktop_runtime_not_created",
            "native_bridge_not_created",
            "asr_sidecar_not_spawned",
            "packaging_pipeline_not_started",
            "permissions_not_designed",
        ],
        "desktop_runtime_next_decisions": [
            "create_tauri_shell_spike",
            "define_native_bridge_command_contract",
            "define_asr_sidecar_worker_packaging",
            "define_macos_permission_preflight_copy",
            "define_desktop_update_and_distribution_policy",
            "define_windows_adapter_followup",
        ],
        "desktop_runtime_safe_to_create_shell": False,
        "desktop_runtime_safe_to_start_native_bridge": False,
        "desktop_runtime_safe_to_spawn_worker": False,
        "desktop_runtime_safe_to_package_installer": False,
        "desktop_runtime_safe_to_request_permissions": False,
        "desktop_runtime_safe_to_capture_audio": False,
        "desktop_runtime_safe_to_call_remote_asr": False,
        "desktop_runtime_safe_to_call_llm": False,
    }
```

- [ ] **Step 3: Verify API GREEN**

Run the focused endpoint tests from Task 1 Step 7.

### Task 3: Implement Frontend Runtime Panel

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`

- [ ] **Step 1: Add panel shell**

Add a `desktop-runtime-boundary-panel` section after `desktop-readiness-panel` and before the state board. Use the same section heading pattern as the readiness panel.

- [ ] **Step 2: Load runtime boundary at startup and reset**

Call `loadDesktopRuntimeBoundary()` from `DOMContentLoaded` after `loadDesktopShellReadiness()`. In `renderEmpty()`, call `renderDesktopRuntimeBoundaryEmpty()` and `loadDesktopRuntimeBoundary()` next to the existing desktop readiness reset.

- [ ] **Step 3: Add loader and renderer**

Use `requestJson("/desktop/runtime-boundary")`. Render metrics, false flags, blockers, next decisions, and eight `.desktop-runtime-phase` rows. Use CSS classes `.desktop-runtime-boundary-panel`, `.desktop-runtime-summary`, and `.desktop-runtime-phase`.

- [ ] **Step 4: Verify static GREEN**

Run the focused tests from Task 1 Step 7.

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

Assert the runtime endpoint before page load. Assert the runtime boundary panel after passive startup. During the existing `apiReview` capture, assert blocked runtime status, Tauri-first/Electron-fallback recommendation, sidecar worker model, eight phase rows, and `desktop_runtime_safe_to_create_shell=false`.

- [ ] **Step 2: README update**

Document `GET /desktop/runtime-boundary`, `desktop-runtime-boundary-panel`, `PCWEB-080`, the Tauri-first/Electron-fallback recommendation, sidecar ASR worker model, passive startup rule, and no new desktop dependency/no-capture/no-paid-call boundary.

- [ ] **Step 3: Requirements and acceptance update**

Add a `PCWEB-080` traceability row and an `AC-PCWEB-073` acceptance row requiring the endpoint, UI panel, false runtime safe flags, no storage write, and no desktop dependency creation.

- [ ] **Step 4: Privacy, structure, roadmap, and decision log update**

Record that PCWEB-080 is a response-only runtime decision boundary. Add the decision log entry with the Tauri-first/Electron-fallback process model and the explicit decision not to create the desktop shell in this increment.

- [ ] **Step 5: Verify docs/browser GREEN**

Run browser smoke and the focused docs tests.

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

Ask reviewers to check PCWEB-080 for hidden desktop dependency creation, native permission probes, audio/config/secret access, misleading runtime semantics, startup write regressions, frontend regressions, and docs consistency.

- [ ] **Step 4: Hygiene**

Remove test caches under `code/core` and `code/web_mvp`; check ports `8767` and `9223`; run the local sensitive-marker scan while excluding `configs/local/**`, virtualenvs, caches, and runtime outputs.
