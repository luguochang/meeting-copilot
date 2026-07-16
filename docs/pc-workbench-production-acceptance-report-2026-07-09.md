# PC Workbench Production Acceptance Report

> Date: 2026-07-09
> Status: production LLM acceptance passed for the PC Workbench browser-live-mic mainline.
> Scope: real browser microphone, local speaker playback, local realtime ASR, production `mode=enabled` derivations, remote non-mock OpenAI-compatible gateway, Workbench UI, history/delete evidence bundle.

## 1. Conclusion

Production acceptance for the PC Workbench mainline is **Go** for this lane:

```text
visible Chrome Workbench
  -> real browser microphone via getUserMedia
  -> local speaker playback test prompt
  -> sherpa_onnx_realtime ASR
  -> ASR semantic quality gate
  -> production /live/asr/sessions/{id}/... derivations
  -> remote non-mock OpenAI-compatible LLM gateway
  -> suggestion cards / approach cards / minutes
  -> Workbench same-session UI
  -> delete verification
  -> standard mainline evidence bundle
```

The acceptance evidence explicitly proves this was not the no-cost selftest lane:

```json
{
  "derivation_mode": "production_enabled",
  "llm_provider": "real_gateway",
  "gateway_base_url_kind": "remote",
  "counts_as_production_llm_evidence": true,
  "llm_call_count": 5,
  "llm_usage_total_tokens": 24116
}
```

## 2. Fixes Made During Acceptance

### 2.1 Production Evidence Gateway Classification

Problem:

- The backend can load LLM gateway config from `.env`.
- The browser-live-mic verifier runs in Node and may not have `LLM_GATEWAY_BASE_URL` in its own process environment.
- Before this fix, the verifier could generate real remote LLM outputs but still fail to classify the gateway kind as remote if only backend-side config was present.

Fix:

- `workbench_browser_live_mic_verify.mjs` now infers gateway kind from persisted session traces, especially `suggestion_cards[*].llm_trace.provider` and `approach_cards[*].llm_trace.provider`.
- It still treats local `127.0.0.1` / `localhost` gateways as local and not production LLM evidence.

### 2.2 Structured LLM Usage Evidence

Problem:

- The standard bundle initially showed `llm_called=true` but `llm_call_count=0` and `llm_usage_total_tokens=0`.
- That was incorrect for production acceptance because the remote gateway returned usage.

Fix:

- `workbench_browser_live_mic_verify.mjs` now emits:
  - `llm_call_count`
  - `llm_usage_total_tokens`
- `tools/mainline_evidence_bundle_runner.py` now copies those fields into the standard manifest.
- `privacy_cost_flags.llm_called` is now synchronized to the final manifest LLM status for browser-live-mic bundles.

Files changed:

```text
code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
tools/mainline_evidence_bundle_runner.py
tools/release_acceptance_runner.py
code/web_mvp/backend/tests/test_workbench.py
tests/test_mainline_evidence_bundle_runner.py
tests/test_release_acceptance_runner.py
```

### 2.3 Strict Production LLM Evidence Gate

Problem:

- A previous release-level summary still referenced an older browser-live-mic bundle with `llm_called=true` but `llm_call_count=0` and `llm_usage_total_tokens=0`.
- That summary is valid only as historical evidence and must not be used as production LLM acceptance.

Fix:

- Browser-live-mic standard bundles now preserve `derivation_mode`, `gateway_base_url_kind`, and `counts_as_production_llm_evidence`.
- If a browser-live-mic bundle requests `production_enabled`, it must prove all of the following or become No-Go:
  - remote gateway,
  - `counts_as_production_llm_evidence=true`,
  - `llm_call_count>0`,
  - `llm_usage_total_tokens>0`.
- Release acceptance now propagates these fields and blocks browser-live-mic production LLM claims that are missing usage evidence.
- `browser_environment.json` now records `production_derivation_requested`; the actual production LLM evidence decision lives in `asr_probe.json` and the standard bundle manifest.

## 3. Production Raw Evidence

Raw artifact root:

```text
artifacts/tmp/browser_live_mic/real-speaker-mic-production-usage-20260709-171209
```

Command shape:

```bash
MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE=production_enabled \
MEETING_COPILOT_BROWSER_MIC_HEADLESS=false \
MEETING_COPILOT_BROWSER_MIC_FAKE_UI=true \
MEETING_COPILOT_BROWSER_MIC_SECONDS=34 \
node code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
```

