# PC Workbench Full Chain Selftest Report

> Date: 2026-07-09
> Status: main PC Workbench function chain closed for no-cost selftest; production remote LLM acceptance was later completed in `docs/pc-workbench-production-acceptance-report-2026-07-09.md`.
> Scope: stable local Workbench, browser UI controls, recorded-file import, real browser microphone via local speaker output, ASR, realtime suggestions, formal derivations, minutes, history, export, and delete.

## 1. Executive Conclusion

The main PC Workbench product chain is now functionally runnable end to end in the current workspace:

```text
stable launch on 127.0.0.1:8765
  -> Workbench page loads
  -> audio input reaches ASR
  -> transcript appears
  -> suggestions / approach / minutes are generated
  -> history and exports work
  -> delete clears the session
```

This conclusion has two important boundaries:

- **Go for no-cost product-function selftest:** real browser microphone + local speaker playback produced a valid ASR transcript, generated 3 suggestion cards, 1 approach card, 247 chars of minutes, and verified delete.
- **Historical boundary for this report:** this no-cost run used `no_cost_deterministic` derivation, so `counts_as_production_llm_evidence=false`.
- **Later production update:** the separate production run in `docs/pc-workbench-production-acceptance-report-2026-07-09.md` is now the source of truth for real remote OpenAI-compatible gateway acceptance. That later evidence is `production_enabled`, `llm_provider=real_gateway`, `gateway_base_url_kind=remote`, `counts_as_production_llm_evidence=true`, `llm_call_count=5`, and `llm_usage_total_tokens=24116`.

## 2. What Changed This Round

### 2.1 Explicit No-Cost Derivation Selftest

Root cause of the previous real-mic full-chain failure:

- Real microphone capture and ASR had worked.
- Formal derivation endpoints returned 409 because `LLM_GATEWAY_IS_MOCK=true` is intentionally blocked on production endpoints.
- This was a correct safety guard, not a page rendering bug.

Fix:

- Workbench now supports explicit no-cost selftest via `?noCostDerivationSelfTest=1` or `localStorage.meetingCopilotNoCostDerivationSelfTest=1`.
- In that mode only, formal derivations use:
  - `/live/asr/demo/sessions/{session_id}/...`
  - `mode=deterministic_demo`
  - `llm_provider=deterministic_demo`
  - `llm_call_status=not_called`
  - `cost_status=no_cost`
- Default user/product path remains production `mode=enabled` on `/live/asr/sessions/{session_id}/...`.

Files:

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- `code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs`
- `code/web_mvp/backend/tests/test_workbench.py`

### 2.2 E2E Evidence Boundary

`workbench_browser_live_mic_verify.mjs` now records:

- `derivation_mode`
- `derivations_generated`
- `llm_provider`
- `gateway_base_url_kind`
- `counts_as_production_llm_evidence`

This prevents no-cost selftest evidence from being misreported as production remote LLM acceptance.

### 2.3 Chrome Profile Cleanup Stability

`workbench_smoke.mjs` previously completed the business checks but exited with `ENOTEMPTY` while removing Chrome profile temp files. The E2E cleanup now retries recoverable `ENOTEMPTY` / `EBUSY` / `EPERM` errors.

Files:

- `code/web_mvp/e2e/workbench_smoke.mjs`
- `code/web_mvp/e2e/workbench_all_buttons_smoke.mjs`
- `tests/test_workbench_all_buttons_smoke.py`

## 3. Fresh Evidence

### 3.1 Stable Product Launch

Command:

```bash
python3 tools/workbench_server.py status --port 8765
```

Result:

```json
{
  "status": "running",
  "port": 8765,
  "pid": 48463,
  "health_ok": true,
  "workbench_url": "http://127.0.0.1:8765/workbench"
}
```

The in-app browser was also opened to `http://127.0.0.1:8765/workbench` and verified:

