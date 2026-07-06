# Meeting Copilot Release Readiness Reset

> Date: 2026-07-05  
> Status: Accepted as current release-readiness reset  
> Scope: Product goal, current code reality, ASR blocker, release feasibility, and next execution boundary  
> Boundary: This document is based on read-only review of docs/code/tests plus fresh local no-remote gates. It does not authorize microphone access, private audio access, `.m4a` reading, `configs/local/` reading, remote ASR/LLM calls, public-audio downloads, model downloads, or paid providers.

## 1. Executive Verdict

Current product state:

```text
Demo / Local Preview Ready: yes
Shadow Pilot Ready: no
Production MVP Ready: no
```

The project has not produced nothing. It can locally demonstrate the Copilot product chain on synthetic/mock/replay/approved ASR event artifacts:

```text
approved ASR event artifact
  -> Live ASR session
  -> transcript final
  -> EvidenceSpan
  -> meeting state
  -> suggestion candidate
  -> no-call LLM request draft
  -> feedback/export preview
```

But this is not the original MVP. The original MVP is a Chinese technical meeting realtime Copilot installed on a laptop, with real/authorized meeting audio, qualified Chinese technical ASR, realtime state/gap/card behavior, and evidence-backed after-meeting artifacts.

The current release blocker is not vague:

- ASR quality has not exited.
- Real microphone/system-audio meeting capture is not enabled as a product path.
- Tauri desktop commands are still no-op for real actions.
- The ASR worker sidecar is still a no-execution boundary.
- LLM execution is disabled; only request drafts/previews are generated.
- Formal suggestion cards and formal meeting reports are not proven on real meeting audio.

The next phase must stop treating new readiness/preflight/approval/preview wrappers as mainline progress unless they directly change one of the decisive product states:

```text
quality_exit_status
real_mic_shadow_readiness_status
user_can_start_real_mic_shadow_test_now
normalized technical entity recall
formal card/report evidence status
real meeting feedback useful/wrong/too_late/too_intrusive
```

If a new task does not change at least one of those states, it is maintenance, documentation, or safety audit. It must not be called mainline release progress.

## 2. Original Product Commitments

The original product is documented as a Chinese technical meeting realtime AI Copilot, not a generic transcription tool. The core value is to detect engineering discussion gaps during the meeting and preserve evidence-backed structure afterward.

Original MVP commitments from `docs/product-requirements.md`, `docs/feature-map.md`, and `docs/minimum-valuable-demo-script.md`:

| Area | Original MVP commitment |
| --- | --- |
| Product value | Realtime/near-realtime Copilot that finds engineering gaps, not merely audio-to-text or after-meeting summary. |
| Platform | macOS Apple Silicon first; Windows later. |
| Desktop control | Manual start, pause, resume, stop, visible privacy state, local delete. |
| Audio | Microphone and system audio, at least distinguish local input from system mix. |
| ASR | Default local/open-source ASR; remote ASR optional only after explicit cost/privacy decision. |
| Chinese technical quality | ASR must handle Chinese technical meetings, service names, fields, metrics, error codes, English/Chinese mixed terms. |
| Stabilizer | `partial/final/revision`; only stable final/revision evidence can trigger strong behavior. |
| Evidence | Formal DecisionCandidate, ActionItem, Risk, OpenQuestion, SuggestionCard, and MeetingSummary content must cite EvidenceSpan. |
| State machine | Maintain topics, candidate decisions, action items, risks, open questions, and event log. |
| Engineering gate | Non-engineering meetings must produce zero engineering gap cards. |
| Gap radar | Owner, deadline, rollback, test/verification, metric/monitoring gaps. |
| Cards | Low-frequency, evidence-backed, actionable, dismissible, mark-wrong capable, not judge-like. |
| Timing | Realtime cards should appear within 10-30 seconds after the relevant final segment; too late becomes after-meeting confirmation. |
| After meeting | Markdown/JSON report with decisions, actions, risks, open questions, technical entities, evidence timestamps. |
| Privacy/cost | No hidden recording, no hidden remote ASR, no hidden paid provider, no secrets in repo/logs/artifacts. |

The minimum valuable demo script requires:

```text
real recording
  -> local ASR final segment
  -> EvidenceSpan
  -> engineering context gate
  -> incremental meeting state machine
  -> state diff / gap rule
  -> engineering gap card
  -> evidence-backed minutes
  -> user feedback into evaluation
```

The current implementation demonstrates a related but weaker chain because the input is synthetic/mock/replay/artifact evidence, not real/authorized meeting audio with qualified ASR.

## 2.1 Multi-Agent Review Consensus

This reset used four independent read-only review tracks:

| Review track | Consensus |
| --- | --- |
| Product gap review | The product has not forgotten the Copilot goal, but current proof is Local Shadow Preview, not the original MVP. Continuing to add readiness/preflight wrappers would effectively drift away from the product goal. |
| Code chain review | Web/API/workbench and approved ASR event artifact handoff are runnable; Tauri, ASR worker, microphone/system-audio, real LLM execution, and production packaging are not proven as a continuous real meeting chain. |
| ASR/realtime review | The blocker is technical entity recall, not speed. `timeout`, `监控阈值`, and `staging` are still missing from current transcripts; broad evaluation should stop in favor of one bounded input/parameter experiment or a pivot decision. |
| Release governance review | The project has been counting process artifacts as mainline progress. Future mainline work must change release-decisive states or be treated as maintenance/safety documentation only. |

All four tracks converge on the same status:

```text
Local Shadow Preview: feasible now.
Real meeting shadow pilot: blocked unless explicit degraded risk is accepted.
Production MVP: blocked.
```

## 3. What Is Actually Implemented

Fresh local verification on 2026-07-05:

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id release_reset_fresh_20260705 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr

Result: exit 0
overall_status=mainline_product_chain_exercised_with_expected_blockers
```

Important fields from that run:

```text
live_asr.transcript_final_count=4
live_asr.state_event_count=3
live_asr.suggestion_candidate_count=3
live_asr.llm_request_draft_count=3
live_asr.llm_call_status=not_called
copilot_report_preview.preview_status=copilot_report_preview_created
copilot_report_preview.value_chain=[
  transcript,
  evidence_span,
  meeting_state,
  suggestion_candidate,
  llm_request_draft,
  feedback_export_preview
]
closure.closure_status=mainline_trial_feedback_export_preview_created
closure.go_evidence_status=not_go_evidence_replay_or_feedback_missing
gap_summary.implemented_and_verified=8
gap_summary.blocked_by_asr_quality=1
gap_summary.blocked_requires_m2_system_audio_capture=1
gap_summary.blocked_requires_explicit_user_approval=1
```

Current runnable chains:

| Chain | Evidence | What it proves | What it does not prove |
| --- | --- | --- | --- |
| Web demo fixtures | `POST /demo/fixtures/{fixture_id}/sessions` | Core/API/UI fixture behavior, evidence-backed snapshots, report preview. | Real audio, real ASR, real LLM, desktop runtime. |
| Live mock | `POST /live/asr/mock/sessions` and synthetic events | Event contract, SSE/UI incremental rendering. | Real ASR provider, real latency, real audio quality. |
| Local ASR event artifact handoff | `POST /live/asr/local-event-files/sessions` | Approved event JSON can drive Live ASR session and product chain. | The artifact itself came from qualified real meeting audio. |
| Mac Local Shadow MVP synthetic demo | `POST /desktop/mac-local-shadow-mvp-demo/sessions` | Clickable desktop-shaped synthetic Copilot workflow. | Real desktop capture or real ASR quality. |
| Realistic meeting simulation pack | `POST /desktop/realistic-meeting-simulation-pack/sessions` | Richer synthetic Chinese technical meeting simulation. | Actual meeting acoustics, provider ASR accuracy, microphone readiness. |
| Mainline ASR event artifact trial | `POST /desktop/mainline-asr-event-artifact-trial/sessions` | Artifact-backed mainline trial and closure can run through UI/API. | Production ASR, real microphone, formal cards, LLM execution. |
| Mainline feedback/export closure | `POST /desktop/mainline-trial-feedback-export-closures` | Candidate feedback and Markdown/JSON export preview can close locally. | Formal export from a real meeting or Go evidence. |

Current desktop status:

- `code/desktop_tauri/src-tauri/src/lib.rs` binds desktop commands.
- Every `NoopBridgeResponse` reports `implementation_status=none/noop_only` style safety boundaries for real actions:
  - `safe_to_execute_real_action=false`
  - `captures_audio=false`
  - `spawns_process=false`
  - `calls_remote_provider=false`
  - `writes_local_files=false`

Current ASR worker status:

- `code/asr_runtime/scripts/asr_worker_sidecar.py` is a no-execution skeleton.
- It explicitly avoids process spawning, audio capture, model imports, event-file IO, and network IO.
- Current allowed source kind is synthetic; mic/file/system-audio remain future approval paths.

Current LLM status:

- Live ASR generates LLM request drafts and OpenAI-compatible request body previews.
- Real execution endpoints accept disabled/dry-run modes only.
- Current run shows `llm_call_status=not_called`.

## 4. Current ASR Quality Conclusion

Fresh local gate on 2026-07-05:

```text
python3 tools/asr_quality_decision_gate.py \
  --funasr-smoke-assembly-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly-chunk20_hotword.json

