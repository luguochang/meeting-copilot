# Workbench Real Mic Mainline Fix Report - 2026-07-07

## Context

User-reported symptom: after opening the microphone in the PC Web workbench, visible transcript text disappeared or looked unreliable.

Latest local URL:

- `http://127.0.0.1:8765/workbench`

## Root Causes Found

1. The browser could be looking at a stale workbench bundle.
   - The old HTML referenced `/static/workbench.js` without a version.
   - After frontend changes, the browser could continue using old behavior.

2. The "刷新实时文字" button used an SSE replay path during recording.
   - Recording sessions are persisted only after `END`.
   - Refreshing while recording could hit a not-yet-persisted or stale session path, making the UI look broken.

3. Ending a meeting entered a long "processing" state while LLM ASR correction ran.
   - Real mic audio was already recognized, but the UI did not clearly explain that final text was being corrected.
   - Users could reasonably think the text was gone.

4. Local fake LLM gateway tests were blocked by system proxy inheritance in `httpx`.
   - `httpx.Client` inherited proxy settings and returned `502` for `127.0.0.1:18767`.
   - This can also break real local OpenAI-compatible providers or local relay stations.

## Changes Made

- Added a workbench script version: `/static/workbench.js?v=20260707-mainline`.
- Reworked "刷新实时文字":
  - During recording, it now explains that realtime text is already auto-updating.
  - After recording, it refreshes from `/live/asr/sessions/{session_id}/events`.
  - Removed stale frontend `EventSource` replay client code from the button path.
- Preserved visible realtime text during final processing.
  - The status now says: "正在整理最终文字，当前实时文字会保留在这里。"
- Added a shared frontend render helper for loaded/persisted session events.
- Changed `HttpxLlmClient` to use `httpx.Client(..., trust_env=False)` so local providers are not hijacked by system proxy settings.

## Real Mic Evidence

Manual browser run on the in-app browser with real microphone:

- Page loaded script: `/static/workbench.js?v=20260707-mainline`
- Start meeting:
  - Browser console showed microphone authorization succeeded.
  - WebSocket connected.
  - Live transcript appeared in the main panel.
- Refresh during recording:
  - Text remained visible.
  - UI displayed: "实时文字正在自动更新。结束会议后会整理成完整文字。"
- End meeting:
  - Realtime text stayed visible while processing.
  - Backend received `100` audio chunks.
  - ASR correction took about `13.5s`.
  - Session `rec_mrasvvgi` persisted with `1` final transcript event.
  - Page returned to "开始会议" and showed "已生成文字".

Observed final text quality is still imperfect under ambient/external audio. That is an ASR quality/provider issue, not a broken UI/microphone pipeline.

## Verification

Fresh verification commands run after fixes:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_minutes.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_mic_capture.py \
  -q -p no:cacheprovider
```

Result:

- `37 passed, 2 warnings`

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

Result:

- `workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified`

## Current Status

The main PC Web MVP path is now usable for:

- real microphone start/stop
- realtime transcript display
- persisted final transcript after meeting end
- history list
- sample meeting
- generated meeting suggestions
- approach analysis
- post-meeting review
- delete/reset state

Current dev server is running at:

- `http://127.0.0.1:8765/workbench`