- title: `会议助手`
- `开始会议` enabled
- `导入录音` enabled
- `历史记录` enabled
- session-only actions present and disabled before a session
- status showed microphone and realtime ASR available

### 3.2 All-Buttons Browser UI Smoke

Command:

```bash
node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
```

Result:

```json
{
  "status": "go_workbench_all_buttons_smoke",
  "imported_session_id": "file_12476cbc83e9",
  "fake_llm_request_count": 12,
  "downloads": [
    "file_12476cbc83e9.transcript.txt",
    "file_12476cbc83e9.minutes.md"
  ],
  "final_state": {
    "currentSession": null,
    "utterances": 0,
    "suggestions": 0,
    "approaches": 0,
    "sessionMeta": "准备开始"
  }
}
```

Artifact:

```text
artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/workbench_all_buttons_report.json
```

Covered controls:

- `试用示例`
- history open and reopen
- browser file import with `DOM.setFileInputFiles`
- formal suggestions
- evidence clickback
- approach cards
- minutes
- refresh transcript
- pause/resume auto suggestions
- one-click organize
- transcript export
- minutes export
- delete with confirm

### 3.3 Workbench Core Smoke

Command:

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

Result:

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

### 3.4 Real Browser Microphone, Local Speaker Playback

Command shape:

```bash
MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE=no_cost_deterministic \
MEETING_COPILOT_BROWSER_MIC_HEADLESS=false \
MEETING_COPILOT_BROWSER_MIC_FAKE_UI=true \
MEETING_COPILOT_BROWSER_MIC_SECONDS=34 \
node code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
```

Local audio prompt was played by macOS `say -v Tingting` after recording started.

Result:

```json
{
  "artifact_root": "artifacts/tmp/browser_live_mic/real-speaker-mic-nocost-20260709-164605",
  "input_mode": "real_browser_mic",
  "ui_coverage": "visible_chrome",
  "health_status": "audio_capture_health_passed",
  "chunk_count": 113,
  "asr_final_count": 1,
  "derivation_mode": "no_cost_deterministic",
  "derivations_generated": true,
  "counts_as_production_llm_evidence": false,
  "suggestion_card_count": 3,
  "approach_card_count": 1,
  "minutes_char_count": 247,
  "delete_verified": true
}
```

Key ASR evidence:

```json
{
  "provider": "sherpa_onnx_realtime",
  "provider_mode": "real",
  "is_mock": false,
  "asr_fallback_used": false,
  "degradation_reasons": [],
  "acceptance_eligible": true,
  "acceptance_blockers": []
}
```

Semantic quality:

```json
{
  "status": "passed",
  "technical_entity_hit_count": 12,
  "technical_group_hit_count": 4,
  "matched_entity_groups": ["release_control", "reliability", "ownership", "action"]
}
```

UI verification:

```json
{
  "workbench_same_session_visible": true,
  "frontend_utterance_count": 1,
  "frontend_card_count": 4,
  "frontend_minutes_visible": true,
  "browser_console_error_count": 0,
  "network_error_count": 0
}
```

Artifacts:

```text
artifacts/tmp/browser_live_mic/real-speaker-mic-nocost-20260709-164605/browser_mic_health_report.json
artifacts/tmp/browser_live_mic/real-speaker-mic-nocost-20260709-164605/asr_probe.json
artifacts/tmp/browser_live_mic/real-speaker-mic-nocost-20260709-164605/ui_verification.json
artifacts/tmp/browser_live_mic/real-speaker-mic-nocost-20260709-164605/session_events.json
artifacts/tmp/browser_live_mic/real-speaker-mic-nocost-20260709-164605/workbench-browser-live-mic.png
```

### 3.5 Chrome Fake-Audio-File Mic Diagnostic

Command shape:

```bash
MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE=no_cost_deterministic \
MEETING_COPILOT_BROWSER_MIC_AUDIO_FILE=code/asr_runtime/outputs/simulated-release-review.16k.wav \
node code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
```

