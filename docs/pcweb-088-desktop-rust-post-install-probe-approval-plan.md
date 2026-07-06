# PCWEB-088 Desktop Rust Post-Install Probe Approval Plan

## Goal

PCWEB-088 defines the approval boundary for the first Rust post-install probe after a user-run toolchain install. It records the future probe command allowlist, redaction rules, required approval tokens, expected report schema, and cargo-check blockers without running probes, installers, shell commands, Cargo, Tauri, audio, worker, secret, or remote-provider paths.

## Position In The PC Path

```text
PCWEB-084
  cargo check artifact, network, target, and cleanup policy

PCWEB-085
  Rust toolchain readiness and platform prerequisite probe boundary

PCWEB-086
  Rust toolchain installation decision boundary, no install

PCWEB-087
  Rust toolchain install approval packet, manual-user-run only

PCWEB-088
  Rust post-install probe approval packet, no probe execution
```

PCWEB-088 answers the next practical question: after a user manually installs Rust outside this repo, exactly what narrow read-only probe may be requested, what output must be redacted, and why does `cargo check` remain blocked? It still does not install Rust and does not run any probe command.

## Artifacts

PCWEB-088 adds:

- `code/desktop_tauri/rust-post-install-probe-approval.policy.json`
- `tools/desktop_rust_post_install_probe_approval.py`
- `tests/test_desktop_rust_post_install_probe_approval.py`
- `docs/pcweb-088-desktop-rust-post-install-probe-approval-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-088-desktop-rust-post-install-probe-approval.md`

It also updates README, Web README, desktop README, traceability matrix, acceptance matrix, privacy/data-flow, project structure, roadmap, decision log, and docs gate.

## Decision Boundary

Default mode:

- Generates a post-install probe approval packet only.
- Does not run `rustc --version`, `cargo --version`, `rustup --version`, `xcode-select -p`, shell commands, installers, package managers, Cargo, Tauri, Node, ASR workers, audio operations, secret reads, provider config reads, or remote calls.
- Does not inspect Cargo home, rustup home, PATH, shell profiles, package-manager state, dependency caches, `Cargo.lock`, target directories, bundles, installers, signing, or notarization artifacts.
- Does not read `configs/local/`, environment secrets, provider config, real audio, runtime sessions, or local user data.
- Reports `probe_approval_mode=no_probe_execution_approval_packet_only`, `probe_execution_status=not_run`, `external_command_execution_status=not_run`, `safe_to_run_post_install_probe_now=false`, and `safe_to_run_cargo_check_now=false`.

## Future Probe Allowlist

PCWEB-088 may record these future command texts as inert allowlist items:

- `rustc --version`
- `cargo --version`
- `rustup --version`
- `xcode-select -p` on macOS only, with `presence_only_no_path`

These strings are not executable in PCWEB-088. They exist so a future, separately approved probe increment can be checked against a fixed allowlist. The future macOS `xcode-select -p` result must remain presence-only and must not return the local developer tools path.

## Required Approval Tokens

Before any future post-install probe may execute, all tokens must be explicitly satisfied in a separate increment:

- `explicit_user_approval_for_post_install_probe`
- `rust_toolchain_install_completed_by_user`
- `approved_post_install_probe_command_allowlist`
- `approved_probe_output_redaction_policy`
- `approved_no_cargo_check_boundary_reconfirmed`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

These tokens are intentionally separate from PCWEB-087's installation tokens. Installing Rust manually does not automatically authorize this repo to run probes.

## Expected Probe Report Schema

The future probe result, if separately approved later, must report only bounded status fields:

- `rustc_status=available|missing|unexpected_error|not_run`
- `cargo_status=available|missing|unexpected_error|not_run`
- `rustup_status=available|missing|unexpected_error|not_run`
- `macos_xcode_select_status=available|missing|unexpected_error|not_run|not_applicable`
- `macos_xcode_select_path_status=path_present|path_missing|not_applicable|not_run`
- `first_cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`

It must not return raw local paths, shell profiles, Cargo home, rustup home, PATH contents, dependency cache contents, `Cargo.lock`, target files, credentials, provider config, or audio data.

## Cargo Check Remains Blocked

Even if a future post-install probe later reports `rustc`, `cargo`, and `rustup` as available, `cargo check` remains blocked until all of these are true:

- PCWEB-084 artifact policy is re-acknowledged.
- The first dependency-resolution network/cache policy is approved.
- `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target` is approved for generated target output.
- `Cargo.lock` generation/commit policy is approved.
- The user explicitly approves the first `cargo check`.
- No-audio/no-worker/no-secret/no-remote boundary is reconfirmed.

## Explicit Non-Goals

PCWEB-088 does not:

- Run `rustc`, `cargo`, `rustup`, `xcode-select`, `curl`, `sh`, `rustup-init`, `brew`, `xcode-select --install`, Visual Studio installers, WebView2 installers, `apt`, `dnf`, `yum`, `winget`, `scoop`, `choco`, `npm`, `pnpm`, `yarn`, `npx`, Tauri CLI, or shell commands.
- Install, update, uninstall, repair, probe, or validate Rust, rustup, Cargo, Xcode, Xcode Command Line Tools, Visual Studio Build Tools, WebView2, Linux system packages, Tauri CLI, Node, or frontend dependencies.
- Read PATH, shell profiles, Cargo home, rustup home, package-manager state, dependency caches, registry, keychain, credential stores, system settings, `Cargo.lock`, or target output.
- Generate `Cargo.lock`, target output, dependency caches, `node_modules`, dist, bundles, installers, signing, notarization, update, app-store, or mobile artifacts.
- Request permissions, enumerate devices, capture microphone/system audio, start ASR workers, read provider config, read secrets, read `configs/local/`, write runtime/audio/session data, or call remote ASR, LLM, or proxy providers.

## Acceptance

- Root tests prove `rust-post-install-probe-approval.policy.json` exists and records a no-execute post-install probe approval packet.
- Root tests prove the future probe command allowlist is exactly `rustc --version`, `cargo --version`, `rustup --version`, and macOS-only `xcode-select -p`.
- Root tests prove the tool returns `probe_execution_status=not_run`, `external_command_execution_status=not_run`, `safe_to_run_post_install_probe_now=false`, and `safe_to_run_cargo_check_now=false`.
- Root tests prove custom policy cannot add probe commands, relax safety flags, remove approval tokens, remove redaction requirements, remove forbidden default side effects, override top-level report identity/status, or mark cargo check as ready.
- Root tests prove custom policy paths under `configs/local`, local runtime data, outputs, temporary artifacts, or audio sample roots are blocked before file read, including outside-repo paths and mixed-case path components.
- Root tests prove the tool source has no command-execution entrypoints.
- Quality-gate tests prove default `pc-web` and `all-local` profiles still do not run Rust, Cargo, rustup, xcode-select, curl, package managers, Tauri, remote providers, or `configs/local/`.
- Documentation gate records PCWEB-088 in README, Web README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Implementation Status

Status: implemented with post-review hardening; final targeted re-review passed.

Verification:

- RED was confirmed with `python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py tests/test_quality_gate.py -q`; failures were the missing `rust-post-install-probe-approval.policy.json` and `desktop_rust_post_install_probe_approval.py`.
- Post-review hardening RED was confirmed with `python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py -q` (`2 failed, 6 passed`): custom policy could remove `forbidden_default_side_effects`, and mixed-case forbidden roots such as `CONFIGS/LOCAL` were not blocked before file read.
- Post-review hardening added case-insensitive forbidden path component matching and canonical `forbidden_default_side_effects` validation, with blocked/invalid policy reports returning trusted PCWEB-088 canonical lists instead of untrusted custom policy values.
- Post-review hardening GREEN passed with `python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py -q` (`8 passed`).
- Final re-review found one Important issue: invalid custom policies could still control top-level `pcweb_id`, `policy_name`, and `policy_status` in failed reports. A RED test reproduced this with `python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py -q` (`1 failed, 7 passed`).
- Final re-review fix made top-level report identity/status canonical for invalid reports and added `policy_name` validation. GREEN passed with `python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py -q` (`8 passed`).
- Final targeted re-review confirmed the Important issue is resolved and found no remaining Critical or Important issues.
- Focused post-review combined gate passed with `python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py tests/test_quality_gate.py -q` (`15 passed`).
- Fresh docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q` (`1 passed`).
- Fresh desktop/root regression passed with `python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_desktop_tauri_scaffold.py tests/test_quality_gate.py -q` (`61 passed`).
- Fresh `pc-web` gate passed with `python3 tools/run_quality_gate.py --profile pc-web`: root `61 passed`, core `34 passed`, Web backend `300 passed`, browser smoke `status=ok`.
- Fresh `all-local --no-browser` gate passed with `python3 tools/run_quality_gate.py --profile all-local --no-browser`: ASR runtime `65 passed`, ASR bake-off `18 passed`, root `61 passed`, core `34 passed`, Web backend `300 passed`.
- `python3 tools/desktop_rust_post_install_probe_approval.py` returned `policy_validation_status=passed`, `probe_approval_status=generated_for_manual_review`, `probe_approval_mode=no_probe_execution_approval_packet_only`, `probe_execution_status=not_run`, `external_command_execution_status=not_run`, `cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`, and all probe/build/fetch/artifact/read safety flags remained false.
- Hygiene checks found no targeted pytest caches, no `Cargo.lock`, target, `desktop_tauri_target`, `node_modules`, dist, bundle, installer, `.cargo`, or `.rustup` artifacts, no listeners on `8767` or `9223`, and no `sk-...` token hits outside excluded paths.
- PCWEB-088 did not run Rust probes, install Rust, modify shell profiles, read PATH/cargo home/rustup home, run cargo/Tauri/package managers, fetch dependencies, generate `Cargo.lock` or target output, read secrets, read `configs/local/`, inspect real audio, capture audio, spawn workers, or call remote providers.
