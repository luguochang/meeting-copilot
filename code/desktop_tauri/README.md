# Meeting Copilot Desktop Tauri Scaffold

PCWEB-082 creates the first desktop shell scaffold against the PCWEB-081 native bridge contract.

This scaffold is intentionally narrow:

- Tauri v2 Rust source lives under `src-tauri/`.
- The shell points at the existing local Web MVP backend during development: `http://127.0.0.1:8765/`.
- The shell points at the existing static Web MVP assets for build-time frontend distribution.
- Only three no-op bridge commands are bound:
  - `runtime_get_status` maps to `runtime.get_status`.
  - `session_prepare` maps to `session.prepare`.
  - `asr_worker_health` maps to `asr_worker.health`.

Each command returns a no-op response envelope with no audio capture, no process spawn, no remote provider call, and no local file write.

## Explicit Boundaries

PCWEB-082 does not run Tauri, Cargo, npm, or any package manager. It also does not create dependency lock files, installers, signing assets, notarization assets, bundles, model files, audio files, or runtime output artifacts.

The following remain unimplemented until later dedicated increments:

- microphone capture
- system audio capture
- permission requests
- audio device enumeration
- ASR worker startup
- provider config loading
- secret storage
- local runtime persistence
- remote ASR calls
- remote LLM calls

