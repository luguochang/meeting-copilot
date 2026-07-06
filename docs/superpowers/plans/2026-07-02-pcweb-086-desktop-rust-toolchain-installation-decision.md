# PCWEB-086 Desktop Rust Toolchain Installation Decision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested no-install Rust toolchain installation decision boundary before any Rust install, shell profile modification, or first desktop `cargo check`.

**Architecture:** Store the install decision boundary in `code/desktop_tauri/rust-toolchain-installation.policy.json` and expose a Python static report tool in `tools/desktop_rust_toolchain_installation_decision.py`. The tool must not import or call subprocess, must never execute commands, and must force all PCWEB-086 safety flags false even if a custom policy tries to relax them.

**Tech Stack:** JSON policy, Python standard library without subprocess execution, pytest, existing root and Web documentation gates.

**Boundary:** Keep `safe_to_install_toolchain_now=false`, `safe_to_modify_shell_profile_now=false`, `safe_to_run_install_command_now=false`, `safe_to_run_rustup_now=false`, and `safe_to_run_cargo_check_now=false` throughout PCWEB-086.

**Implementation status: completed for the no-install Rust toolchain installation decision boundary.**

---

### Task 1: RED Rust Toolchain Installation Decision Tests

**Files:**
- Create: `tests/test_desktop_rust_toolchain_installation_decision.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add failing policy tests**

Require `code/desktop_tauri/rust-toolchain-installation.policy.json` to declare `PCWEB-086`, `policy_status=rust_toolchain_installation_decision_policy_only`, `installation_decision_mode=no_install_decision_report_only`, `recommended_install_provider=official_rustup`, required approval tokens, official source URLs, platform notes, post-install verification order, and all install/build/fetch/artifact/config-local safety flags false.

- [x] **Step 2: Add failing tool tests**

Require `tools/desktop_rust_toolchain_installation_decision.py` to return `report_mode=rust_toolchain_installation_decision_static_report`, `installation_execution_status=not_run`, `installation_decision_status=blocked_requires_explicit_user_approval`, approval blockers, platform notes, official sources, and false safety flags.

- [x] **Step 3: Add failing no-execution and custom-policy tests**

Require the tool source to contain no `subprocess`, `os.system`, `Popen`, `check_call`, `check_output`, `run(`, `exec(`, or `eval(` command execution path. Require custom policies that flip safety flags or remove approval tokens to fail validation and still report every safety flag as false.

- [x] **Step 4: Add failing documentation gate**

Extend `test_web_mvp_readme_documents_scripted_browser_e2e_gate` so README, Web README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan all mention PCWEB-086, `rust-toolchain-installation.policy.json`, `desktop_rust_toolchain_installation_decision.py`, `no_install_decision_report_only`, and `safe_to_install_toolchain_now=false`.

- [x] **Step 5: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py tests/test_quality_gate.py -q
```

Expected before implementation: missing policy/tool failures.

### Task 2: Implement Policy And Static Report Tool

**Files:**
- Create: `code/desktop_tauri/rust-toolchain-installation.policy.json`
- Create: `tools/desktop_rust_toolchain_installation_decision.py`

- [x] **Step 1: Add JSON policy**

Record the no-install decision status, official rustup recommendation, approval tokens, platform notes, post-install verification order, forbidden commands, forbidden side effects, and remaining preconditions.

- [x] **Step 2: Add static decision report tool**

Implement `build_rust_toolchain_installation_decision_report()` so it loads and validates the policy, runs no external commands, forces every safety flag false, blocks missing approval tokens, and returns `installation_decision_status=blocked_requires_explicit_user_approval`.

- [x] **Step 3: Verify GREEN**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py tests/test_quality_gate.py -q
```

Expected after implementation: pass.

### Task 3: Documentation And Decision Records

**Files:**
- Modify: `README.md`
- Modify: `code/web_mvp/README.md`
- Modify: `code/desktop_tauri/README.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/pcweb-086-desktop-rust-toolchain-installation-decision-plan.md`

- [x] **Step 1: Document PCWEB-086 scope**

Record the policy file, report tool, official rustup recommendation, explicit approval tokens, no-install status, platform notes, and future post-install verification order.

- [x] **Step 2: Keep side effects blocked**

Document that PCWEB-086 still does not install Rust, run curl/sh/rustup/cargo/package managers, modify shell profiles, fetch dependencies, generate artifacts, read secrets, read `configs/local/`, capture audio, spawn workers, or call remote providers.

- [x] **Step 3: Verify docs gate**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q
```

Expected: pass after docs are updated.

### Task 4: Full Verification And Hygiene

**Files:**
- Review: all modified files.

- [x] **Step 1: Run PC Web quality gate**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
```

- [x] **Step 2: Run all-local no-browser quality gate**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [x] **Step 3: Hygiene**

Remove test caches outside virtual environments; check ports `8767` and `9223`; scan for real `sk-...` token shapes while excluding `configs/local/**`; confirm no `Cargo.lock`, target, `desktop_tauri_target`, node_modules, dist, bundle, installer, signing, notarization, shell profile, Rust install, cargo home, or rustup home artifacts were created.

## Implementation Status

Status: completed for the no-install Rust toolchain installation decision boundary.

Verification evidence:

- RED confirmed with `python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py tests/test_quality_gate.py -q` before `rust-toolchain-installation.policy.json` and `desktop_rust_toolchain_installation_decision.py` existed.
- Focused GREEN passed with `python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py tests/test_quality_gate.py -q`.
- Docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`.
- Combined desktop/root regression passed with `python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q`.
- Static report passed with `python3 tools/desktop_rust_toolchain_installation_decision.py`; it returned `installation_execution_status=not_run`, `external_command_execution_status=not_run`, `installation_decision_status=blocked_requires_explicit_user_approval`, `recommended_install_provider=official_rustup`, and kept all install/build/fetch/artifact safety flags false.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with root, core, Web backend, and browser smoke checks.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with ASR runtime, ASR bake-off, root, core, and Web backend checks.
- Post-review RED was confirmed with `python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py::test_custom_policy_path_rejects_configs_local_before_reading tests/test_desktop_rust_toolchain_installation_decision.py::test_installation_decision_tool_source_has_no_command_execution_entrypoints -q`; the path-boundary case failed by reaching `read_text()` before rejecting a `configs/local` shaped path.
- Post-review GREEN passed for the same focused tests after the tool was hardened to reject sensitive custom policy roots before file reads and the source-level no-command-execution scan was aligned with the plan by checking `run(`.
- Hygiene confirmed no Rust install, shell profile modification, curl/sh/rustup/cargo/Tauri/package-manager run, dependency fetch, `Cargo.lock`, target output, node modules, dist, bundle, installer, secret/config-local read, real-audio inspection, audio capture, worker spawn, or remote provider call was introduced.
