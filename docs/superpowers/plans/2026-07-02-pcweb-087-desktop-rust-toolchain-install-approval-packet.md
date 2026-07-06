# PCWEB-087 Desktop Rust Toolchain Install Approval Packet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static Rust toolchain install approval packet that records official manual instructions and risks without executing install, shell, Cargo, Tauri, audio, secret, or remote-provider paths.

**Architecture:** Follow PCWEB-086's policy-plus-static-report pattern. A JSON policy stores inert manual instruction text and boundaries; a Python tool validates the policy and emits a report with forced-false safety flags; tests prove no command execution, no policy path reads under sensitive roots, and no docs-gate drift.

**Tech Stack:** Python 3 standard library, pytest, JSON policy, Markdown docs.

**Boundary:** PCWEB-087 is `manual_user_run_only`, keeps `safe_to_execute_install_now=false`, and must not execute manual instruction text.

---

## File Structure

- Create `code/desktop_tauri/rust-toolchain-install-approval.policy.json`: machine-readable PCWEB-087 approval packet policy.
- Create `tools/desktop_rust_toolchain_install_approval_packet.py`: static report generator; no subprocess or shell execution entrypoints.
- Create `tests/test_desktop_rust_toolchain_install_approval_packet.py`: root contract tests for policy, report, custom-policy hardening, path guards, and source scan.
- Modify `tests/test_quality_gate.py`: keep default gates free of Rust/toolchain/package-manager commands.
- Modify `code/web_mvp/backend/tests/test_app.py`: require PCWEB-087 docs to appear in the browser docs gate.
- Modify README and docs listed below to record the decision:
  - `README.md`
  - `code/web_mvp/README.md`
  - `code/desktop_tauri/README.md`
  - `docs/requirements-traceability-matrix.md`
  - `docs/pc-local-web-mvp-acceptance.md`
  - `docs/privacy-and-data-flow.md`
  - `docs/project-structure.md`
  - `docs/implementation-roadmap.md`
  - `docs/decision-log.md`
  - `docs/pcweb-087-desktop-rust-toolchain-install-approval-packet-plan.md`

### Task 1: Write PCWEB-087 Failing Tests

**Files:**
- Create: `tests/test_desktop_rust_toolchain_install_approval_packet.py`
- Modify: `tests/test_quality_gate.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [ ] **Step 1: Add root tests for the missing policy and tool**

```python
def test_install_approval_policy_exists_and_records_manual_boundary():
    policy = load_policy()
    assert policy["pcweb_id"] == "PCWEB-087"
    assert policy["approval_packet_mode"] == "manual_user_run_only"
    assert policy["safe_to_execute_install_now"] is False
```

- [ ] **Step 2: Add report tests for no-execute behavior**

```python
def test_install_approval_report_is_static_and_never_executes_manual_text():
    tool = load_tool_module()
    report = tool.build_rust_toolchain_install_approval_packet(policy_path=POLICY_PATH)
    assert report["execution_mode"] == "manual_user_run_only"
    assert report["command_execution_status"] == "not_run"
    assert report["installation_execution_status"] == "not_run"
```

- [ ] **Step 3: Add hardening tests**

```python
def test_custom_policy_cannot_relax_safety_flags_or_remove_approval_tokens(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    custom_policy["safe_to_execute_install_now"] = True
    custom_policy["required_approval_tokens_before_install"] = REQUIRED_APPROVAL_TOKENS[:-1]
    custom_policy_path = tmp_path / "rust-toolchain-install-approval.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")
    report = tool.build_rust_toolchain_install_approval_packet(policy_path=custom_policy_path)
    assert report["policy_validation_status"] == "failed"
```

- [ ] **Step 4: Run RED**

Run:

```bash
python3 -m pytest tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_quality_gate.py -q
```

Expected: FAIL because `rust-toolchain-install-approval.policy.json` and `desktop_rust_toolchain_install_approval_packet.py` do not exist yet.

### Task 2: Add Static Policy And Report Tool

**Files:**
- Create: `code/desktop_tauri/rust-toolchain-install-approval.policy.json`
- Create: `tools/desktop_rust_toolchain_install_approval_packet.py`

- [ ] **Step 1: Add policy JSON**

The policy must include `PCWEB-087`, `manual_user_run_only`, official source URLs, platform-specific manual instruction text, all required approval tokens, rollback notes, post-install verification order, and false safety flags.

- [ ] **Step 2: Add Python report generator**

The tool must:

- read only the policy path after path validation,
- validate fixed identifiers, approval tokens, platform keys, source URLs, manual instruction text fields, rollback notes, and false safety flags,
- return a blocked report instead of reading sensitive policy paths,
- force all safety flags false in output,
- never import subprocess or expose any command runner.

- [ ] **Step 3: Run focused GREEN**

Run:

```bash
python3 -m pytest tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_quality_gate.py -q
```

Expected: PASS.

### Task 3: Update Documentation Gates

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
- Modify: `docs/pcweb-087-desktop-rust-toolchain-install-approval-packet-plan.md`
- Modify: `docs/superpowers/plans/2026-07-02-pcweb-087-desktop-rust-toolchain-install-approval-packet.md`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [ ] **Step 1: Record the PCWEB-087 decision in cross-docs**

Each doc must say the packet is manual-user-run only, command text is inert, and no install/build/audio/secret/remote path is opened.

- [ ] **Step 2: Run docs gate**

Run:

```bash
cd code/web_mvp/backend && python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q
```

Expected: PASS.

### Task 4: Verify Regression And Quality Gates

**Files:**
- No new files.

- [ ] **Step 1: Run desktop/root regression**

```bash
python3 -m pytest tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q
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

Check that no `Cargo.lock`, `target`, `desktop_tauri_target`, `node_modules`, dist, bundle, installer, `.cargo`, `.rustup`, or exposed `sk-...` token appears outside excluded local/virtualenv/cache paths.

### Task 5: Review

**Files:**
- No new files unless feedback requires fixes.

- [ ] **Step 1: Request read-only code review**

Dispatch a reviewer with the PCWEB-087 plan, changed files, and safety requirements.

- [ ] **Step 2: Fix Critical or Important feedback**

Any valid Critical or Important issue must be fixed with a focused regression test and re-run of the relevant gates.

- [ ] **Step 3: Update implementation status**

Record RED/GREEN/gate/review results in `docs/pcweb-087-desktop-rust-toolchain-install-approval-packet-plan.md`.
