# PCWEB-090 Desktop First Cargo Check Execution Boundary Plan

## Goal

PCWEB-090 bridges PCWEB-084 cargo-check artifact policy and PCWEB-089 Rust post-install probe result intake into a no-command first `cargo check` execution boundary. It can generate a manual execution packet only when bounded toolchain status is acceptable and artifact policy is valid, while keeping `safe_to_run_cargo_check_now=false` and never running Cargo itself.

## Position In The PC Path

```text
PCWEB-084
  Cargo check artifact policy: command, CARGO_TARGET_DIR, Cargo.lock and cleanup policy

PCWEB-089
  Rust post-install probe result intake: caller_provided_json_only bounded toolchain status

PCWEB-090
  First cargo check execution boundary: manual execution packet only, no Cargo execution
```

PCWEB-090 is intentionally not a build step. It converts already documented preconditions into a machine-readable boundary report so a later explicitly approved step can run the exact command with the exact environment and artifact policy.

## Artifacts

PCWEB-090 adds:

- `code/desktop_tauri/first-cargo-check-execution.policy.json`
- `tools/desktop_first_cargo_check_execution_boundary.py`
- `tests/test_desktop_first_cargo_check_execution_boundary.py`
- `docs/pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-090-desktop-first-cargo-check-execution-boundary.md`

It also updates README, Web README, desktop README, traceability matrix, acceptance matrix, privacy/data-flow, project structure, roadmap, decision log, current status, progress report, and docs gate.

## Boundary

Default mode:

- Does not execute `cargo check`.
- Does not execute `rustc`, `cargo`, `rustup`, `xcode-select`, Tauri, Node, npm/pnpm/yarn/npx, package managers, shell commands, audio, worker, secret, or remote-provider paths.
- Does not install Rust, fetch dependencies, generate `Cargo.lock`, create target output, or clean artifacts.
- Does not read PATH, shell profiles, Cargo home, rustup home, dependency caches, provider config, secrets, `configs/local`, real audio, runtime sessions, or local user data.
- Reads only policy JSON files and optional caller-provided bounded PCWEB-089 result JSON.
- Rejects policy/result paths under `configs/local`, `data/local_runtime`, `outputs`, `artifacts/tmp`, and `data/asr_eval/samples` before file read, including mixed-case path components.

## Manual Execution Packet

When PCWEB-084 policy validation passes and PCWEB-089 normalized result proves `rustc`, `cargo`, and `rustup` are `available`, PCWEB-090 may return:

```text
execution_packet_status=ready_for_explicit_user_approval
manual_execution_packet.command=cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
manual_execution_packet.env.CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target
```

Even then:

- `cargo_check_execution_status=not_run`
- `external_command_execution_status=not_run`
- `safe_to_run_cargo_check_now=false`
- `safe_to_fetch_dependencies_now=false`
- `safe_to_generate_cargo_lock_now=false`
- `safe_to_generate_target_dir_now=false`

The packet is a precise, auditable handoff. It is not an in-process approval to run the command.

## Acceptance

- Root tests prove the PCWEB-090 policy exists and records `execution_boundary_mode=explicit_manual_execution_packet_only`.
- Root tests prove the tool has no external command execution entrypoints.
- Root tests prove default report is blocked when no PCWEB-089 result is provided.
- Root tests prove valid bounded PCWEB-089 status plus valid PCWEB-084 policy yields `ready_for_explicit_user_approval` while all run/fetch/artifact safety flags remain false.
- Root tests prove missing or invalid toolchain status blocks the packet.
- Root tests prove invalid caller-provided raw/path/secret-like result values are rejected without echoing sensitive values.
- Root tests prove custom PCWEB-090 policy cannot alter top-level identity, command, env, artifacts, preconditions, or safety flags.
- Root tests prove custom PCWEB-084 policy drift blocks the packet.
- Root tests prove forbidden policy/result paths are blocked before read.
- Documentation gate records PCWEB-090 in README, Web README, desktop README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, current status, progress report, this plan, and the implementation plan.

## Implementation Status

Status: implemented.

RED evidence:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_first_cargo_check_execution_boundary.py -q -p no:cacheprovider
9 failed, 1 warning
```

The expected RED failures were missing `code/desktop_tauri/first-cargo-check-execution.policy.json` and missing `tools/desktop_first_cargo_check_execution_boundary.py`.

GREEN evidence:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_first_cargo_check_execution_boundary.py -q -p no:cacheprovider
11 passed, 1 warning

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_first_cargo_check_execution_boundary.py tests/test_desktop_rust_post_install_probe_result_intake.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_quality_gate.py -q -p no:cacheprovider
37 passed, 1 warning

cd code/web_mvp/backend && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q -p no:cacheprovider
1 passed, 2 warnings
```

Quality gate evidence:

```text
python3 tools/run_quality_gate.py --profile pc-web
root 83 passed
core 34 passed
web backend 300 passed
browser smoke status ok
quality gate profile=pc-web passed

python3 tools/run_quality_gate.py --profile all-local --no-browser
ASR runtime 65 passed
ASR bake-off 18 passed
root 83 passed
core 34 passed
web backend 300 passed
quality gate profile=all-local passed
```

Implementation notes:

- Valid PCWEB-089 bounded result plus valid PCWEB-084 policy returns only `ready_for_explicit_user_approval`; it does not run Cargo.
- `safe_to_run_cargo_check_now=false`, `safe_to_fetch_dependencies_now=false`, `safe_to_generate_cargo_lock_now=false`, and `safe_to_generate_target_dir_now=false` remain false in every report state.
- Forbidden paths are blocked before read, including mixed-case `CONFIGS/LOCAL` and `DATA/ASR_EVAL/SAMPLES`.
- Invalid raw/path/secret-like toolchain result values are rejected without echoing the caller-provided value.
- Post-review hardening adds coverage that unknown result field names containing path/secret-like content do not leak through delegated PCWEB-089 validation errors.
- Post-review path coverage exercises all forbidden path labels against `policy_path`, `artifact_policy_path`, `probe_result_intake_policy_path`, and `probe_result_path`.
