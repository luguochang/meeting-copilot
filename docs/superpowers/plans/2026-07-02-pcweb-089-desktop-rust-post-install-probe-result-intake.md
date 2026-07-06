# PCWEB-089 Desktop Rust Post-Install Probe Result Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a no-command Rust post-install probe result intake boundary that validates caller-provided bounded status JSON with `accepted_result_source=caller_provided_json_only` without executing probes or unlocking `cargo check`.

**Architecture:** Follow the PCWEB-088 policy/report pattern. A JSON policy defines allowed result fields, enum values, forbidden raw fields, and false safety flags; a Python tool validates the policy and caller-provided result objects or result JSON files with sensitive path guards; root tests prove no command execution and no path/raw-output leakage.

**Tech Stack:** Python 3 standard library, pytest, JSON policy, Markdown docs.

**Implementation status:** completed on 2026-07-02. RED was confirmed with `10 failed, 1 warning` because the PCWEB-089 policy/tool did not exist. Post-review focused GREEN is `11 passed, 1 warning`; adjacent PCWEB-088/quality-gate regression is `26 passed, 1 warning`; desktop/root regression is `72 passed, 1 warning`; docs gate is `1 passed, 2 warnings`; `pc-web` and `all-local --no-browser` quality gates both passed with `root 72 passed`.

---

## File Structure

- Create `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json`: machine-readable PCWEB-089 result-intake policy.
- Create `tools/desktop_rust_post_install_probe_result_intake.py`: no-command result intake report generator.
- Create `tests/test_desktop_rust_post_install_probe_result_intake.py`: root contract tests for policy, result validation, custom-policy hardening, path guards, and source scan.
- Modify `code/web_mvp/backend/tests/test_app.py`: add PCWEB-089 docs to the browser docs gate.
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
  - `docs/project-progress-report-2026-07-02.md`

### Task 1: Write PCWEB-089 Failing Tests

**Files:**
- Create: `tests/test_desktop_rust_post_install_probe_result_intake.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [ ] **Step 1: Add policy and default no-result report tests**

```python
def test_probe_result_intake_policy_exists_and_blocks_execution():
    policy = load_policy()
    assert policy["pcweb_id"] == "PCWEB-089"
    assert policy["result_intake_mode"] == "manual_result_validation_only"
    assert policy["probe_execution_status"] == "not_run"
    assert policy["safe_to_run_post_install_probe_now"] is False
    assert policy["safe_to_run_cargo_check_now"] is False


def test_result_intake_report_without_result_is_no_command_no_result():
    tool = load_tool_module()
    report = tool.build_rust_post_install_probe_result_intake_report(policy_path=POLICY_PATH)
    assert report["result_validation_status"] == "not_provided"
    assert report["probe_execution_status"] == "not_run"
    assert report["safe_to_run_cargo_check_now"] is False
```

- [ ] **Step 2: Add valid bounded result test**

```python
def test_valid_caller_provided_result_is_normalized_but_cargo_check_stays_blocked():
    tool = load_tool_module()
    report = tool.build_rust_post_install_probe_result_intake_report(
        policy_path=POLICY_PATH,
        probe_result={
            "rustc_status": "available",
            "cargo_status": "available",
            "rustup_status": "available",
            "macos_xcode_select_status": "available",
            "macos_xcode_select_path_status": "path_present",
            "first_cargo_check_readiness": "blocked_until_pcweb_084_and_user_approval",
        },
    )
    assert report["result_validation_status"] == "passed"
    assert report["toolchain_presence_summary_status"] == "toolchain_probe_result_available"
    assert report["safe_to_run_cargo_check_now"] is False
```

- [ ] **Step 3: Add raw output and invalid status rejection tests**

```python
def test_result_intake_rejects_raw_output_paths_unknown_fields_and_ready_cargo_check():
    tool = load_tool_module()
    report = tool.build_rust_post_install_probe_result_intake_report(
        policy_path=POLICY_PATH,
        probe_result={
            "rustc_status": "available",
            "cargo_status": "available",
            "rustup_status": "available",
            "macos_xcode_select_status": "available",
            "macos_xcode_select_path_status": "path_present",
            "first_cargo_check_readiness": "ready",
            "stdout": "rustc 1.90.0",
            "xcode_path": "/Library/Developer/CommandLineTools",
        },
    )
    assert report["result_validation_status"] == "failed"
```

- [ ] **Step 4: Run RED**

```bash
python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py -q
```

Expected: FAIL because `rust-post-install-probe-result-intake.policy.json` and `desktop_rust_post_install_probe_result_intake.py` do not exist yet.

### Task 2: Add Static Policy And Intake Tool

**Files:**
- Create: `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json`
- Create: `tools/desktop_rust_post_install_probe_result_intake.py`

- [ ] **Step 1: Add policy JSON**

The policy must include `PCWEB-089`, `manual_result_validation_only`, `caller_provided_json_only`, exact allowed result fields, exact status enums, forbidden raw fields, cargo-check blockers, `safe_to_accept_raw_probe_output_now=false`, and false safety flags.

- [ ] **Step 2: Add Python result intake generator**

The tool must:

- validate policy and result paths before read, blocking `configs/local`, `data/local_runtime`, `outputs`, `artifacts/tmp`, and `data/asr_eval/samples`, including outside-repo and mixed-case paths;
- validate fixed identifiers, allowed fields, allowed status values, forbidden raw fields, cargo-check blockers, and false safety flags;
- accept either a caller-provided dict or a result JSON file;
- normalize absent result to all `not_run`/`not_applicable` safe status without executing commands;
- force `safe_to_run_post_install_probe_now=false` and `safe_to_run_cargo_check_now=false`;
- never import subprocess or expose command runners.

- [ ] **Step 3: Run focused GREEN**

```bash
python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py tests/test_quality_gate.py -q
```

Expected: PASS.

### Task 3: Update Documentation Gates

**Files:**
- Modify all cross-docs listed above.

- [ ] **Step 1: Record the PCWEB-089 decision in cross-docs**

Each doc must say PCWEB-089 is caller-provided JSON only, uses `manual_result_validation_only`, rejects raw output/path fields, keeps `safe_to_run_post_install_probe_now=false`, and keeps `safe_to_run_cargo_check_now=false`.

- [ ] **Step 2: Run docs gate**

```bash
cd code/web_mvp/backend && python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q
```

Expected: PASS.

### Task 4: Verify Regression And Quality Gates

**Files:**
- No new files.

- [ ] **Step 1: Run desktop/root regression**

```bash
python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py tests/test_desktop_rust_post_install_probe_approval.py tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_desktop_tauri_scaffold.py tests/test_quality_gate.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full PC Web gate**

```bash
python3 tools/run_quality_gate.py --profile pc-web
```

Expected: PASS.

- [ ] **Step 3: Run all-local no-browser gate**

```bash
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: PASS.

- [ ] **Step 4: Run hygiene checks**

Check no `Cargo.lock`, `target`, `desktop_tauri_target`, `node_modules`, dist, bundle, installer, `.cargo`, `.rustup`, lingering test cache, listening test ports, or exposed `sk-...` token appears outside excluded paths.

### Task 5: Review

**Files:**
- No new files unless feedback requires fixes.

- [ ] **Step 1: Request read-only code review**

Dispatch a reviewer with the PCWEB-089 plan, changed files, and safety requirements.

- [ ] **Step 2: Fix Critical or Important feedback**

Any valid Critical or Important issue must be fixed with a focused regression test and re-run of relevant gates.

- [ ] **Step 3: Update implementation status**

Record RED/GREEN/gate/hygiene/review results in `docs/pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md`.
