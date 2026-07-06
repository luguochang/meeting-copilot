# Mainline Usable E2E Self-Test Report

> Date: 2026-07-04  
> Decision: DEC-208  
> Runner: `tools/mainline_usable_e2e_runner.py`  
> Session: `m15_full_selftest_20260704`

## 1. Purpose

This self-test verifies the corrected mainline goal from DEC-207:

```text
audio input readiness
  -> Web mainline ASR-blocked trial
  -> Live ASR events
  -> transcript / EvidenceSpan / state / suggestion candidate
  -> no-call LLM request drafts
  -> draft review
  -> feedback/export preview closure
  -> browser workbench smoke
  -> traceable JSON/Markdown report
```

This is not only a report-only check. The new M1.5 runner is an implementation artifact: a single local command now exercises the current PC product chain and classifies remaining gaps.

## 2. Command

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id m15_full_selftest_20260704 \
  --run-browser-smoke
```

Result:

```text
exit code: 0
overall_status=mainline_product_chain_exercised_with_expected_blockers
browser_smoke_status=passed
```

## 3. Artifacts

```text
artifacts/tmp/mainline_selftests/m15_full_selftest_20260704.mainline-usable-e2e.json
artifacts/tmp/mainline_selftests/m15_full_selftest_20260704.mainline-usable-e2e.md
artifacts/tmp/audio_health/m15_full_selftest_20260704.mainline-health.wav
```

The audio artifact is a generated synthetic healthcheck WAV, not user audio.

## 4. Mainline Result

| Stage | Result |
| --- | --- |
| M1 audio healthcheck | `audio_capture_health_passed` |
| Web mainline trial | `mainline_trial_session_created` |
| Live ASR events | `live_asr_events_loaded` |
| Draft review | `draft_review_created` |
| Copilot report preview | `formal_report_preview_created` |
| Feedback/export closure | `mainline_trial_feedback_export_preview_created` |
| Browser smoke | `passed` |
| Final decision | `inconclusive_requires_more_shadow_tests` |

Event counts:

| Event | Count |
| --- | ---: |
| `transcript_partial` | 5 |
| `transcript_final` | 13 |
| `transcript_revision` | 3 |
| `state_event` | 17 |
| `scheduler_event` | 17 |
| `suggestion_candidate_event` | 17 |
| `llm_request_draft_event` | 17 |
| `evaluation_summary` | 1 |

## 5. Gap Classification

Implemented and verified in the current local product chain:

- `m1_audio_healthcheck`
- `web_mainline_trial`
- `live_asr_state_candidate_chain`
- `feedback_export_preview_closure`
- `copilot_report_preview`
- `browser_smoke`

Expected blockers that remain:

- `production_asr_quality`: `blocked_by_asr_quality`
- `mac_system_audio_capture`: `blocked_requires_m2_system_audio_capture` by default until explicit virtual-device capture is run
- `real_meeting_go_evidence`: `blocked_requires_explicit_user_approval`

These blockers are not hidden failures. They are the current truthful product boundary:

- zero-cost local ASR is still not production-sufficient for Chinese technical meetings;
- Mac system audio capture adapter is implemented, but default run remains preflight-only and does not capture without explicit device/user approval;
- real meeting validation requires explicit user start and real audio permission.

## 5.1 2026-07-04 Follow-Up: Copilot Preview And ASR Quality Evidence

The runner has been extended after the original M1.5 report:

```text
copilot_report_preview.preview_status=copilot_report_preview_created
draft_review.formal_report_status=formal_report_preview_created
```

The preview is generated from local Live ASR audit events and shows the product value chain:

```text
transcript
  -> evidence_span
  -> meeting_state
  -> suggestion_candidate
  -> llm_request_draft
  -> feedback_export_preview
```

The runner also accepts an approved ASR quality decision artifact:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id asr_quality_evidence_mainline_20260704 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json
```

Observed result from the existing local FunASR synthetic smoke evidence:

```text
asr_quality.source_status=provided_asr_quality_decision_report
asr_quality.decision_status=blocked_by_funasr_smoke_assembly_input_guard
asr_quality.quality_exit_status=not_exited
production_asr_quality=blocked_by_asr_quality
```

The main blocker is now explicit in the mainline report:

```text
FunASR synthetic smoke batch evidence is not validated and engineering normalized recall is below 0.8.
```

This is a real product conclusion. The PC product chain is usable for local preview and product-shape validation, but ASR quality is not production-ready yet.

The runner now also accepts an approved ASR event artifact:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id final_goal_artifact_handoff_verified_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/api-review-001.mock.events.json \
  --asr-events-provider local_mock_asr_artifact
