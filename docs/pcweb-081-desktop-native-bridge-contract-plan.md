# PCWEB-081 Desktop Native Bridge Contract Plan

## Goal

PCWEB-081 defines the native bridge command contract that a future Mac-first desktop shell will use to talk to platform adapters, ASR sidecar workers, local storage, and the existing Web/Core pipeline.

This is not another open-ended evaluation. It turns the PCWEB-080 process model into a concrete, testable bridge contract while still keeping the current increment no-shell, no-native-bridge, no-worker-spawn, no-permission, no-capture, no-config-read, no-write, and no-paid-call.

## Position In The PC Path

```text
PCWEB-079
  desktop shell readiness boundary

PCWEB-080
  desktop runtime/process model boundary

PCWEB-081
  native bridge command contract boundary

Next desktop increment
  create a minimal Tauri shell scaffold against this contract
```

PCWEB-081 should make the later Tauri scaffold less risky by defining command names, lifecycle states, error envelope, resource guard metadata, and forbidden side effects before any native code exists.

## API Contract

PCWEB-081 adds:

```http
GET /desktop/native-bridge-contract
```

Response contract:

- `desktop_bridge_contract_mode=contract_preflight_only`
- `desktop_bridge_contract_status=specified_not_bound`
- `native_bridge_status=not_created`
- `desktop_shell_runtime_status=not_created`
- `bridge_transport_status=not_created`
- `bridge_command_contract_status=specified_not_bound`
- `bridge_process_lifecycle_status=specified_not_started`
- `bridge_resource_policy_status=specified_not_enforced`
- `bridge_error_contract_status=specified`
- `bridge_audit_contract_status=response_only`
- `bridge_platform_adapter_status=not_created`
- `desktop_bridge_command_count=8`
- `desktop_bridge_commands`
- `desktop_bridge_phase_count=8`
- `desktop_bridge_phases`
- `desktop_bridge_error_contract`
- `desktop_bridge_resource_policy`
- `desktop_bridge_blockers`
- `desktop_bridge_next_decisions`
- Scoped false flags:
  - `desktop_bridge_safe_to_create_native_bridge=false`
  - `desktop_bridge_safe_to_bind_ipc=false`
  - `desktop_bridge_safe_to_invoke_commands=false`
  - `desktop_bridge_safe_to_request_permissions=false`
  - `desktop_bridge_safe_to_enumerate_devices=false`
  - `desktop_bridge_safe_to_capture_audio=false`
  - `desktop_bridge_safe_to_spawn_worker=false`
  - `desktop_bridge_safe_to_write_local_files=false`
  - `desktop_bridge_safe_to_call_remote_asr=false`
  - `desktop_bridge_safe_to_call_llm=false`

## Command Contract

`desktop_bridge_commands` is a response-only command catalog. Every command is `contract_only`, `not_bound`, and `safe_to_invoke=false`.

Initial command IDs cover the shortest real desktop-client path:

- `runtime.get_status`
- `session.prepare`
- `audio.permissions_status`
- `audio.devices_list`
- `audio.capture_start`
- `audio.capture_stop`
- `asr_worker.start`
- `asr_worker.health`

Each command must include:

- `command_id`
- `command_group`
- `command_status=contract_only`
- `implementation_status=not_bound`
- `transport_status=not_created`
- `effect_class`
- `requires_explicit_user_action`
- `read_set`
- `write_set`
- `spawns_process`
- `captures_audio`
- `calls_remote_provider`
- `side_effect_status=forbidden`
- `safe_to_execute_now=false`
- `future_adapter`
- `request_schema_status=outline_only`
- `response_schema_status=outline_only`
- `failure_mode`
- `security_classification`

The command catalog intentionally includes commands that will later have effects, such as `audio.capture_start` and `asr_worker.start`. In PCWEB-081 they remain contract-only and `safe_to_execute_now=false`; the value is that the later Tauri shell spike can wire no-op IPC against the same command IDs.

## Error And Resource Contract

The bridge must define a future error envelope without throwing or invoking native code:

- `error_code`
- `error_kind`
- `user_recoverable`
- `safe_message_policy`
- `secret_redaction_policy`
- `retry_policy`

The resource policy is also response-only:

- ASR sidecar memory limit is planned but not enforced.
- Worker health checks are planned but not started.
- Audio buffer retention is planned but not created.
- Process shutdown and crash restart are planned but not implemented.
- Event queue backpressure, payload size limits, heartbeat interval, and log line limits are specified but not enforced.
- Audio chunks remain non-persistent by default in this contract.

## Handoff Gate

PCWEB-081 is the last bridge/process preflight before a real desktop shell scaffold. After PCWEB-081 is green, the next desktop increment must be `create_tauri_shell_scaffold_against_bridge_contract`, limited to loading the existing Web UI and wiring two or three no-op bridge commands against this contract. Further preflight-only bridge/status panels should be treated as scope drift unless a real Tauri scaffold is already underway.

## Web Workbench

The Web workbench adds `desktop-native-bridge-contract-panel` after the runtime boundary panel. It loads at startup and reset alongside the existing desktop readiness/runtime panels.

Allowed passive startup GETs:

- `GET /desktop/shell-readiness`
- `GET /desktop/runtime-boundary`
- `GET /desktop/native-bridge-contract`
- `GET /demo/fixtures`

Forbidden:

- Automatic demo session creation.
- Browser storage writes.
- Native bridge creation.
- IPC binding.
- Permission request/probe.
- Device enumeration.
- Audio capture.
- Worker spawn.
- Model load.
- Provider config or secret read.
- Remote ASR/LLM call.
- Installer/package creation.

## Explicit Non-Goals

PCWEB-081 does not:

- Create a Tauri/Electron project.
- Add Rust, Node, Tauri, or Electron dependencies.
- Add `src-tauri`, `Cargo.toml`, `package.json`, lock files, or installer files.
- Start a native process or worker.
- Bind a native bridge, IPC transport, websocket, localhost bridge, or command handler.
- Request macOS microphone, screen recording, accessibility, or system audio permissions.
- Enumerate audio devices.
- Capture microphone or system audio.
- Read provider config, API keys, keychain, environment secrets, or `configs/local/`.
- Write local data directories, audio chunks, browser storage, bridge audit files, installer files, signing files, or notarization files.
- Call remote ASR, LLM, relay, or paid providers.

## Acceptance

- API test proves `GET /desktop/native-bridge-contract` returns the deterministic contract-preflight envelope, eight command contracts, eight phases, error contract, resource policy, blockers, next decisions, and false scoped safe flags.
- No-side-effect test proves the endpoint does not read config/secrets or leak guarded markers.
- Data-dir test proves `create_app(data_dir=...)` plus native bridge contract GET creates no local storage directories.
- Static tests prove the workbench contains `desktop-native-bridge-contract-panel`, loads `/desktop/native-bridge-contract`, renders `desktop_bridge_commands`, and resets the panel through `renderEmpty()`.
- Browser smoke proves passive startup renders the native bridge contract and still writes no local data before explicit fixture load.
- Contract tests prove every command has effect/read/write/spawn/capture/remote/safe flags, security classification, and future owner metadata.
- Documentation gate proves PCWEB-081 is recorded in README, requirements traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Review Notes

The main product value of PCWEB-081 is that the next real desktop scaffold can be built against a stable bridge command catalog instead of ad hoc native calls. The main risk is semantic overclaiming: every command must stay contract-only and `safe_to_invoke=false` until a later increment creates and tests a real shell, real bridge, permission UX, audio adapters, worker lifecycle, local data lifecycle, and packaging path.
