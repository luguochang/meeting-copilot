# Workbench Visual Acceptance Report

> Date: 2026-07-09
> Status: page-level visual acceptance passed for the current PC Workbench functions.
> Scope: automated local browser UI clicks, per-step screenshots, recorded-file workflow, exports, history, AI cards, minutes, delete, and a fresh real browser microphone no-cost visual run.

## 1. Conclusion

The page-level automated selftest is **Go** for the tested Workbench functions.

The in-app Browser control surface refused to claim the already-open `127.0.0.1:8765/workbench` tab because of its URL policy. That means this report does not claim direct control of the user's current in-app tab. Instead, the project E2E automation opened the same local Workbench in an isolated Chrome instance, clicked the controls, and saved per-step screenshots and JSON evidence.

## 2. All-Buttons Visual Evidence

Artifact root:

```text
artifacts/tmp/ui_screenshots/workbench-visual-acceptance-20260709-184959
```

Report:

```text
artifacts/tmp/ui_screenshots/workbench-visual-acceptance-20260709-184959/workbench_visual_acceptance_report.json
```

Result:

```json
{
  "status": "go_workbench_all_buttons_smoke",
  "screenshot_count": 14,
  "fake_llm_request_count": 12,
  "final_state": {
    "currentSession": null,
    "utterances": 0,
    "suggestions": 0,
    "approaches": 0,
    "sessionMeta": "准备开始"
  }
}
```

Covered steps:

```text
01 initial_page
02 import_recording
03 history_open
04 history_reopen
05 suggestions_generated
06 evidence_clickback
07 approach_generated
08 minutes_generated
09 transcript_refreshed
10 exports_verified
11 auto_suggestion_paused
12 auto_suggestion_resumed
13 organize_completed
14 delete_reset
```

Download evidence:

```text
file_cf91f6ace6c8.transcript.txt
file_cf91f6ace6c8.minutes.md
```

Cost boundary:

```text
LLM gateway for this all-buttons visual run was a local fake OpenAI-compatible server.
It does not count as production LLM evidence.
Production LLM evidence remains the strict browser-live-mic bundle documented in docs/pc-workbench-production-acceptance-report-2026-07-09.md.
```

## 3. Fresh Real Microphone Visual Evidence

Artifact root:

```text
artifacts/tmp/browser_live_mic/workbench-live-mic-visual-nocost-20260709-185047
```

Screenshot:

```text
artifacts/tmp/browser_live_mic/workbench-live-mic-visual-nocost-20260709-185047/workbench-browser-live-mic.png
```

Result:

```json
{
  "session_id": "rec_mrddyqko",
  "input_mode": "real_browser_mic",
  "ui_coverage": "visible_chrome",
  "health_status": "audio_capture_health_passed",
  "chunk_count": 113,
  "asr_final_count": 1,
  "derivation_mode": "no_cost_deterministic",
  "derivations_generated": true,
  "counts_as_production_llm_evidence": false,
  "suggestion_card_count": 4,
  "approach_card_count": 1,
  "minutes_char_count": 235,
  "delete_verified": true
}
```

ASR quality:

```text
provider=sherpa_onnx_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
asr_semantic_quality_status=passed
browser_console_error_count=0
network_error_count=0
```

Cost boundary:

```text
This fresh real-mic visual run intentionally used no_cost_deterministic derivation to avoid additional LLM gateway spend.
It proves page start/stop microphone, real browser microphone input, local ASR, UI propagation, no-cost suggestions/approach/minutes, and delete.
It does not replace the previous production LLM evidence.
```

## 4. Current Remaining Boundaries

Not covered by this visual run:

```text
Direct control of the already-open in-app Browser tab, because Browser Use blocked that local URL.
Production LLM was not called again in this visual pass to avoid extra cost.
Mac signed/notarized/Gatekeeper release package.
Windows real-machine verification.
Long real meeting soak with production LLM cost and performance controls.
Human product-feel acceptance in an actual meeting.
```
