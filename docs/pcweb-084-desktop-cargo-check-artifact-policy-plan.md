# PCWEB-084 Desktop Cargo Check Artifact Policy Plan

## Goal

PCWEB-084 decides the artifact and execution boundary for the first future desktop `cargo check` without running Cargo yet. It converts the remaining PCWEB-083 preconditions into a machine-readable policy and a no-run report so the next desktop increment can attempt a controlled compile check instead of an ad hoc build.

## Position In The PC Path

```text
PCWEB-082
  static Tauri shell scaffold

PCWEB-083
  build readiness policy and rustc/cargo version probe only

PCWEB-084
  cargo check artifact policy, schema validation, and no-run execution plan
```

PCWEB-084 is still a policy boundary, but it resolves several previously undecided choices:

- `Cargo.lock` policy: for the desktop app, the first explicitly approved dependency-resolution run may generate `code/desktop_tauri/src-tauri/Cargo.lock`; once generated, it should be committed as a reproducibility artifact for the executable app.
- target directory policy: future Cargo output must use `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`, which is under the already ignored `artifacts/tmp/` area, instead of writing `code/desktop_tauri/src-tauri/target`.
- dependency fetch policy: default remains no network fetch; a first dependency-resolution run may fetch crates only after explicit approval. Repeat checks should prefer locked/offline mode once `Cargo.lock` exists and dependencies are cached.
- cleanup policy: `artifacts/tmp/desktop_tauri_target` is disposable and may be removed after checks; source, policy files, docs, and `Cargo.lock` must not be removed by cleanup.
- side-effect policy: no audio permission, no audio device enumeration, no microphone/system audio capture, no ASR worker spawn, no provider config/secret read, no `configs/local/` read, no remote ASR/LLM call, no Tauri dev/build/package/sign/notarize.

## Artifacts

PCWEB-084 adds:

- `code/desktop_tauri/cargo-check.policy.json`
- `tools/desktop_cargo_check_policy.py`
- `tests/test_desktop_cargo_check_artifact_policy.py`
- `docs/pcweb-084-desktop-cargo-check-artifact-policy-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-084-desktop-cargo-check-artifact-policy.md`

The policy file records:

- `pcweb_id=PCWEB-084`
- `policy_status=cargo_check_artifact_policy_only`
- `safe_to_run_cargo_check_now=false`
- `safe_to_install_toolchain_now=false`
- `safe_to_fetch_dependencies_now=false`
- `safe_to_generate_cargo_lock_now=false`
- `safe_to_generate_target_dir_now=false`
- `cargo_lock_policy_status=decided_not_generated`
- `cargo_target_dir_policy_status=decided_not_created`
- `network_dependency_fetch_policy_status=blocked_by_default`
- `cleanup_policy_status=decided_not_executed`
- future first approved command: `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`
- future repeat command after lock/cache exist: `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml --locked --offline`
- required future environment: `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`

## Official Source Notes

The policy references official Cargo/Tauri documentation for the future build increment:

- [Cargo check command](https://doc.rust-lang.org/cargo/commands/cargo-check.html)
- [Cargo build cache](https://doc.rust-lang.org/cargo/reference/build-cache.html)
- [Cargo environment variables](https://doc.rust-lang.org/cargo/reference/environment-variables.html)
- [Cargo.toml vs Cargo.lock](https://doc.rust-lang.org/cargo/guide/cargo-toml-vs-cargo-lock.html)
- [Tauri v2 prerequisites](https://v2.tauri.app/start/prerequisites/)

PCWEB-084 does not run any command from those pages. They anchor the policy choices before a later increment changes execution behavior.

## Explicit Non-Goals

PCWEB-084 does not:

- Install Rust, Cargo, system packages, npm packages, Tauri CLI, or frontend dependencies.
- Run `cargo check`, `cargo build`, `cargo tauri dev`, `cargo tauri build`, `npm install`, `npm ci`, `pnpm install`, `yarn install`, `npx tauri`, or any package/build command.
- Generate `Cargo.lock`, `target`, `node_modules`, lock files, dist, bundle, installer, signing, notarization, update, or app-store artifacts.
- Request permissions, enumerate devices, capture microphone/system audio, start ASR workers, read provider config, read secrets, read `configs/local/`, write runtime/audio/session data, or call remote ASR/LLM.

## No-Run Report

`tools/desktop_cargo_check_policy.py` returns a static/read-only report. It validates policy shape, confirms the future commands and environment, checks whether the current expected `Cargo.lock` and target directories exist, and reports:

- `report_mode=cargo_check_policy_static_report`
- `cargo_check_execution_status=not_run`
- `policy_validation_status=passed` or `failed`
- `cargo_lock_exists`
- `approved_target_dir_exists`
- `forbidden_in_source_target_dir_exists`
- `safe_to_run_cargo_check_now=false`
- `first_approved_cargo_check_plan.status=blocked_until_explicit_approval_and_toolchain`
- `repeat_locked_offline_check_plan.status=blocked_until_cargo_lock_and_cache_exist`

The tool must not spawn external commands and must not read `configs/local/` or secrets.

## Future Preconditions

The first real `cargo check` remains blocked until all of these are true:

- `explicit_user_approval_for_first_cargo_check`
- `rust_toolchain_available`
- `first_dependency_resolution_network_fetch_approved_or_cache_preseeded`
- `cargo_lock_policy_acknowledged`
- `cargo_target_dir_policy_acknowledged`
- `cleanup_policy_acknowledged`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

After a lockfile exists and dependencies are cached, repeat checks should use the locked/offline command and the same `CARGO_TARGET_DIR`.

## Acceptance

- Root tests prove `cargo-check.policy.json` exists and records the decided lockfile, target directory, network, cleanup, command, environment, and side-effect boundaries.
- Root tests prove `desktop_cargo_check_policy.py` does not run external commands and reports current artifact existence read-only.
- Root tests prove malformed policy command shapes fail validation instead of producing a ready report.
- Root tests prove PCWEB-084 remains in the root pytest quality gate and default quality gate still does not run Cargo, Tauri, npm/pnpm/yarn/npx, remote providers, or `configs/local/`.
- Documentation gate records PCWEB-084 in README, Web README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Implementation Status

Status: completed for the no-run cargo-check artifact policy boundary.

Verification:

- RED was confirmed with `python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py tests/test_quality_gate.py -q`; failures were the missing `cargo-check.policy.json` and `desktop_cargo_check_policy.py`.
- Focused GREEN passed with `python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py tests/test_quality_gate.py -q`.
- Docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`.
- `python3 tools/desktop_cargo_check_policy.py` returned `policy_validation_status=passed`, `cargo_check_execution_status=not_run`, `external_command_execution_status=not_run`, `cargo_lock_exists=false`, `approved_target_dir_exists=false`, and `forbidden_in_source_target_dir_exists=false`.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with 29 root tests, 34 core tests, 300 Web backend tests, and browser smoke `status=ok`.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with 65 ASR runtime tests, 18 ASR bake-off tests, 29 root tests, 34 core tests, and 300 Web backend tests.
- Post-review hardening added a source-level guard that forbids external execution entrypoints in `tools/desktop_cargo_check_policy.py`; `python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py -q` passed with 8 tests.
- The policy and tool remain no-run by default; they do not install Rust, run Cargo/Tauri/package managers, fetch dependencies, generate `Cargo.lock` or target output, read secrets, read `configs/local/`, capture audio, spawn workers, or call remote providers.
