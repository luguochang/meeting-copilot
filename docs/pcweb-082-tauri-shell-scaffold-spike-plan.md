# PCWEB-082 Tauri Shell Scaffold Spike Plan

## Goal

PCWEB-082 creates the first real desktop shell scaffold after PCWEB-081. It must be a narrow Tauri-first spike that can load the existing Web UI and bind a small set of no-op bridge commands against the PCWEB-081 native bridge contract.

This is the handoff named by PCWEB-081: `create_tauri_shell_scaffold_against_bridge_contract`.

## Position In The PC Path

```text
PCWEB-079
  desktop shell readiness boundary

PCWEB-080
  desktop runtime/process model boundary

PCWEB-081
  native bridge command contract boundary

PCWEB-082
  create_tauri_shell_scaffold_against_bridge_contract
```

PCWEB-082 is not another preflight panel. It is the first desktop scaffold. The scaffold is still deliberately small: it must not capture audio, request permissions, enumerate devices, spawn workers, read secrets, write local files, create installers, or call remote providers.

## Technical Basis

The scaffold follows Tauri v2's project shape:

- A Rust application under `src-tauri/`.
- `tauri.conf.json` as the default Tauri configuration file.
- `Cargo.toml` for Rust dependencies.
- `capabilities/` for the Tauri v2 capability file.
- `build.devUrl` for development loading of the existing local Web backend.
- `build.frontendDist` for the static Web UI asset directory.

The initial scaffold intentionally avoids a Node frontend package. The existing Web MVP assets remain in `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static`, and the desktop shell points at them. This keeps PCWEB-082 small and avoids installing npm dependencies or generating lock files before the real desktop build pipeline is designed.

## Files

PCWEB-082 may create:

```text
code/desktop_tauri/
  README.md
  src-tauri/
    Cargo.toml
    build.rs
    tauri.conf.json
    capabilities/
      default.json
    src/
      main.rs
      lib.rs
```

PCWEB-082 must not create:

- `package.json`
- `package-lock.json`
- `pnpm-lock.yaml`
- `yarn.lock`
- `Cargo.lock`
- installer, signing, notarization, bundle, model, audio, or runtime-output artifacts

`package.json` is deferred until a dedicated desktop frontend/build pipeline is designed. `Cargo.lock` is deferred until the first dependency install/build step is explicitly enabled and verified.

## Tauri Configuration Contract

`code/desktop_tauri/src-tauri/tauri.conf.json` must:

- Use `productName=Meeting Copilot`.
- Use a stable reverse-DNS identifier.
- Set `build.devUrl=http://127.0.0.1:8765/`.
- Set `build.frontendDist` to the existing Web MVP static asset directory.
- Keep `beforeDevCommand` empty.
- Keep `beforeBuildCommand` empty.
- Create a single `main` window.
- Keep bundling inactive in this spike.

The scaffold must not auto-start the FastAPI backend. Developers must start the existing local backend explicitly before running a future Tauri dev command.

## No-Op Bridge Commands

PCWEB-082 binds exactly three no-op Tauri commands:

- `runtime_get_status` -> PCWEB-081 `runtime.get_status`
- `session_prepare` -> PCWEB-081 `session.prepare`
- `asr_worker_health` -> PCWEB-081 `asr_worker.health`

Each no-op response must include:

- `command_id`
- `command_status=noop_bound`
- `implementation_status=noop_only`
- `transport_status=tauri_ipc_bound`
- `side_effect_status=none`
- `safe_to_invoke_noop=true`
- `safe_to_execute_real_action=false`
- `captures_audio=false`
- `spawns_process=false`
- `calls_remote_provider=false`
- `writes_local_files=false`
- `message`

No audio command is bound in PCWEB-082. In particular, `audio.capture_start`, `audio.capture_stop`, `audio.devices_list`, and `audio.permissions_status` must remain unbound until explicit permission UX, audio adapter, and device enumeration boundaries are implemented.

## Explicit Non-Goals

PCWEB-082 does not:

- Install Rust or Node dependencies.
- Run `cargo build`, `cargo tauri dev`, `npm install`, or any package manager.
- Generate `Cargo.lock` or npm/pnpm/yarn lock files.
- Create installers, bundles, signing files, notarization files, or update artifacts.
- Bind audio capture commands.
- Request microphone, screen recording, accessibility, or system audio permissions.
- Enumerate audio devices.
- Capture microphone or system audio.
- Spawn ASR workers or any background process.
- Load ASR models or model metadata.
- Read provider config, API keys, keychain, environment secrets, or `configs/local/`.
- Write local session/audio/runtime data.
- Call remote ASR, LLM, relay, or paid providers.

## Acceptance

- Scaffold contract tests prove the Tauri file structure exists and uses Tauri v2 config keys (`devUrl`, `frontendDist`, `app`, `bundle`).
- Scaffold contract tests prove no `package.json`, dependency lock files, installer files, or runtime-output artifacts are created.
- Scaffold contract tests prove the config points to the existing local Web backend and static Web UI assets without auto-starting the backend.
- Scaffold contract tests prove only the three no-op commands are bound.
- Scaffold contract tests prove bound commands map back to PCWEB-081 command IDs and return no-op, no-side-effect metadata.
- Scaffold contract tests prove no audio capture/device/permission commands are bound.
- Quality gate includes the root scaffold contract tests in `pc-web`.
- Documentation gate records PCWEB-082 in README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Review Notes

The product value of PCWEB-082 is reducing desktop implementation risk by creating a real Tauri scaffold while keeping the dangerous platform actions unimplemented. This makes the next desktop work concrete without pretending that audio capture, worker lifecycle, permissions, packaging, signing, or ASR quality are complete.

## Implementation Status

Status: completed for the static scaffold spike.

Implemented files:

- `code/desktop_tauri/README.md`
- `code/desktop_tauri/src-tauri/Cargo.toml`
- `code/desktop_tauri/src-tauri/build.rs`
- `code/desktop_tauri/src-tauri/tauri.conf.json`
- `code/desktop_tauri/src-tauri/capabilities/default.json`
- `code/desktop_tauri/src-tauri/src/main.rs`
- `code/desktop_tauri/src-tauri/src/lib.rs`

Implemented behavior:

- `runtime_get_status` maps to `runtime.get_status`.
- `session_prepare` maps to `session.prepare`.
- `asr_worker_health` maps to `asr_worker.health`.
- All three commands return `noop_bound`, `noop_only`, `tauri_ipc_bound`, `side_effect_status=none`, and `safe_to_execute_real_action=false`.

Verification scope:

- RED was confirmed before scaffold creation through `tests/test_desktop_tauri_scaffold.py` and `tests/test_quality_gate.py`.
- GREEN focused verification passed for the same tests after the scaffold and `root-pytest` quality gate step were added.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with root scaffold contract tests, core tests, Web backend tests, and browser smoke.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with ASR runtime tests, ASR bake-off tests, root scaffold contract tests, core tests, and Web backend tests.
- The scaffold remains static only: no `Cargo.lock`, no `package.json`, no package manager install, no cargo/Tauri build, no audio capture, no worker spawn, no secret read, no local runtime write, and no remote provider call.
