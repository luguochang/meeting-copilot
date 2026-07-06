# PCWEB-084 Desktop Cargo Check Artifact Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested, no-run policy for the first future desktop `cargo check` artifact, environment, network, cleanup, and side-effect boundaries.

**Architecture:** Store the cargo-check artifact policy in `code/desktop_tauri/cargo-check.policy.json` and expose a small read-only report tool in `tools/desktop_cargo_check_policy.py`. The report validates policy shape, inspects only expected artifact paths, and never executes Cargo, Tauri, package managers, audio, worker, secret, or remote-provider paths.

**Tech Stack:** JSON policy, Python standard library, pytest, existing root and Web documentation gates.

**Implementation status: completed for the no-run cargo-check artifact policy boundary.**

---

### Task 1: RED Cargo Check Artifact Policy Tests

**Files:**
- Create: `tests/test_desktop_cargo_check_artifact_policy.py`
- Modify: `tests/test_quality_gate.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add failing policy tests**

Require `code/desktop_tauri/cargo-check.policy.json` to declare `PCWEB-084`, `policy_status=cargo_check_artifact_policy_only`, `safe_to_run_cargo_check_now=false`, `cargo_lock_policy_status=decided_not_generated`, `cargo_target_dir_policy_status=decided_not_created`, `network_dependency_fetch_policy_status=blocked_by_default`, and `cleanup_policy_status=decided_not_executed`.

- [x] **Step 2: Add failing command/environment tests**

Require the policy to specify first approved command `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`, repeat command `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml --locked --offline`, and environment `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`.

- [x] **Step 3: Add failing report tool tests**

Require `tools/desktop_cargo_check_policy.py` to return `report_mode=cargo_check_policy_static_report`, `cargo_check_execution_status=not_run`, read-only artifact existence fields, and `safe_to_run_cargo_check_now=false`.

- [x] **Step 4: Add failing malformed policy validation test**

Create a temporary policy with a malformed command entry and assert the report returns `policy_validation_status=failed`, keeps `cargo_check_execution_status=not_run`, and reports no executable command readiness.

- [x] **Step 5: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py tests/test_quality_gate.py -q
```

Expected before implementation: missing policy/tool failures.

### Task 2: Implement Policy And Report Tool

**Files:**
- Create: `code/desktop_tauri/cargo-check.policy.json`
- Create: `tools/desktop_cargo_check_policy.py`

- [x] **Step 1: Add JSON policy**

Record the no-run status, future command plans, lockfile policy, target directory policy, dependency fetch policy, cleanup policy, forbidden side effects, forbidden commands, allowed future artifacts, forbidden source-tree artifacts, official source URLs, and remaining preconditions.

- [x] **Step 2: Add read-only report tool**

Implement `build_cargo_check_policy_report()` so it loads and validates the policy, checks only expected artifact path existence, returns a static report, and never calls subprocess.

- [x] **Step 3: Verify GREEN**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py tests/test_quality_gate.py -q
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
- Modify: `docs/pcweb-084-desktop-cargo-check-artifact-policy-plan.md`

- [x] **Step 1: Document PCWEB-084 scope**

Record the policy file, report tool, future command plans, `CARGO_TARGET_DIR`, Cargo.lock decision, network fetch boundary, cleanup boundary, and no-run status.

- [x] **Step 2: Keep side effects blocked**

Document that PCWEB-084 still does not run cargo/Tauri/package manager commands, install toolchains, generate artifacts, request permissions, capture audio, spawn workers, read secrets, read `configs/local/`, or call remote providers.

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

Remove test caches outside virtual environments; check ports `8767` and `9223`; scan for real `sk-...` token shapes while excluding `configs/local/**`; confirm `code/desktop_tauri` has no generated lock/build/package/install artifacts and `artifacts/tmp/desktop_tauri_target` was not created.

## Implementation Status

Status: completed for the no-run policy boundary.

Verification evidence:

- RED confirmed with `python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py tests/test_quality_gate.py -q` before `cargo-check.policy.json` and `desktop_cargo_check_policy.py` existed.
- Focused GREEN passed with `python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py tests/test_quality_gate.py -q`.
- Combined desktop/root regression passed with `python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q`.
- Docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`.
- Static report passed with `python3 tools/desktop_cargo_check_policy.py`.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with root/core/Web/browser smoke.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with ASR runtime, ASR bake-off, root, core, and Web backend tests.
- Post-review hardening added a source-level no-external-execution guard test for `tools/desktop_cargo_check_policy.py`; `python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py -q` passed with 8 tests.
- Hygiene confirmed ports `8767` and `9223` were not listening, sensitive token scan had no hits outside excluded local/config/runtime paths, and no `Cargo.lock`, target, `desktop_tauri_target`, `node_modules`, dist, bundle, or installer artifacts were present.
