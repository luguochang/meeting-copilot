# Talktrace Frontend

React + TypeScript workbench for meetings, live transcription, AI assistance, notes, review, exports, and local capability management.

## Commands

```bash
npm ci
npm run dev
npm run lint
npm run typecheck
npm test
npm run build
```

Development requires an explicit backend target:

```bash
VITE_DEV_API_TARGET=http://127.0.0.1:8765 npm run dev
```

On PowerShell:

```powershell
$env:VITE_DEV_API_TARGET = "http://127.0.0.1:8765"
npm run dev
```

The production build is written to `dist/`. The FastAPI service exposes it at `/workbench` and serves hashed assets from `/workbench-assets/`.

## Source Layout

- `src/features/` contains product workflows.
- `src/components/` contains shared UI and diagnostics.
- `src/api/` contains local API clients and contracts.
- `src/domain/` contains meeting events and state types.
- `src/desktop/` contains the Tauri bridge boundary.
- `src/styles.css` contains the shared product visual system.

Browser builds use the local FastAPI service. Desktop builds additionally use Tauri commands for native audio, secure provider configuration, and runtime supervision.