Result: **No-Go for fake-audio-file diagnostic.**

Evidence:

```json
{
  "input_mode": "fake_audio_file_browser_mic",
  "health_status": "blocked_audio_too_quiet",
  "rms": 0,
  "peak": 0,
  "active_sample_ratio": 0,
  "asr_final_count": 0
}
```

The WAV itself is not silent:

```json
{
  "duration": 17.93,
  "rms": 4127.09,
  "peak": 24082,
  "active_ratio_gt_500": 0.7469
}
```

Interpretation:

- The file is valid and audible.
- Chrome fake media file injection produced zero samples inside the Workbench AudioContext on this run.
- This is a diagnostic lane failure, not a product microphone failure, because real browser microphone with local speaker playback passed.

Artifact:

```text
artifacts/tmp/browser_live_mic/fake-audio-browser-mic-nocost-20260709-164435/asr_probe.json
```

## 4. Verification Commands

Fresh commands run after implementation:

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_workbench_server_tool.py \
  tests/test_workbench_all_buttons_smoke.py \
  tests/test_fake_llm_gateway_script.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_packaged_no_cost_demo_derivation.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_real_llm_path.py
```

Result:

```text
92 passed, 2 warnings
```

```bash
node --check code/web_mvp/e2e/workbench_smoke.mjs
node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
node --check code/web_mvp/e2e/fake_llm_gateway.mjs
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
python3 -m py_compile tools/workbench_server.py
git diff --check
```

Result: all passed.

Additional E2E:

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
```

Result: both exited 0.

Temporary E2E ports checked after runs:

```text
8771 / 8777 / 8778 / 9237 / 9238: no listener
```

## 5. Current Supported Functions

Supported and freshly self-tested:

- Stable local product launch at `http://127.0.0.1:8765/workbench`.
- Workbench page loads in the in-app browser.
- Demo meeting flow.
- Recorded-file import through the browser file input.
- Local FunASR file-lane conversion.
- Real browser microphone capture from local speaker playback.
- Realtime ASR with `sherpa_onnx_realtime`.
- ASR semantic quality gate on Chinese technical meeting content.
- Transcript rendering.
- Realtime suggestion candidate rendering.
- Formal suggestion cards in no-cost selftest and fake-gateway UI smoke.
- Approach cards.
- Minutes.
- History list and reopen.
- Evidence clickback.
- Refresh transcript.
- Auto suggestion pause/resume.
- One-click organize.
- Transcript export.
- Minutes export.
- Session delete and visible reset.

## 6. Remaining Gaps

These are not hidden and should not be described as complete:

1. **Production remote LLM quality acceptance is still pending.**
   The real-mic full-chain pass used deterministic no-cost derivations. It proves product wiring, not real model recommendation quality.

2. **Chrome fake-audio-file mic diagnostic is blocked.**
   The current Chrome fake device/file route produced zero samples. Real mic worked, so this is lower priority unless we want a fully headless deterministic mic lane.

3. **Public release is still separate.**
   Developer ID signing, notarization, Gatekeeper, and Windows real-machine verification remain outside this PC Web Workbench function gate.

4. **Manual user meeting acceptance is still valuable.**
   Automation proved the local speaker-to-mic lane. A real human meeting with your normal microphone/input setup should still be the final product feel check.

## 7. Recommended Next Step

Do not continue broad audits now. The next focused step should be one of:

1. Run one production remote LLM acceptance lane with a real non-mock gateway and cost cap, then compare the generated suggestions/minutes quality against the no-cost selftest.
2. Improve the Workbench UI copy and layout based on the now-working mainline, because functional completeness is no longer the main blocker for local PC Web usage.
3. Add a long-meeting soak run using the same selftest boundary to check memory, card frequency, and transcript usability over 20-60 minutes.

Recommended order:

```text
production remote LLM acceptance
  -> UI simplification polish
  -> long meeting soak
  -> packaged desktop release gate
```
