# Full Chain Completion Implementation Plan

> Date: 2026-07-09
> Status: implemented for the PC Workbench browser-live-mic lane; public release and cross-OS gates remain separate.
> Goal: make the PC Workbench main product flow start reliably, run every user-facing function through UI automation, and produce current evidence for real microphone/audio input.

## 0. Current Result Snapshot

As of 2026-07-09, W1-W4 are implemented and have current evidence:

- Stable `127.0.0.1:8765` Workbench launch was verified.
- All-buttons browser smoke covered demo, history, import, suggestions, evidence clickback, approach, minutes, refresh, auto-suggestion pause/resume, organize, transcript export, minutes export, and delete.
- Real browser microphone with local speaker playback passed using local `sherpa_onnx_realtime` ASR.
- No-cost selftest is explicitly separated from production LLM proof.
- Production LLM proof passed in `docs/pc-workbench-production-acceptance-report-2026-07-09.md` with remote non-mock OpenAI-compatible gateway, `llm_call_count=5`, and `llm_usage_total_tokens=24116`.
- The current release-level summary that points at the strict production browser-live-mic evidence is `artifacts/tmp/release_acceptance/release-current-20260709-production-browser-llm-evidence-fixed/summary.json`.
- Evidence gate policy is now strict: `production_enabled` browser-live-mic evidence without remote gateway, `counts_as_production_llm_evidence=true`, non-zero `llm_call_count`, and non-zero `llm_usage_total_tokens` is No-Go.

Remaining outside this PC Workbench production-LLM lane:

- W5 packaged full user-click flow.
- Mac Developer ID signing, notarization, and Gatekeeper acceptance.
- Windows real-machine verification.
- Long meeting production soak with real cost/performance controls.
- Human product-feel acceptance in a real meeting.

## 1. Completion Definition

The project is not complete until the following chain has fresh evidence from the current workspace:

```text
stable launch
  -> Workbench opens at the product URL
  -> audio input enters ASR
  -> transcript appears
  -> AI suggestions / approach / minutes are generated
  -> history can reopen the session
  -> transcript/minutes can export
  -> delete clears the session
  -> evidence files state Go or a precise blocker
```

External release checks do not replace this function-completion gate.

## 2. Non-Negotiable Evidence Rules

- A button is complete only when an automated browser test clicks it and verifies the page or artifact result.
- A backend/API test is useful but not enough for a user-facing control.
- Historical evidence is useful context but must be labeled historical.
- Paid LLM calls are used only when validating production LLM integration. UI closure should use a local fake OpenAI-compatible gateway.
- Remote paid ASR stays disabled by default. ASR should use local sherpa/FunASR paths unless explicitly changed.
- User secrets and `configs/local/` must not be read.
- No-cost full-chain selftest must be explicit. It may use `?noCostDerivationSelfTest=1` to route Workbench derivations through `/live/asr/demo/sessions/{id}/...` with `mode=deterministic_demo`, but that evidence counts only as UI/business-chain closure, not production LLM proof.
- Production LLM proof requires `production_enabled`, a non-mock remote OpenAI-compatible gateway, non-zero LLM usage evidence, and `LLM_GATEWAY_IS_MOCK!=true`. A local fake gateway or deterministic no-cost mode must never be reported as production LLM acceptance.

## 3. Work Items

### W1. Stable 8765 Product Launch

Problem: Tauri, release tools, docs, and the user-visible Workbench assume `127.0.0.1:8765`, but the live process may be on `8000` or absent.

Implementation:

- Add `tools/workbench_server.py` with `start`, `stop`, and `status`.
- Default port: `8765`.
- Default data dir: `artifacts/tmp/web_mvp_data`.
- PID/log files: `artifacts/tmp/workbench_server/workbench_server.pid` and `.log`.
- `status` must report health URL, workbench URL, PID, and whether the listener is reachable.
- `start` must not read `configs/local/` and must not inject paid provider config.
- If the port is occupied by an unknown process, return a blocker instead of killing it.

