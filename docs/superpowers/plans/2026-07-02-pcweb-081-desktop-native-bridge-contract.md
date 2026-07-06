# PCWEB-081 Desktop Native Bridge Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a read-only native bridge command contract boundary and Web workbench panel that define how the future desktop shell will call platform adapters without creating a real bridge or invoking native capabilities.

**Architecture:** Keep the backend as a deterministic FastAPI template boundary. Reuse the current static Web workbench and render a third desktop boundary panel at startup and reset. Do not add desktop dependencies, native bridge handlers, IPC, process spawning, permission probes, model loading, provider config reads, local writes, or remote calls.

**Tech Stack:** FastAPI, static HTML/CSS/JS, pytest, headless Chrome CDP smoke.

**Implementation status:** completed on 2026-07-02. Fresh verification after implementation included focused PCWEB-081 pytest, `node e2e/browser_smoke.mjs`, backend regression pytest, and `python3 tools/run_quality_gate.py --profile pc-web`.

---

### Task 1: RED API, Static, Browser, And Docs Expectations

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [x] **Step 1: Add failing API expectations**

Add `test_desktop_native_bridge_contract_reports_contract_preflight` to `test_app.py`:

```python
def test_desktop_native_bridge_contract_reports_contract_preflight():
    client = TestClient(create_app())

    response = client.get("/desktop/native-bridge-contract")

    assert response.status_code == 200
    payload = response.json()
    assert payload["desktop_bridge_contract_mode"] == "contract_preflight_only"
    assert payload["desktop_bridge_contract_status"] == "specified_not_bound"
    assert payload["native_bridge_status"] == "not_created"
    assert payload["desktop_shell_runtime_status"] == "not_created"
    assert payload["bridge_transport_status"] == "not_created"
    assert payload["bridge_command_contract_status"] == "specified_not_bound"
    assert payload["bridge_process_lifecycle_status"] == "specified_not_started"
    assert payload["bridge_resource_policy_status"] == "specified_not_enforced"
    assert payload["bridge_error_contract_status"] == "specified"
    assert payload["bridge_audit_contract_status"] == "response_only"
    assert payload["bridge_platform_adapter_status"] == "not_created"
    assert payload["desktop_bridge_command_count"] == 8
    assert len(payload["desktop_bridge_commands"]) == 8
    assert payload["desktop_bridge_phase_count"] == 8
    assert len(payload["desktop_bridge_phases"]) == 8
    command_ids = {command["command_id"] for command in payload["desktop_bridge_commands"]}
    assert "runtime.get_status" in command_ids
    assert "audio.capture_start" in command_ids
    assert "asr_worker.start" in command_ids
    assert all(command["safe_to_invoke"] is False for command in payload["desktop_bridge_commands"])
    assert all(command["safe_to_execute_now"] is False for command in payload["desktop_bridge_commands"])
    assert all("effect_class" in command for command in payload["desktop_bridge_commands"])
    assert all("read_set" in command for command in payload["desktop_bridge_commands"])
    assert all("write_set" in command for command in payload["desktop_bridge_commands"])
    assert all("security_classification" in command for command in payload["desktop_bridge_commands"])
    assert payload["desktop_bridge_error_contract"]["secret_redaction_policy"] == "no_secret_values"
    assert payload["desktop_bridge_resource_policy"]["worker_spawn_status"] == "not_started"
    assert "native_bridge_not_created" in payload["desktop_bridge_blockers"]
    assert "create_tauri_shell_scaffold_against_bridge_contract" in payload["desktop_bridge_next_decisions"]
    assert payload["desktop_bridge_safe_to_create_native_bridge"] is False
    assert payload["desktop_bridge_safe_to_bind_ipc"] is False
    assert payload["desktop_bridge_safe_to_invoke_commands"] is False
    assert payload["desktop_bridge_safe_to_request_permissions"] is False
    assert payload["desktop_bridge_safe_to_enumerate_devices"] is False
    assert payload["desktop_bridge_safe_to_capture_audio"] is False
    assert payload["desktop_bridge_safe_to_spawn_worker"] is False
    assert payload["desktop_bridge_safe_to_write_local_files"] is False
    assert payload["desktop_bridge_safe_to_call_remote_asr"] is False
    assert payload["desktop_bridge_safe_to_call_llm"] is False
```

- [x] **Step 2: Add failing no-side-effect expectations**

Add `test_desktop_native_bridge_contract_does_not_probe_audio_or_read_secrets` using `_install_no_llm_config_or_secret_read_guards(...)`, then assert no leaked markers and false safe flags.

- [x] **Step 3: Add failing no-local-storage expectation**

Add `test_desktop_native_bridge_contract_with_data_dir_does_not_create_local_storage`, mirroring the desktop readiness/runtime data-dir no-write tests.

- [x] **Step 4: Add failing static expectations**

Extend `test_workbench_index_serves_state_first_ui_shell`:

```python
assert 'id="desktop-native-bridge-contract-panel"' in response.text
```

Extend `test_workbench_static_assets_are_served`:

```python
assert "loadDesktopNativeBridgeContract" in script.text
assert "renderDesktopNativeBridgeContract" in script.text
assert "/desktop/native-bridge-contract" in script.text
assert "desktop_bridge_commands" in script.text
assert "desktop_bridge_safe_to_create_native_bridge" in script.text
assert "desktop-native-bridge-contract-panel" in styles.text
render_empty_body = script.text.split("function renderEmpty()", 1)[1].split(
    "async function setEventMode", 1
)[0]
assert "loadDesktopNativeBridgeContract();" in render_empty_body
```

