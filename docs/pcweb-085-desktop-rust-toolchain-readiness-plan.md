# PCWEB-085 Desktop Rust Toolchain Readiness Plan

## Goal

PCWEB-085 defines a no-install Rust toolchain readiness boundary before the first future desktop `cargo check`. It can produce a static report by default and an explicitly requested local toolchain probe, while still keeping Cargo execution, dependency fetch, artifact generation, shell-profile edits, audio, worker, secrets, and remote providers blocked.

## Position In The PC Path

```text
PCWEB-083
  rustc/cargo version probe only

PCWEB-084
  cargo check artifact, network, target, and cleanup policy

PCWEB-085
  Rust toolchain readiness and platform prerequisite probe boundary
```

PCWEB-085 answers the next practical question: is this machine close enough to attempt the first controlled `cargo check` later? It does not install Rust or run Cargo. It only records the expected toolchain prerequisites and, when explicitly requested, probes a strict command allowlist.

## Artifacts

PCWEB-085 adds:

- `code/desktop_tauri/rust-toolchain-readiness.policy.json`
- `tools/desktop_rust_toolchain_readiness.py`
- `tests/test_desktop_rust_toolchain_readiness.py`
- `docs/pcweb-085-desktop-rust-toolchain-readiness-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-085-desktop-rust-toolchain-readiness.md`

The policy file records:

- `pcweb_id=PCWEB-085`
- `policy_status=rust_toolchain_readiness_policy_only`
- `toolchain_probe_mode=local_version_and_platform_probe_only`
- `safe_to_install_toolchain_now=false`
- `safe_to_modify_shell_profile_now=false`
- `safe_to_run_cargo_check_now=false`
- `safe_to_fetch_dependencies_now=false`
- `safe_to_generate_cargo_lock_now=false`
- `safe_to_generate_target_dir_now=false`
- allowed probe commands: `rustc --version`, `cargo --version`, `rustup --version`, and `xcode-select -p`
- `xcode-select -p` output redaction policy: `presence_only`; report only present/missing, never the raw local path

## Official Source Notes

The policy references official Rust, rustup, Cargo, and Tauri documentation:

- [Install Rust](https://www.rust-lang.org/tools/install)
- [rustup components](https://rust-lang.github.io/rustup/concepts/components.html)
- [Cargo check command](https://doc.rust-lang.org/cargo/commands/cargo-check.html)
- [Cargo build cache](https://doc.rust-lang.org/cargo/reference/build-cache.html)
- [Tauri v2 prerequisites](https://v2.tauri.app/start/prerequisites/)

These sources anchor the future install/build decision. PCWEB-085 does not execute installation instructions from them.

## Probe Boundary

Default mode:

- Does not run external commands.
- Does not read `configs/local/`, environment secrets, provider config, keychain, or secret adapters.
- Does not inspect real audio or runtime session data.
- Reports `toolchain_probe_status=not_run` and `first_cargo_check_readiness_status=not_evaluated`.

Explicit probe mode:

- May run only the hard-coded allowlist:
  - `rustc --version`
  - `cargo --version`
  - `rustup --version`
  - `xcode-select -p`
- Must reject custom policy attempts to add `cargo check`, `cargo build`, `rustup update`, install scripts, shell commands, or package managers.
- Must sanitize `xcode-select -p` output to `path_present` or `path_missing`; it must not return the local developer tools path.
- Must still report `safe_to_run_cargo_check_now=false`.

## Explicit Non-Goals

PCWEB-085 does not:

- Install Rust, rustup, Xcode Command Line Tools, Visual Studio Build Tools, Tauri CLI, npm/pnpm/yarn packages, or system packages.
- Run `cargo check`, `cargo build`, `cargo tauri dev`, `cargo tauri build`, `rustup update`, `rustup toolchain install`, install scripts, package managers, or shell-profile modifications.
- Generate `Cargo.lock`, target output, dependency caches, node modules, dist, bundle, installer, signing, notarization, update, or app-store artifacts.
- Request permissions, enumerate devices, capture microphone/system audio, start ASR workers, read provider config, read secrets, read `configs/local/`, write runtime/audio/session data, or call remote ASR/LLM.

## Future Preconditions

The first real `cargo check` remains blocked until all of these are true:

- `explicit_user_approval_for_first_cargo_check`
- `rustc_available`
- `cargo_available`
- `macos_command_line_tools_available_or_non_macos_equivalent`
- `pcweb_084_artifact_policy_acknowledged`
- `first_dependency_resolution_network_fetch_approved_or_cache_preseeded`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

`rustup` is recommended for toolchain management but not the sole possible way to have `rustc` and `cargo`; missing `rustup` should be reported as a management warning, not the only hard blocker.

## Acceptance

- Root tests prove `rust-toolchain-readiness.policy.json` exists and keeps install/build/dependency/artifact/secret/audio side effects blocked.
- Root tests prove `desktop_rust_toolchain_readiness.py` is static by default and does not run external commands unless `probe_local_toolchain=True`.
- Root tests prove optional probe mode only runs the hard-coded allowlist and redacts `xcode-select -p` stdout/stderr path output.
- Root tests prove custom policy cannot expand the executable probe allowlist or relax PCWEB-085 safety flags.
- Root tests prove missing executables are reported without traceback.
- Documentation gate records PCWEB-085 in README, Web README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Implementation Status

Status: completed for the no-install Rust toolchain readiness boundary.

Verification:

- RED was confirmed with `python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py tests/test_quality_gate.py -q`; failures were the missing `rust-toolchain-readiness.policy.json` and `desktop_rust_toolchain_readiness.py`.
- Focused GREEN passed with `python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py tests/test_quality_gate.py -q`.
- Docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`.
- Combined desktop/root regression passed with `python3 -m pytest tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q`.
- `python3 tools/desktop_rust_toolchain_readiness.py` returned `policy_validation_status=passed`, `toolchain_probe_status=not_run`, `first_cargo_check_readiness_status=not_evaluated`, and all install/build/fetch/artifact safety flags remained false.
- `python3 tools/desktop_rust_toolchain_readiness.py --probe-local-toolchain` returned `rustc_status=missing`, `cargo_status=missing`, `rustup_status=missing`, `macos_command_line_tools_status=available`, `first_cargo_check_blocker=missing_required_rust_toolchain`, and `safe_to_run_cargo_check_now=false`; `xcode-select -p` output was redacted to `presence_only` status instead of exposing the local path.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with root, core, Web backend, and browser smoke checks.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with ASR runtime, ASR bake-off, root, core, and Web backend checks.
- Post-review hardening added regression coverage that custom policies cannot flip any PCWEB-085 install/build/Tauri/fetch/artifact/config-local safety flag to true, and that `xcode-select -p` path-like stderr is also redacted before reports are serialized.
- PCWEB-085 did not install Rust, modify shell profiles, run Cargo/Tauri/package managers, fetch dependencies, generate `Cargo.lock` or target output, read secrets, read `configs/local/`, inspect real audio, capture audio, spawn workers, or call remote providers.