Local speaker playback prompt was played by macOS `say -v Tingting` after recording started.

Top-level result:

```json
{
  "session_id": "rec_mrdafwcv",
  "input_mode": "real_browser_mic",
  "ui_coverage": "visible_chrome",
  "health_status": "audio_capture_health_passed",
  "chunk_count": 113,
  "asr_final_count": 1,
  "derivation_mode": "production_enabled",
  "derivations_generated": true,
  "counts_as_production_llm_evidence": true,
  "suggestion_card_count": 3,
  "approach_card_count": 3,
  "minutes_char_count": 490,
  "delete_verified": true
}
```

ASR and LLM probe:

```json
{
  "provider": "sherpa_onnx_realtime",
  "provider_mode": "real",
  "is_mock": false,
  "asr_fallback_used": false,
  "degradation_reasons": [],
  "acceptance_eligible": true,
  "acceptance_blockers": [],
  "llm_called": true,
  "llm_provider": "real_gateway",
  "gateway_base_url_kind": "remote",
  "counts_as_production_llm_evidence": true,
  "llm_call_count": 5,
  "llm_usage_total_tokens": 24116,
  "all_cards_have_evidence": true
}
```

ASR semantic quality:

```json
{
  "status": "passed",
  "technical_entity_hit_count": 11,
  "technical_group_hit_count": 4,
  "matched_entity_groups": ["release_control", "reliability", "ownership", "action"]
}
```

## 4. Standard Acceptance Bundle

Bundle root:

```text
artifacts/tmp/acceptance/real-speaker-mic-production-usage-20260709-171209
```

Post-fix refresh bundle root, regenerated from the same raw real-mic evidence without making new LLM calls:

```text
artifacts/tmp/acceptance/real-speaker-mic-production-usage-20260709-171209-refresh
```

Strict post-review refresh bundle root, regenerated after the stricter production LLM evidence gate:

```text
artifacts/tmp/acceptance/real-speaker-mic-production-usage-20260709-171209-refresh2
```

Command:

```bash
PYTHONPATH=code/web_mvp/backend:code/core python3 tools/mainline_evidence_bundle_runner.py \
  --lane browser-live-mic \
  --browser-mic-health-report artifacts/tmp/browser_live_mic/real-speaker-mic-production-usage-20260709-171209/browser_mic_health_report.json \
  --asr-probe artifacts/tmp/browser_live_mic/real-speaker-mic-production-usage-20260709-171209/asr_probe.json \
  --ui-report artifacts/tmp/browser_live_mic/real-speaker-mic-production-usage-20260709-171209/ui_verification.json \
  --run-id real-speaker-mic-production-usage-20260709-171209 \
  --artifact-root artifacts/tmp/acceptance/real-speaker-mic-production-usage-20260709-171209
```

Manifest result:

```json
{
  "verdict": "go",
  "audio_source": "browser_live_mic",
  "input_audio_path_kind": "browser_get_user_media",
  "asr_provider": "sherpa_onnx_realtime",
  "asr_provider_mode": "real",
  "asr_fallback_used": false,
  "asr_semantic_quality_status": "passed",
  "llm_provider": "real_gateway",
  "llm_called": true,
  "llm_call_count": 5,
  "llm_usage_total_tokens": 24116,
  "ui_coverage": "visible_chrome",
  "transcript_char_count": 68,
  "final_segment_count": 1,
  "suggestion_card_count": 3,
  "approach_card_count": 3,
  "minutes_char_count": 490,
  "all_cards_have_evidence": true,
  "delete_verified": true,
  "workbench_same_session_visible": true,
  "frontend_utterance_count": 1,
  "frontend_card_count": 6,
  "frontend_minutes_visible": true,
  "browser_console_error_count": 0,
  "network_error_count": 0,
  "browser_live_mic_go_evidence": true,
  "counts_as_real_mic_go_evidence": true,
  "degradation_reasons": []
}
```

Refresh bundle result:

```json
{
  "verdict": "go",
  "llm_called": true,
  "llm_call_count": 5,
  "llm_usage_total_tokens": 24116,
  "browser_live_mic_go_evidence": true,
  "counts_as_real_mic_go_evidence": true
}
```

Strict refresh2 bundle result:

```json
{
  "verdict": "go",
  "derivation_mode": "production_enabled",
  "gateway_base_url_kind": "remote",
  "counts_as_production_llm_evidence": true,
  "llm_called": true,
  "llm_call_count": 5,
  "llm_usage_total_tokens": 24116,
  "browser_live_mic_go_evidence": true,
  "counts_as_real_mic_go_evidence": true
}
```

