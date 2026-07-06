# Mainline Product Smoke Self-Test Report

> Date: 2026-07-04  
> Decision: DEC-203  
> Scope: Run the current PC product main flow through real program paths, without expanding ASR/provider boundary tests.

## User Request

The user asked to stop deep boundary testing and first run the main flow. In this report, "main flow" means the current implemented product path:

```text
PC workbench / mainline endpoint
  -> create Live ASR trial session
  -> stream transcript final/revision events
  -> create EvidenceSpan/state/scheduler/suggestion candidates
  -> create no-call LLM request drafts
  -> expose draft review/report data
  -> show ASR quality blocker and real-mic blocker
```

This is a product-chain smoke test, not a new ASR provider bake-off. It does not use microphone input or private audio.

## What Ran

Existing local Web MVP server:

```text
GET http://127.0.0.1:8000/health
Result: {"status":"ok","service":"meeting-copilot-web-mvp"}
```

Manual mainline session creation:

```text
POST http://127.0.0.1:8000/desktop/mainline-asr-blocked-trial/sessions
Body: {"session_id":"manual_true_mainline_selftest_20260704"}
Result: 201 Created
```

Focused backend gate:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker \
  -q -p no:cacheprovider
Result: 1 passed, 2 warnings
```

Browser E2E gate:

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok
Checked includes: "mainline ASR blocked trial"
```

## Observed Mainline Session

Manual session summary from `manual_true_mainline_selftest_20260704`:

| Field | Value |
| --- | --- |
| `trial_id` | `mainline_asr_blocked_trial` |
| `trial_status` | `mainline_trial_session_created` |
| `provider` | `local_mock_asr` |
| `execution_boundary` | `synthetic_live_events_only_no_mic_no_audio_file_no_remote_calls` |
| `mainline_decision_id` | `DEC-201` |
| `asr_quality_exit_status` | `not_exited` |
| `asr_quality_decision_status` | `blocked_by_funasr_smoke_assembly_input_guard` |
| `selected_product_route` | `pc_product_flow_with_asr_quality_blocked_visible` |
| `recommended_next_action` | `continue_pc_product_flow_keep_real_mic_blocked` |

Event counts returned by the session:

| Event | Count |
| --- | ---: |
| `transcript_partial` | 5 |
| `transcript_final` | 13 |
| `transcript_revision` | 3 |
| `state_event` | 17 |
| `scheduler_event` | 17 |
| `suggestion_candidate_event` | 17 |
| `llm_request_draft_event` | 17 |
| `provider_error` | 0 |
| `evaluation_summary` | 1 |

Draft review summary from `/live/asr/sessions/manual_true_mainline_selftest_20260704/draft`:

| Field | Value |
| --- | ---: |
| transcript segments | 16 |
| evidence spans | 19 |
| state candidates | 17 |
| suggestion candidates | 17 |
| suggestion cards | 0 |
| LLM request drafts | 17 |
| LLM call status | `not_called` |

The first generated no-call request draft suggested:

```text
确认决策是否包含 owner、回滚条件和监控口径。
```

This confirms the current product chain can turn meeting-like transcript events into state and realtime suggestion draft material without calling the LLM.

## UI Result

The browser smoke test opened the workbench and verified the visible mainline path, including:

- desktop shell/runtime/native bridge readiness panels
- replay event stream
- live mock EventSource stream
- Live ASR local EventSource skeleton
- Mac Local Shadow MVP synthetic demo closure
- realistic and long realistic meeting simulations
- `mainline ASR blocked trial`

This confirms the path is not only an API-only path; the workbench can expose it through the browser UI.

## Decision

DEC-203 is accepted:

- The current PC product main flow is connected end to end for a synthetic/live-event trial.
- The main product value path is present: transcript events become EvidenceSpan/state/scheduler/suggestion candidates/no-call LLM request drafts and a draft review.
- The current flow still stops before paid/remote LLM execution, before real ASR quality Go, and before microphone capture.
- The blocker is no longer "mainline product chain not wired"; the blocker is "real ASR quality and real microphone readiness are not yet safe to promote."

## What This Does Not Prove

This self-test does not prove:

- real meeting microphone capture works
- local FunASR quality is good enough for real meetings
- remote ASR is better or cheaper
- LLM card generation quality is good enough
- long-running desktop packaging is production-ready

Those are separate exits. This run only proves the current product chain can be exercised as a mainline smoke without turning into more provider evaluation.

## Safety And Cost

This run did not:

- access or enumerate microphone devices
- request macOS microphone permission
- capture microphone audio
- read user recordings or any `.m4a`
- read `configs/local/**`
- read `data/asr_eval/local_samples/**`
- read `data/local_runtime/**`
- read `outputs/**`
- call remote ASR
- call remote LLM
- download public audio
- download models
- create extra provider charges

Real microphone readiness remains:

```text
readiness_status=blocked_not_ready_for_user_real_mic_shadow_test
user_can_start_real_mic_shadow_test_now=false
safe_to_access_microphone_from_gate_now=false
safe_to_call_remote_asr_from_gate_now=false
safe_to_call_llm_from_gate_now=false
```

## Next Mainline

The next bounded product step should be product closure rather than more ASR bake-off:

```text
mainline trial session
  -> choose one suggestion candidate
  -> collect local feedback
  -> produce Markdown/JSON report preview
  -> keep "not Go evidence" visible unless real feedback + real audio readiness exists
```

Recommended implementation label:

```text
PCWEB-129 Mainline Trial Feedback And Export Closure
```