Verification:

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q tests/test_workbench_server_tool.py
python3 tools/workbench_server.py status --port 8765
python3 tools/workbench_server.py start --port 8765
curl -fsS http://127.0.0.1:8765/health
curl -fsS http://127.0.0.1:8765/workbench
curl -fsS http://127.0.0.1:8765/workbench | rg "workbench.js\\?v=|本场会议|会议驾驶舱|transcript-mode-label"
python3 tools/workbench_server.py stop --port 8765
```

Runtime freshness rule:

- UI validation must compare source expectations with the actual HTML served from `127.0.0.1:8765`.
- If `/workbench` still serves an old cache buster or old labels, restart the controlled 8765 server before judging the UI.
- 2026-07-11 observed failure mode: browser and `curl` served `workbench.js?v=20260711-focus-filter2` and `会议状态/会议重点` while source had `workbench.js?v=20260711-cockpit-ux1` and `本场会议/重点筛选`; restarting `tools/workbench_server.py` fixed the mismatch.

### W2. Workbench All-Buttons UI Smoke

Problem: current smoke covers many controls but not all user-facing actions.

Implementation:

- Add or extend a no-cost browser E2E script to click:
  - `试用示例`
  - `历史记录`
  - history item reopen
  - `生成会议建议`
  - evidence quote clickback
  - `分析方案利弊`
  - `生成会议纪要`
  - `刷新文字`
  - `暂停/恢复自动建议`
  - `整理会议`
  - `导出文字稿`
  - `导出纪要`
  - `删除本次会议`
  - `导入录音`
- For import, use an approved local test WAV under `code/asr_runtime/outputs/simulated-release-review.16k.wav` if available.
- If FunASR file conversion is unavailable, the script must write a clear blocker rather than silently skipping import.
- The left meeting cockpit is part of this UI gate, not decoration:
  - The visible left-column sections are now `本场会议` and `重点筛选`; the accessible role remains meeting cockpit.
  - `本场会议` must remain visible as the mainline status projection for transcript, realtime reminders, AI suggestions, approach analysis, recording saved, and minutes generated.
  - `重点筛选` must remain actionable for candidate-reminder filtering when a type has at least one reminder: decision, action item, risk, and open question.
  - On narrow/mobile layouts, the meeting cockpit must appear before realtime transcript/detail panels so users can understand the meeting state before digging into content.
- Business boundary for the left column:
  - It is the meeting cockpit, not a duplicate transcript list. Its first job is to answer whether the real-time meeting product chain is alive: text is arriving, reminders are being derived, AI cards/minutes are generated, and the recording is saved.
  - Its second job is in-meeting triage. The four `重点筛选` rows are controls that filter the realtime reminder stream, so a user can quickly switch between decisions, action items, risks, and open questions while the meeting is still running.
  - It is not the place for long-form explanation, raw event ids, mock/demo-only data, or final minutes content. Those belong in the realtime transcript, suggestion, approach, minutes, and history panels.
  - Current implemented scope: status projection, cockpit phase badge, accessible meeting-cockpit name, focus counts, candidate filtering, zero-count filter disabled state, responsive discoverability.
  - Current implemented navigation: the six `本场会议` rows are actionable overview jumps. `文字记录` jumps to the realtime transcript, `实时提醒` jumps to the reminder panel, `AI 建议` jumps to suggestion cards, `方案分析` jumps to approach analysis, `录音保存` jumps to the audio export action when saved, and `会后复盘` jumps to minutes. Empty states explain why content is not available yet instead of behaving like dead metrics.
  - Current UX risk reduced: the empty state still has `0 / 未保存 / 未生成`, but `本场会议` names the role, `重点筛选` rows start disabled until that type has content, and the realtime transcript title shows whether the page is `待开始 / 已记录 + 正在听 / 整理中 / 已记录`.
  - Planned scope before production acceptance:
    - Verify the cockpit during long real-mic recording and persist `recording_phase_ui_samples[].cockpit_counts`.
    - Confirm `文字记录` and `实时提醒` grow while text appends, not only after the meeting ends.
    - Confirm the realtime transcript projection is append-first: stable partial chunks appear as `已记录`, while only the current ASR tail mutates as `正在听`.
    - Confirm the live WebSocket stream carries derived `suggestion_candidate_event` messages during recording; the UI must not rely on the post-stop `/live/asr/sessions/{sid}/events` snapshot to make `实时提醒` appear.
    - For every real-mic evidence run, compare recording-phase cockpit counts with final session event counts and document any drift. Expected drift is allowed only for events that are genuinely created after stop/finalization; stable partial/final candidates created during recording must be visible during recording.
    - Confirm `AI 建议`、`方案分析`、`录音保存`、`会后复盘` reflect the same session after organize/export.
    - Confirm `cockpitStage` transitions through the same session lifecycle: `待开始 -> 录音中/整理中 -> 已记录 -> 已复盘 -> 待开始 after delete`.
    - Confirm zero-count focus filters are disabled and are reported as `disabled_zero_count_filter` in `workbench_all_buttons_smoke.mjs`; non-empty filters must remain clickable and must filter the right-side realtime reminder panel.
    - Keep `本场会议` overview jumps covered by browser E2E `overview_jump_coverage`. If any target exists in session data but the click lands on an empty panel, treat it as a mainline sync bug.
    - P1: upgrade reminder/status rows from panel-level navigation to concrete evidence/action handling only after adding keyboard-order and accessibility tests. Examples: jump to exact source quote, copy a suggested follow-up question, mark a reminder handled, dismiss/mark wrong, and show stale/out-of-window state.
    - P1 backend support before exact click-to-evidence: add a read-only cockpit summary only if front-end derivation keeps drifting; persist `approach_cards[].evidence_span_ids/evidence_spans` instead of only `evidence_quote`; standardize `partial_hint_event` evidence ids when stable final evidence exists. Do not rewrite the event bus or build a full task-management lifecycle before the core meeting chain is production-usable.

Verification:

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
```

