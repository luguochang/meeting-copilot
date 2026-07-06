# PCWEB-079 Desktop Shell Readiness Boundary Plan

## Context

PCWEB-078 finishes the current local Web workbench card-lifecycle readiness surface. The roadmap now points toward the real desktop capture MVP, but the project should not jump directly into microphone/system-audio capture, permissions, model workers, installers, or paid providers.

PCWEB-079 adds a local, read-only readiness boundary that makes the desktop-entry blockers explicit in the Web workbench and API. It answers: what still has to be true before this local Web MVP can become a Mac-first desktop shell?

## Scope

Add a local endpoint:

```http
GET /desktop/shell-readiness
```

The response is template-only and side-effect-free. It must report:

- `desktop_readiness_mode=preflight_only`
- `desktop_readiness_status=blocked_before_desktop_shell`
- `desktop_shell_status=not_started`
- `target_platform_status=macos_first_windows_deferred`
- `audio_capture_status=not_connected`
- `microphone_permission_status=not_requested`
- `system_audio_permission_status=not_requested`
- `asr_worker_status=not_started`
- `llm_provider_status=not_connected`
- `local_data_dir_status`
- `desktop_readiness_phase_count=8`
- `desktop_readiness_phases`
- `desktop_readiness_blockers`
- `desktop_readiness_next_decisions`
- scoped `desktop_safe_to_*` false flags.
- `desktop_safe_to_capture_audio=false`
- `desktop_safe_to_request_permissions=false`
- `desktop_safe_to_start_asr_worker=false`
- `desktop_safe_to_call_remote_asr=false`
- `desktop_safe_to_call_llm=false`
- `desktop_safe_to_write_audio_chunks=false`

Add a Web workbench panel:

- `desktop-readiness-panel`
- Load the endpoint at startup and when the workbench is reset.
- Render status metrics, 8 phase rows, blockers, next decisions, and false safety flags.
- Startup remains passive: the workbench may load readiness and the fixture list, but it must not auto-create a demo session or write local session JSON until the user explicitly loads a fixture.

## Boundaries

- No microphone capture.
- No system audio capture.
- No macOS permission prompt.
- No Windows permission check.
- No CoreAudio, ScreenCaptureKit, WASAPI, loopback, virtual device, or native API access.
- No ASR worker process spawn.
- No ASR model load.
- No remote ASR call.
- No LLM call.
- No provider config, API key, environment secret, keychain, authorization header, bearer token, or `configs/local/` read.
- No file write, audio chunk write, or local data directory creation.
- No browser storage write.
- No automatic demo session write on passive workbench load.
- No installer, package, notarization, signing, app-store, or auto-update action.

## Readiness Semantics

The endpoint is not a permission checker. It is a contract boundary for the future desktop shell. It can expose the runtime's current high-level mode, but it must not infer permission state from the operating system, check device lists, or read local config.

The current product status is intentionally blocked:

- macOS Apple Silicon remains the first desktop target.
- Windows remains a second-stage adapter.
- Web MVP is still the active runtime.
- Capture, ASR worker, provider execution, and installer packaging are not safe to start.

## UI Contract

The panel renders a compact desktop readiness summary without becoming a setup wizard. It must not contain buttons that request permissions, start capture, start workers, open native settings, or write configuration.

The panel should make the next desktop decisions visible:

- Choose desktop shell runtime and process model.
- Define macOS audio capture permission UX.
- Define mic/system audio source separation.
- Define ASR worker lifecycle and resource limits.
- Define local data directory and retention policy.
- Define installer/signing/notarization path.

## Tests

- API test proves `GET /desktop/shell-readiness` returns the disabled preflight envelope and false scoped safety flags.
- API guard test proves the endpoint does not touch config/secret readers, environment secrets, `configs/local/`, keychain-like adapters, audio files, ASR workers, or remote calls.
- Static asset test proves the page contains `desktop-readiness-panel`, frontend loader/render functions, and endpoint string.
- Browser smoke proves the workbench renders `blocked_before_desktop_shell`, `8 phases`, `not_connected`, `not_requested`, `not_started`, and false `desktop_safe_to_*` flags.
- Docs gate proves PCWEB-079 is recorded in README, requirements traceability, acceptance, privacy/data-flow, project structure, implementation roadmap, and decision log.

## Implementation Status

- Status: implemented in the PCWEB-079 TDD increment.
- Backend files: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, `code/web_mvp/backend/tests/test_app.py`.
- Frontend files: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`, `app.js`, `styles.css`.
- Browser gate: `code/web_mvp/e2e/browser_smoke.mjs`.

## Verification Plan

- RED: focused API/static/docs/browser-script tests fail before implementation.
- GREEN: focused tests pass after implementation.
- Browser smoke: `node e2e/browser_smoke.mjs`.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- PC Web quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Final hygiene: remove test caches, confirm no app ports are left listening, and run local sensitive marker scan excluding `configs/local/**`.