Start the existing Web MVP backend manually before a future desktop dev run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
PYTHONPATH=.:../../core uvicorn meeting_copilot_web_mvp.app:app --host 127.0.0.1 --port 8765
```

Do not run `cargo build`, `cargo tauri dev`, or package-manager commands as part of PCWEB-082 verification.

## PCWEB-083 Build Readiness

PCWEB-083 adds `build-readiness.policy.json` and `../../tools/desktop_build_readiness.py`.

The default readiness report is static and does not run external commands. The only explicit probe mode is `toolchain_version_probe_only`, which is limited to:

- `rustc --version`
- `cargo --version`

The policy keeps `safe_to_run_cargo_check_now=false`, `safe_to_run_tauri_dev_now=false`, `safe_to_run_tauri_build_now=false`, `safe_to_install_dependencies_now=false`, `safe_to_generate_lockfiles_now=false`, and `safe_to_generate_build_artifacts_now=false`.

The executable probe allowlist is enforced in `../../tools/desktop_build_readiness.py`; a custom policy file cannot expand execution beyond the two version probes. Invalid custom probe commands are reported as blocked and are not passed to the command runner.

A future `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` remains blocked until lockfile, target directory, dependency fetch, cleanup, and no-audio/no-secret/no-remote boundaries are explicitly approved.

## PCWEB-084 Cargo Check Artifact Policy

PCWEB-084 adds `cargo-check.policy.json` and `../../tools/desktop_cargo_check_policy.py`.

The policy decides the future cargo-check artifact boundary without running Cargo. The first explicitly approved dependency-resolution run may generate `code/desktop_tauri/src-tauri/Cargo.lock`, and Cargo build output must use `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target` rather than `src-tauri/target`. Repeat checks should prefer `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml --locked --offline` after `Cargo.lock` and the dependency cache exist.

The policy keeps `safe_to_run_cargo_check_now=false`; it does not install Rust, fetch dependencies, run Cargo/Tauri/package managers, create target output, request permissions, capture audio, spawn workers, read provider config or `configs/local/`, read secrets, or call remote providers.

## PCWEB-085 Rust Toolchain Readiness

PCWEB-085 adds `rust-toolchain-readiness.policy.json` and `../../tools/desktop_rust_toolchain_readiness.py`.

The default report is static and does not execute commands. Explicit `local_version_and_platform_probe_only` is hard-allowlisted to `rustc --version`, `cargo --version`, `rustup --version`, and `xcode-select -p`. The `xcode-select -p` result is redacted to presence only, so the local developer tools path is not returned.

The policy keeps `safe_to_install_toolchain_now=false`, `safe_to_run_cargo_check_now=false`, `safe_to_fetch_dependencies_now=false`, and `safe_to_generate_target_dir_now=false`; it does not install Rust, modify shell profiles, run Cargo/Tauri/package managers, generate artifacts, capture audio, spawn workers, read provider config or `configs/local/`, read secrets, or call remote providers.

## PCWEB-086 Rust Toolchain Installation Decision

PCWEB-086 adds `rust-toolchain-installation.policy.json` and `../../tools/desktop_rust_toolchain_installation_decision.py`.

The default report is static and uses `no_install_decision_report_only`. It records the recommended `official_rustup` install provider, explicit approval tokens, platform notes, and a future post-install verification order, but it does not execute any installer or shell command.

The policy keeps `safe_to_install_toolchain_now=false`, `safe_to_modify_shell_profile_now=false`, `safe_to_run_install_command_now=false`, `safe_to_run_rustup_now=false`, and `safe_to_run_cargo_check_now=false`; it does not run curl/sh/rustup/cargo/package managers, modify shell profiles, fetch dependencies, generate artifacts, capture audio, spawn workers, read provider config or `configs/local/`, read secrets, or call remote providers.

## PCWEB-087 Rust Toolchain Install Approval Packet

PCWEB-087 adds `rust-toolchain-install-approval.policy.json` and `../../tools/desktop_rust_toolchain_install_approval_packet.py`.

The default report is static and uses `manual_user_run_only`. It records official Rust/rustup/Tauri source URLs, inert macOS/Linux manual command text, Windows `rustup-init.exe` manual guidance, explicit approval tokens, platform risk notes, rollback notes, and a future post-install verification order, but it does not execute any installer, shell command, package manager, Cargo command, or Tauri command.

The policy keeps `safe_to_execute_install_now=false`, `safe_to_install_toolchain_now=false`, `safe_to_modify_shell_profile_now=false`, `safe_to_run_install_command_now=false`, `safe_to_run_rustup_now=false`, and `safe_to_run_cargo_check_now=false`; it does not modify shell profiles or PATH, fetch dependencies, generate artifacts, capture audio, spawn workers, read provider config or `configs/local/`, read secrets, or call remote providers.

## PCWEB-088 Rust Post-Install Probe Approval

PCWEB-088 adds `rust-post-install-probe-approval.policy.json` and `../../tools/desktop_rust_post_install_probe_approval.py`.

The default report is static and uses `no_probe_execution_approval_packet_only`. It records the future read-only post-install probe allowlist (`rustc --version`, `cargo --version`, `rustup --version`, and macOS `xcode-select -p`), redaction requirements, explicit approval tokens, expected result schema, and cargo-check blockers, but it does not execute any probe command.

The policy keeps `safe_to_run_post_install_probe_now=false`, `safe_to_run_cargo_check_now=false`, `safe_to_fetch_dependencies_now=false`, `safe_to_generate_cargo_lock_now=false`, and `safe_to_generate_target_dir_now=false`; it does not inspect PATH, shell profiles, cargo home, rustup home, dependency caches, `Cargo.lock`, target output, audio, worker state, provider config, `configs/local/`, secrets, or remote providers.

## PCWEB-089 Rust Post-Install Probe Result Intake

PCWEB-089 adds `rust-post-install-probe-result-intake.policy.json` and `../../tools/desktop_rust_post_install_probe_result_intake.py`.

The default report is static and uses `manual_result_validation_only` with `caller_provided_json_only`. It validates only bounded caller-provided status fields shaped by PCWEB-088, rejects raw stdout/stderr, command, path, env, shell profile, cargo/rustup home, dependency cache, provider config, api_key, authorization, and bearer token fields, and normalizes the accepted status object without executing any probe command.

The policy keeps `safe_to_accept_raw_probe_output_now=false`, `safe_to_run_post_install_probe_now=false`, `safe_to_run_cargo_check_now=false`, `cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`, `safe_to_fetch_dependencies_now=false`, `safe_to_generate_cargo_lock_now=false`, and `safe_to_generate_target_dir_now=false`; it does not run rustc, cargo, rustup, xcode-select, cargo check, Tauri, package managers, inspect raw output or local paths, read `configs/local/`, capture audio, spawn workers, read secrets, or call remote providers.

## PCWEB-090 First Cargo Check Execution Boundary

PCWEB-090 adds `first-cargo-check-execution.policy.json` and `../../tools/desktop_first_cargo_check_execution_boundary.py`.

The default report is static and uses `explicit_manual_execution_packet_only`. It validates the PCWEB-084 cargo-check command/env/artifact policy and a PCWEB-089 bounded toolchain result, then either blocks or returns `execution_packet_status=ready_for_explicit_user_approval` with a manual execution packet for `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` and `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`.

The policy keeps `safe_to_run_cargo_check_now=false`, `safe_to_fetch_dependencies_now=false`, `safe_to_generate_cargo_lock_now=false`, `safe_to_generate_target_dir_now=false`, and all audio/worker/secret/remote flags false. It does not run Cargo, Tauri, package managers, dependency fetches, cleanup, shell commands, audio capture, workers, provider config reads, `configs/local/`, secrets, or remote providers.

## PCWEB-091 Tauri No-op Shell Local Run Smoke

PCWEB-091 adds `tauri-noop-shell-run-smoke.policy.json` and `../../tools/desktop_tauri_noop_shell_run_smoke.py`.

The default report is static and uses `readiness_report_only`. It validates the PCWEB-082 no-op scaffold, `devUrl=http://127.0.0.1:8765/`, `frontendDist`, minimal `core:default` capability, exact no-op command catalog, absence of generated artifacts, and the PCWEB-090 no-command cargo-check boundary. A passing report can only return `smoke_packet_status=ready_for_explicit_tauri_run_approval`.