decision_status=blocked_by_funasr_smoke_assembly_input_guard
quality_exit_status=not_exited
counts_as_asr_quality_go_evidence=false
```

Current `chunk20_hotword` quality result:

| Scenario | normalized recall | Missing entities | Status |
| --- | ---: | --- | --- |
| `api-review-001` | `1.0` | none | Pass |
| `architecture-review-001` | `1.0` | none | Pass |
| `incident-review-001` | `0.5` | `timeout`, `监控阈值` | Fail |
| `release-review-001` | `0.75` | `staging` | Fail |
| `non-engineering-control-001` | `0.0`, candidate cards `0` | none | Negative control OK |

Key interpretation:

- Speed is not the current blocker. Batch measurement showed RTF around `0.36`, below the `0.6` gate used by the current synthetic smoke evidence.
- The blocker is Chinese technical entity recall in engineering scenarios.
- The remaining missing terms do not appear in the current transcripts. Backfilling them from golden scripts would fake evidence and must remain forbidden.
- The next ASR action must be finite: inspect/fix the synthetic script-to-audio input for those three entities, then try small hotword/chunk/model parameter changes. If still blocked, choose remote ASR cost/privacy review or explicit degraded pilot/pivot.

Current real microphone readiness gate:

```text
python3 tools/real_mic_shadow_test_readiness_gate.py

readiness_status=blocked_not_ready_for_user_real_mic_shadow_test
blockers=[
  asr_quality_decision_requires_funasr_model_dir_or_drv019_approval,
  real_tauri_noop_run_result_not_provided,
  worker_mic_source_not_approved,
  mic_adapter_real_implementation_not_available,
  asr_worker_real_mic_source_not_available
]
asr_quality_exit_status=not_exited
```

This means real microphone is not the next default product action unless the project explicitly chooses a degraded pilot and records that it is not ASR quality Go evidence.

## 5. Why It Felt Like A Loop

The loop did not come from having no plan. It came from repeatedly counting process artifacts as mainline progress:

```text
readiness
  -> preflight
  -> approval packet
  -> no-op binding
  -> dry-run
  -> preview
  -> wrapper
  -> report
```

Those artifacts were useful for privacy and safety, but they did not change the decisive product states:

- ASR quality still `not_exited`.
- Real mic shadow test still `blocked`.
- Desktop real audio capture still not active.
- LLM still `not_called`.
- Formal cards still `not_created`.
- Current closure still `draft_export_preview_only` / `not_go_evidence`.

The user concern is therefore correct. If the project continues to add wrappers without changing the states above, it will keep looking busy while the product does not become more usable in a real meeting.

Decision from this reset:

```text
No new mainline task is allowed unless it directly changes at least one release-decisive state:
  quality_exit_status
  real_mic_shadow_readiness_status
  user_can_start_real_mic_shadow_test_now
  normalized technical entity recall
  formal card/report evidence status
  real meeting feedback useful/wrong/too_late/too_intrusive
