# PCWEB-082 Tauri Shell Scaffold Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the first minimal Tauri desktop scaffold against the PCWEB-081 native bridge contract while binding only three no-op commands and preserving all no-audio/no-worker/no-secret/no-paid-call boundaries.

**Architecture:** Add a small `code/desktop_tauri/src-tauri` Rust scaffold that points to the existing local Web MVP backend/static assets and exposes no-op command handlers for `runtime.get_status`, `session.prepare`, and `asr_worker.health`. Keep the scaffold cargo-only for this spike, return `safe_to_execute_real_action=false` from every no-op handler, do not add a Node frontend package or dependency lock files, and verify the file/contract shape with local pytest.

**Tech Stack:** Tauri v2 scaffold files, Rust source skeleton, JSON/TOML contract tests, Python pytest, existing PC Web quality gate.

**Implementation status: completed**

---

### Task 1: RED Scaffold Contract And Quality Gate Expectations

**Files:**
- Create: `tests/test_desktop_tauri_scaffold.py`
- Modify: `tests/test_quality_gate.py`

- [x] **Step 1: Add failing scaffold file/config tests**

Create tests that require `code/desktop_tauri/src-tauri/Cargo.toml`, `build.rs`, `tauri.conf.json`, `capabilities/default.json`, `src/main.rs`, `src/lib.rs`, and `code/desktop_tauri/README.md`.

- [x] **Step 2: Add failing no-op command tests**

Require `src/lib.rs` to bind exactly `runtime_get_status`, `session_prepare`, and `asr_worker_health`, each mapping to the PCWEB-081 command IDs and each returning no-op/no-side-effect fields including `safe_to_execute_real_action=false`.

- [x] **Step 3: Add failing negative-boundary tests**

Require no `package.json`, no dependency lock files, no installer/signing/bundle artifacts, no audio capture command binding, no worker spawn, no provider config/secret reads, and no remote provider dependency.

- [x] **Step 4: Add failing quality gate tests**

Update `tests/test_quality_gate.py` so the `pc-web` and `all-local` profiles include a root pytest step before core/backend/browser.

- [x] **Step 5: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_tauri_scaffold.py tests/test_quality_gate.py -q
```

Expected: failures for missing `code/desktop_tauri` scaffold and missing quality gate root pytest step.

### Task 2: Create Minimal Tauri Scaffold

**Files:**
- Create: `code/desktop_tauri/README.md`
- Create: `code/desktop_tauri/src-tauri/Cargo.toml`
- Create: `code/desktop_tauri/src-tauri/build.rs`
- Create: `code/desktop_tauri/src-tauri/tauri.conf.json`
- Create: `code/desktop_tauri/src-tauri/capabilities/default.json`
- Create: `code/desktop_tauri/src-tauri/src/main.rs`
- Create: `code/desktop_tauri/src-tauri/src/lib.rs`

- [x] **Step 1: Add cargo-only Tauri source tree**

Create the Tauri v2 source tree without running cargo, npm, or Tauri CLI.

- [x] **Step 2: Add config pointing at existing Web MVP**

Set `devUrl` to `http://127.0.0.1:8765/` and `frontendDist` to `../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static`. Keep `beforeDevCommand` and `beforeBuildCommand` empty and `bundle.active=false`.

- [x] **Step 3: Add no-op command handlers**

Implement `runtime_get_status`, `session_prepare`, and `asr_worker_health` in Rust as no-op responses with no audio, process, provider, or local write side effects.

- [x] **Step 4: Verify scaffold GREEN**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_tauri_scaffold.py tests/test_quality_gate.py -q
```

Expected: all selected tests pass.

### Task 3: Update Documentation And Decision Records

**Files:**
- Modify: `code/web_mvp/README.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`

- [x] **Step 1: Document PCWEB-082 scope**

Record the Tauri scaffold, cargo-only/no-package-json choice, no-op command list, and forbidden side effects.

- [x] **Step 2: Document quality gate inclusion**

Record that `pc-web` now includes root pytest for scaffold contract tests.

- [x] **Step 3: Verify docs gate**

Run the web backend docs gate:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q
```

Expected: pass after docs are updated.

### Task 4: Full Verification And Hygiene

**Files:**
- Review: all modified files.

- [x] **Step 1: Run focused root tests**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_tauri_scaffold.py tests/test_quality_gate.py -q
```

- [x] **Step 2: Run PC Web quality gate**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
```

- [x] **Step 3: Hygiene**

Remove test caches under `code/core`, `code/web_mvp`, and root `tests`; check ports `8767` and `9223`; scan for real `sk-...` token shapes while excluding `configs/local/**`, venvs, caches, and runtime outputs.

- [x] **Step 4: Read-only review**

Ask reviewers to check that PCWEB-082 creates only the intended Tauri scaffold, binds only no-op commands, preserves the no-audio/no-worker/no-secret/no-paid-call boundary, and documents the next desktop risk honestly.