The policy keeps `safe_to_run_tauri_dev_now=false`, `safe_to_run_tauri_build_now=false`, `safe_to_run_cargo_check_now=false`, `safe_to_spawn_process_now=false`, and `safe_to_capture_audio_now=false`; it does not run Tauri, Cargo, package managers, dependency fetches, audio capture, ASR workers, provider config reads, `configs/local/`, secrets, or remote providers.

## PCWEB-095 ASR Worker Handoff Preflight

PCWEB-095 adds `asr-worker-handoff-preflight.policy.json` and `../../tools/desktop_asr_worker_handoff_preflight.py`.

The report validates a future desktop ASR worker descriptor and returns a `/live/asr/local-event-files/sessions` request preview. It accepts only `source_kind=preflight_only|synthetic` at this stage and only event paths under `artifacts/tmp/asr_events`.

The policy keeps worker execution, event file reads/writes, web mutation, microphone capture, provider config reads, model downloads, remote ASR, and LLM calls disabled.

## PCWEB-096 ASR Worker Handoff Local Dry Run

PCWEB-096 adds `asr-worker-handoff-local-dry-run.policy.json` and `../../tools/desktop_asr_worker_handoff_local_dry_run.py`.

The default mode is `preview_only`, which reuses PCWEB-095 preflight and does not read event files or mutate Web sessions. Explicit `synthetic_local_test` may use FastAPI `TestClient` to call `/live/asr/local-event-files/sessions` with a synthetic event file under `artifacts/tmp/asr_events` and a temporary Web data dir under `artifacts/tmp/desktop_handoff_dry_run`.

It still does not run Tauri/Cargo, start an ASR worker, access microphone/system audio, request permissions, read real user audio, read `configs/local`, download models, or call remote ASR/LLM.

## PCWEB-097 ASR Worker Handoff Dry-run Readiness UI/API

PCWEB-097 adds the Web/Tauri no-op readiness endpoint and panel for PCWEB-096.

The Web backend exposes `GET /desktop/asr-worker-handoff-dry-run-readiness`, and the Web workbench renders `desktop-asr-handoff-dry-run-panel`. The response is readiness-only: it reports preview mode, explicit synthetic local test status, approved roots, blockers, next decisions, and false safety flags.

It does not start a worker, read an event file, mutate a Web session, access microphone/system audio, read real user audio, read `configs/local`, call remote ASR/LLM, download models, or run Tauri/Cargo.

## PCWEB-098 ASR Worker Process Contract

PCWEB-098 adds `asr-worker-process-contract.policy.json` and `../../tools/desktop_asr_worker_process_contract.py`.

The report defines the future ASR worker sidecar process contract without spawning any process. It specifies:

- worker lifecycle status: `specified_not_started`
- worker process status: `not_spawned`
- command catalog: `worker.prepare`, `worker.start`, `worker.stop`, `worker.health`, `worker.collect_events`, `worker.cleanup`
- event output contract: `partial`, `final`, `revision`, `error`, `end_of_stream`
- approved event output root: `artifacts/tmp/asr_events`
- approved runtime root: `artifacts/tmp/desktop_asr_worker_runtime`
- Web handoff endpoint: `/live/asr/local-event-files/sessions`

The policy keeps all execution, audio, secret, model-download, remote-provider, event-file write/read, Web mutation, and Tauri/Cargo flags false. A caller-provided worker contract can be validated for future review, but the tool never starts the worker or touches audio.

## PCWEB-099 ASR Worker Command Protocol