Latest evidence:

```text
artifact=artifacts/tmp/ui_screenshots/workbench-all-buttons-cockpit-ux-disabled-20260711-112328
status=go_workbench_all_buttons_smoke
focus_filter_coverage includes ActionItem=disabled_zero_count_filter
downloads verified: transcript.txt / minutes.md / audio.wav
served-page screenshot after 8765 restart:
artifacts/tmp/ui_screenshots/current-workbench-cockpit-reload-20260711/01-current-workbench-after-server-restart.png

artifact=artifacts/tmp/ui_screenshots/workbench-all-buttons-overview-jump-focus-20260711-122831
status=go_workbench_all_buttons_smoke
overview_jump_coverage:
  transcript=clicked_navigation
  reminders=clicked_navigation
  suggestions=clicked_navigation
  approach=clicked_navigation
  audio=clicked_navigation
  minutes=clicked_navigation
overview_jump_focus_state:
  all six targets active_element_matches=true
  all six targets target_in_viewport=true
  all six targets toast_after_click_matches=true
screenshot_count=23
```

### W3. Real Microphone Browser Chain

Problem: historical browser-live-mic evidence exists, but current completion requires a fresh run.

Latest post-navigation recheck:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-post-overview-jump-3min-nocost-20260711-123349
input_mode=real_browser_mic
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
health_status=audio_capture_health_passed
chunk_count=600
suggestion_card_count=3
approach_card_count=1
minutes_char_count=354
audio_export_http_status=200
audio_sha256_matches_session=true
frontend_utterance_count=12
frontend_card_count=4
frontend_minutes_visible=true
meeting_cockpit_stage=已复盘/reviewed
live_reminder_drift_status=passed
first_text_after_audio_active_latency_ms=7195
first_final_after_audio_active_latency_ms=180336
```

Current interpretation:

- The same-session main chain still works after the left overview navigation change.
- Recording-phase samples prove realtime text appends during the meeting (`before_stop.partial_draft_count=41`).
- This does not close production readiness because it is only 3 minutes, no-cost deterministic, and final ASR latency remains near stop.
- Follow-up was partially completed by DEC-327: the 10-minute no-cost real mic soak passed after normalizer v5, and normalizer v6 fixed the residual terms found in that artifact.
- DEC-328 added a machine-readable realtime UX gate. A fresh 45-second real-mic run passed with first visible text at 8247 ms and first final at 45059 ms. The 10-minute artifact would still be classified as realtime-text pass plus slow-final warning, so final latency remains open rather than being hidden.
- DEC-329 hardened that gate after independent review: audio health, recording-time visibility, legal SLO values and backend reminder probes now fail closed. A separate `mainline_completion_status` also requires ASR acceptance, cards, approach, minutes, evidence, same-session reviewed UI and matching audio export. Historical 45-second/10-minute successful artifacts still pass strict offline reclassification; fresh acoustically contaminated real-mic and quiet fake-audio runs correctly exit No-Go.
- DEC-330 hardened formal realtime suggestions: each automatic API request executes at most one remote candidate, partial hints no longer make guaranteed-to-fail formal LLM calls before final, END/finalize final is persisted before browser delivery, and an in-flight request coalesces one pending trigger. The next production LLM rerun must use this behavior after a controlled backend restart that preserves the configured gateway environment.
- DEC-331 added a separate production recording-time formal AI gate. Three latest production real-mic artifacts contain 18/19/25 recording samples but `max_recording_ai_suggestions=0`, so they are explicit No-Go evidence even though realtime text and audio saving worked. A separate bounded production file-chain run proved local offline FunASR -> real `gpt-5.5` suggestion/approach/minutes with evidence, localizing the open issue to realtime final/candidate/card delivery during microphone recording.
- Remaining follow-up: controlled Chinese technical speaker-to-mic production rerun that passes `realtime_ai_suggestion_status`, endpoint/final latency work if it fails, 20-minute real mic gate, natural multi-speaker Chinese quality, and real user meeting acceptance.

Latest 10-minute no-cost recheck:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-normalizer-v5-10min-nocost-20260711-135007
input_mode=real_browser_mic
health_status=audio_capture_health_passed
chunk_count=2000
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=402
audio_export_http_status=200
audio_sha256_matches_session=true
frontend_utterance_count=37
frontend_card_count=4
frontend_minutes_visible=true
meeting_cockpit_counts.transcript=37
meeting_cockpit_counts.realtime_reminders=46
live_reminder_drift_status=passed
first_text_after_audio_active_latency_ms=6694
first_final_after_audio_active_latency_ms=288627
```

