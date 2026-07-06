# PCWEB-086 Desktop Rust Toolchain Installation Decision Plan

## Goal

PCWEB-086 defines a no-install Rust toolchain installation decision boundary after PCWEB-085 proved the current machine is missing `rustc`, `cargo`, and `rustup`. It records the recommended official install path, platform-specific decision points, approval requirements, and post-install verification order without executing installers, package managers, shell profile edits, Cargo, Tauri, audio, worker, secret, or remote-provider paths.

## Position In The PC Path

```text
PCWEB-084
  cargo check artifact, network, target, and cleanup policy

PCWEB-085
  Rust toolchain readiness and platform prerequisite probe boundary

PCWEB-086
  Rust toolchain installation decision boundary, no install
```

PCWEB-086 answers the next practical question: what would have to be true before this project is allowed to install or use a Rust toolchain for the first controlled desktop compile check? It does not install Rust. It only turns the installation decision into a machine-readable, reviewable report.

## Artifacts

PCWEB-086 adds:

- `code/desktop_tauri/rust-toolchain-installation.policy.json`
- `tools/desktop_rust_toolchain_installation_decision.py`
- `tests/test_desktop_rust_toolchain_installation_decision.py`
- `docs/pcweb-086-desktop-rust-toolchain-installation-decision-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-086-desktop-rust-toolchain-installation-decision.md`

The policy file records:

- `pcweb_id=PCWEB-086`
- `policy_status=rust_toolchain_installation_decision_policy_only`
- `installation_decision_mode=no_install_decision_report_only`
- `recommended_install_provider=official_rustup`
- `safe_to_install_toolchain_now=false`
- `safe_to_modify_shell_profile_now=false`
- `safe_to_run_install_command_now=false`
- `safe_to_run_rustup_now=false`
- `safe_to_run_cargo_check_now=false`
- `safe_to_fetch_dependencies_now=false`
- `safe_to_generate_cargo_lock_now=false`
- `safe_to_generate_target_dir_now=false`
- `safe_to_read_configs_local_now=false`
- approval tokens that must be present before any later install increment can execute
- platform notes for macOS, Windows, and Linux
- post-install verification order for a future approved install

## Official Source Notes

The policy references official Rust, rustup, Cargo, and Tauri documentation:

