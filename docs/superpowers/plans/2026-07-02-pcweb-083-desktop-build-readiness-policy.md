# PCWEB-083 Desktop Build Readiness Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested desktop build-readiness policy and no-build readiness report after the PCWEB-082 Tauri scaffold.

**Architecture:** Store the build readiness rules in `code/desktop_tauri/build-readiness.policy.json` and expose a small Python report tool in `tools/desktop_build_readiness.py`. The tool is static by default and only supports `toolchain_version_probe_only` when explicitly requested; it never runs `cargo check`, Tauri dev/build, package-manager commands, audio, worker, secret, or remote-provider paths.

**Tech Stack:** JSON policy, Python standard library, pytest, existing root quality gate.

**Implementation status: completed**

**Post-review hardening:** completed. `tools/desktop_build_readiness.py` now enforces the executable probe allowlist in code, so a custom policy cannot expand probe execution beyond `rustc --version` and `cargo --version`. Blocked custom policy commands are reported with `returncode=126` and are not passed to the runner.

---

### Task 1: RED Build Readiness Policy Tests

**Files:**
- Create: `tests/test_desktop_build_readiness_policy.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add failing policy tests**

Require `code/desktop_tauri/build-readiness.policy.json` to declare `PCWEB-083`, `policy_status=build_readiness_policy_only`, `toolchain_version_probe_only`, and `safe_to_run_cargo_check_now=false`.

- [x] **Step 2: Add failing tool tests**

Require `tools/desktop_build_readiness.py` to return a static report by default and to run only `rustc --version` and `cargo --version` when `probe_toolchain=True`.

- [x] **Step 3: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q
```

Expected before implementation: missing policy/tool failures.

### Task 2: Implement Policy And Report Tool

**Files:**
- Create: `code/desktop_tauri/build-readiness.policy.json`
- Create: `tools/desktop_build_readiness.py`

- [x] **Step 1: Add JSON policy**

Record `safe_to_run_cargo_check_now=false`, `safe_to_run_tauri_dev_now=false`, `safe_to_run_tauri_build_now=false`, `safe_to_install_dependencies_now=false`, `safe_to_generate_lockfiles_now=false`, allowed version probes, forbidden build/package commands, forbidden artifacts, future `cargo check` candidate, and future preconditions.

- [x] **Step 2: Add no-build report tool**

Implement `build_readiness_report()` so default mode is static and optional probe mode is `toolchain_version_probe_only`.

- [x] **Step 2a: Enforce probe allowlist in code**

Reject or skip any custom policy probe command except exactly `rustc --version` and `cargo --version`; do not rely on policy JSON to define the executable boundary.

- [x] **Step 3: Verify GREEN**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q
```

Expected after implementation: pass.

### Task 3: Documentation And Decision Records

**Files:**
- Modify: `README.md`
- Modify: `code/web_mvp/README.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`

- [x] **Step 1: Document PCWEB-083 scope**

Record `build-readiness.policy.json`, `desktop_build_readiness.py`, `safe_to_run_cargo_check_now=false`, and `toolchain_version_probe_only`.

- [x] **Step 2: Keep build side effects blocked**

Document that PCWEB-083 still does not run cargo check/build, Tauri dev/build, dependency install, lockfile generation, audio capture, worker spawn, secret reads, local runtime writes, or remote calls.

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

- [x] **Step 2: Hygiene**

Remove test caches; check ports `8767` and `9223`; scan for real `sk-...` token shapes while excluding `configs/local/**`; confirm `code/desktop_tauri` has no lock/build/package/install artifacts.

### Task 5: Post-Review Boundary Hardening

**Files:**
- Modify: `tools/desktop_build_readiness.py`
- Modify: `tests/test_desktop_build_readiness_policy.py`
- Modify: `code/desktop_tauri/build-readiness.policy.json`
- Modify: `docs/pcweb-083-desktop-build-readiness-policy-plan.md`
- Modify: `docs/decision-log.md`

- [x] **Step 1: Confirm RED for custom policy expansion**

Add a regression test where a custom policy includes `cargo check` in `allowed_probe_commands`; verify the runner is called only for `rustc --version`.

- [x] **Step 2: Block invalid policy probe commands**

Return a blocked probe result for invalid commands without invoking the runner.

- [x] **Step 3: Lock side effects and launcher variants**

Assert forbidden side effects are preserved in the report, and expand forbidden command coverage to include `npm ci`, `pnpm run tauri dev/build`, `yarn tauri dev/build`, and `npx tauri dev/build`.
