# PCWEB-089 Desktop Rust Post-Install Probe Result Intake Plan

## Goal

PCWEB-089 defines a no-command result-intake boundary for Rust post-install probe status. It accepts only caller-provided bounded JSON status fields shaped by PCWEB-088, validates and normalizes those fields, rejects raw command output or path-bearing data, and keeps `cargo check` blocked until PCWEB-084 and explicit user approval are satisfied.

## Position In The PC Path

```text
PCWEB-087
  Rust toolchain install approval packet, manual-user-run only

PCWEB-088
  Rust post-install probe approval packet, no probe execution

PCWEB-089
  Rust post-install probe result intake, caller_provided_json_only
```

PCWEB-089 does not run `rustc`, `cargo`, `rustup`, `xcode-select`, Cargo, Tauri, package managers, shell commands, audio, worker, secret, or remote-provider paths. It only validates a bounded status object that may have been produced manually or by a future separately approved probe increment.

## Artifacts

PCWEB-089 adds:

- `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json`
- `tools/desktop_rust_post_install_probe_result_intake.py`
- `tests/test_desktop_rust_post_install_probe_result_intake.py`
- `docs/pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-089-desktop-rust-post-install-probe-result-intake.md`

It also updates README, Web README, desktop README, traceability matrix, acceptance matrix, privacy/data-flow, project structure, roadmap, decision log, and docs gate.

## Decision Boundary

Default mode:

- Does not execute any post-install probe.
- Does not read PATH, shell profiles, Cargo home, rustup home, dependency caches, `Cargo.lock`, target output, provider config, secrets, `configs/local`, real audio, runtime sessions, or local user data.
- Does not accept raw stdout, stderr, command text, local paths, environment variables, shell profile names, home directories, credential material, provider config values, or dependency cache data.
- Accepts only bounded JSON fields:
  - `rustc_status`
  - `cargo_status`
  - `rustup_status`
  - `macos_xcode_select_status`
  - `macos_xcode_select_path_status`
  - `first_cargo_check_readiness`
- Reports `result_intake_mode=manual_result_validation_only`, `accepted_result_source=caller_provided_json_only`, `probe_execution_status=not_run`, `external_command_execution_status=not_run`, `safe_to_accept_raw_probe_output_now=false`, `safe_to_run_post_install_probe_now=false`, and `safe_to_run_cargo_check_now=false`.

## Allowed Result Status Values

`rustc_status`, `cargo_status`, and `rustup_status` may be:

- `available`
- `missing`
- `unexpected_error`
- `not_run`

`macos_xcode_select_status` may be:

- `available`
- `missing`
- `unexpected_error`
- `not_run`
- `not_applicable`

`macos_xcode_select_path_status` may be:

- `path_present`
- `path_missing`
- `not_applicable`
- `not_run`

`first_cargo_check_readiness` must remain:

- `blocked_until_pcweb_084_and_user_approval`

## Cargo Check Remains Blocked

Even when a caller-provided result says `rustc_status=available`, `cargo_status=available`, and `rustup_status=available`, PCWEB-089 must keep:

- `safe_to_run_cargo_check_now=false`
- `cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`
- `next_required_decision=explicit_first_cargo_check_approval_still_required`

PCWEB-089 validates readiness evidence; it does not authorize dependency resolution, network/cache use, `Cargo.lock` generation, target output, or any Cargo command.

## Explicit Non-Goals

PCWEB-089 does not:

- Run `rustc`, `cargo`, `rustup`, `xcode-select`, `curl`, `sh`, `rustup-init`, `brew`, `xcode-select --install`, Visual Studio installers, WebView2 installers, package managers, Tauri CLI, Node, npm/pnpm/yarn/npx, or shell commands.
- Parse raw stdout/stderr from probe commands.
- Store local developer tools paths, PATH, shell profiles, Cargo home, rustup home, target paths, dependency cache paths, provider config values, credentials, or audio data.
- Mark `cargo check` ready.
- Generate `Cargo.lock`, target output, dependency caches, `node_modules`, dist, bundles, installers, signing, notarization, update, app-store, or mobile artifacts.
- Request permissions, enumerate devices, capture microphone/system audio, start ASR workers, read provider config, read secrets, read `configs/local`, write runtime/audio/session data, or call remote ASR, LLM, or proxy providers.

## Acceptance

- Root tests prove `rust-post-install-probe-result-intake.policy.json` exists and records a no-command caller-provided result intake policy with `accepted_result_source=caller_provided_json_only`.
- Root tests prove the tool returns a no-result report by default without command execution.
- Root tests prove a valid bounded result is normalized and still keeps `safe_to_run_cargo_check_now=false`.
- Root tests prove raw output/path/command fields, unknown fields, invalid enum values, and any `first_cargo_check_readiness` value other than `blocked_until_pcweb_084_and_user_approval` are rejected.
- Root tests prove custom policy cannot relax safety flags, expand result fields, expand status enums, accept raw output fields, or override top-level report identity/status.
- Root tests prove result/policy paths under `configs/local`, local runtime data, outputs, temporary artifacts, or audio sample roots are blocked before file read, including outside-repo paths and mixed-case path components.
- Root tests prove the tool source has no command-execution entrypoints.
- Quality-gate tests prove default `pc-web` and `all-local` profiles still do not run Rust, Cargo, rustup, xcode-select, curl, package managers, Tauri, remote providers, or `configs/local`.
- Documentation gate records PCWEB-089 in README, Web README, desktop README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Implementation Status

Status: implemented.

RED evidence:

```text
python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py -q
10 failed, 1 warning
```

The expected RED failures were missing `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json` and missing `tools/desktop_rust_post_install_probe_result_intake.py`.

GREEN evidence:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py -q -p no:cacheprovider
11 passed, 1 warning

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py tests/test_desktop_rust_post_install_probe_approval.py tests/test_quality_gate.py -q -p no:cacheprovider
26 passed, 1 warning

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_scaffold.py tests/test_desktop_build_readiness_policy.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_desktop_rust_post_install_probe_approval.py tests/test_desktop_rust_post_install_probe_result_intake.py tests/test_quality_gate.py -q -p no:cacheprovider
72 passed, 1 warning

cd code/web_mvp/backend && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q -p no:cacheprovider
1 passed, 2 warnings
```

Quality gate evidence:

```text
python3 tools/run_quality_gate.py --profile pc-web
root 72 passed
core 34 passed
web backend 300 passed
browser smoke status ok
quality gate profile=pc-web passed

python3 tools/run_quality_gate.py --profile all-local --no-browser
ASR runtime 65 passed
ASR bake-off 18 passed
root 72 passed
core 34 passed
web backend 300 passed
quality gate profile=all-local passed
```

Implementation notes:

- `desktop_rust_post_install_probe_result_intake.py` has no command execution path and source scan blocks command-execution tokens.
- Policy and result paths under `configs/local`, `data/local_runtime`, `outputs`, `artifacts/tmp`, and `data/asr_eval/samples` are rejected before read, including mixed-case path components.
- Valid caller-provided bounded results can be normalized, but `cargo_check_readiness` remains `blocked_until_pcweb_084_and_user_approval`.
- Raw output/path/command/env/home/cache/provider config/secret-bearing result fields are rejected and not reflected back in the report.
- Post-review hardening adds coverage that invalid enum errors do not echo caller-provided raw/path/secret-like values, failed validation resets `normalized_probe_result` to the default safe result, and policy validation covers `next_required_decisions` plus `cargo_check_blockers`.
