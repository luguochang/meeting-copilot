# PCWEB-085 Desktop Rust Toolchain Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested no-install Rust toolchain readiness policy and optional local version/platform probe before the first future desktop `cargo check`.

**Architecture:** Store the toolchain readiness boundary in `code/desktop_tauri/rust-toolchain-readiness.policy.json` and expose a Python report/probe tool in `tools/desktop_rust_toolchain_readiness.py`. The tool is static by default; explicit probe mode is hard-allowlisted to version/platform prerequisite probes and still never runs Cargo, Tauri, package managers, installers, audio, worker, secret, or remote-provider paths.

**Tech Stack:** JSON policy, Python standard library, pytest, existing root and Web documentation gates.

**Boundary:** Keep `safe_to_install_toolchain_now=false`, `safe_to_modify_shell_profile_now=false`, and `safe_to_run_cargo_check_now=false` throughout PCWEB-085.

**Implementation status: completed for the no-install Rust toolchain readiness boundary.**

---

### Task 1: RED Rust Toolchain Readiness Tests

**Files:**
- Create: `tests/test_desktop_rust_toolchain_readiness.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add failing policy tests**

Require `code/desktop_tauri/rust-toolchain-readiness.policy.json` to declare `PCWEB-085`, `policy_status=rust_toolchain_readiness_policy_only`, `toolchain_probe_mode=local_version_and_platform_probe_only`, and all install/build/fetch/artifact safety flags false.

- [x] **Step 2: Add failing probe allowlist tests**

Require allowed probes to be exactly `rustc --version`, `cargo --version`, `rustup --version`, and `xcode-select -p`, and require forbidden commands to include Cargo build/check/Tauri, rustup install/update, curl shell install, package managers, and profile modifications.

- [x] **Step 3: Add failing report/probe tests**

Require `desktop_rust_toolchain_readiness.py` to be static by default, optionally run only the hard-coded allowlist, redact `xcode-select -p` output, block custom policy expansion, and report missing executables without traceback.

- [x] **Step 4: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py tests/test_quality_gate.py -q
```

Expected before implementation: missing policy/tool failures.

### Task 2: Implement Policy And Report Tool

**Files:**
- Create: `code/desktop_tauri/rust-toolchain-readiness.policy.json`
- Create: `tools/desktop_rust_toolchain_readiness.py`

- [x] **Step 1: Add JSON policy**

Record the static default, optional allowlisted probe mode, forbidden commands, forbidden side effects, output redaction, first cargo-check preconditions, and official source URLs.

- [x] **Step 2: Add readiness report tool**

Implement `build_rust_toolchain_readiness_report()` so default mode runs no external commands, optional probe mode runs only the hard-coded allowlist, missing executables return `127`, invalid custom policy probes return `126`, and `xcode-select -p` returns only presence metadata.

- [x] **Step 3: Verify GREEN**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py tests/test_quality_gate.py -q
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

- [x] **Step 1: Document PCWEB-085 scope**

Record the policy file, report tool, optional probe allowlist, redaction rule, and remaining preconditions before a real `cargo check`.

- [x] **Step 2: Keep side effects blocked**

Document that PCWEB-085 still does not install toolchains, modify shell profiles, run Cargo/Tauri/package managers, generate artifacts, read secrets, read `configs/local/`, capture audio, spawn workers, or call remote providers.

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

Remove test caches outside virtual environments; check ports `8767` and `9223`; scan for real `sk-...` token shapes while excluding `configs/local/**`; confirm no `Cargo.lock`, target, `desktop_tauri_target`, node_modules, dist, bundle, installer, signing, notarization, or shell profile artifacts were created.

## Implementation Status

Status: completed for the no-install Rust toolchain readiness boundary.

Verification evidence:

- RED confirmed with `python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py tests/test_quality_gate.py -q` before `rust-toolchain-readiness.policy.json` and `desktop_rust_toolchain_readiness.py` existed.
- Focused GREEN passed with `python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py tests/test_quality_gate.py -q`.
- Docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`.
- Combined desktop/root regression passed with `python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q`.
- Static report passed with `python3 tools/desktop_rust_toolchain_readiness.py`; it returned `toolchain_probe_status=not_run`, `first_cargo_check_readiness_status=not_evaluated`, and kept all install/build/fetch/artifact safety flags false.
- Explicit local probe passed with `python3 tools/desktop_rust_toolchain_readiness.py --probe-local-toolchain`; it reported `rustc_status=missing`, `cargo_status=missing`, `rustup_status=missing`, `macos_command_line_tools_status=available`, and `first_cargo_check_blocker=missing_required_rust_toolchain`, while redacting `xcode-select -p` output to `presence_only`.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with root, core, Web backend, and browser smoke checks.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with ASR runtime, ASR bake-off, root, core, and Web backend checks.
- Post-review RED was confirmed with `python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py::test_custom_policy_cannot_relax_safety_flags tests/test_desktop_rust_toolchain_readiness.py::test_xcode_select_probe_redacts_path_from_stderr -q`; failures showed incomplete custom-policy safety flag validation and stdout-only xcode path redaction.
- Post-review GREEN passed for the same focused tests after the tool was hardened to force all PCWEB-085 safety flags false in reports, validate every install/build/Tauri/fetch/artifact/config-local flag, and redact `xcode-select -p` stderr as well as stdout.
- Hygiene confirmed no Rust install, shell profile modification, Cargo/Tauri/package-manager run, dependency fetch, `Cargo.lock`, target output, node modules, dist, bundle, installer, secret/config/local read, real-audio inspection, audio capture, worker spawn, or remote provider call was introduced.