PCWEB-099 adds `asr-worker-command-protocol.policy.json` and `../../tools/desktop_asr_worker_command_protocol.py`.

The report defines the future ASR worker command protocol without accepting or executing any command. It specifies request/response envelopes for:

- `worker.prepare`
- `worker.start`
- `worker.stop`
- `worker.health`
- `worker.collect_events`
- `worker.cleanup`

Each command has an allowed lifecycle transition preview, but the response preview stays `accepted=false`, `status=validated_not_executed`, and `worker_lifecycle_status=unchanged_not_executed`.

The policy keeps command execution, process spawn, audio capture, event-file read/write, runtime-audio write, Web mutation, model download, remote ASR/LLM, and Tauri/Cargo flags false. A caller-provided command request can be validated for future review, but the tool never starts the worker, touches audio, reads event files, mutates Web sessions, or calls remote providers.

## PCWEB-100 ASR Worker Synthetic Lifecycle Harness

PCWEB-100 adds `asr-worker-synthetic-lifecycle.policy.json` and `../../tools/desktop_asr_worker_synthetic_lifecycle.py`.

The harness applies the PCWEB-099 command protocol to a bounded synthetic lifecycle:

- `worker.prepare`
- `worker.start`
- `worker.collect_events`
- `worker.stop`
- `worker.cleanup`

During `worker.collect_events`, it reuses the PCWEB-096 `synthetic_local_test` path to load an approved synthetic ASR event file under `artifacts/tmp/asr_events` into a temporary Web data dir under `artifacts/tmp/desktop_handoff_dry_run`.

The policy keeps real worker startup, audio capture, permission requests, model download, remote ASR/LLM, runtime-audio write, event-file write, production Web mutation, and Tauri/Cargo flags false. The only narrow runtime flags that can become true are `safe_to_read_approved_asr_event_file_now` and `safe_to_mutate_temp_web_session_now`, and only inside the synthetic lifecycle harness.

## PCWEB-101 ASR Worker Implementation Approval Packet

PCWEB-101 adds `asr-worker-implementation-approval.policy.json` and `../../tools/desktop_asr_worker_implementation_approval.py`.

The report defines a bounded manual review packet for future worker implementation. It records:

- required previous contracts: `PCWEB-098`, `PCWEB-099`, `PCWEB-100`
- future worker entrypoint and command runner paths
- approved event output root: `artifacts/tmp/asr_events`
- approved runtime root: `artifacts/tmp/desktop_asr_worker_runtime`
- provider modes currently allowed only for preview: `mock_streaming`, `sherpa_onnx_streaming`
- future provider mode requiring approval: `funasr_streaming`
- forbidden provider modes: `remote_asr`, `remote_llm_asr`
- source kind currently allowed only for preview: `synthetic`
- source kinds requiring later approval: `mic`, `file`, `system_audio`

Even a valid packet returns `ready_for_manual_review_not_executable` with `approved_to_implement_now=false` and `approved_to_execute_now=false`. The policy keeps worker implementation, process execution, audio capture, event-file read/write, runtime-audio write, Web mutation, model download, remote ASR/LLM, and Tauri/Cargo flags false.

## PCWEB-102 ASR Worker No-Execution Skeleton

PCWEB-102 adds `asr-worker-no-execution-skeleton.policy.json`, `../../tools/desktop_asr_worker_no_execution_skeleton.py`, and `../asr_runtime/scripts/asr_worker_sidecar.py`.

The sidecar module is a module-boundary skeleton only. It defines preview contracts for:

- worker identity
- command envelope intake
- lifecycle state
- event writer
- provider adapter
- health/status
- cleanup plan

The default report returns `worker_skeleton_status=specified_not_executable` and `worker_execution_status=not_executed`. It allows only `synthetic` source plus `mock_streaming` or `sherpa_onnx_streaming` provider preview. `funasr_streaming` still requires local model directory or DRV-019 approval, and `mic`, `file`, `system_audio`, `remote_asr`, and `remote_llm_asr` remain blocked.

The policy keeps process spawn, worker start, audio capture, permission requests, user audio reads, event-file read/write, runtime-audio write, provider execution, model import/download, remote ASR/LLM, Web mutation, and Tauri/Cargo flags false.

## PCWEB-103 ASR Worker Command Runner Binding Preview

