# Meeting Copilot Desktop

Tauri 2 desktop shell for Meeting Copilot. It owns native audio capture, local backend supervision, private storage, and secure AI provider credentials.

## Prerequisites

- Rust stable toolchain
- Platform requirements for Tauri 2
- A completed frontend build in `code/web_mvp/frontend_v2/dist`
- A prepared local runtime bundle for packaged releases

## Validate

```bash
cargo check --locked --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

## Build

Build the frontend first:

```bash
cd code/web_mvp/frontend_v2
npm ci
npm run build
```

Then run Tauri from the repository root:

```bash
cargo tauri build --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

Release packaging also needs the Python backend, local ASR workers, native audio helpers, and model manifests. See [`../../docs/development.md`](../../docs/development.md) for the verified repository workflow.

## Native Boundaries

- `src-tauri/src/native_mic_capture_runtime.rs`: Windows microphone capture.
- `src-tauri/src/native_system_audio_capture_runtime.rs`: Windows system audio capture.
- `native_mic/`: macOS microphone helper.
- `native_system_audio/`: macOS system audio helper.
- `src-tauri/src/provider_config_runtime.rs`: provider metadata and OS keychain integration.
- `src-tauri/src/desktop_backend_supervisor.rs`: local service lifecycle.

Audio and meeting data stay local unless the user configures an AI provider and invokes an AI feature. AI requests send meeting text, not source audio.