Implementation:

- Use `code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs`.
- The verifier must write machine-readable evidence, not only stdout:
  - `summary.json`
  - `summary.json -> first_text_after_audio_active_latency_ms / first_final_after_audio_active_latency_ms`
  - `summary.json -> realtime_experience_status / realtime_experience_report`
  - `summary.json -> realtime_ai_suggestion_status / realtime_ai_suggestion_report`
  - `summary.json -> max_recording_ai_suggestions / first_ai_suggestion_visible_latency_ms`
  - `summary.json -> mainline_completion_status / mainline_completion_report`
  - `summary.json -> meeting_cockpit_stage / meeting_cockpit_counts`
  - `summary.json -> frontend_card_count / frontend_minutes_visible / browser_console_error_count / network_error_count`
  - `summary.json -> live_reminder_drift_status / live_reminder_drift_report`
  - `recording_phase_ui_samples.json`
  - `recording_phase_ui_samples.json -> samples[].cockpit_stage`
  - `recording_phase_ui_samples.json -> samples[].cockpit_counts.realtime_reminders`
  - `recording_phase_ui_samples.json -> samples[].backend_live_reminder_count`
  - `session_events.json -> suggestion_candidate_event + partial_hint_event count`
  - `ui_verification.json -> meeting_cockpit_stage`
  - `ui_verification.json -> meeting_cockpit_counts`
  - `audio_export_probe.json`