```

Observed result:

```text
asr_event_handoff.handoff_status=local_asr_event_file_handoff_created
asr_event_handoff.live_event_counts.transcript_final=3
asr_event_handoff.live_event_counts.state_event=1
asr_event_handoff.live_event_counts.suggestion_candidate_event=1
asr_event_handoff.live_event_counts.llm_request_draft_event=1
asr_event_artifact_handoff=implemented_and_verified
gap_summary.implemented_and_verified=7
```

This proves approved ASR event artifacts can enter the Web Live ASR path from the mainline runner. It still does not prove ASR quality or real-meeting Go evidence.

Follow-up artifact-backed mainline closure:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id final_goal_artifact_mainline_closure_green_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
```

Observed result:

```text
overall_status=mainline_product_chain_exercised_with_expected_blockers
mainline_trial.trial_id=mainline_asr_event_artifact_trial
mainline_trial.trial_status=mainline_artifact_trial_session_created
live_asr.event_counts.transcript_final=4
live_asr.event_counts.suggestion_candidate_event=3
closure.source_trial_id=mainline_asr_event_artifact_trial
closure.source_event_artifact_status=local_asr_event_file_handoff_created
closure.closure_status=mainline_trial_feedback_export_preview_created
browser_smoke_status=passed
asr_event_artifact_closure=implemented_and_verified
gap_summary.implemented_and_verified=8
```

Boundary result:

```text
api-review-001.mock.events.json -> valid handoff, but only 1 suggestion candidate -> closure reports blocked_by_candidate_report
m15_runner_artifact_mainline.events.json -> 3 suggestion candidates -> feedback/export preview closure created
```

This proves approved ASR event artifacts can now drive the mainline product chain through feedback/export closure. The remaining blockers are ASR quality, user-authorized real capture, and optional LLM execution.

PC workbench follow-up:

```text
Button: 工件主线
Endpoint: POST /desktop/mainline-asr-event-artifact-trial/sessions
Closure: POST /desktop/mainline-trial-feedback-export-closures
```

Browser smoke now checks both the synthetic blocked trial and the artifact-backed trial:

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: exit 0
Checked:
  - mainline ASR blocked trial
  - mainline trial feedback export closure
  - mainline ASR event artifact trial
  - mainline ASR event artifact feedback export closure
```

## 6. Safety And Cost

This run did not:

- read old recordings or `.m4a`;
- read `configs/local/**`;
- read `data/asr_eval/local_samples/**`;
- read `data/local_runtime/**`;
- read `outputs/**`;
- upload audio;
- call remote ASR;
- call remote LLM;
- use a paid provider;
- start real microphone capture;
- request system audio permission.

The run did start a local backend and headless browser for the existing browser smoke test.

## 7. Verification

Focused and adjacent regression from the original M1.5 runner:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_mainline_usable_e2e_runner.py \
  tests/test_audio_capture_healthcheck.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_trial_feedback_export_closure_creates_preview_not_go_evidence \
  -q -p no:cacheprovider
```

Result:

```text
16 passed, 2 warnings
```

Runner with browser smoke:

```text
overall_status=mainline_product_chain_exercised_with_expected_blockers
browser_smoke_status=passed
```

Additional follow-up verification:

```text
copilot preview RED: failed because formal_report_status was not_created
copilot preview GREEN: 1 passed, 2 warnings
ASR quality evidence RED: 3 failed because function/CLI support did not exist
ASR quality evidence GREEN: 3 passed, 2 warnings
ASR event artifact handoff RED: failed because asr_events_path was unsupported
ASR event artifact handoff GREEN: 1 passed, 2 warnings
ASR event artifact closure RED: runner still used blocked trial as main source
ASR event artifact closure GREEN: 1 passed, 2 warnings
ASR event artifact UI RED: static workbench test failed because 工件主线 button was missing
ASR event artifact UI GREEN: static workbench test passed
mainline runner focused regression: 15 passed, 2 warnings
mainline with ASR quality artifact: exit 0
adjacent regression: 36 passed, 2 warnings
syntax check: exit 0
sensitive scan: no matches
final browser mainline smoke with ASR quality artifact: exit 0, browser_smoke_status=passed
final browser mainline smoke with ASR quality + ASR event artifact closure: exit 0, browser_smoke_status=passed
browser smoke with user-visible artifact mainline UI: exit 0
```

## 8. Next Step

```text
1. Improve or retune local FunASR synthetic smoke until DRV-046/044/032 can pass, or record a clear Pivot/Stop decision if local ASR cannot meet Chinese technical-meeting recall.
2. Run real Mac virtual-system-audio or microphone health capture only after explicit user authorization and device route selection.
3. Add disabled-by-default OpenAI-compatible LLM execution and keep provider/cost approval explicit.
4. Refine the UI around artifact-backed closure state, candidate insufficiency, ASR quality blockers, and real-capture readiness.
```

Do not continue adding report-only wrappers. The remaining high-value gaps are ASR quality, real ASR artifact handoff, and user-authorized real capture evidence.
