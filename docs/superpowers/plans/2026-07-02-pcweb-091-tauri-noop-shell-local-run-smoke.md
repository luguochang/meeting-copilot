# PCWEB-091 Tauri No-op Shell Local Run Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a no-command Tauri no-op shell local run smoke readiness boundary that validates the static desktop shell is ready for a future explicitly approved smoke run without executing Cargo, Tauri, package managers, audio capture, workers, secrets, or remote providers.

**Architecture:** Follow the existing desktop policy/report pattern. A JSON policy defines the expected Tauri dev URL, frontend static path, no-op command catalog, PCWEB-090 dependency, generated-artifact blockers, and false safety flags; a Python report tool reads only safe project files, validates scaffold drift, validates PCWEB-090 remains no-command, and emits a blocked or ready-for-explicit-approval report.

**Tech Stack:** Python 3 standard library, pytest, JSON policy, Markdown docs.

**Traceability:** PCWEB-091 creates `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` and `tools/desktop_tauri_noop_shell_run_smoke.py`. The policy/report mode is `readiness_report_only`; a valid scaffold can only produce `ready_for_explicit_tauri_run_approval`, while `safe_to_run_tauri_dev_now=false` and `safe_to_capture_audio_now=false` remain fixed.

---

## File Structure

- Create `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json`: PCWEB-091 policy.
- Create `tools/desktop_tauri_noop_shell_run_smoke.py`: no-command readiness report generator.
- Create `tests/test_desktop_tauri_noop_shell_run_smoke.py`: root contract tests.
- Modify `README.md`: add PCWEB-091 summary.
- Modify `code/web_mvp/README.md`: add PCWEB-091 docs reference if needed by docs gate.
- Modify `code/desktop_tauri/README.md`: add PCWEB-091 section.
- Modify `docs/requirements-traceability-matrix.md`: add PCWEB-091 row.
- Modify `docs/pc-local-web-mvp-acceptance.md`: add PCWEB-091 acceptance note.
- Modify `docs/privacy-and-data-flow.md`: add no-command/no-audio/no-secret boundary note.
- Modify `docs/project-structure.md`: add new policy/tool/test.
- Modify `docs/implementation-roadmap.md`: append PCWEB-091 route note.
- Modify `docs/decision-log.md`: append PCWEB-091 accepted decision.
- Modify `docs/project-current-status-2026-07-02.md`, `docs/project-progress-report-2026-07-02.md`, and `docs/project-stage-status-and-next-work-2026-07-02.md`: update current/next status.

### Task 1: Write PCWEB-091 Failing Tests

**Files:**
- Create: `tests/test_desktop_tauri_noop_shell_run_smoke.py`

- [ ] **Step 1: Add policy identity and safety flag tests**

Create tests that load `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` and assert exact PCWEB-091 identity, `readiness_report_only`, `not_run` statuses, expected dev URL/frontend dist, expected commands, minimal capability, PCWEB-090 dependency, and all safety flags false.

- [ ] **Step 2: Add no-command source tests**

Assert `tools/desktop_tauri_noop_shell_run_smoke.py` does not contain `subprocess`, `os.system`, `Popen`, `check_call`, `check_output`, `cargo`, `tauri dev`, `npm`, `pnpm`, `yarn`, or `npx` execution snippets beyond inert string constants required for reporting.

- [ ] **Step 3: Add report behavior tests**

Add tests for:

- valid scaffold returns `smoke_packet_status=ready_for_explicit_tauri_run_approval` while `tauri_shell_run_status=not_run`
- config `devUrl` drift blocks the packet
- `bundle.active=true` blocks the packet
- capability permission drift blocks the packet
- missing or extra no-op command blocks the packet
- generated artifacts such as `Cargo.lock`, `target`, or `node_modules` block the packet
- PCWEB-090 policy drift blocks the packet
- forbidden input paths are blocked before file reads

- [ ] **Step 4: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_noop_shell_run_smoke.py -q -p no:cacheprovider
```

Expected: FAIL because the PCWEB-091 policy/tool do not exist yet.

### Task 2: Add Policy And Tool

**Files:**
- Create: `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json`
- Create: `tools/desktop_tauri_noop_shell_run_smoke.py`

- [ ] **Step 1: Add policy JSON**

Define PCWEB-091 with expected scaffold values, generated artifact blockers, required validations, false safety flags, and `default_quality_gate_status=included_in_root_pytest`.

- [ ] **Step 2: Add Python report generator**

Implement a no-command report function:

```python
build_tauri_noop_shell_run_smoke_report(
    policy_path=...,
    tauri_config_path=...,
    capability_path=...,
    lib_rs_path=...,
    cargo_check_boundary_policy_path=...,
    desktop_root=...,
)
```

The function validates paths before read, validates PCWEB-091 policy, validates Tauri config/capability/lib.rs/scaffold artifacts, validates PCWEB-090 remains no-command, and returns a JSON-serializable report.

- [ ] **Step 3: Add CLI**

The CLI should print the report JSON and accept optional path arguments for tests. It must not execute external commands.

- [ ] **Step 4: Run focused GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_noop_shell_run_smoke.py -q -p no:cacheprovider
```

Expected: PASS.

### Task 3: Update Documentation And Traceability

**Files:**
- Modify all docs listed in File Structure.

- [ ] **Step 1: Record PCWEB-091 decision**

Docs must state PCWEB-091 is readiness/report only, validates Tauri no-op shell static readiness, can only produce a manual future smoke packet, and keeps all Cargo/Tauri/audio/worker/secret/remote flags false.

- [ ] **Step 2: Update route status**

Current status docs should say PCWEB-090 is complete, PCWEB-091 is implemented, and PCWEB-092 is the next recommended no-op IPC integration step.

- [ ] **Step 3: Run docs gate**

```bash
cd code/web_mvp/backend
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q -p no:cacheprovider
```

Expected: PASS.

### Task 4: Verify Regression

**Files:**
- No new files.

- [ ] **Step 1: Run adjacent desktop tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_noop_shell_run_smoke.py tests/test_desktop_first_cargo_check_execution_boundary.py tests/test_desktop_tauri_scaffold.py tests/test_quality_gate.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 2: Run full local gates**

```bash
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: PASS without running Cargo, Tauri, package managers, remote providers, or `configs/local`.

### Task 5: Review And Close PCWEB-091

**Files:**
- Modify: `docs/pcweb-091-tauri-noop-shell-local-run-smoke-plan.md`
- Modify: `docs/project-current-status-2026-07-02.md`
- Modify: `docs/project-progress-report-2026-07-02.md`
- Modify: `docs/project-stage-status-and-next-work-2026-07-02.md`

- [ ] **Step 1: Request focused review**

Ask for review of command execution safety, generated artifact blocking, policy drift validation, forbidden path guards, and docs consistency.

- [ ] **Step 2: Fix Critical or Important findings**

Any valid Critical or Important finding must get a regression test before implementation changes.

- [ ] **Step 3: Record final verification**

Update docs with RED/GREEN/gate/review results and leave next recommended route as PCWEB-092 no-op IPC integration unless a separate explicit approval authorizes real Cargo/Tauri execution.