PCWEB-103 adds `asr-worker-command-runner-binding.policy.json`, `../../tools/desktop_asr_worker_command_runner_binding.py`, and `../../tests/test_desktop_asr_worker_command_runner_binding.py`.

The binding report validates only the future shape between `../asr_runtime/scripts/asr_worker_sidecar.py` and `src-tauri/src/asr_worker_command_runner.rs`. The Rust command runner file is a reserved path only; PCWEB-103 does not create it, bind it, run it, or invoke Tauri IPC.

A valid binding request returns `ready_for_no_execution_binding_review` with a `future_native_command_preview` marked `validated_not_bound`. It also reports `command_dispatch_status=not_dispatched`, `tauri_ipc_status=not_invoked`, `process_spawn_status=not_spawned`, `health_probe_status=not_executed`, `event_collection_status=not_executed`, and `worker_execution_status=not_executed`.

The policy keeps command runner bind/execute, worker command accept/dispatch/execute, subprocess, process spawn, worker start/stop/health/event collection, audio capture, permission requests, user audio reads, event-file read/write, runtime-audio write, provider execution, model import/download, remote ASR/LLM, Web mutation, and Tauri/Cargo flags false.

## PCWEB-104 ASR Worker Command Runner Implementation Skeleton

PCWEB-104 adds `asr-worker-command-runner-implementation-skeleton.policy.json`, `../../tools/desktop_asr_worker_command_runner_implementation_skeleton.py`, `../../tests/test_desktop_asr_worker_command_runner_implementation_skeleton.py`, and `src-tauri/src/asr_worker_command_runner.rs`.

The Rust file is deliberately inert. It defines preview-only structs and functions such as `AsrWorkerCommandRunnerPreview`, `BlockedCommandRunnerResponse`, `command_catalog_preview`, and `preview_blocked_response`, but it is not imported from `lib.rs`, is not included in `tauri::generate_handler!`, and has no `#[tauri::command]` macro.

A valid skeleton request returns `ready_for_no_dispatch_skeleton_review` with `future_native_command_runner_skeleton.implementation_status=skeleton_source_validated_not_bound` and a blocked command preview. It keeps `command_dispatch_status=not_dispatched`, `tauri_ipc_status=not_invoked`, `process_spawn_status=not_spawned`, `worker_execution_status=not_executed`, `event_file_read_status=not_read`, and `event_file_write_status=not_written`.

The skeleton and policy still do not spawn a worker, bind a Tauri command, invoke IPC, run subprocesses, read/write event files, capture audio, request permissions, read user audio or `configs/local`, call remote ASR/LLM, download models, or run Cargo/Tauri.

## PCWEB-105 Microphone Adapter Contract

PCWEB-105 adds `mic-adapter-contract.policy.json`, `../../tools/desktop_mic_adapter_contract.py`, and `../../tests/test_desktop_mic_adapter_contract.py`.

The report defines the future microphone adapter command contract without binding or executing any native audio code. It specifies:

- `mic_adapter.prepare`
- `mic_adapter.status`
- `mic_adapter.start`
- `mic_adapter.pause`
- `mic_adapter.resume`
- `mic_adapter.stop`
- `mic_adapter.delete_audio_chunks`

The policy fixes the user-start boundary as `explicit_user_start_required_before_capture`, the approved runtime root as `artifacts/tmp/desktop_mic_adapter_runtime`, the approved audio chunk root as `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`, and delete semantics as `delete_audio_chunks_before_session_discard`.

Even a valid `mic_adapter.start` request with `user_consent_state=explicit_user_start_granted` only returns `accepted=false` and `status=validated_not_executed`. The tool does not request permission, enumerate devices, capture audio, write audio chunks, delete chunks, read user audio, call remote ASR/LLM, download models, mutate Web sessions, or run Cargo/Tauri. `file` and `system_audio` source kinds remain blocked until a separate approval path exists.

## PCWEB-106 Microphone Adapter Readiness UI/API

PCWEB-106 adds `GET /desktop/mic-adapter-contract-readiness` and the Web workbench panel `desktop-mic-adapter-contract-panel`.

The endpoint reuses the PCWEB-105 static contract report and exposes the contract to the desktop no-op workbench:

- `source_pcweb_id=PCWEB-105`
- `mic_adapter_ui_status=ready_noop_contract_visible`
- `mic_adapter_contract_status=specified_not_executable`
- `permission_request_status=not_requested`
- `audio_capture_status=not_started`
- `audio_chunk_write_status=not_written`
- `audio_chunk_delete_status=not_executed`
- the 7-command mic adapter catalog
- the approved runtime/audio chunk roots
- the user-start boundary and delete semantics
- all false safety flags

