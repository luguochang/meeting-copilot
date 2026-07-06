# PCWEB-088 Desktop Rust Post-Install Probe Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static post-install Rust probe approval packet that defines future read-only probe commands and redaction rules without executing probes, Cargo, Tauri, installers, audio, secrets, or remote-provider paths.

**Architecture:** Follow the PCWEB-087 static policy/report pattern with stricter probe-specific fields. A JSON policy stores the future allowlist and safety contract; a Python tool validates the policy and emits a forced-safe report; root tests prove no command execution and no sensitive policy path reads.

**Tech Stack:** Python 3 standard library, pytest, JSON policy, Markdown docs.

**Boundary:** PCWEB-088 is `no_probe_execution_approval_packet_only`, keeps `safe_to_run_post_install_probe_now=false` and `safe_to_run_cargo_check_now=false`, and must not run `rustc`, `cargo`, `rustup`, `xcode-select`, Cargo check, Tauri, installers, package managers, audio, secret, or remote-provider paths.

---

## File Structure

- Create `code/desktop_tauri/rust-post-install-probe-approval.policy.json`: machine-readable PCWEB-088 post-install probe approval policy.
- Create `tools/desktop_rust_post_install_probe_approval.py`: static report generator with no subprocess or shell execution entrypoints.
- Create `tests/test_desktop_rust_post_install_probe_approval.py`: root contract tests for policy, report, custom-policy hardening, path guards, and source scan.
- Modify `tests/test_quality_gate.py`: ensure default gates do not run Rust/rustup/xcode-select/Cargo/Tauri/package-manager commands.
- Modify `code/web_mvp/backend/tests/test_app.py`: require PCWEB-088 docs to appear in the browser docs gate.
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

### Task 1: Write PCWEB-088 Failing Tests

**Files:**
- Create: `tests/test_desktop_rust_post_install_probe_approval.py`
- Modify: `tests/test_quality_gate.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [ ] **Step 1: Add root tests for the missing policy and tool**

```python
def test_post_install_probe_approval_policy_exists_and_blocks_execution():
    policy = load_policy()
    assert policy["pcweb_id"] == "PCWEB-088"
    assert policy["probe_approval_mode"] == "no_probe_execution_approval_packet_only"
    assert policy["safe_to_run_post_install_probe_now"] is False
    assert policy["safe_to_run_cargo_check_now"] is False
```

- [ ] **Step 2: Add report tests for no-execute behavior**

```python
def test_post_install_probe_approval_report_is_static_and_never_runs_probes():
    tool = load_tool_module()
    report = tool.build_rust_post_install_probe_approval_report(policy_path=POLICY_PATH)
    assert report["probe_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["safe_to_run_post_install_probe_now"] is False
```

- [ ] **Step 3: Add custom-policy hardening tests**

```python
def test_custom_policy_cannot_add_probe_commands_or_enable_cargo_check(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    custom_policy["future_probe_command_allowlist"].append(
        {"probe_id": "cargo_check", "command_text": "cargo check", "platform": "all"}
    )
    custom_policy["safe_to_run_post_install_probe_now"] = True
    custom_policy["safe_to_run_cargo_check_now"] = True
    custom_policy_path = tmp_path / "rust-post-install-probe-approval.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")
    report = tool.build_rust_post_install_probe_approval_report(policy_path=custom_policy_path)
    assert report["policy_validation_status"] == "failed"
```

- [ ] **Step 4: Run RED**

```bash
python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py tests/test_quality_gate.py -q
```

Expected: FAIL because `rust-post-install-probe-approval.policy.json` and `desktop_rust_post_install_probe_approval.py` do not exist yet.

### Task 2: Add Static Policy And Report Tool

**Files:**
- Create: `code/desktop_tauri/rust-post-install-probe-approval.policy.json`
- Create: `tools/desktop_rust_post_install_probe_approval.py`

- [ ] **Step 1: Add policy JSON**

The policy must include `PCWEB-088`, `no_probe_execution_approval_packet_only`, exact future probe command allowlist, required approval tokens, redaction requirements, expected result schema, cargo-check blockers, official source references, and false safety flags.

- [ ] **Step 2: Add Python report generator**

The tool must:

- validate policy paths before read, blocking `configs/local`, `data/local_runtime`, `outputs`, `artifacts/tmp`, and `data/asr_eval/samples`, including outside-repo paths;
- validate fixed identifiers, approval tokens, allowlist, redaction requirements, expected result schema, cargo-check blockers, official sources, and false safety flags;
- force all safety flags false in output;
- return canonical trusted allowlist/sources on validation failure;
- never import subprocess or expose command runners.

- [ ] **Step 3: Run focused GREEN**

```bash
python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py tests/test_quality_gate.py -q
```

Expected: PASS.

### Task 3: Update Documentation Gates

**Files:**
- Modify all cross-docs listed above.

- [ ] **Step 1: Record the PCWEB-088 decision in cross-docs**

Each doc must say PCWEB-088 is no-probe/no-cargo-check, uses `no_probe_execution_approval_packet_only`, keeps `safe_to_run_post_install_probe_now=false`, and only records the future read-only probe allowlist.

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
python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q
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

Dispatch a reviewer with the PCWEB-088 plan, changed files, and safety requirements.

- [ ] **Step 2: Fix Critical or Important feedback**

Any valid Critical or Important issue must be fixed with a focused regression test and re-run of relevant gates.

- [ ] **Step 3: Update implementation status**

Record RED/GREEN/gate/hygiene/review results in `docs/pcweb-088-desktop-rust-post-install-probe-approval-plan.md`.
