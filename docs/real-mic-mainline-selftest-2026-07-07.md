# Real Mic Mainline Self-Test Report - 2026-07-07

## Scope

This run focused on the product mainline requested by the user:

1. Open the PC Web MVP frontend.
2. Start real microphone capture from Chrome on macOS.
3. Capture current ambient/external audio.
4. Stream PCM audio to the local realtime ASR WebSocket.
5. Persist the ASR session.
6. Run the same suggestion-card and approach-card chain on the persisted ASR session.
7. Fix any mainline break found during the run and add regression coverage.

## Environment

- Workspace: `/Users/chase/Documents/面试/meeting-copilot`
- Backend: `http://127.0.0.1:8765/`
- Backend process: `uvicorn meeting_copilot_web_mvp.app:app --host 127.0.0.1 --port 8765`
- Browser used for real mic: Google Chrome
- Mic device observed in permission prompt: `MacBook Air麦克风 (Built-in)`
- ASR provider path: local sherpa sidecar, CPU
- LLM gateway model in logs: `gpt-5.5`

## Initial Failure

The first real browser run reached the backend but produced no usable ASR:

- Session: `rec_mrapdl1q`
- Browser permission: allowed
- WebSocket: accepted
- sherpa sidecar: started
- Final backend log: `asr.stream.end chunks=0 finals=0`
- Persisted session: not created; `/live/asr/sessions/rec_mrapdl1q/events` returned `404`

Root cause: the frontend opened the WebSocket and microphone permission succeeded, but the browser audio graph did not reliably emit/send PCM frames. The old implementation created `AudioContext` inside `WebSocket.onopen`, did not explicitly `resume()` it, kept audio nodes only in local variables, and did not stop MediaStream tracks on stop. Chrome still showed the tab as using the microphone after stopping.

## Fix Implemented

Files changed for this mainline fix:

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- `code/web_mvp/backend/tests/test_workbench.py`

Main behavior changes:

- Keep stable references for microphone stream/source/processor/monitor nodes.
- Explicitly `await _micCtx.resume()` after microphone permission.
- Resample incoming browser audio to 16 kHz Float32 PCM.
- Send 300 ms Float32 PCM frames to the existing WebSocket protocol.
- Flush pending PCM before sending `END`.
- Stop MediaStream tracks and disconnect audio nodes on stop/close/error.
- Render raw realtime ASR `partial` and `final` events in the workbench.
- On WebSocket close, refresh `/live/asr/sessions/{sid}/events` so the page displays the persisted session and can run cards.

Regression tests added:

- real mic capture must resume audio, send PCM, and release tracks.
- real mic WebSocket close must refresh the persisted session.
- workbench must render raw realtime ASR partial/final events.

## Real Mic Verification

Fixed real mic session:

- Session: `rec_mraq1vmd`
- Browser state after start: `● 录制中`
- Realtime UI evidence: 23 ASR events within ~7 seconds, with visible Chinese partial text.
- Backend accepted WebSocket: `/live/asr/stream/ws/rec_mraq1vmd`
- sherpa sidecar started successfully.
- Stop action sent `END`.
- Backend finalized and persisted:
  - `asr.sidecar.end events=1`
  - `asr_correct.start chars=277`
  - LLM correction returned HTTP 200
  - `asr_correct.end tokens=1651 chars_out=278`
  - `asr.stream.persisted finals=1 events=1`
  - `asr.stream.end chunks=157 finals=1`
- Persisted API result:
  - `GET /live/asr/sessions/rec_mraq1vmd/events` returned HTTP 200
  - event count: 1
  - event type: `transcript_final`
  - transcript chars: 278
- Workbench refreshed to:
  - `ASR local_real_asr`
  - `会话 rec_mraq1vmd · 1 事件 (真实录音)`
  - `真实录音已落库，可生成建议卡片。`

Observed transcript topic: non-technical剧情/影视解说 audio from the user's current external playback. ASR quality was good enough to produce readable Chinese text and L2 correction improved several terms.

## Card Chain Verification

Same real mic session `rec_mraq1vmd`:

- `POST /live/asr/sessions/rec_mraq1vmd/llm-execution-runs`
  - HTTP 200
  - elapsed: ~0.01 s
  - run_count: 0
- `POST /live/asr/sessions/rec_mraq1vmd/approach-cards`
  - HTTP 200
  - elapsed: ~5.16 s
  - count: 0
  - LLM call returned HTTP 200

Interpretation: this is expected for the captured real mic content because it was not a technical meeting and produced no meeting-gap candidates.

Positive technical-meeting control session:

- Session: `positive_tech_1783433411`
- Input: synthetic technical meeting ASR events for payment-service rollout discussion.
- Event count: 17
- Suggestion candidate count: 3
- `POST /llm-execution-runs`
  - HTTP 200
  - elapsed: ~15.48 s
  - run_count: 3
  - completed cards: 3
- `POST /approach-cards`
  - HTTP 200
  - elapsed: ~9.37 s
  - count: 3
  - degraded: false
  - usage: 650 total tokens

This proves the same card chain works when the ASR transcript contains technical-meeting signals.

## Test Verification

Fresh test command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_mic_capture.py \
  code/web_mvp/backend/tests/test_e2e_mainline.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  -q -p no:cacheprovider
```

Result:

- `22 passed`
- `2 warnings`

## Current Conclusion

The previously broken mainline was real: the frontend was not sending PCM frames even after microphone permission and WebSocket startup. That is now fixed and verified.

The current PC Web MVP can now run this mainline:

`真实麦克风 -> WebSocket PCM -> local sherpa realtime ASR -> realtime partial/final display -> persisted ASR session -> suggestion-card endpoint -> approach-card endpoint`

The real mic test used the user's current external audio. It verified audio capture and Chinese realtime ASR. The card endpoints ran successfully on that same real session but returned zero cards because the captured content was not a technical meeting. A technical-meeting control session produced 3 suggestion cards and 3 approach cards, confirming the downstream product value path is alive.

## Remaining Product Risks

- Production-grade card value still requires a real technical meeting or realistic technical audio through the microphone, not generic external video/audio.
- L2 correction adds noticeable finalize latency: this run spent about 31 seconds in correction/finalization.
- Current frontend still uses `ScriptProcessorNode`, which works for MVP but should eventually be replaced with `AudioWorklet` for production stability.
- Existing historical uvicorn/sherpa worker processes from earlier work remain outside this fix; no new worker from this run was left hanging.
