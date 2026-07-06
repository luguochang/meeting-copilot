# PCWEB-087 Desktop Rust Toolchain Install Approval Packet Plan

## Goal

PCWEB-087 turns the PCWEB-086 Rust toolchain installation decision into a static, human-reviewable approval packet. It records official-source manual install instructions, approval tokens, platform risks, rollback notes, and post-install checks without running installers, package managers, shells, Cargo, Tauri, audio, worker, secret, or remote-provider paths.

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
```

PCWEB-087 answers the next practical question: if the user later decides to install Rust, exactly what instructions, risks, approvals, and verification order must be reviewed first? It still does not install Rust. It only creates a reproducible approval packet.

## Artifacts

PCWEB-087 adds:

- `code/desktop_tauri/rust-toolchain-install-approval.policy.json`
- `tools/desktop_rust_toolchain_install_approval_packet.py`
- `tests/test_desktop_rust_toolchain_install_approval_packet.py`
- `docs/pcweb-087-desktop-rust-toolchain-install-approval-packet-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-087-desktop-rust-toolchain-install-approval-packet.md`

It also updates the README, Web README, desktop README, traceability matrix, acceptance matrix, privacy/data-flow, project structure, roadmap, decision log, docs gate, and root quality-gate coverage.

## Official Source Notes

The packet records current official-source facts checked on 2026-07-02:

- Rust official install page recommends `rustup`; macOS, Linux, and WSL use manual command text `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`, while Windows downloads and runs `rustup-init.exe`.
- Rust official install notes say Windows additionally needs MSVC build tools, and Rust tools live under Cargo's bin directory (`~/.cargo/bin` on Unix, `%USERPROFILE%\.cargo\bin` on Windows); PATH may require a new shell/session.
- The rustup book says rustup installs `rustc`, `cargo`, and `rustup`, and notes `rustup self uninstall` for removal.
- Tauri v2 prerequisites say macOS desktop-only development can use Xcode Command Line Tools, Windows development requires Microsoft C++ Build Tools and Microsoft Edge WebView2, and Linux development requires distribution-specific system packages.

Source URLs:

- <https://www.rust-lang.org/tools/install>
- <https://rust-lang.github.io/rustup/installation/index.html>
- <https://v2.tauri.app/start/prerequisites/>

## Decision Boundary

Default mode:

- Generates an approval packet only.
- Does not execute any command, installer, shell, package manager, Cargo, Tauri, Node, ASR worker, audio operation, secret read, provider config read, or remote call.
- Does not modify shell profiles, PATH, Cargo home, rustup home, login shells, keychain, registry, system settings, package-manager state, caches, lockfiles, target directories, bundles, installers, signing, or notarization artifacts.
- Does not read `configs/local/`, environment secrets, provider config, real audio, runtime sessions, or local user data.
- Reports `execution_mode=manual_user_run_only`, `command_execution_status=not_run`, `installation_execution_status=not_run`, and `safe_to_execute_install_now=false`.

## Manual Instruction Text Boundary

PCWEB-087 permits install command strings only as inert text fields in the policy and report:

- `manual_instruction_text_by_platform.macos.manual_command_text`
- `manual_instruction_text_by_platform.linux.manual_command_text`
- `manual_instruction_text_by_platform.windows.manual_installer_text`
- `manual_instruction_text_by_platform.windows.manual_optional_winget_text`

These fields are for human review only. The Python tool must treat them as strings, not commands. The tool source must not contain `subprocess`, `os.system`, `Popen`, `check_call`, `check_output`, `run(`, `exec(`, or `eval(`.

## Approval Tokens

The first real Rust toolchain install remains blocked until all tokens are explicitly approved in a future separate increment:

- `explicit_user_approval_for_rust_toolchain_install`
- `approved_install_provider_official_rustup`
- `approved_shell_profile_modification_policy`
- `approved_network_download_policy_for_rustup`
- `approved_post_install_probe_policy`
- `no_audio_worker_secret_remote_boundary_reconfirmed`
- `approved_manual_user_run_only_boundary`
- `approved_rustup_uninstall_or_rollback_understanding`

The final two PCWEB-087 tokens tighten PCWEB-086 by making the manual-run boundary and rollback awareness explicit.

## Platform Notes

macOS:

- Current local PCWEB-085 probe found Rust tools missing and Xcode Command Line Tools available.
- The future manual install source is official rustup.
- The install may edit shell/PATH state or require a new terminal session.
- No automated install is allowed from this repo.

Windows:

- Future work must account for `rustup-init.exe`, MSVC Build Tools, the MSVC default host triple, and WebView2.
- Any `winget` command remains manual text only and is not a default recommendation for this repo automation.
- Windows packaging and installer validation remain later work.

Linux:

- Future work must account for distribution-specific WebKit/system packages before Tauri dev/build.
- Package-manager commands remain manual text only and out of scope for default gates.

## Rollback And Recovery Notes

The approval packet records:

- `manual_rustup_self_uninstall_only`: Rust removal is generally via `rustup self uninstall`, but this project must not run it automatically.
- `manual_path_recovery_review_required`: PATH/shell profile recovery must be manual and reviewed before any automated edit is considered.
- If a future install fails, the safe response is to stop, collect redacted version/probe output, and avoid cargo/Tauri runs until PCWEB-084 and PCWEB-085 are revalidated.

## Explicit Non-Goals

PCWEB-087 does not:

- Run `curl`, `sh`, `rustup-init`, `rustup`, `cargo`, `brew`, `xcode-select --install`, Visual Studio installers, WebView2 installers, `apt`, `dnf`, `yum`, `winget`, `scoop`, `choco`, `npm`, `pnpm`, `yarn`, `npx`, Tauri CLI, or shell commands.
- Install, update, uninstall, or repair Rust, rustup, Cargo, Xcode, Xcode Command Line Tools, Visual Studio Build Tools, WebView2, Linux system packages, Tauri CLI, Node, or frontend dependencies.
- Modify `.zshrc`, `.bashrc`, `.bash_profile`, `.profile`, PATH, Cargo home, rustup home, login shell settings, registry, keychain, credential stores, system settings, or package-manager state.
- Generate `Cargo.lock`, target output, dependency caches, `node_modules`, dist, bundles, installers, signing, notarization, update, app-store, or mobile artifacts.
- Request permissions, enumerate devices, capture microphone/system audio, start ASR workers, read provider config, read secrets, read `configs/local/`, write runtime/audio/session data, or call remote ASR, LLM, or proxy providers.

## Acceptance

- Root tests prove `rust-toolchain-install-approval.policy.json` exists and records official-source manual instructions, platform notes, approval tokens, risk notes, rollback notes, and post-install checks.
- Root tests prove all manual commands remain inert text and the report returns `execution_mode=manual_user_run_only`, `command_execution_status=not_run`, `installation_execution_status=not_run`, and `safe_to_execute_install_now=false`.
- Root tests prove custom policy cannot relax safety flags, remove required approval tokens, remove official sources, or change command text into an executable mode.
- Root tests prove custom policy paths under `configs/local`, local runtime data, outputs, temporary artifacts, or audio sample roots are blocked before file read.
- Root tests prove the tool source has no command-execution entrypoints.
- Quality-gate tests prove default `pc-web` and `all-local` profiles still do not run Cargo, rustup, curl, package managers, Tauri, Node package managers, remote providers, or `configs/local/`.
- Documentation gate records PCWEB-087 in README, Web README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Implementation Status

Status: implemented with post-review hardening.

Verification:

- RED was confirmed with `python3 -m pytest tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_quality_gate.py -q`; failures were the missing `rust-toolchain-install-approval.policy.json` and `desktop_rust_toolchain_install_approval_packet.py`.
- Focused GREEN passed with `python3 -m pytest tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_quality_gate.py -q` (`13 passed`).
- Docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q` (`1 passed`).
- Desktop/root regression passed with `python3 -m pytest tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q` (`45 passed`).
- `python3 tools/desktop_rust_toolchain_install_approval_packet.py` returned `policy_validation_status=passed`, `approval_packet_status=generated_for_manual_review`, `execution_mode=manual_user_run_only`, `manual_instruction_text_status=inert_text_only`, `command_execution_status=not_run`, `installation_execution_status=not_run`, and all install/build/fetch/artifact safety flags remained false.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with root, core, Web backend, and browser smoke checks.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with ASR runtime, ASR bake-off, root, core, and Web backend checks.
- Hygiene checks found no targeted pytest caches, no `Cargo.lock`, target, `desktop_tauri_target`, `node_modules`, dist, bundle, installer, `.cargo`, or `.rustup` artifacts, no listeners on `8767` or `9223`, and no `sk-...` token hits outside excluded paths.
- PCWEB-087 did not install Rust, modify shell profiles, run curl/sh/rustup/cargo/Tauri/package managers, fetch dependencies, generate `Cargo.lock` or target output, read secrets, read `configs/local/`, inspect real audio, capture audio, spawn workers, or call remote providers.

Post-review hardening:

- Reviewer found the custom `--policy` path guard only blocked forbidden roots after resolving paths relative to `REPO_ROOT`; this could allow outside-repo paths such as external `configs/local`. The tool now checks both original path parts and resolved path parts before any file read, independent of repository containment.
- Reviewer found invalid custom policies could still echo untrusted `manual_instruction_text_by_platform` or remove `official_sources` from the blocked report. The tool now emits canonical trusted official sources and manual instruction text when validation fails, while keeping `approval_packet_status=blocked_by_policy_validation`.
- Regression coverage now checks all five forbidden policy roots (`configs/local`, `data/local_runtime`, `outputs`, `artifacts/tmp`, `data/asr_eval/samples`) plus an outside-repo forbidden path before read, and checks invalid custom policy output cannot lose canonical official URLs or surface executable nested manual boundaries.
- Focused post-review regression passed with `python3 -m pytest tests/test_desktop_rust_toolchain_install_approval_packet.py -q` (`7 passed`).
- Targeted read-only reviewer re-check found no remaining Critical or Important issues and assessed the hardening as approvable from the safety-boundary perspective.