- Run two separated derivation modes:
  - `MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE=no_cost_deterministic`: proves real browser microphone/audio capture, ASR, Workbench UI propagation, suggestion cards, approach cards, minutes, history, and delete without calling a paid gateway. Evidence must say `counts_as_production_llm_evidence=false`.
  - `MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE=production_enabled`: proves the production remote LLM path only when a non-mock remote gateway is configured. Evidence must say `counts_as_production_llm_evidence=true`; otherwise it is a blocker, not a pass.
- Chrome fake-audio-file mode is no longer a required gate on this machine. Visible and headless diagnostics both produced zero RMS/peak and there is no virtual input device; retain the artifacts as environment No-Go evidence and do not repeat without a routing change.
- Run real microphone mode using local speaker output:
  - Use macOS `say -v Tingting` or `afplay` to play the technical meeting prompt.
  - Start Workbench recording before playback.
  - Stop recording after playback.
  - Verify transcript, suggestions, approach, minutes, history, and delete.
- Write artifacts under `artifacts/tmp/browser_live_mic/current-real-speaker-mic-20260709/`.

Expected blockers:

- Browser microphone permission denied.
- Chrome fake media route unavailable.
- Mac input device cannot hear built-in speaker.
- ASR produces no non-empty final.
- ASR semantic quality blocks suggestions.
- LLM provider unavailable when real gateway mode is requested.

Verification:

```bash
MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE=no_cost_deterministic \
MEETING_COPILOT_BROWSER_MIC_AUDIO_FILE=code/asr_runtime/outputs/simulated-release-review.16k.wav \
MEETING_COPILOT_BROWSER_MIC_SECONDS=12 \
node code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
```

```bash
# Real speaker-to-mic run: browser captures actual Mac microphone while local audio plays.
# A separate shell or subshell plays the meeting prompt after recording starts.
MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE=no_cost_deterministic \
MEETING_COPILOT_BROWSER_MIC_HEADLESS=false \
MEETING_COPILOT_BROWSER_MIC_SECONDS=28 \
node code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
```

### W4. File Import And Export Real Download

Problem: file import and export need browser-level proof, not only API proof.

Implementation:

- Use CDP `DOM.setFileInputFiles` to set `#btn-upload`.
- Wait for imported transcript and session source badge.
- Generate minutes.
- Use CDP download behavior or anchor interception to verify:
  - transcript filename ends in `.transcript.txt`,
  - minutes filename ends in `.minutes.md`,
  - URLs use `apiUrl()`.
- If true browser download-to-file is not supported in current CDP helper, record a blocker and keep anchor-click validation.

Verification:

```bash
node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
```

### W5. Tauri Packaged User Flow

Problem: packaged no-cost runtime probe exists, but not a full user-click flow.

Implementation:

- Keep Web Workbench as the main shared UI.
- Use packaged runtime probe as baseline.
- Add a no-cost packaged click-flow probe only after W1-W4 are green.
- Do not claim public release until signing/notarization/Gatekeeper are complete.

Verification:

```bash
cargo tauri build --bundles app
python3 tools/packaged_frontend_probe_evidence.py ...
```

## 4. Current Execution Order

1. W1 stable launch.
2. W2 all-buttons no-cost smoke.
3. W3 controlled Chinese technical real speaker-to-mic production run; require recording-time formal AI suggestion visibility.
4. W3 endpoint/final latency correction if the production realtime-AI gate fails.
5. W3 20-minute real microphone stability run after the short production gate passes.
6. W4 import/download closure if not already covered by W2.
7. W5 packaged user flow.
8. External release only after function gates pass.

## 5. Current Known Risk

True real microphone selftest may fail even when the product code is correct if macOS routes speaker output poorly into the built-in microphone or if browser microphone permission is blocked. That is not to be hidden. The evidence must say exactly whether the failure is permission, audio level, ASR final quality, LLM generation, or UI propagation.