Release-level summary regenerated from existing lane evidence without making new LLM calls:

```text
artifacts/tmp/release_acceptance/release-current-20260709-production-browser-llm-evidence-fixed
```

Summary:

```json
{
  "verdict": "go",
  "blockers": [],
  "llm_call_count": 10,
  "llm_usage_total_tokens": 26199,
  "browser_lane": {
    "verdict": "go",
    "derivation_mode": "production_enabled",
    "gateway_base_url_kind": "remote",
    "counts_as_production_llm_evidence": true,
    "llm_call_count": 5,
    "llm_usage_total_tokens": 24116
  }
}
```

Privacy/cost flags:

```json
{
  "raw_audio_uploaded": false,
  "remote_asr_called": false,
  "llm_called": true,
  "configs_local_read": false,
  "user_audio_committed_to_repo": false
}
```

Interpretation:

- Raw audio was not uploaded to remote ASR.
- Local ASR was used.
- Remote LLM gateway was called.
- No secret values are written to the report.
- Session delete was verified.

## 5. Verification

Focused tests:

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_infers_remote_gateway_from_session_traces_not_only_node_env \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_exports_llm_usage_summary_for_production_acceptance \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_script_separates_no_cost_selftest_from_production_llm_evidence \
  tests/test_mainline_evidence_bundle_runner.py::test_browser_live_mic_lane_accepts_visible_chrome_as_real_user_mic_ui_evidence
```

Result:

```text
4 passed, 2 warnings
```

Related regression:

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_workbench_server_tool.py \
  tests/test_workbench_all_buttons_smoke.py \
  tests/test_fake_llm_gateway_script.py \
  code/web_mvp/backend/tests/test_workbench.py \
  tests/test_mainline_evidence_bundle_runner.py
```

Result:

```text
91 passed, 2 warnings
```

Syntax/checks:

```bash
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
python3 -m py_compile tools/mainline_evidence_bundle_runner.py
git diff --check
```

Result: all passed.

Refresh bundle verification:

```bash
PYTHONPATH=code/web_mvp/backend:code/core python3 tools/mainline_evidence_bundle_runner.py \
  --lane browser-live-mic \
  --browser-mic-health-report artifacts/tmp/browser_live_mic/real-speaker-mic-production-usage-20260709-171209/browser_mic_health_report.json \
  --asr-probe artifacts/tmp/browser_live_mic/real-speaker-mic-production-usage-20260709-171209/asr_probe.json \
  --ui-report artifacts/tmp/browser_live_mic/real-speaker-mic-production-usage-20260709-171209/ui_verification.json \
  --run-id real-speaker-mic-production-usage-20260709-171209-refresh \
  --artifact-root artifacts/tmp/acceptance/real-speaker-mic-production-usage-20260709-171209-refresh
```

Result:

```text
verdict=go, llm_called=true, llm_call_count=5, llm_usage_total_tokens=24116
```

Strict gate focused regression:

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_mainline_evidence_bundle_runner.py::test_browser_live_mic_lane_blocks_production_llm_claim_without_usage_evidence \
  tests/test_release_acceptance_runner.py::test_release_acceptance_runner_blocks_browser_live_mic_production_llm_without_usage \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_environment_does_not_overstate_production_llm_evidence
```

Result:

```text
3 passed, 2 warnings
```

Release-level regenerated summary:

```text
verdict=go, blockers=[], browser lane points to real-speaker-mic-production-usage-20260709-171209-refresh2
```

## 6. Remaining Risks

This production acceptance does not mean public release is complete.

Still outside this lane:

- Mac Developer ID signing.
- Notarization.
- Gatekeeper acceptance.
- Windows real-machine verification.
- Long meeting soak with real production LLM cost controls.
- Human meeting product-feel acceptance in a real meeting environment.

Chrome fake-audio-file diagnostic remains No-Go from the previous selftest report, but real browser microphone with local speaker playback is Go.

## 7. Current Product Status

Allowed claim:

```text
PC Workbench browser-live-mic mainline has passed production LLM acceptance on macOS local environment:
real browser mic -> local realtime ASR -> production remote LLM suggestions/approach/minutes -> Workbench UI -> delete -> standard Go evidence bundle.
```

Not allowed claim:

```text
The app is ready for public Mac/Windows release.
All OS packaging/signing/distribution gates are complete.
The product has passed long-meeting cost/performance soak.
```
