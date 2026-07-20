# Meeting Copilot Frontend V2

Typed React projection for the Phase 1C live meeting workbench. It reads only
committed backend state from the V2 snapshot and event APIs. Production code
contains no fixture or demo meeting data.

## Production entry

With the backend running, open:

```text
http://127.0.0.1:<runtime-port>/workbench
```

`/workbench-v2` is an alias. `/workbench-legacy` is the retained legacy UI.
SSE is the default event transport. Set `VITE_EVENT_TRANSPORT=poll` only for
the explicit compatibility fallback. The runtime port is selected by the
current local backend or packaged desktop supervisor; do not assume that a
historical process on `8767` belongs to this worktree.

## Development

```bash
npm install
VITE_DEV_API_TARGET=http://127.0.0.1:<backend-port> npm run dev
```

The Vite development proxy requires an explicit local backend target so a
frontend worktree cannot silently connect to an unrelated historical process.
Open a specific meeting in development with:

```text
http://127.0.0.1:5174/workbench-assets/?meeting_id=<meeting-id>
```

Set `VITE_DEV_API_TARGET` for every development run, for example:

```bash
VITE_DEV_API_TARGET=http://127.0.0.1:8788 npm run dev -- --host 127.0.0.1 --port 5188
```

The target must be a loopback URL. Production and packaged builds continue to
use the same-origin backend selected by the desktop runtime.

## Backend contracts

- `POST /v2/meetings`
- `GET /v2/meetings`
- `DELETE /v2/meetings/{meeting_id}`
- `GET /v2/meetings/{meeting_id}/snapshot`
- `GET /v2/meetings/{meeting_id}/events?after_seq=N`
- `GET /v2/meetings/{meeting_id}/transcript`
- `GET /v2/meetings/{meeting_id}/audio`
- `GET /v2/meetings/{meeting_id}/audio/content`
- `POST /v2/meetings/{meeting_id}/end`
- `PUT /v2/meetings/{meeting_id}/suggestions/{suggestion_id}/feedback`
- `GET /v2/meetings/{meeting_id}/traces`
- `POST /v2/traces/{trace_id}/ui-rendered`
- `GET /v2/storage/preflight`

All listed contracts exist in the current backend. The frontend deliberately
surfaces backend errors instead of pretending a command succeeded locally.
Production UI contains no fixture or demo meeting entry.

Realtime recording uses a recoverable chunk journal. Ending capture seals that
journal first; WAV assembly runs in a lease-protected backend worker. The UI
shows `录音整理中` until `recording.export.ready` arrives over SSE, then reloads
the audio contract and enables playback. A stale pre-export WAV is never exposed
as the current recording.

Headless responsive smoke and controlled-WAV relay tests do not replace real
microphone verification. The latest visible-Chrome V2 real microphone evidence
is `artifacts/tmp/browser_live_mic/v2-real-mic-mainline-20260715/report.json`.