```

If two consecutive tasks only add readiness/preflight/approval/preview/wrapper artifacts without reducing blockers, the third task must stop boundary work and choose one of:

```text
ASR quality exit
explicit degraded pilot
product pivot
```

## 6. Release Feasibility

| Release tier | Current feasibility | Allowed claim | Forbidden claim |
| --- | --- | --- | --- |
| Local Shadow Preview / Engineering Demo | Feasible now | Local synthetic/replay/artifact product chain is demonstrable. | Do not call it real meeting ready or beta. |
| Degraded Shadow Pilot | Possible only with explicit risk acceptance | One controlled user-authorized test may measure timing/feedback despite ASR quality risk. | Do not count it as ASR quality Go evidence. |
| Production MVP | Not feasible now | Current code has a credible architecture and preview chain. | Do not claim real-time Chinese technical meeting Copilot is production ready. |

Short-term deliverable should be named conservatively:

```text
Meeting Copilot Local Shadow Preview
```

It may include:

- PC/Web workbench mainline artifact-backed demo.
- Readiness/status banner: Preview ready, ASR blocked, real mic blocked, LLM disabled.
- ASR quality diagnostic table with missing entities.
- Feedback/export preview.
- Explicit not-Go evidence tags.

It must not claim:

- Real microphone meeting is usable.
- ASR is production quality.
- Real desktop audio capture is implemented.
- Formal cards/reports are stable on real meetings.
- Remote ASR or public-audio validation has solved quality.
- The product is release/Beta ready.

## 7. Gap Matrix

| Module | Original MVP requirement | Current state | Blocking reason | Next valid action |
| --- | --- | --- | --- | --- |
| macOS desktop | Manual meeting lifecycle with visible privacy state | Tauri shell/no-op IPC and Web workbench exist | Real commands are no-op; no real audio capture | Implement or explicitly approve one real local capture path after ASR/degraded decision |
| Microphone/system audio | mic/system/mixed capture with health logs | Preflight/health tools and synthetic WAV health exist | No active mic/system capture product path | Only enter after quality exit or degraded pilot acceptance |
| ASR quality | Chinese technical ASR gate passed | `quality_exit_status=not_exited` | `incident` recall `0.5`; `release` recall `0.75` | Two-scenario bounded input/parameter experiment, then Go/Pivot |
| Streaming events | partial/final/revision event contract | Implemented for mock/replay/artifacts | Real provider endpoint final and desktop streaming not proven | Keep contract; focus on ASR input/provider evidence |
| EvidenceSpan | Formal content cites evidence | Implemented in core/live preview | Real meeting evidence not proven | Preserve as non-negotiable gate |
| State machine | Incremental meeting state + event lifecycle | Created events and candidates exist | Full lifecycle updated/answered/confirmed not proven on real flow | Implement only as part of Shadow Trial path |
| Gap cards | Realtime cards within 10-30 seconds | Suggestion candidates and drafts exist | Formal cards not created; LLM disabled | Choose local rule card degraded path or real LLM execution gate |
| LLM provider | OpenAI-compatible optional execution with cost tracking | Request draft/preview only; disabled execution | No safe secret/config execution path in product | Add optional disabled-by-default provider only after P0 preview reset |
| Feedback/export | Feedback and Markdown/JSON preview | Preview closure exists | Not formal real meeting artifact | Keep preview in Local Shadow; formalize after real pilot |
| Delete/retention | Delete audio/transcript/intermediates/exports | Artifact retention boundary exists | No full real session data lifecycle | Finish once real capture path exists |
| Packaging | Installable desktop app | Scaffold/readiness only | No production installer/signing/notarization | Defer until real value chain passes |

## 8. Next Execution Plan

### P0: Release-Truth Local Shadow Preview

Timebox: 3-5 days.

Goal:

```text
Turn the current work into a truthful, user-facing preview:
Preview works; ASR blocked; real mic blocked; LLM disabled; no hidden costs.
```

Deliverables:

- One workbench entry for the mainline preview.
- One release/readiness summary panel.
- ASR quality diagnostic table:
  - `api-review-001=1.0`
  - `architecture-review-001=1.0`
  - `incident-review-001=0.5 missing timeout/监控阈值`
  - `release-review-001=0.75 missing staging`
  - negative control `candidate_cards=0`
- Not-Go evidence labels on every synthetic/replay/artifact preview.
- Fresh command report:
  - mainline runner exits 0
  - ASR quality exits blocked
  - real mic readiness exits blocked for known reasons

Exit criteria:

```text
Local Shadow Preview can be run and explained in under 5 minutes.
No screen or report implies real meeting readiness.
All blockers are visible without reading logs.
```

### P1: ASR Quality Exit Or Pivot

Timebox: 1-2 weeks, no open-ended expansion.

Goal:

```text
Resolve the ASR gate or stop treating local FunASR as the default real-time ASR path.
```

Allowed work:

- Inspect whether synthetic audio actually contains `timeout`, `监控阈值`, and `staging`.
- If audio generation is defective, fix the evaluation asset and rerun the same gate.
- If audio is correct but FunASR misses entities, try a bounded set of hotword/chunk/model parameters.
- Re-run DRV-046 -> DRV-032.

Forbidden work:

- Broad provider bakeoff without a separate decision.
- More ASR wrapper/schema work unless it directly changes `quality_exit_status`.
- Golden-script backfill into normalizer.
- Calling remote ASR by default.

Exit options:

```text
Go: strict quality gate not blocking.
Degraded Pilot: user explicitly accepts quality risk; does not count as ASR Go evidence.
Pivot: remote ASR optional cost/privacy review or product downgrade to after-meeting evidence review.
Stop: no credible ASR path and no accepted remote/degraded path.
```

### P2: One Real Meeting Shadow Trial

Timebox: 1 week preparation plus 1 real user-authorized meeting.

Entry condition:

```text
P1 passes strict ASR gate
OR explicit degraded pilot acceptance is documented.
```

Execution:

- User explicitly starts capture.
- No background listening.
- Capture a 20-30 minute Chinese technical meeting or a controlled realistic external-audio session.
- Record:
  - transcript timeline
  - ASR metrics
  - EvidenceSpan timeline
  - state timeline
  - candidate/card timeline
  - feedback labels
  - export/delete behavior

Pilot success criteria:

```text
useful or would_have_asked >= 40%
wrong + too_late + too_intrusive <= 20-25%
non-engineering segments produce 0 engineering cards
all useful cards cite EvidenceSpan
delete/retention behavior is explainable and executable
```

Stop criteria:

```text
ASR technical entity recall remains below 0.8 with no path to 0.9.
Cards arrive too late to affect meetings.
User feedback says cards are mostly wrong or distracting.
Evidence cannot be traced back to transcript/audio.
```

## 9. Concrete Next Goal Boundary

The next engineering goal should be:

```text
Build and verify a single Local Shadow Preview release path with truthful status:
one workbench flow -> approved source selection -> Live ASR session -> state/candidate/draft
-> feedback/export preview -> ASR diagnostic/readiness summary -> not-Go labels.
```

This goal intentionally does not claim production readiness. It is valuable because it removes ambiguity for the user and gives the project a stable base from which P1 ASR exit and P2 real pilot can be evaluated.

After this reset, the backlog must be filtered:

- Keep tasks that move P0/P1/P2 exit criteria.
- Drop or park tasks that only add more wrappers.
- Do not start Windows/iOS/Android packaging, collaboration, long-term memory, knowledge-base retrieval, issue creation, or marketplace work until P2 has evidence that realtime Copilot is useful.

## 10. Source Evidence

Primary docs:

- `docs/product-requirements.md`
- `docs/feature-map.md`
- `docs/minimum-valuable-demo-script.md`
- `docs/mainline-goal-progress-and-next-plan-2026-07-04.md`
- `docs/asr-quality-exit-followup-2026-07-04.md`
- `docs/current-mainline-index.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

Primary code:

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/desktop_tauri/src-tauri/src/lib.rs`
- `code/asr_runtime/scripts/asr_worker_sidecar.py`
- `tools/asr_quality_decision_gate.py`
- `tools/real_mic_shadow_test_readiness_gate.py`
- `tools/mainline_usable_e2e_runner.py`

Fresh verification commands:

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id release_reset_fresh_20260705 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr

python3 tools/asr_quality_decision_gate.py \
  --funasr-smoke-assembly-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly-chunk20_hotword.json

python3 tools/real_mic_shadow_test_readiness_gate.py
```
