# PCWEB-090 Desktop First Cargo Check Execution Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a no-command first `cargo check` execution boundary that turns PCWEB-084 artifact policy plus PCWEB-089 bounded toolchain status into a manual execution packet without running Cargo.

**Architecture:** Follow the existing desktop policy/report pattern. A JSON policy defines fixed command, env, allowed artifacts, preconditions, and false safety flags; a Python report tool validates PCWEB-090, validates PCWEB-084, delegates bounded result validation to PCWEB-089 semantics, and returns either a blocked report or a ready-for-explicit-approval packet. No command execution APIs are imported or called.

**Tech Stack:** Python 3 standard library, pytest, JSON policy, Markdown docs.

**Implementation status:** completed on 2026-07-02. RED was confirmed with `9 failed, 1 warning` because the PCWEB-090 policy/tool did not exist. Post-review focused GREEN is `11 passed, 1 warning`; post-review adjacent desktop/quality tests are `37 passed, 1 warning`; docs gate is `1 passed, 2 warnings`; `pc-web` and `all-local --no-browser` quality gates both passed with `root 83 passed`.

---

## File Structure

- Create `code/desktop_tauri/first-cargo-check-execution.policy.json`: PCWEB-090 policy.
- Create `tools/desktop_first_cargo_check_execution_boundary.py`: no-command execution-boundary report generator.
- Create `tests/test_desktop_first_cargo_check_execution_boundary.py`: root contract tests.
- Modify `code/web_mvp/backend/tests/test_app.py`: add PCWEB-090 docs to the browser docs gate.
- Modify cross-docs:
  - `README.md`
  - `code/web_mvp/README.md`
  - `code/desktop_tauri/README.md`
  - `docs/requirements-traceability-matrix.md`
  - `docs/pc-local-web-mvp-acceptance.md`
  - `docs/privacy-and-data-flow.md`
  - `docs/project-structure.md`
  - `docs/implementation-roadmap.md`
  - `docs/decision-log.md`
  - `docs/project-current-status-2026-07-02.md`
  - `docs/project-progress-report-2026-07-02.md`

### Task 1: Write PCWEB-090 Failing Tests

**Files:**
- Create: `tests/test_desktop_first_cargo_check_execution_boundary.py`

- [ ] **Step 1: Add policy identity and no-command tests**

Add tests that load `first-cargo-check-execution.policy.json`, assert `PCWEB-090`, `explicit_manual_execution_packet_only`, exact `cargo check` command/env, and all run/fetch/artifact safety flags false.

- [ ] **Step 2: Add report behavior tests**

Add tests for no-result blocked report, valid PCWEB-089 result ready-for-explicit-approval packet, missing cargo blocked result, raw/path/secret-like value redaction, custom PCWEB-090 policy drift, custom PCWEB-084 policy drift, and forbidden paths.

- [ ] **Step 3: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_first_cargo_check_execution_boundary.py -q -p no:cacheprovider
```

Expected: FAIL because the PCWEB-090 policy/tool do not exist yet.

### Task 2: Add Policy And Tool

**Files:**
- Create: `code/desktop_tauri/first-cargo-check-execution.policy.json`
- Create: `tools/desktop_first_cargo_check_execution_boundary.py`

- [ ] **Step 1: Add policy JSON**

The policy must declare PCWEB-090, `execution_boundary_mode=explicit_manual_execution_packet_only`, exact first `cargo check` command, `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`, allowed artifacts, required preconditions, and false safety flags.

- [ ] **Step 2: Add Python report generator**

The generator must validate paths before read, validate PCWEB-090 policy, validate PCWEB-084 artifact policy, validate optional PCWEB-089 bounded result, produce blocked reports for missing/invalid result, and produce only a `ready_for_explicit_user_approval` manual packet when preconditions pass.

- [ ] **Step 3: Run focused GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_first_cargo_check_execution_boundary.py -q -p no:cacheprovider
```

Expected: PASS.

### Task 3: Update Documentation Gates

**Files:**
- Modify all cross-docs listed above.

- [ ] **Step 1: Record the PCWEB-090 decision**

Each doc must say PCWEB-090 is manual execution packet only, does not run Cargo, keeps `safe_to_run_cargo_check_now=false`, uses PCWEB-084 command/env/artifact policy, and requires PCWEB-089 bounded toolchain result.

- [ ] **Step 2: Run docs gate**

```bash
cd code/web_mvp/backend && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q -p no:cacheprovider
```

Expected: PASS.

### Task 4: Verify Regression

**Files:**
- No new files.

- [ ] **Step 1: Run adjacent desktop tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_first_cargo_check_execution_boundary.py tests/test_desktop_rust_post_install_probe_result_intake.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_quality_gate.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 2: Run quality gates**

```bash
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: PASS without running Cargo, Tauri, package managers, remote providers, or `configs/local`.

### Task 5: Review And Status Update

**Files:**
- Modify: `docs/pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md`
- Modify: `docs/project-current-status-2026-07-02.md`
- Modify: `docs/project-progress-report-2026-07-02.md`

- [ ] **Step 1: Request read-only review**

Ask a reviewer to check command execution safety, path blocking, redaction, policy drift validation, and docs consistency.

- [ ] **Step 2: Fix Critical or Important findings**

Any valid Critical or Important finding must get a regression test before implementation changes.

- [ ] **Step 3: Record final verification**

Update the PCWEB-090 plan and project status docs with RED/GREEN/gate/review results.