The ASR handoff readiness endpoint now points to `next_pcweb_id=PCWEB-106` and reports `mic_adapter_contract_status=specified_not_executable` instead of the old `not_defined` value.

PCWEB-106 still does not bind a native adapter, request macOS microphone permission, enumerate input devices, capture audio, write or delete audio chunks, read real audio, call remote ASR/LLM, download models, mutate Web sessions, or run Cargo/Tauri.

## PCWEB-107 Microphone Adapter No-op Tauri IPC Binding

PCWEB-107 extends the static no-op Tauri command catalog from the original PCWEB-082 three-command runtime bridge to a 10-command catalog that includes the seven PCWEB-105 microphone adapter commands.

New no-op commands in `src-tauri/src/lib.rs`:

- `mic_adapter_prepare -> mic_adapter.prepare`
- `mic_adapter_status -> mic_adapter.status`
- `mic_adapter_start -> mic_adapter.start`
- `mic_adapter_pause -> mic_adapter.pause`
- `mic_adapter_resume -> mic_adapter.resume`
- `mic_adapter_stop -> mic_adapter.stop`
- `mic_adapter_delete_audio_chunks -> mic_adapter.delete_audio_chunks`

`tauri-noop-shell-run-smoke.policy.json` and `../../tools/desktop_tauri_noop_shell_run_smoke.py` now validate the exact 10-command set, exact bridge command IDs, exact `generate_handler!` set, function-to-command mappings, and no-side-effect response fields.

The focused static gate is:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_tauri_scaffold.py \
  tests/test_desktop_tauri_noop_shell_run_smoke.py \
  -q -p no:cacheprovider
```

Result:

```text
20 passed, 1 warning
```

PCWEB-107 still does not run Cargo/Tauri, request microphone permission, enumerate devices, capture audio, write or delete audio chunks, start an ASR worker, read real audio, call remote ASR/LLM, download models, or mutate Web sessions.

## PCWEB-108 Worker Output to Web Live ASR Session Closure

PCWEB-108 strengthens the PCWEB-096/100 synthetic handoff path. `../../tools/desktop_asr_worker_handoff_local_dry_run.py` still uses only an approved synthetic ASR event file under `artifacts/tmp/asr_events` and a temporary Web data directory under `artifacts/tmp/desktop_handoff_dry_run`, but its summary now verifies the downstream Live ASR closure:

- transcript final count
- EvidenceSpan count
- state event count
- scheduler event count
- suggestion candidate count
- LLM request draft count
- formal suggestion card count
- LLM status list
- `worker_to_web_live_session_closure_status`

A technical synthetic input must close to `closed_to_evidence_state_gap`. A non-engineering input can still create transcript/EvidenceSpan, but if it has no state/gap candidate the dry-run returns `blocked_by_live_session_closure` with `blocked_no_state_or_gap_candidate`.

PCWEB-108 still does not spawn or start a real worker, access the microphone, request permissions, read user audio, read `configs/local`, call remote ASR/LLM, download models, mutate production Web sessions, write runtime audio, write event files, or run Cargo/Tauri.

## PCWEB-109 Mic Adapter No-op UI Invocation

PCWEB-109 connects the PCWEB-107 no-op mic adapter command catalog to the Web workbench invocation surface.

The Web panel `desktop-mic-adapter-contract-panel` now renders both:

- the PCWEB-105/106 microphone adapter contract readiness, and
- a seven-row no-op invocation status list for:
  - `mic_adapter_prepare`
  - `mic_adapter_status`
  - `mic_adapter_start`
  - `mic_adapter_pause`
  - `mic_adapter_resume`
  - `mic_adapter_stop`
  - `mic_adapter_delete_audio_chunks`

In a normal browser, the panel must show `mic_adapter_browser_fallback` and `not_invoked` for all seven rows. In a future Tauri WebView, the same UI path may call `window.__TAURI__.core.invoke` or `window.__TAURI__.tauri.invoke`, but only against the no-op commands already bound by PCWEB-107.

PCWEB-109 still does not run Cargo/Tauri, request microphone permission, enumerate devices, capture audio, write or delete audio chunks, start an ASR worker, read real audio, call remote ASR/LLM, download models, or mutate production Web sessions.