- [x] **Step 5: Add failing browser smoke expectations**

Before loading a fixture, add:

```javascript
await waitForHttp(`http://127.0.0.1:${port}/desktop/native-bridge-contract`);
await waitForCdpExpression(
  page,
  "document.getElementById('desktop-native-bridge-contract-panel')?.textContent?.includes('specified_not_bound')",
);
```

Capture panel text and `.desktop-bridge-command` count during `apiReview`, then assert it includes `runtime.get_status`, `audio.capture_start`, `asr_worker.start`, `desktop_bridge_safe_to_create_native_bridge=false`, and 8 command rows.

- [x] **Step 6: Add failing docs gate expectations**

Read `docs/pcweb-081-desktop-native-bridge-contract-plan.md` and `docs/superpowers/plans/2026-07-02-pcweb-081-desktop-native-bridge-contract.md` in the docs gate. Require `PCWEB-081` and `/desktop/native-bridge-contract` in README, traceability, acceptance, privacy, project structure, roadmap, decision log, and both PCWEB-081 plan files.

- [x] **Step 7: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "desktop_native_bridge_contract or workbench_index_serves_state_first_ui_shell or workbench_static_assets_are_served or scripted_browser_e2e_gate_exists_and_checks_critical_ui_paths or web_mvp_readme_documents_scripted_browser_e2e_gate" -q
```

Expected: failures for missing endpoint, DOM, script functions, browser smoke text, and docs references.

### Task 2: Implement Backend Native Bridge Contract

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add route**

Add near `/desktop/runtime-boundary`:

```python
    @app.get("/desktop/native-bridge-contract")
    def desktop_native_bridge_contract() -> dict[str, Any]:
        return _desktop_native_bridge_contract()
```

- [x] **Step 2: Add deterministic helper**

Add `_desktop_native_bridge_contract()` near `_desktop_runtime_boundary()`. It must return eight `desktop_bridge_commands`, eight `desktop_bridge_phases`, one `desktop_bridge_error_contract`, one `desktop_bridge_resource_policy`, blockers, next decisions, and all scoped false safe flags from the design document.

- [x] **Step 3: Verify API GREEN**

Run the focused tests from Task 1 Step 7.

### Task 3: Implement Frontend Native Bridge Panel

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`

- [x] **Step 1: Add panel shell**

Add `desktop-native-bridge-contract-panel` after `desktop-runtime-boundary-panel` and before the state board.

- [x] **Step 2: Load bridge contract at startup and reset**

Call `loadDesktopNativeBridgeContract()` from `DOMContentLoaded` after `loadDesktopRuntimeBoundary()`. In `renderEmpty()`, call `renderDesktopNativeBridgeContractEmpty()` and `loadDesktopNativeBridgeContract()` next to the other desktop boundary resets.

- [x] **Step 3: Add loader and renderer**

Use `requestJson("/desktop/native-bridge-contract")`. Render metrics, false flags, blockers, next decisions, eight `.desktop-bridge-phase` rows, eight `.desktop-bridge-command` rows, error contract, and resource policy.

- [x] **Step 4: Verify static GREEN**

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

- [x] **Step 1: Browser smoke assertions**

Assert the native bridge endpoint before page load. Assert the bridge contract panel after passive startup. During `apiReview`, assert blocked contract status, command IDs, false safe flags, and command row count.

- [x] **Step 2: README update**

Document `GET /desktop/native-bridge-contract`, `desktop-native-bridge-contract-panel`, `PCWEB-081`, the command catalog, error/resource contract, passive startup rule, no native bridge/no IPC/no worker/no permission/no capture/no write boundary, and the exact next desktop increment `create_tauri_shell_scaffold_against_bridge_contract`.

- [x] **Step 3: Requirements and acceptance update**

Add a `PCWEB-081` traceability row and an `AC-PCWEB-074` acceptance row requiring the endpoint, UI panel, command catalog, false safe flags, no storage write, and no bridge/dependency creation.

- [x] **Step 4: Privacy, structure, roadmap, and decision log update**

Record that PCWEB-081 is a response-only native bridge command contract boundary. Add the decision log entry with the decision to defer real Tauri scaffold until this contract is green, and state that the next desktop increment must be `create_tauri_shell_scaffold_against_bridge_contract`.

- [x] **Step 5: Verify docs/browser GREEN**

Run browser smoke and the focused docs tests.

### Task 5: Regression, Review, And Hygiene

**Files:**
- Review: all modified files.

- [x] **Step 1: Run backend regression**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

- [x] **Step 2: Run PC Web quality gate**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
```

- [x] **Step 3: Request read-only review**

Ask reviewers to check PCWEB-081 for hidden desktop dependency creation, native bridge/IPC binding, process spawn, permission probes, audio/config/secret access, misleading bridge semantics, startup write regressions, frontend regressions, and docs consistency.

- [x] **Step 4: Hygiene**

Remove test caches under `code/core` and `code/web_mvp`; check ports `8767` and `9223`; run the local sensitive-marker scan while excluding `configs/local/**`, virtualenvs, caches, and runtime outputs.
