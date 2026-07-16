# macOS capture and bundle spike

This isolated Phase 0 spike answers two high-risk questions without changing the
current Tauri application:

1. Can an arm64/x86_64 macOS process capture microphone and ScreenCaptureKit
   system audio into separate local tracks?
2. Can the existing backend and FunASR worker entrypoints start from explicit
   executable commands before a self-contained runtime is assembled?

## Build and permission probe

```bash
./build.sh
./run.sh
```

The default `run.sh` invocation uses `--mode probe --no-request-permissions`. It
does not display a privacy prompt or capture audio.

## Short capture

```bash
./run.sh --mode mic --duration 3 --output-dir .build/manual-mic
./run.sh --mode system --duration 3 --output-dir .build/manual-system
./run.sh --mode both --duration 3 --output-dir .build/manual-both
```

Capture modes request missing permissions by default. Pass
`--no-request-permissions` for automation. macOS grants microphone and Screen
Recording permission to the launching terminal/application; permission changes
normally require restarting that host process.

The command returns `0` only if every requested track contains samples. It
returns `2` for denied permissions, startup failures, write failures, partial
capture, or a completed capture with no samples. In all capture cases it writes
JSON evidence describing each track. Existing output tracks are never
overwritten.

## Bundle feasibility

```bash
./bundle_feasibility.py --timeout 2.5
```

This starts the backend long enough to request `/health` and invokes the FunASR
worker with `--help`. It does not import/load model weights, copy files, download
dependencies, call remote services, or claim that a distributable bundle exists.
