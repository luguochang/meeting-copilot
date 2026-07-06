# PCWEB-080 Desktop Runtime Boundary Plan

## Goal

PCWEB-080 records the desktop runtime decision before creating a real desktop shell. It makes the Mac-first process model visible in the local Web workbench and API while keeping the current increment read-only, no-capture, no-permission, no-worker, no-install, and no-paid-call.

This is the bridge between PCWEB-079 desktop shell readiness and the later desktop shell implementation. The product direction is still PC-first: local Web MVP proves realtime meeting state and suggestion value, then a thin desktop shell wraps the same UI and delegates platform-specific work to adapters and sidecar workers.

## Runtime Decision

Recommended route:

```text
Local Web MVP
  -> Tauri Desktop Shell first
  -> Electron fallback if packaging, updater, native bridge, or audio capture blocks Tauri
```

Decision status:

- `recommended_desktop_runtime=tauri_first_electron_fallback`
- `desktop_runtime_decision_status=recommended_not_created`
- `desktop_process_model_status=planned_not_started`
- `macos_target_status=apple_silicon_first`
- `windows_target_status=deferred_adapter`

Reasons:

- Tauri can reuse the existing static Web UI with a smaller desktop shell.
- The current core and Web MVP already separate local workers, JSON/session state, event streams, evidence spans, and reports.
- Heavy ASR dependencies should run in a sidecar worker, not inside the UI process.
- Electron remains a fallback because its desktop packaging, auto-update, and native ecosystem are mature.
- Direct Swift plus Windows-native dual codebases are not recommended for the current stage because they raise delivery cost before the Copilot value is proven.

## Architecture Boundary

The locked architecture boundary is:

```text
desktop shell
  -> native bridge adapter
  -> audio capture adapter
  -> ASR sidecar worker
  -> core transcript/state/suggestion/report pipeline
  -> local storage adapter
```

The UI process must not own ASR model lifecycle, LLM execution, local state mutation policy, audio chunk persistence, report generation, or provider secret handling. The shell only presents UI, requests explicit user actions, and talks to narrow platform adapters.

## API Contract

PCWEB-080 adds:

```http
GET /desktop/runtime-boundary
```

Response contract:

- `desktop_runtime_mode=decision_preflight_only`
- `desktop_runtime_boundary_status=blocked_before_runtime_creation`
- `recommended_desktop_runtime=tauri_first_electron_fallback`
- `desktop_runtime_decision_status=recommended_not_created`
- `desktop_process_model_status=planned_not_started`
- `ui_reuse_status=web_mvp_static_assets_reusable`
- `core_isolation_status=platform_independent`
- `native_bridge_status=not_created`
- `asr_worker_process_model=sidecar_worker_planned`
- `packaging_pipeline_status=not_started`
- `macos_target_status=apple_silicon_first`
- `windows_target_status=deferred_adapter`
- `desktop_runtime_phase_count=8`
- `desktop_runtime_phases`
- `desktop_runtime_blockers`
- `desktop_runtime_next_decisions`
- Scoped false flags:
  - `desktop_runtime_safe_to_create_shell=false`
  - `desktop_runtime_safe_to_start_native_bridge=false`
  - `desktop_runtime_safe_to_spawn_worker=false`
  - `desktop_runtime_safe_to_package_installer=false`
  - `desktop_runtime_safe_to_request_permissions=false`
  - `desktop_runtime_safe_to_capture_audio=false`
  - `desktop_runtime_safe_to_call_remote_asr=false`
  - `desktop_runtime_safe_to_call_llm=false`

## Web Workbench

The Web workbench adds a `desktop-runtime-boundary-panel` near the existing desktop readiness panel. It must load on startup and after reset, but the startup path remains passive:

- Allowed: `GET /desktop/runtime-boundary`, `GET /desktop/shell-readiness`, `GET /demo/fixtures`.
- Forbidden: automatic demo session creation, browser storage write, native permission probe, worker spawn, model load, provider config read, remote ASR/LLM call, installer creation.

The panel should show the runtime recommendation, phase count, blocked status, process model, platform targets, blockers, next decisions, and false `desktop_runtime_safe_to_*` flags.

## Explicit Non-Goals

PCWEB-080 does not:

- Create a Tauri, Electron, Rust, Node desktop package, or installer.
- Add Tauri/Electron dependencies or lock files.
- Request microphone, screen recording, accessibility, or system audio permissions.
- Capture microphone or system audio.
- Probe CoreAudio, ScreenCaptureKit, WASAPI, browser media APIs, or native permission APIs.
- Start an ASR worker or load ASR models.
- Read provider config, secrets, keychain, environment credentials, or `configs/local/`.
- Call remote ASR, LLM, relay, or paid providers.
- Write audio chunks, local data directories, browser storage, install artifacts, signing artifacts, notarization artifacts, or app-store metadata.

## Acceptance

- API test proves `GET /desktop/runtime-boundary` returns the deterministic decision-preflight envelope.
- No-side-effect test proves the endpoint does not read local config/secrets or leak guarded markers.
- Data-dir test proves `create_app(data_dir=...)` plus runtime boundary GET creates no local storage directories.
- Static tests prove the workbench contains `desktop-runtime-boundary-panel`, loads `/desktop/runtime-boundary`, renders `desktop_runtime_phases`, and resets the panel through `renderEmpty()`.
- Browser smoke proves passive startup renders the runtime boundary and still writes no local data before explicit fixture load.
- Documentation gate proves PCWEB-080 is recorded in README, requirements traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Review Notes

The main risk is semantic overclaiming. The response must say a runtime is recommended, not created. The UI must not look like a setup wizard or permission entry point. All safe-to flags stay false until a later increment explicitly designs and tests the real desktop shell, native bridge, permission UX, audio adapters, ASR worker lifecycle, packaging, signing, and distribution path.