- [Install Rust](https://www.rust-lang.org/tools/install)
- [rustup book](https://rust-lang.github.io/rustup/)
- [Cargo check command](https://doc.rust-lang.org/cargo/commands/cargo-check.html)
- [Cargo build cache](https://doc.rust-lang.org/cargo/reference/build-cache.html)
- [Tauri v2 prerequisites](https://v2.tauri.app/start/prerequisites/)

These sources anchor the future install/build decision. PCWEB-086 does not execute installation instructions from them and does not fetch dependency crates.

## Decision Boundary

Default mode:

- Does not run external commands.
- Does not install Rust, rustup, Xcode Command Line Tools, Visual Studio Build Tools, system packages, Tauri CLI, npm/pnpm/yarn packages, or frontend dependencies.
- Does not modify shell profiles, PATH, cargo home, rustup home, caches, login shells, system settings, or package-manager state.
- Does not read `configs/local/`, environment secrets, provider config, keychain, or secret adapters.
- Does not inspect real audio or runtime session data.
- Reports `installation_execution_status=not_run` and `installation_decision_status=blocked_requires_explicit_user_approval`.

Future install approval must be explicit and separate from general autonomy. The required approval tokens are:

- `explicit_user_approval_for_rust_toolchain_install`
- `approved_install_provider_official_rustup`
- `approved_shell_profile_modification_policy`
- `approved_network_download_policy_for_rustup`
- `approved_post_install_probe_policy`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

The future post-install probe sequence remains:

1. `rustc --version`
2. `cargo --version`
3. `rustup --version`
4. `xcode-select -p` on macOS, redacted to presence only
5. PCWEB-084 first cargo-check preflight review

## Explicit Non-Goals

PCWEB-086 does not:

- Run `curl`, `sh`, `rustup-init`, `rustup`, `cargo`, `brew`, `xcode-select --install`, Visual Studio installers, `apt`, `dnf`, `yum`, `winget`, `scoop`, `choco`, `npm`, `pnpm`, `yarn`, `npx`, Tauri CLI, or shell commands.
- Install or update Rust, rustup, Cargo, Xcode Command Line Tools, Visual Studio Build Tools, system packages, Tauri CLI, npm packages, or frontend dependencies.
- Modify `.zshrc`, `.bashrc`, `.bash_profile`, `.profile`, PATH, cargo home, rustup home, shell startup files, launch agents, registry, keychain, credential stores, or system settings.
- Generate `Cargo.lock`, target output, dependency caches, node modules, dist, bundle, installer, signing, notarization, update, or app-store artifacts.
- Request permissions, enumerate devices, capture microphone/system audio, start ASR workers, read provider config, read secrets, read `configs/local/`, write runtime/audio/session data, or call remote ASR/LLM.

## Future Preconditions

The first real Rust toolchain install remains blocked until all of these are true:

- `explicit_user_approval_for_rust_toolchain_install`
- `approved_install_provider_official_rustup`
- `approved_shell_profile_modification_policy`
- `approved_network_download_policy_for_rustup`
- `approved_post_install_probe_policy`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

The first real `cargo check` remains separately blocked after install until PCWEB-084 and PCWEB-085 preconditions are true and the user explicitly approves the first compile/dependency-resolution run.

## Acceptance

- Root tests prove `rust-toolchain-installation.policy.json` exists and keeps install/build/dependency/artifact/secret/audio side effects blocked.
- Root tests prove `desktop_rust_toolchain_installation_decision.py` is static and contains no subprocess or command-execution entrypoints.
- Root tests prove custom policy cannot relax PCWEB-086 safety flags or required approval tokens.
- Root tests prove custom policy paths under `configs/local`, local runtime data, outputs, temporary artifacts, or audio sample roots are blocked before file read.
- Root tests prove the report exposes official-source references, approval requirements, platform notes, and post-install verification order without executable command readiness.
- Root tests prove PCWEB-086 remains in the root pytest quality gate and default quality gate still does not run installers, Rust, Cargo, Tauri, npm/pnpm/yarn/npx, remote providers, or `configs/local/`.
- Documentation gate records PCWEB-086 in README, Web README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Implementation Status

Status: completed for the no-install Rust toolchain installation decision boundary.

Verification:

- RED was confirmed with `python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py tests/test_quality_gate.py -q`; failures were the missing `rust-toolchain-installation.policy.json` and `desktop_rust_toolchain_installation_decision.py`.
- Focused GREEN passed with `python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py tests/test_quality_gate.py -q`.
- Docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`.
- Combined desktop/root regression passed with `python3 -m pytest tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q`.
- `python3 tools/desktop_rust_toolchain_installation_decision.py` returned `policy_validation_status=passed`, `installation_execution_status=not_run`, `external_command_execution_status=not_run`, `installation_decision_status=blocked_requires_explicit_user_approval`, `recommended_install_provider=official_rustup`, and all install/build/fetch/artifact safety flags remained false.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with root, core, Web backend, and browser smoke checks.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with ASR runtime, ASR bake-off, root, core, and Web backend checks.
- Post-review hardening added regression coverage that custom `--policy` paths under `configs/local` are blocked before `read_text()`, and aligned the source-level no-command-execution scan with the implementation plan by checking `run(` in addition to subprocess and shell execution tokens.
- PCWEB-086 did not install Rust, modify shell profiles, run curl/sh/rustup/cargo/Tauri/package managers, fetch dependencies, generate `Cargo.lock` or target output, read secrets, read `configs/local/`, inspect real audio, capture audio, spawn workers, or call remote providers.
