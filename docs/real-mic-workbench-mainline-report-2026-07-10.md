# Real Mic Workbench Mainline Report - 2026-07-10

## 1. Scope

This report records the current Workbench mainline evidence for:

```text
real browser microphone
  -> realtime Chinese ASR
  -> realtime transcript visible in Workbench
  -> production LLM suggestion card
  -> production LLM approach cards
  -> production LLM minutes
  -> local recording saved and exportable
```

This report does not claim production release readiness. The current evidence includes a 20-minute real browser microphone no-cost soak that exposed and fixed a cockpit count bug, plus a 10-minute post-fix no-cost soak that proves the cockpit now grows during long recording. Production LLM evidence after the latest cockpit/ASR fixes is still pending.

## 2. Root Cause Fixed

The failing 5-minute and first 2-minute real-mic runs were not blocked by microphone capture or audio export. They were blocked in the Workbench organize flow.

Frontend order:

```text
organizeCurrentSession()
  -> generateSuggestionCards()
  -> generateApproachCards()
  -> generateMinutes()
```

The first step called `/llm-execution-runs` and waited for candidate-level LLM calls to finish. Real mic sessions can generate many `llm_request_draft_event` items. A 5-minute run generated 33 draft candidates; the first 2-minute run generated 14. Calling the remote LLM for several candidates in a row prevented approach cards and minutes from running within the 45s organize evidence window.

## 3. Implementation

Implemented bounded candidate execution for formal suggestion cards:

- Backend request schema now accepts `max_candidates`.
- Backend applies `llm-execution-candidate-selection.v1`.
- Candidate selection returns `candidate_selection` with total, selected, skipped, selected ids, skipped ids, max, and reason.
- Default endpoint behavior keeps a bounded normal budget.
- Workbench `整理会议` uses a fast path: `ORGANIZE_FAST_SUGGESTION_BUDGET = 1`.
- Single-purpose `生成会议建议` remains able to request the normal endpoint budget.

Code touched in this round:

```text
code/web_mvp/backend/meeting_copilot_web_mvp/app.py
code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
code/web_mvp/backend/tests/test_app.py
code/web_mvp/backend/tests/test_workbench.py
```

## 4. TDD Evidence

New regression tests were added red-first and then made green:

```text
test_asr_live_llm_execution_runs_enabled_caps_long_meeting_candidates
test_asr_live_llm_execution_runs_enabled_honors_request_candidate_budget
test_workbench_organize_meeting_uses_fast_suggestion_candidate_budget
```

Focused verification:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_workbench.py \
  -q -k 'request_candidate_budget or fast_suggestion_candidate_budget'

2 passed, 164 deselected, 2 warnings
```

Related regression verification:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  -q -k 'llm_execution_runs or llm_execution or approach or minutes or caps_long_meeting or request_candidate_budget or fast_suggestion_candidate_budget or organize or live_mic'

40 passed, 151 deselected, 2 warnings
```

## 5. Failed Run Before Final Fix

Artifact:

```text
artifacts/tmp/browser_live_mic/real-mic-workbench-2min-capped-20260710-172828
```

Result:

```text
input_mode=real_browser_mic
provider=funasr_realtime
provider_mode=real
acceptance_eligible=true
health_status=audio_capture_health_passed
chunk_count=400
asr_final_count=2
suggestion_card_count=1
approach_card_count=0
minutes_char_count=0
llm_call_count=1
audio_export_http_status=200
audio_sha256_matches_session=true
organize_wait_status=timed_out
```

Interpretation:

The first suggestion card was generated, but organize had not reached approach cards or minutes before timeout. This confirmed the first candidate cap was not enough for the formal organize flow; the Workbench organize path needed its own fast suggestion budget.

## 6. Passing Real-Mic Mainline Run

Artifact:

```text
artifacts/tmp/browser_live_mic/real-mic-workbench-2min-fast-organize-20260710-173727
```

Runner shape:

```text
visible Chrome Workbench
real browser getUserMedia microphone
local macOS say -v Tingting Chinese technical meeting playback
production_enabled derivation mode
delete_session_after_run=false
```

Headline:

```text
session_id=rec_mreqsa6o
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
acceptance_eligible=true
acceptance_blockers=[]
health_status=audio_capture_health_passed
derivations_generated=true
counts_as_production_llm_evidence=true
suggestion_card_count=1
approach_card_count=3
minutes_char_count=668
llm_call_count=3
llm_usage_total_tokens=15364
audio_export_http_status=200
audio_sha256_matches_session=true
organize_wait_status=matched
```

Realtime UI metrics:

```text
first_audio_active_offset_ms=247
first_text_visible_latency_ms=7432
first_text_after_audio_active_latency_ms=7197
first_final_visible_latency_ms=32549
first_final_after_audio_active_latency_ms=32304
partial_visible_count=65
final_visible_count=2
frontend_utterance_count=10
frontend_card_count=4
frontend_minutes_visible=true
```

Audio evidence:

```text
audio.saved=true
source_type=browser_live_mic
duration_ms=119808
sample_rate_hz=16000
channel_count=1
file_size_bytes=3833900
format=wav
audio_file_magic=RIFF
audio_sha256_matches_session=true
raw_audio_uploaded=false
remote_asr_called=false
configs_local_read=false
user_audio_committed_to_repo=false
```

## 6.1 5-Minute Real-Mic Recheck

Artifact:

```text
artifacts/tmp/browser_live_mic/real-mic-workbench-5min-fast-organize-history-filter-20260710-175608
```

Runner shape:

```text
visible Chrome Workbench
real browser getUserMedia microphone
local macOS say -v Tingting Chinese technical meeting playback
production_enabled derivation mode
delete_session_after_run=false
history filter enabled
```

Headline:

```text
session_id=rec_mrergapj
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
acceptance_eligible=true
acceptance_blockers=[]
health_status=audio_capture_health_passed
derivations_generated=true
counts_as_production_llm_evidence=true
suggestion_card_count=1
approach_card_count=3
minutes_char_count=817
llm_call_count=3
llm_usage_total_tokens=16332
audio_export_http_status=200
audio_sha256_matches_session=true
organize_wait_status=matched
```

Realtime UI metrics:

```text
first_audio_active_offset_ms=254
first_text_visible_latency_ms=7431
first_text_after_audio_active_latency_ms=7178
first_final_visible_latency_ms=175889
first_final_after_audio_active_latency_ms=175636
partial_visible_count=165
final_visible_count=2
frontend_utterance_count=19
frontend_card_count=4
frontend_minutes_visible=true
```

Audio evidence:

```text
audio.saved=true
source_type=browser_live_mic
duration_ms=299776
sample_rate_hz=16000
channel_count=1
file_size_bytes=9592876
format=wav
audio_file_magic=RIFF
audio_sha256_matches_session=true
raw_audio_uploaded=false
remote_asr_called=false
configs_local_read=false
user_audio_committed_to_repo=false
```

Interpretation:

The organize fast path remains stable with 24 draft candidates: one formal suggestion card is generated first, then approach cards and minutes complete in the same session. The biggest remaining realtime UX issue is not organize completion; it is ASR final latency and transcript quality. Partial/revision text appears around 7.2s after audio activity, but the first final in this 5-minute run appears around 175.6s after audio activity.

## 6.2 Browser All-Buttons E2E Update

Artifact:

```text
artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke
```

Runner shape:

```text
headless Chrome Workbench
local imported Chinese technical meeting audio fixture
fake OpenAI-compatible LLM gateway
no paid LLM usage
no remote ASR
```

Result:

```text
status=go_workbench_all_buttons_smoke
imported_session_id=file_28aae6711d74
fake_llm_request_count=10
screenshot_count=15
downloads=[
  file_28aae6711d74.transcript.txt,
  file_28aae6711d74.minutes.md,
  file_28aae6711d74.audio.wav
]
```

Covered browser buttons:

```text
btn-upload
btn-history
btn-cards
btn-approach
btn-minutes
btn-live
btn-export-transcript
btn-export-minutes
btn-export-audio
btn-auto-suggestion-toggle
btn-organize
btn-delete
```

`btn-record` and `btn-stop` are not faked in this imported-audio lane; they are covered by the real browser microphone E2E lane (`workbench_browser_live_mic_verify.mjs`).

Verification:

```text
python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
6 passed, 1 warning

node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
exit 0

node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
exit 0
```

Interpretation:

Workbench browser-level user-visible controls now have traceable E2E coverage and screenshots. The fixed/imported audio is only a deterministic regression fixture; it is not a substitute for the real microphone long-meeting release gate.

## 6.3 Realtime Auto-Suggestion Race Fix

Problem:

```text
server sends realtime final to browser
  -> browser immediately calls /auto-suggestions/run-once
  -> server had not yet persisted the live session/candidates
  -> possible 404 / empty candidate race during the meeting
```

Fix:

- `asr_stream.handle_stream()` now persists chunk `partial` / `final` state before sending those events to the browser.
- Server VAD endpoint finals are also persisted before browser delivery.
- Partial hints still travel over the same websocket after their raw partial event.

TDD evidence:

```text
test_asr_stream_persists_live_final_before_sending_to_browser

Before fix:
send_text(final) observed repo.get(session_id) == KeyError

After fix:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_asr_stream.py \
  -q -k 'persists_live_final_before_sending_to_browser'

1 passed, 18 deselected, 2 warnings
```

Regression:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_asr_stream.py -q
19 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  tests/test_workbench_all_buttons_smoke.py \
  -q -k 'history or sessions_list or persists_cards or workbench_full_flow or demo_load or llm_execution_runs or approach or minutes or live_mic or organize or workbench_all_buttons or auto_suggestion or partial_hint'

60 passed, 148 deselected, 2 warnings
```

Short real-mic no-cost recheck:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-order-fix-nocost-20260710-183032
session_id=rec_mresojzc
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
acceptance_eligible=true
acceptance_blockers=[]
health_status=audio_capture_health_passed
sample_count=479232
chunk_count=100
active_sample_ratio=0.7804591513087606
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_file_size_bytes=958508
audio_sha256_matches_session=true
```

Realtime UI metrics:

```text
first_audio_active_offset_ms=264
first_text_after_audio_active_latency_ms=6158
first_final_after_audio_active_latency_ms=30258
partial_visible_count=16
final_visible_count=1
frontend_utterance_count=1
frontend_card_count=4
frontend_minutes_visible=true
```

Interpretation:

This closes a concrete race in the meeting-in-progress suggestion path. The recheck deliberately used no-cost deterministic derivations, so it proves the real microphone/ASR/UI/audio-save path after the ordering fix, not production LLM quality or cost.

## 6.3 Technical-Term Normalizer Update

After the order-fix and no-cost real-mic rechecks, the latest visible issue was not another product surface gap. It was Chinese technical-meeting ASR quality: the real browser microphone path produced near misses such as `发布庭审`, `t九九`, and `斯隆看板` in a release-review script. A follow-up short real-mic no-cost recheck proved the Workbench mainline still runs, but also exposed additional variants: `t九`, `t一九`, `四low看板`, and `ure flap`.

Decision:

- Keep public audio as a small deterministic regression aid only; do not expand it into a new download/bake-off loop.
- Keep the main evidence path as real browser microphone / Workbench / realtime ASR / same-session suggestions / saved recording.
- Fix only observed, bounded release-review near misses in the Workbench realtime normalizer.
- Do not add paid remote ASR or broader global replacements.
- Stop treating public-audio download as the main task; the mainline evidence remains real microphone Workbench.

Short real-mic no-cost recheck before the fourth-rule fix:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-normalizer-v3-nocost-20260710-185357
session_id=rec_mretintu
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
acceptance_eligible=true
acceptance_blockers=[]
health_status=audio_capture_health_passed
sample_count=765952
chunk_count=160
active_sample_ratio=0.7612631078709893
derivation_mode=no_cost_deterministic
derivations_generated=true
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_file_size_bytes=1531948
audio_sha256_matches_session=true
workbench_same_session_visible=true
frontend_utterance_count=4
frontend_card_count=4
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
first_text_after_audio_active_latency_ms=7003
first_final_after_audio_active_latency_ms=47283
```

Observed latest normalized text still included:

```text
t九延迟
四low看板
ure flap
t一九延迟迟
```

Implemented bounded rules:

```text
发布庭审 -> 发布评审
  only near release-review / 灰度 / 错误率 / 回滚 / 延迟 / 毫秒 context

t九九 -> P99
  only in metric latency/error-rate context, before generic dictionary replacement can create tP99

t九 / t一九 -> P99
  only in metric latency/error-rate context

斯隆看板 -> SLO看板
  exact phonetic near miss observed in the Workbench real-mic run

四low看板 -> SLO看板
  exact mixed Chinese/English near miss observed in the follow-up real-mic run

ure flap -> feature flag
  only before release-review connectors such as 和 / roll / 回滚 / 王五 / 补充 / 确认
```

TDD evidence:

```text
Red test:
test_normalize_recovers_third_real_browser_mic_release_review_variants

Before fix:
发布庭审 remained unchanged
t九九 became tP99
斯隆看板 remained unchanged

After fix:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py \
  -q -k 'third_real_browser_mic_release_review_variants'

1 passed, 17 deselected, 1 warning

Red test:
test_normalize_recovers_fourth_real_browser_mic_release_review_variants

Before fix:
t九 remained unchanged
四low看板 remained unchanged
ure flap remained unchanged
t一九 remained unchanged

After fix:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py \
  -q -k 'fourth_real_browser_mic_release_review_variants'

1 passed, 18 deselected, 1 warning
```

Regression:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py -q

19 passed, 1 warning

PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py \
  tests/test_workbench_all_buttons_smoke.py \
  -q -k 'history or sessions_list or persists_cards or workbench_full_flow or demo_load or llm_execution_runs or approach or minutes or live_mic or organize or workbench_all_buttons or auto_suggestion or partial_hint or transcript_normalizer'

79 passed, 148 deselected, 2 warnings
```

Boundary:

This improves stability for the currently observed Chinese technical release-review scenario. It does not prove production-grade ASR for noisy natural multi-speaker meetings and does not replace the pending 20-minute real microphone long-meeting gate. If more variants appear, the next product-level step should be hotword/profile tuning plus lightweight post-ASR correction, not an endless sequence of one-off replacements.

## 6.4 Accumulated Partial Transcript Update

Problem:

The user-visible issue was not “escaping” or a minor page text bug. During a real meeting, the page must keep showing the meeting content as it grows. The old real-time path updated one short live tail while waiting for final ASR output. With FunASR final latency around tens of seconds or worse, that made the product feel like it was replacing text instead of accumulating a usable meeting transcript.

Decision:

- Public audio is only a small deterministic Chinese ASR regression aid. It is not the product and is not a reason to keep downloading more samples.
- The mainline remains real browser microphone -> realtime ASR -> Workbench visible transcript -> realtime suggestions -> saved recording -> review.
- FunASR stream partials must be emitted as accumulated text, not only the latest rolling short fragment.
- Stable partials may be shown as visible provisional transcript drafts labeled `临时稿`.
- Once final/revision arrives for the same segment, the page must remove or suppress the matching provisional draft so users do not see duplicate meeting content after the meeting is organized/refreshed.

Implementation:

```text
code/asr_runtime/scripts/funasr_stream_worker.py
  partial events now emit merged_text produced by merge_partial_hypothesis(...)

code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
  live path shows stable partials as provisional transcript drafts
  live final/revision removes matching provisional drafts
  snapshot render path computes resolvedPartialDraftKeysFromEvents(...)
  snapshot render suppresses partial drafts already covered by final/revision

code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py
  retains stable partial snapshots so later short tails do not erase the useful partial
```

TDD evidence:

```text
test_stream_worker_emits_accumulated_partial_text_for_live_transcript
  red: worker emitted ["发布评审", "P99延迟超过九百毫秒", "张三补SLO看板"]
  green: worker emits accumulated partials ending with
         "发布评审P99延迟超过九百毫秒张三补SLO看板"

test_workbench_snapshot_renderer_hides_resolved_partial_drafts
  red: snapshot renderer had no resolved partial draft filtering
  green: transcript_partial drafts already covered by final/revision are suppressed
```

Verification:

```text
python3 -m pytest code/asr_runtime/tests/test_transcribe_funasr.py -q
21 passed, 1 warning

PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_live_events.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_workbench.py \
  -q -k 'partial_hint or stable_partial or auto_suggestion or live_final or suggestion_candidate or llm_request_draft or workbench_auto_suggestion or provisional_transcript_draft or snapshot_renderer_hides_resolved_partial_drafts or raw_realtime_asr_partial'
30 passed, 126 deselected, 2 warnings

PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py \
  code/web_mvp/backend/tests/test_live_events.py \
  tests/test_workbench_all_buttons_smoke.py \
  -q -k 'history or sessions_list or persists_cards or workbench_full_flow or demo_load or llm_execution_runs or approach or minutes or live_mic or organize or workbench_all_buttons or auto_suggestion or partial_hint or transcript_normalizer or stable_partial or suggestion_candidate or provisional_transcript_draft or snapshot_renderer_hides_resolved_partial_drafts'
95 passed, 185 deselected, 2 warnings
```

Real-mic evidence before snapshot filtering:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-merged-partial-nocost-20260710-192740
session_id=rec_mreuq0q0
input_mode=real_browser_mic
provider=funasr_realtime
provider_mode=real
acceptance_eligible=true
health_status=audio_capture_health_passed
chunk_count=160
asr_final_count=1
derivation_mode=no_cost_deterministic
suggestion_card_count=3
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_sha256_matches_session=true
first_text_after_audio_active_latency_ms=7462
first_final_after_audio_active_latency_ms=48319
partial_visible_count=25
final_visible_count=1
```

Important observation:

The session now contains a long `transcript_partial`, and final-before evidence includes `partial_hint_event` plus `local_deterministic_asr_stable_partial_skeleton` suggestion candidates. However, the refreshed page still showed both `临时稿` and `发言` for the same segment, so snapshot filtering was required.

Real-mic evidence after snapshot filtering:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-merged-partial-snapshot-filter-nocost-20260710-193221
session_id=rec_mreuw1ex
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
acceptance_eligible=true
acceptance_blockers=[]
health_status=audio_capture_health_passed
chunk_count=160
asr_final_count=1
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_sha256_matches_session=true
workbench_same_session_visible=true
frontend_utterance_count=4
frontend_card_count=4
frontend_partial_hint_count=1
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
first_text_after_audio_active_latency_ms=7450
first_partial_after_audio_active_latency_ms=7450
first_final_after_audio_active_latency_ms=48346
partial_visible_count=25
final_visible_count=1
```

The refreshed page no longer displays the resolved `临时稿` as a duplicate final transcript item. The underlying session still keeps the long `transcript_partial` event for traceability and suggestion timing.

Remaining boundary:

This is a real microphone and real local FunASR evidence run, but its derivations are no-cost deterministic. It does not add production LLM evidence. ASR still misrecognizes natural external playback prefixes and some Chinese technical terms. The first final in these short runs is still around 48s after audio activity, so the realtime product must rely on partial/revision until ASR endpoint/final latency is improved.

## 7. Current Product Status

Can claim:

- Real browser microphone capture works in visible Chrome.
- Local recording is saved and exportable as WAV.
- FunASR realtime provider is used, not mock ASR.
- Realtime transcript appears in the Workbench during capture.
- Stable partial transcript can now accumulate on the page as provisional meeting text, and resolved provisional drafts are suppressed after final/revision in refreshed session snapshots.
- The formal organize flow can now complete suggestion, approach, and minutes in the same session.
- Production OpenAI-compatible gateway evidence is present for this passing run.
- Normal Workbench history now excludes mock/demo/local-event test sessions by default; demo history requires explicit `?demo=1`/demo opt-in and backend `include_demo=true`.
- Browser-level Workbench main buttons now have E2E coverage with screenshots, including audio export.
- Realtime final events are now persisted before browser delivery, reducing the race where in-meeting auto-suggestions could fire before the session/candidates existed.
- The latest observed release-review technical term near misses are normalized in the Workbench realtime path without adding paid ASR or broad global replacements.

Cannot claim yet:

- 20-minute real long-meeting production gate is complete.
- Chinese ASR quality is production-grade for noisy or natural multi-speaker meetings.
- Tauri packaged desktop all-button click flow is complete.
- Mac signed/notarized package or Windows real-machine delivery is complete.

## 8. Remaining Risks

P0/P1 risks to handle next:

- ASR still produces visible Chinese/English technical term errors such as `feature flag`, Redis, checklist, and owner variants.
- First text latency is around 7.2s after audio activity in the latest 5-minute run; acceptable for current pre-release evidence but still high for a polished realtime Copilot.
- First final latency is still high in the 5-minute run, around 175.6s after audio activity. The meeting UI currently depends on partial/revision text for the realtime experience.
- Short real-mic no-cost runs after the accumulated-partial fix still show first final around 48s after audio activity.
- `整理会议` is now bounded, but approach/minutes are still remote LLM calls and can vary with gateway latency.
- The current passing evidence is 5 minutes, not a 20-minute soak.
- The latest post-fix real-mic recheck used no-cost deterministic derivations, so it does not add production LLM evidence.

## 9. Next Recommended Steps

1. Improve realtime ASR final latency and Chinese technical-term stability without adding paid remote ASR by default.
2. Run a 20-minute real microphone wall-clock soak after the ASR latency/quality risks are accepted or improved.
3. Add packaged Tauri click-flow verification after the browser Workbench lane remains stable.

## 10. History Isolation Update

Implemented after the passing 2-minute mainline run:

```text
GET /live/asr/sessions
  -> default: production/user-facing history only
GET /live/asr/sessions?include_demo=true
  -> explicit demo/selftest history
```

Hidden by default:

```text
mock_asr_session
local_asr_event_file
input_source=mock
input_source=local_event_file
input_source=simulated_realtime_wav
provider=local_mock_asr
provider=fake
acceptance_blocker=mock_or_demo_session
acceptance_blocker=local_event_file_not_real_input
```

Frontend:

```text
loadSessionHistory() -> sessionHistoryPath()
normal mode -> /live/asr/sessions
demo opt-in -> /live/asr/sessions?include_demo=true
```

Verification:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_workbench.py \
  -q -k 'hides_mock_sessions_by_default or history_requests_demo_sessions_only_after_demo_opt_in'

2 passed, 165 deselected, 2 warnings

node code/web_mvp/e2e/workbench_smoke.mjs
workbench smoke OK
```

## 11. Append-First Transcript Projection Update

Implemented after the user clarified that a meeting page that keeps replacing the visible text would force users to wait until the end of the meeting to understand the discussion.

Product rule:

```text
ASR partial may revise the current hypothesis.
Workbench transcript must remain user-readable while the meeting is happening.
Stable partial growth is appended as provisional meeting content.
Only the current live tail is mutable.
Final/revision replaces the matching provisional chunks and removes duplicates.
```

This is not an escaping problem. `escapeHtml(...)` is still required for safe rendering, but it does not define transcript append semantics. The relevant business boundary is:

```text
backend session/events/audio
  -> durable trace and replay source
frontend transcript projection
  -> append-first user-readable meeting stream
frontend live tail
  -> the only replace-in-place ASR hypothesis row
```

Implementation:

```text
code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
  partialDraftDeltaText(...)
  partialLiveTailText(...)
  appendPartialDraftUtterance(...)
    appends new provisional chunks instead of replacing one draft node
  removeCommittedPartialDraftsForSegment(...)
    removes all provisional chunks for the resolved segment
```

The same pass restored frontend contracts that were lost during the `workbench.js` recovery:

```text
shouldRunRealtimeAutoSuggestionFromHint(...)
  production LLM only, no no-cost self-test auto-trigger

ORGANIZE_FAST_SUGGESTION_BUDGET = 1
  organize flow caps suggestion candidates before approach/minutes

sessionHistoryPath()
  normal Workbench history excludes demo/mock unless demo opt-in requests include_demo=true
```

Verification:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
80 passed, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
exit 0
```

Remaining boundary:

This update fixes the frontend projection rule and restores the lost Workbench contracts. It has not yet added a new real-microphone long-meeting evidence run. The next browser/real-mic verification must confirm that the page scrolls naturally, provisional chunks do not flood the view, final replacement does not visibly jump too much, recording export still hashes to the session audio, and realtime suggestion cards still trigger from the same session.

## 12. Clean User Projection Real-Mic Recheck

Implemented after the append-first fix exposed another product issue: the final Workbench view still looked like an engineering event log because transcript revisions showed `修正原话：rec_...`, and realtime reminder metadata showed raw evidence/source event ids.

Code changes:

```text
code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
  resolvedRevisionKeysFromEvents(...)
  removeRevisionRowsForSegment(...)
  candidateReminderMetaLabel(...)

code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
  recording_phase_ui_samples.json
  readRecordingPhaseUiSample(...)
```

User-facing behavior:

```text
main transcript
  shows clean 发言 rows
  hides revisions already covered by final
  does not display 修正原话 / rec_* ids

realtime reminder panel
  shows 来自会议原话 / 来自实时文字
  does not display asr_ev_* or transcript_partial:* ids

evidence metadata
  remains in events / DOM dataset for clickback and audit
```

Fresh verification:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
84 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py \
  code/web_mvp/backend/tests/test_live_events.py \
  tests/test_workbench_all_buttons_smoke.py \
  -q -k 'history or sessions_list or persists_cards or workbench_full_flow or demo_load or llm_execution_runs or approach or minutes or live_mic or organize or workbench_all_buttons or auto_suggestion or partial_hint or transcript_normalizer or stable_partial or suggestion_candidate or provisional_transcript_draft or snapshot_renderer_hides_resolved_partial_drafts or partial_drafts_append_chunks or final_removes_all_provisional_partial_chunks or revision_rows_without_engineering_metadata or hides_revisions_covered_by_final_segment or live_final_removes_revision_rows or candidate_reminders_hide_engineering_evidence_ids'
102 passed, 184 deselected, 2 warnings
```

Fresh real browser microphone evidence:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-clean-ui-nocost-20260710-203134
session_id=rec_mrex06ny
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
acceptance_eligible=true
acceptance_blockers=[]
health_status=audio_capture_health_passed
sample_count=876544
chunk_count=183
active_sample_ratio=0.8525367808119159
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_file_magic=RIFF
audio_sha256_matches_session=true
session_audio_saved=true
session_audio_duration_ms=54784
recording_phase_ui_samples=12
partial_draft_count progression=1 -> 8
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=4
frontend_partial_hint_count=1
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
first_text_after_audio_active_latency_ms=7220
first_final_after_audio_active_latency_ms=55301
partial_visible_count=30
final_visible_count=1
```

Clean UI check:

```text
jq -r '.transcript_text, .candidate_text' page_state_after_failure.json |
  rg -n '修正原话|修正：|rec_|asr_ev_|transcript_partial:'

no matches
```

Remaining boundary:

This is still a no-cost deterministic derivation run, not new production LLM evidence. It confirms the visible Workbench mainline shape, recording preservation, and local ASR provider path. It also confirms the remaining blocker: ASR quality is not production-grade for the current Mac speaker-to-mic setup. The transcript still contains obvious Chinese/English technical term errors such as `payment get`, `flalaw backor`, `古斯欧/古斯捞看板`, and `featfeatfflap`. The next production work should target ASR quality/latency and a 20-minute real meeting gate, not more UI cleanup.

## 13. Partial Draft Visibility Decoupled From Technical Keywords

Follow-up after the user challenged the append/replace direction again:

```text
If the page only replaces the visible realtime text, the user cannot keep reading the meeting while it happens.
If partial drafts only append when the text contains technical-release keywords, ordinary meetings still look replace-only.
```

Root cause:

```text
DEC-305 fixed the frontend projection model:
  stable partial growth -> provisional `临时稿`
  current live tail -> mutable `实时`
  final/revision -> remove matching draft chunks

But `shouldCommitPartialDraft(...)` still required PARTIAL_DRAFT_MARKERS such as 灰度、发布、回滚、P99.
That made transcript visibility depend on semantic keywords.
```

Decision:

```text
Visible transcript append is a baseline meeting UX rule.
It must depend on basic stability/readability signals, not on technical meeting semantics.

Current commit rule:
  segment id exists
  compact text length >= PARTIAL_DRAFT_MIN_CHARS
  confidence is absent or >= PARTIAL_DRAFT_MIN_CONFIDENCE

Semantic quality and technical keywords remain useful for:
  suggestion candidates
  realtime partial hints
  AI card triggering

They do not gate whether the user can see the meeting text.
```

Implementation:

```text
code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
  removed PARTIAL_DRAFT_MARKERS gate from shouldCommitPartialDraft(...)

code/web_mvp/backend/tests/test_workbench.py
  added test_workbench_partial_draft_visibility_is_not_gated_by_semantic_markers
```

Verification:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_partial_draft_visibility_is_not_gated_by_semantic_markers -q

red first:
  failed because PARTIAL_DRAFT_MARKERS.some(...) was still inside shouldCommitPartialDraft(...)

green after implementation:
  1 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  -q -k 'partial_draft or partial_drafts or live_partial or snapshot_renderer_hides_resolved_partial_drafts or revision_rows_without_engineering_metadata or candidate_reminders_hide_engineering_evidence_ids'
  6 passed, 79 deselected, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
exit 0
```

Boundary:

This is a product projection fix, not an ASR quality fix. The next real browser mic run still needs to verify that ordinary speech creates readable `临时稿` chunks during recording, that the page scrolls naturally, and that final/revision cleanup does not visually jump too much. The 20-minute real meeting gate and production LLM evidence remain separate blockers.

## 14. Ordinary Chinese Real-Mic Append And Context-Aware No-Cost Derivation

Purpose:

```text
Verify the Workbench mainline with ordinary Chinese meeting content, not a release-review script.
Confirm that realtime transcript visibility no longer depends on technical keywords.
Confirm no-cost deterministic derivations do not mislead by returning fixed release-review minutes.
```

First run after DEC-307:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-ordinary-append-nocost-20260710-205103
session_id=rec_mrexpauk
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
acceptance_eligible=true
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=1
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_file_magic=RIFF
audio_sha256_matches_session=true
session_audio_saved=true
session_audio_duration_ms=69888
recording_phase_ui_samples=15
partial_draft_count progression=0 -> 9
```

Finding:

```text
Realtime transcript append was good:
  ordinary Chinese partial drafts grew during recording even without release keywords.

Recording preservation was good:
  WAV export returned RIFF and sha256 matched the saved session audio.

No-cost derivation was wrong:
  final minutes still contained fixed release-review content:
  灰度 / 回滚 / P99 / 错误率
  even though the meeting discussed 用户访谈反馈、首页、持续看到、录音文字稿和会议纪要.
```

Fix:

```text
code/web_mvp/backend/meeting_copilot_web_mvp/app.py
  deterministic_demo remains no-cost / not_called / local only
  suggestion, approach, and minutes now derive from current session transcript
  release-review template is only used when transcript contains release context

code/web_mvp/backend/tests/test_packaged_no_cost_demo_derivation.py
  added test_demo_no_cost_derivation_uses_transcript_context_for_ordinary_meeting
  updated demo history assertion to use include_demo=true, preserving mock/demo history isolation
```

TDD:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_packaged_no_cost_demo_derivation.py::test_demo_no_cost_derivation_uses_transcript_context_for_ordinary_meeting -q

red first:
  failed because ordinary meeting output still used 灰度/回滚 release template

green:
  1 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_packaged_no_cost_demo_derivation.py -q
  4 passed, 2 warnings
```

Post-fix real browser microphone evidence:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-ordinary-context-nocost-20260710-205801
session_id=rec_mrexy7fd
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
acceptance_eligible=true
acceptance_blockers=[]
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=1
approach_card_count=1
minutes_char_count=431
audio_export_http_status=200
audio_file_magic=RIFF
audio_file_size_bytes=2236460
audio_sha256_matches_session=true
session_audio_saved=true
session_audio_duration_ms=69888
recording_phase_ui_samples=15
partial_draft_count progression=0 -> 9
```

Visible final content:

```text
Approach:
  可以把用户访谈反馈先收敛为首页引导、实时上下文展示、录音、文字稿和会议纪要三类体验问题，再排优先级。

Minutes:
  本次讨论围绕用户访谈反馈。
  首页需要更清楚地告诉新用户下一步该做什么。
  会议中需要持续看到前面的讨论内容，而不是只显示最后一句话。
  结束后需要马上看到录音、文字稿和会议纪要。
  王五负责整理反馈。
  李四明天确认页面文案。
```

Clean-context check:

```text
jq -r '.transcript_text, .candidate_text, .approach_text, .minutes_text' page_state_after_failure.json |
  rg -n '灰度|回滚|P99|错误率|修正原话|修正：|rec_|asr_ev_|transcript_partial:'

no matches
```

Remaining boundary:

This improves no-cost self-test credibility, not production LLM quality. ASR still has ordinary speech errors such as `今天 -> 这天`, and final text may truncate when the run stops before the repeated script fully completes. The production path still needs a 20-minute real meeting gate, latency targets, and at least one current production LLM evidence run before any production-ready claim.

## 15. Mixed Long-Meeting No-Cost Findings And Fixes

Purpose:

```text
Stop treating every new real-mic run as open-ended evaluation.
Use the 10-minute mixed real-mic soak to close concrete user-facing issues:
  repeated candidate reminders
  mixed-topic no-cost minutes dropping one topic
  long meeting recording/export integrity
```

10-minute mixed no-cost soak:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-mixed-10min-nocost-20260710-210533
session_id=rec_mrey7wdr
input_mode=real_browser_mic
provider=funasr_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
acceptance_eligible=true
chunk_count=2000
audio_export_http_status=200
audio_file_size_bytes=19193900
audio_sha256_matches_session=true
session_audio_duration_ms=599808
recording_phase_ui_samples=121
partial_draft_count progression≈0 -> 87
first_text_after_audio_active_latency_ms≈7199
first_final_after_audio_active_latency_ms≈600345
partial_visible_count=333
final_visible_count=1
browser_console_error_count=0
network_error_count=0
```

Findings:

```text
Good:
  real browser mic chain stayed alive for about 10 minutes
  recording was saved/exported and sha256 matched session audio
  live partial/provisional text accumulated during recording

Problems:
  candidate reminder panel repeated the same reminders many times
  mixed-topic no-cost minutes could be dominated by one template/topic
  first final latency was near end-of-recording, so realtime UX still relies on partial/provisional text
  Chinese technical ASR still misheard SLO, rollback owner, feature flag, payment-gateway-like terms
```

Fixes:

```text
DEC-309:
  Workbench candidate reminders now dedupe visible reminders by semantic key
  visible list is capped at 8
  older/repeated reminders are folded as N 条较早或重复提醒已收起

DEC-310:
  no-cost deterministic mixed-topic minutes preserve both product feedback and release-risk topics
  key points cap expanded to 6
  no-cost path remains local/not_called and does not count as production LLM evidence
```

Post-fix evidence:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-mixed-context-dedupe-nocost-20260710-212125
session_id=rec_mreysb3u
input_mode=real_browser_mic
provider=funasr_realtime
acceptance_eligible=true
chunk_count=800
audio_export_http_status=200
audio_sha256_matches_session=true
session_audio_duration_ms=239872
recording_phase_ui_samples=49
visible fold text: 20 条较早或重复提醒已收起
approach preserved product + release context

artifact=artifacts/tmp/browser_live_mic/real-mic-mixed-minutes-both-nocost-20260710-212729
session_id=rec_mrez03qt
input_mode=real_browser_mic
provider=funasr_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
acceptance_eligible=true
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=514
audio_export_http_status=200
audio_file_magic=RIFF
audio_file_size_bytes=3833900
audio_sha256_matches_session=true
session_audio_saved=true
session_audio_duration_ms=119808
recording_phase_ui_samples=25
visible fold text: 11 条较早或重复提醒已收起
minutes include both:
  用户访谈反馈、首页、持续看到前文、录音/文字稿/会议纪要
  小流量灰度、错误率阈值、回滚负责人、观察窗口、兼容性测试
```

Remaining boundary:

```text
These runs are no-cost deterministic derivation evidence, not production LLM evidence.
No 20-minute real meeting gate has passed yet.
Final ASR latency remains too high for realtime UX without partial/provisional text.
ASR quality for mixed Chinese technical meetings remains below production expectation.
```

## 16. Workbench Left Column Product Boundary

User problem:

```text
The page had a left column of counts that looked like it had no real business purpose.
The underlying counts were not fake, but they did not explain the realtime meeting Copilot workflow.
```

Root cause:

```text
Old left column:
  会议重点:
    决定了什么 / 待办事项 / 风险提醒 / 待确认问题
  实时建议:
    正在提醒 / 方案分析

This mixed candidate classifications and derived card counts.
It did not show whether transcript, realtime reminders, AI suggestions, recording, and minutes were complete.
```

Implemented change:

```text
New left column:
  会议状态:
    文字记录
    实时提醒
    AI 建议
    方案分析
    录音保存
    会后复盘
  会议重点:
    决定了什么
    待办事项
    风险提醒
    待确认问题

syncMeetingOverview() updates the status from current visible transcript/cards/audio/minutes state.
runAutoSuggestionsOnce() now merges returned cards into currentSuggestionCards before rendering,
so the left AI 建议 count cannot lag behind the right suggestion cards.
```

TDD and focused verification:

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_left_column_explains_meeting_mainline_status -q

GREEN:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_left_column_explains_meeting_mainline_status \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_auto_suggestion_updates_left_ai_suggestion_count_state \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_auto_suggestion_uses_session_orchestrator_api_not_manual_button_click \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_auto_suggestion_triggers_after_final_and_session_snapshot -q

4 passed, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
exit 0
```

Browser verification:

```text
url=http://127.0.0.1:8765/workbench
note=/workbench HTML is cached in WORKBENCH_HTML at backend process startup, so 8765 had to be restarted.
visible left column includes:
  会议状态
  文字记录 0
  实时提醒 0
  AI 建议 0
  方案分析 0
  录音保存 未保存
  会后复盘 未生成
  会议重点

screenshot=artifacts/tmp/ui_screenshots/workbench-left-status-20260711/workbench-left-status-mobile.png
```

Remaining boundary:

```text
This fixes product comprehension and status consistency.
DEC-312 now makes the 会议重点 rows a candidate-reminder filter action panel.
Future left-column work should add click-to-evidence only after filter behavior remains stable in browser E2E and long meetings.
This is not a production-ready claim and does not replace the 20-minute real meeting gate.
```

## 17. Workbench Left Meeting Focus Action Panel

User problem:

```text
Even after DEC-311, the left meeting-focus rows still looked like passive counts.
For realtime meetings, passive counts are not enough; the user needs to quickly narrow the right-side reminder queue to risks, action items, decisions, or open questions.
```

Implemented change:

```text
The four 会议重点 rows are now buttons:
  DecisionCandidate -> 决定了什么
  ActionItem -> 待办事项
  Risk -> 风险提醒
  OpenQuestion -> 待确认问题

Clicking a row filters candidate-panel to that reminder type.
The panel shows:
  正在查看：<type>
  显示全部

The filter affects only the visible reminder list:
  session events remain append-only
  c-gap / s-candidates remain total reminder counts
  hidden reminders are not deleted
```

Implementation notes:

```text
workbench.html:
  .focus-filter buttons with data-focus-type and aria-pressed

workbench.js:
  currentCandidateFocusType
  candidateReminderFocusType()
  filteredCandidateReminders()
  setCandidateFocusFilter()
  clearCandidateFocusFilter()
  bindCandidateFocusFilters()
  candidateFocusCounts()
  syncCandidateFocusCounts()
  currentReminderCount()
  visibleCandidateReminderCount()

Snapshot rendering and live incremental rendering now both use syncCandidateFocusCounts(),
so realtime candidate events update c-decision / c-action / c-risk / c-question immediately.
syncMeetingOverview() derives reminder totals from current session events, with a visible candidate panel fallback,
so c-gap does not regress to 0 while reminder cards are visible.
```

TDD:

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_meeting_focus_rows_are_actionable_candidate_filters \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_candidate_filter_shows_clear_state_without_changing_counts \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_live_candidate_events_refresh_focus_counts_with_snapshot_path \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_overview_reminder_count_derives_from_current_events_not_stale_dom -q

GREEN:
4 passed, 2 warnings
```

Browser verification:

```text
In-app browser:
  url=http://127.0.0.1:8765/workbench?demo=1&verify=focus-filter
  script=/static/workbench.js?v=20260711-focus-filter2
  Risk filter:
    visible candidate cards=1
    visible candidate type=Risk
    c-gap=4
    clear button visible=true
  Clear filter:
    visible candidate types=DecisionCandidate, OpenQuestion, Risk, ActionItem
  artifact=artifacts/tmp/ui_screenshots/workbench-left-focus-filter-20260711/browser-report.json
  screenshots:
    artifacts/tmp/ui_screenshots/workbench-left-focus-filter-20260711/risk-filter.png
    artifacts/tmp/ui_screenshots/workbench-left-focus-filter-20260711/all-reminders.png

Headless E2E:
  node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
  status=go_workbench_all_buttons_smoke
  focus_filter_coverage:
    DecisionCandidate=clicked_filter
    ActionItem=clicked_empty_filter
    Risk=clicked_filter
    OpenQuestion=clicked_filter
    all=clicked_clear_filter
  screenshot_count=17
  screenshots:
    artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/05-candidate_filter_risk.png
    artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/06-candidate_filter_all.png
```

Remaining boundary:

```text
This is a Workbench UI/product-flow improvement.
It does not prove ASR quality, production LLM quality, or 20-minute real meeting stability.
Existing headless E2E clicks every focus row and clears the filter.
Existing screenshot evidence covers Risk filtering and clearing back to all reminders.
Future evidence can add per-category screenshots, but that is not the next mainline blocker.
The next mainline blocker remains long real-mic meeting stability and ASR/LLM production acceptance.
```

## 18. Responsive Meeting Cockpit Placement

User problem:

```text
In the in-app browser screenshot, the meeting cockpit appeared near the bottom of a narrow layout.
This made the left column feel secondary even though DEC-311/312 already made it the meeting cockpit.
```

Decision:

```text
On narrow/mobile layouts, the meeting cockpit must appear immediately after the top action bar:
  topbar
  left    <- meeting status and meeting focus
  center  <- realtime transcript
  right   <- details, suggestions, minutes, history
  status
```

TDD:

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_mobile_layout_keeps_meeting_cockpit_before_detail_panels -q

Failure:
  grid-template-areas still used topbar -> center -> right -> left -> status.

GREEN:
  PYTHONPATH=code/web_mvp/backend python3 -m pytest \
    code/web_mvp/backend/tests/test_workbench.py::test_workbench_mobile_layout_keeps_meeting_cockpit_before_detail_panels -q
  1 passed, 2 warnings

Regression:
  PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
  93 passed, 2 warnings

  python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
  7 passed, 1 warning

  node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
  status=go_workbench_all_buttons_smoke
  screenshot_count=17
```

Browser evidence:

```text
url=http://127.0.0.1:8765/workbench?demo=1&verify=responsive-cockpit-final
viewport=696x761
gridTemplateAreas="topbar" "left" "center" "right" "status"
left.top=202
center.top=681
right.top=805
artifact=artifacts/tmp/ui_screenshots/left-column-responsive-20260711/browser-report.json
screenshot=artifacts/tmp/ui_screenshots/left-column-responsive-20260711/workbench-responsive-cockpit.png
```

Boundary:

```text
This is a UI discoverability fix.
It does not change ASR, LLM, audio recording, session persistence, or candidate reminder semantics.
If the status rows later become clickable navigation, add keyboard-order and accessibility coverage.
```

## 19. 20-Minute Real-Mic No-Cost Soak And Cockpit Count Fix

Long real-mic no-cost evidence:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-20min-nocost-20260711-020134
session_id=rec_mrf8skk1
record_seconds=1200
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
acceptance_eligible=true
acceptance_blockers=[]
health_status=audio_capture_health_passed
sample_count=19197952
chunk_count=4000
active_sample_ratio=0.8201879033763602
derivation_mode=no_cost_deterministic
derivations_generated=true
counts_as_production_llm_evidence=false
events_count=363
suggestion_candidate_event=74
llm_request_draft_event=74
transcript_revision=64
transcript_final=1
suggestion_card_count=3
approach_card_count=1
minutes_char_count=382
audio.duration_ms=1199872
audio.file_size_bytes=38395948
audio_export_http_status=200
audio_sha256_matches_session=true
first_text_after_audio_active_latency_ms=7196
first_final_after_audio_active_latency_ms=126046
partial_visible_count=666
final_visible_count=10
recording_phase_ui_samples=241
browser_console_error_count=0
network_error_count=0
delete_session_after_run=false
```

Interpretation:

```text
The 20-minute real browser microphone lane is not empty and not mock:
audio capture, local realtime ASR, append-first realtime text, local recording,
audio export, deterministic no-cost suggestion/approach/minutes, and session
persistence all produced evidence.

This run is not production LLM evidence because it intentionally used
no_cost_deterministic derivation mode.
```

Bug found during the 20-minute run:

```text
The page had visible transcript content, 74 candidate reminders in session state,
and bottom/right panels showed realtime reminders, but the left meeting cockpit
top counters stayed at:

文字记录 0
实时提醒 0

CDP probe during the run showed:
visibleTranscriptCount()=31
currentReminderCount()=3
syncMeetingOverview() still wrote 0

Root cause:
Number.isFinite(Number(null)) is true because Number(null) is 0.
The default null parameters in syncMeetingOverview() were therefore treated as
explicit zero overrides instead of falling back to live DOM/session state.
```

TDD fix:

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_overview_defaults_do_not_treat_null_as_zero -q

GREEN:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_overview_defaults_do_not_treat_null_as_zero \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_overview_reminder_count_derives_from_current_events_not_stale_dom -q

2 passed, 2 warnings

Regression:
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
95 passed, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
exit 0
```

Implementation:

```text
numericCountOverride(value)
  null / undefined / "" -> no override, use live state
  explicit numeric values including 0 -> override

syncMeetingOverview()
  transcript default -> visibleTranscriptCount()
  reminder default -> currentReminderCount()
```

Evidence script improvement:

```text
workbench_browser_live_mic_verify.mjs now records meeting cockpit counts in:
  recording_phase_ui_samples.json -> samples[].cockpit_counts
  ui_verification.json -> meeting_cockpit_counts

This makes cockpit drift visible in machine-readable evidence instead of relying
only on screenshots.
```

Post-fix real-mic recheck:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-cockpit-fix-nocost-20260711-022550
session_id=rec_mrf9nsgc
record_seconds=120
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
acceptance_eligible=true
health_status=audio_capture_health_passed
sample_count=1916928
chunk_count=400
active_sample_ratio=0.805641109107906
derivation_mode=no_cost_deterministic
derivations_generated=true
counts_as_production_llm_evidence=false
events_count=50
suggestion_candidate_event=10
suggestion_card_count=3
approach_card_count=1
minutes_char_count=403
audio.duration_ms=119808
audio_export_http_status=200
audio_sha256_matches_session=true
first_text_after_audio_active_latency_ms=7220
first_final_after_audio_active_latency_ms=72474
partial_visible_count=66
final_visible_count=2
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
```

Post-fix cockpit evidence:

```text
recording phase:
  sample 1: 文字记录=0, 实时提醒=0
  sample 3: 文字记录=1, 实时提醒=0
  late samples: 文字记录=14-16, 实时提醒=2

final ui_verification meeting_cockpit_counts:
  transcript=1
  realtime_reminders=11
  ai_suggestions=3
  approach=1
  audio=已保存
  minutes=已生成
  decisions=4
  actions=1
  risks=6
  questions=0
```

Remaining boundary:

```text
The cockpit count bug is fixed and verified in a fresh real-mic no-cost run.
This still does not prove production LLM behavior after the latest UI fix, and
it does not solve ASR quality for Chinese mixed technical meetings. The ASR still
mishears terms such as payment-gateway, feature flag, error rate, owner, and
some English/Chinese mixed release terms under speaker-to-mic playback.
Next mainline work should avoid endless one-off word chasing and instead move
toward a bounded hotword/profile/post-ASR correction strategy plus production
LLM recheck when cost is intentionally allowed.
```

## 20. Post-Fix 10-Minute Cockpit Soak And ASR Normalizer V4

### 20.1 Why This Was Added

The user again pointed out that the left column looked like it had no practical
business value. The code path already defined the column as a meeting cockpit,
but the empty-state UX still made the concern valid:

```text
会议状态
  文字记录 0
  实时提醒 0
  AI 建议 0
  方案分析 0
  录音保存 未保存
  会后复盘 未生成
会议重点
  决定了什么 0
  待办事项 0
  风险提醒 0
  待确认问题 0
```

The product decision is now explicit:

```text
The left column is the meeting cockpit.
It must prove three states, not just render numbers:
  1. empty / waiting-to-start state
  2. recording state with growing transcript and reminders
  3. review state with suggestions, approach cards, saved audio, and minutes
```

It is not a transcript list, mock area, or final minutes panel. It should not
grow into another long-form content region. If the four `会议重点` rows remain
clickable when the count is 0, the right panel must show a clear empty filtered
state. If the product later disables zero-count filters, the browser E2E and
keyboard/accessibility tests must change in the same implementation.

### 20.2 10-Minute Post-Fix No-Cost Real-Mic Evidence

Artifact:

```text
artifacts/tmp/browser_live_mic/real-mic-cockpit-fix-10min-nocost-20260711-092525
```

Headline:

```text
record_seconds=600
session_id=rec_mrfondiy
input_mode=real_browser_mic
ui_coverage=visible_chrome
provider=funasr_realtime
provider_mode=real
is_mock=false
acceptance_eligible=true
health_status=audio_capture_health_passed
sample_count=9596928
chunk_count=2000
active_sample_ratio=0.8188720390524968
events_count=159
transcript_revision=32
state_event=31
scheduler_event=31
suggestion_candidate_event=31
llm_request_draft_event=31
transcript_partial=1
partial_hint_event=1
transcript_final=1
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=391
audio.duration_ms=599808
audio.file_size_bytes=19193900
audio_export_http_status=200
audio_sha256_matches_session=true
first_text_after_audio_active_latency_ms=7463
first_final_after_audio_active_latency_ms=228944
partial_visible_count=332
final_visible_count=3
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
```

Final cockpit counts:

```text
transcript=1
realtime_reminders=32
ai_suggestions=3
approach=1
audio=已保存
minutes=已生成
decisions=7
actions=3
risks=22
questions=0
```

Recording-phase samples proved the cockpit no longer stayed at 0 during a
longer run:

```text
sample 0: transcript=0, reminders=0
sample 2: transcript=1, reminders=0
sample 10: transcript=6, reminders=1
sample 30: transcript=20, reminders=2
sample 60: transcript=24, reminders=2
sample 90: transcript=25, reminders=2
before_stop: transcript=25, reminders=2
```

Interpretation:

```text
Go for no-cost real browser microphone mainline evidence:
  real mic capture
  local realtime FunASR
  realtime transcript projection
  cockpit growth during recording
  deterministic no-cost suggestions/approach/minutes
  saved/exportable recording with SHA match

No-Go for production release:
  no production LLM call in this run
  first final latency was high
  ASR quality still has mixed Chinese/English technical term misses
```

### 20.3 ASR Normalizer V4 Evidence

The 10-minute run still misheard mixed technical terms such as:

```text
owner -> honor/or/hononor
Kafka lag -> caf collect / have collect / caf fect
error rate -> era r ate / errate / erary
Redis cluster -> closter / ster / dicluster
feature flag -> feature slag in some early text
rollback checklist -> llback checklist / rollbacklist in some early text
```

The next fix stayed bounded and contextual. It does not turn the normalizer into
a global English spelling corrector; it only repairs observed near-misses inside
release review / metric / rollback / reliability contexts.

Focused TDD:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py -q

21 passed, 1 warning
```

Post-normalizer real-mic smoke:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-normalizer-v4-nocost-20260711-093934
session_id=rec_mrfp5kiv
input_mode=real_browser_mic
provider=funasr_realtime
provider_mode=real
is_mock=false
health_status=audio_capture_health_passed
chunk_count=401
derivation_mode=no_cost_deterministic
suggestion_card_count=3
approach_card_count=1
minutes_char_count=342
audio_export_http_status=200
audio_sha256_matches_session=true
first_text_after_audio_active_latency_ms=7194
first_final_after_audio_active_latency_ms=47887
partial_visible_count=66
final_visible_count=3
browser_console_error_count=0
network_error_count=0
```

Term check from the artifact:

```text
payment-gateway=True
error_rate=True
P99=True
feature flag=True
rollback checklist=True
owner=True
SLO看板=True
Kafka lag=True
error rate=False
honor=False
caf collect=False
closter=False
Redis cluster=False
```

Remaining ASR boundary:

```text
Redis cluster still does not recover consistently.
Some early visible partials can still show feature slag / llback checklist before later normalization.
This should not become endless one-off word chasing.
Next ASR-quality work should be a bounded hotword/profile/post-ASR correction strategy, measured against Chinese technical meeting fixtures.
```

### 20.4 Current Go / No-Go After This Section

```text
No-cost real-mic Workbench mainline: Go for continued product iteration.
Left cockpit as business component: Go, but empty-state UX remains a product-improvement item.
Recording save/export: Go in the latest 10-minute no-cost evidence.
Production LLM after latest fixes: No-Go / pending deliberate paid recheck.
ASR production quality: No-Go / needs systematic hotword/profile/correction strategy.
Real user meeting acceptance: No-Go / pending user-run meeting.
```

## 21. Cockpit Phase Badge Browser Evidence

### 21.1 Why This Was Added

Section 20 recorded that the left cockpit is a real business component, but the
empty state could still look like a column of inert zeroes. The next UI step was
to add a compact phase badge beside `会议状态` instead of adding long explanatory
text.

Implemented labels:

```text
待开始
录音中
整理中
已记录
已复盘
```

The badge is derived locally from the current Workbench session state. It does
not call ASR, LLM, or any remote service.

### 21.2 Evidence

TDD:

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_meeting_cockpit_stage_tracks_mainline_phase -q

failed because meetingCockpitStage() was missing

GREEN:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_meeting_cockpit_stage_tracks_mainline_phase \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_left_column_explains_meeting_mainline_status -q

2 passed, 2 warnings
```

Regression and browser smoke:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
96 passed, 2 warnings

python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
7 passed, 1 warning

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
exit 0

node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
exit 0

node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
status=go_workbench_all_buttons_smoke
screenshot_count=17
```

Browser report:

```text
artifact=artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/workbench_visual_acceptance_report.json
cockpitStage:
  initial_page -> 待开始
  import_recording -> 已记录
  history_open/history_reopen -> 已记录
  suggestions_generated/approach_generated -> 已记录
  minutes_generated/exports/organize_completed -> 已复盘
  delete_reset -> 待开始
```

Boundary:

```text
This improves cockpit comprehension in the no-cost browser Workbench lane.
It is not production LLM evidence.
It does not replace the next real-mic long-meeting recheck.
If live-mic evidence needs machine-readable phase tracking, add cockpitStage to
workbench_browser_live_mic_verify.mjs in the same style.
```

## 22. Real-Mic Cockpit Stage Evidence And Summary File

### 22.1 Implementation

The browser all-buttons smoke already recorded `cockpitStage`, but the real
microphone verifier still only recorded cockpit counts. This section closes that
evidence gap.

Changes:

```text
workbench_browser_live_mic_verify.mjs:
  recording_phase_ui_samples.json -> samples[].cockpit_stage
  ui_verification.json -> meeting_cockpit_stage
  summary.json -> machine-readable copy of the stdout summary
```

This is an evidence-only change. It does not alter ASR, LLM, recording,
suggestion cards, approach cards, or minutes behavior.

### 22.2 TDD And Regression

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_records_meeting_cockpit_counts -q

failed because readMeetingCockpitStage() was missing

RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_writes_machine_readable_summary_file -q

failed because summary.json was not written
```

```text
GREEN:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_writes_machine_readable_summary_file \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_records_meeting_cockpit_counts -q

2 passed, 2 warnings
```

```text
Regression:
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
96 passed, 2 warnings

python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
7 passed, 1 warning

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
exit 0

node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
exit 0

node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
exit 0
```

### 22.3 Real-Mic No-Cost Recheck

Artifact:

```text
artifacts/tmp/browser_live_mic/real-mic-cockpit-stage-summary-nocost-20260711-100829
```

Summary:

```text
summary.json exists=true
session_id=rec_mrfq6rmj
input_mode=real_browser_mic
ui_coverage=visible_chrome
chrome_headless=false
chrome_fake_ui_for_media_stream=true
health_status=audio_capture_health_passed
chunk_count=200
asr_final_count=1
derivation_mode=no_cost_deterministic
derivations_generated=true
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_file_size_bytes=1916972
audio_sha256_matches_session=true
recording_phase_ui_samples=13
delete_session_after_run=false
```

Recording-stage cockpit evidence:

```text
recording_started:
  cockpit_stage={label:录音中,state:recording}
  transcript=0
  realtime_reminders=0

late recording sample:
  cockpit_stage={label:录音中,state:recording}
  transcript=6
  realtime_reminders=2
  partial_draft_count=5

before_stop:
  cockpit_stage={label:录音中,state:recording}
  transcript=6
  realtime_reminders=2
```

Final UI evidence:

```text
meeting_cockpit_stage={label:已复盘,state:reviewed}
meeting_cockpit_counts:
  transcript=1
  realtime_reminders=9
  ai_suggestions=3
  approach=1
  audio=已保存
  minutes=已生成
  decisions=2
  actions=1
  risks=6
  questions=0
workbench_same_session_visible=true
frontend_card_count=4
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
first_text_after_audio_active_latency_ms=9006
first_final_after_audio_active_latency_ms=41481
```

Audio export:

```text
audio_export_http_status=200
audio_file_magic=RIFF
audio_sha256_matches_session=true
session_audio_saved=true
session_audio_duration_ms=59904
```

Boundary:

```text
This run proves real browser microphone capture, local realtime ASR, cockpit
stage tracking, no-cost deterministic suggestions/approach/minutes, and saved
recording export for a short speaker-to-mic run.

It is not production LLM evidence because derivation_mode=no_cost_deterministic.
It is not a long-meeting production gate because record_seconds=60.
The next production-directed steps remain:
  1. longer real-mic soak with cockpit_stage tracked,
  2. deliberate production LLM recheck,
  3. ASR hotword/profile/post-ASR correction strategy.
```

## 23. Summary JSON Promotes UI Acceptance Fields

### 23.1 Why This Was Added

After Section 22, `summary.json` existed but still required reviewers to open
`ui_verification.json` to answer basic acceptance questions:

```text
Did the same session remain visible?
What was the first-text latency?
Did the cockpit finish as 已复盘?
Were there browser or network errors?
```

That was still too fragmented for a release or long-soak gate. The summary is
now the first evidence file to inspect.

### 23.2 Implementation

`workbench_browser_live_mic_verify.mjs` now copies these fields from
`ui_verification.json` into top-level `summary.json`:

```text
workbench_same_session_visible
frontend_utterance_count
frontend_card_count
frontend_minutes_visible
meeting_cockpit_stage
meeting_cockpit_counts
first_text_after_audio_active_latency_ms
first_final_after_audio_active_latency_ms
partial_visible_count
final_visible_count
browser_console_error_count
network_error_count
```

This does not change ASR, recording, derivation, or UI behavior. It only makes
the evidence harder to misread.

### 23.3 TDD And Regression

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_summary_promotes_ui_acceptance_fields -q

failed because summary did not include UI acceptance fields

GREEN:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_summary_promotes_ui_acceptance_fields -q

1 passed, 2 warnings
```

```text
Regression:
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
98 passed, 2 warnings

python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
7 passed, 1 warning

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
exit 0

node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
exit 0

node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
exit 0
```

### 23.4 Real-Mic No-Cost Recheck

Artifact:

```text
artifacts/tmp/browser_live_mic/real-mic-summary-ui-fields-nocost-20260711-101557
```

Top-level summary now contains:

```text
session_id=rec_mrfqgcol
input_mode=real_browser_mic
ui_coverage=visible_chrome
health_status=audio_capture_health_passed
chunk_count=200
asr_final_count=1
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_sha256_matches_session=true
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=4
frontend_minutes_visible=true
meeting_cockpit_stage={label:已复盘,state:reviewed}
meeting_cockpit_counts:
  transcript=1
  realtime_reminders=9
  ai_suggestions=3
  approach=1
  audio=已保存
  minutes=已生成
first_text_after_audio_active_latency_ms=8473
first_final_after_audio_active_latency_ms=59310
partial_visible_count=31
final_visible_count=1
browser_console_error_count=0
network_error_count=0
recording_phase_ui_samples=13
```

Recording-phase sample boundary:

```text
first sample cockpit_stage={label:录音中,state:recording}
last sample cockpit_stage={label:录音中,state:recording}
last sample cockpit_counts:
  transcript=5
  realtime_reminders=2
  audio=未保存
  minutes=未生成
```

Audio:

```text
audio_export_http_status=200
audio_file_magic=RIFF
audio_sha256_matches_session=true
session_audio_saved=true
session_audio_duration_ms=59904
```

Boundary:

```text
This run is still no-cost deterministic and short.
It improves release-readiness evidence shape but does not prove production LLM
quality, long-meeting stability, or ASR production quality.
```

## 24. Live Candidate Reminder WebSocket Fix

### 24.1 Why This Was Added

The 10-minute no-cost real browser microphone run exposed a product-level drift:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-summary-ui-fields-10min-nocost-20260711-102156
recording-phase cockpit realtime_reminders mostly stayed at 3
final meeting_cockpit_counts.realtime_reminders=47
session_events:
  suggestion_candidate_event=46
  partial_hint_event=1
```

This was not a visual cap or E2E sampling issue. During recording, transcript
and partial drafts were growing continuously, but the Workbench only received
raw ASR `partial/final` events and a few `partial_hint_event` messages over the
WebSocket. The backend session snapshot already contained many
`suggestion_candidate_event` records, but those were only visible after stop,
when Workbench fetched `/live/asr/sessions/{sid}/events`.

Product interpretation:

```text
This is a core real-time Copilot gap.
If reminders appear only after stop, the product becomes transcription first
and meeting advice second. The live WebSocket stream must carry derived
candidate reminders during recording.
```

### 24.2 Implementation

Decision:

```text
DEC-320: Live ASR WebSocket Streams Derived Candidate Reminders During Recording
```

Code change:

```text
code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py
```

Behavior:

- `_upsert_live_session()` still persists the full derived session snapshot.
- It now also returns the derived `live_events` for the current stream state.
- `handle_stream()` maintains `sent_live_candidate_event_ids`.
- After each upsert, it sends only new `suggestion_candidate_event` records over
  the same WebSocket.
- It intentionally does not stream internal `state_event`, `scheduler_event`,
  or `llm_request_draft_event` records to the UI.
- Frontend polling was not added; Workbench already consumes
  `suggestion_candidate_event` in `appendLiveEvent()`.

### 24.3 Verification

TDD and regression:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_asr_stream.py -q
23 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
98 passed, 2 warnings

python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
7 passed, 1 warning

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
all exit 0

node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
status=go_workbench_all_buttons_smoke
screenshot_count=17
```

New ASR stream tests:

```text
test_asr_stream_sends_stable_partial_candidate_over_same_websocket
test_asr_stream_sends_live_final_candidate_over_same_websocket
```

These lock two paths:

- Stable partials can create user-visible candidate reminders before final.
- If a recognizer emits final during recording, final-derived candidates are also
  streamed over the same WebSocket.

### 24.4 Real-Mic No-Cost Rechecks

#### Recheck A

Artifact:

```text
artifacts/tmp/browser_live_mic/real-mic-live-candidates-ws-nocost-20260711-104608
```

Summary:

```text
session_id=rec_mrfrj6hz
input_mode=real_browser_mic
ui_coverage=visible_chrome
health_status=audio_capture_health_passed
chunk_count=300
asr_final_count=1
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=439
audio_export_http_status=200
audio_sha256_matches_session=true
workbench_same_session_visible=true
frontend_card_count=4
frontend_minutes_visible=true
meeting_cockpit_stage={label:已复盘,state:reviewed}
meeting_cockpit_counts.realtime_reminders=13
browser_console_error_count=0
network_error_count=0
```

Recording-phase cockpit evidence:

```text
realtime_reminders grew during recording:
0 -> 4 -> 5 -> 6

before_stop:
  transcript=13
  realtime_reminders=6
  decisions=1
  actions=1
  risks=4
```

Final session events:

```text
events=57
suggestion_candidate_event=12
partial_hint_event=1
target_type:
  DecisionCandidate=2
  ActionItem=3
  Risk=7
```

#### Recheck B

Artifact:

```text
artifacts/tmp/browser_live_mic/real-mic-live-candidates-ws-short-sentences-nocost-20260711-104824
```

Summary:

```text
session_id=rec_mrfrm2yp
input_mode=real_browser_mic
ui_coverage=visible_chrome
health_status=audio_capture_health_passed
chunk_count=501
asr_final_count=1
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=386
audio_export_http_status=200
audio_sha256_matches_session=true
workbench_same_session_visible=true
frontend_card_count=4
frontend_minutes_visible=true
meeting_cockpit_counts.realtime_reminders=15
browser_console_error_count=0
network_error_count=0
```

Recording-phase cockpit evidence:

```text
realtime_reminders distribution:
  0: 2 samples
  1: 1 sample
  2: 5 samples
  3: 5 samples
  4: 18 samples

transcript grew during recording from 0 to 20.
```

Final session events:

```text
events=67
suggestion_candidate_event=14
partial_hint_event=1
target_type:
  DecisionCandidate=5
  Risk=9
```

### 24.5 Remaining Boundary

```text
The WebSocket candidate-reminder gap is fixed for stable partial candidates and
unit-tested for live final candidates.

The two real-mic rechecks still do not count as production LLM evidence because
they used no-cost deterministic derivation.

The current local real-time ASR profile still tends to emit a final near stop in
these speaker-to-mic runs. That means final/revision candidate visibility is
locked by backend unit tests, but still needs a longer real meeting or provider
profile run to prove repeated mid-meeting finals in realistic conditions.

Do not reopen open-ended provider bakeoffs for this fix. The next useful gates
are bounded: production-enabled LLM recheck, longer real meeting acceptance, and
ASR quality hardening through hotwords/profile/post-ASR correction.
```

### 24.6 Drift Gate Added To The Real-Mic Verifier

Why:

```text
DEC-320 fixed the stream, but the verifier still needed to fail automatically
if a future regression recreated the old symptom. Final cockpit counts alone are
not enough because the post-stop session snapshot can hide a recording-phase
UI drift.
```

Implementation:

```text
code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
```

The verifier now records backend reminder counts for each recording-phase
sample:

```text
backend_probe_status
backend_event_count
backend_suggestion_candidate_count
backend_partial_hint_count
backend_live_reminder_count
```

It also writes a summary-level gate:

```text
live_reminder_drift_status
live_reminder_drift_report
```

Failure condition:

```text
If the backend has recording-phase candidate reminders but the frontend cockpit
lags by more than 2 reminders, the verifier reports
failed_backend_candidates_not_visible and exits non-zero.
```

Verification:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_browser_live_mic_verify_detects_live_reminder_drift_during_recording -q
1 passed, 2 warnings

node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
exit 0

PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
99 passed, 2 warnings
```

Real-mic no-cost gate recheck:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-live-reminder-drift-gate-nocost-20260711-110137
session_id=rec_mrfs33l7
input_mode=real_browser_mic
ui_coverage=visible_chrome
health_status=audio_capture_health_passed
chunk_count=200
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
audio_sha256_matches_session=true
browser_console_error_count=0
network_error_count=0
live_reminder_drift_status=passed
recording_backend_probe_sample_count=11
max_recording_backend_live_reminders=3
max_recording_frontend_realtime_reminders=3
tolerated_delta=2
final_session_live_reminder_count=7
```

Boundary:

```text
This gate proves that the verifier will catch the old live-reminder drift class.
It still does not prove production LLM quality or long-meeting production ASR
quality.
```

## 25. Meeting Cockpit Empty-State And Live Transcript UX

User concern:

```text
The left column still looks like it has no practical business purpose, and the
realtime transcript can feel like text is being replaced instead of appended.
```

Root cause:

```text
The data path was already real, but the product projection was not clear enough:

- the left column opened as a stack of 0 / 未保存 / 未生成 values,
- zero-count focus filters looked clickable,
- realtime partials used the vague 临时稿 / 实时 labels,
- the transcript header did not explain whether the user was seeing stable
  context or the current ASR tail.
```

Implementation:

```text
code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html
code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
```

Product boundary:

```text
The left column remains the Meeting Cockpit. Visible section labels are now:

- 本场会议: same-session mainline status projection
- 重点筛选: realtime reminder triage controls

0-count filters are disabled until their reminder type exists. Stable partials
append as 已记录. The mutable ASR tail displays as 正在听. The transcript title
shows 待开始 / 已记录 + 正在听 / 整理中 / 已记录.
```

Verification:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_left_cockpit_disables_empty_focus_filters_and_names_business_role \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_realtime_transcript_labels_stable_append_and_live_tail -q
2 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
102 passed, 2 warnings

python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
7 passed, 1 warning

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
exit 0

node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
exit 0

Browser all-buttons evidence after disabled-button visual fix:

```text
artifact=artifacts/tmp/ui_screenshots/workbench-all-buttons-cockpit-ux-disabled-20260711-112328
status=go_workbench_all_buttons_smoke
focus_filter_coverage:
  DecisionCandidate=clicked_filter
  ActionItem=disabled_zero_count_filter
  Risk=clicked_filter
  OpenQuestion=clicked_filter
  all=clicked_clear_filter
downloads=transcript.txt / minutes.md / audio.wav
```

Served-page freshness recheck after the user reported the left column still
looked ineffective:

```text
before_restart_served_script=/static/workbench.js?v=20260711-focus-filter2
before_restart_visible_labels=会议状态 / 会议重点
action=python3 tools/workbench_server.py stop --port 8765 && python3 tools/workbench_server.py start --port 8765
after_restart_served_script=/static/workbench.js?v=20260711-cockpit-ux1
after_restart_visible_labels=本场会议 / 重点筛选
after_restart_left_aria=会议驾驶舱：查看本场会议主流程状态并筛选实时提醒
after_restart_transcript_mode=待开始
screenshot=artifacts/tmp/ui_screenshots/current-workbench-cockpit-reload-20260711/01-current-workbench-after-server-restart.png
```
```

Boundary:

```text
This is a product UX and acceptance-gate improvement. It does not change ASR
provider quality, does not add paid ASR, and does not count as production LLM
evidence. A fresh browser screenshot/E2E artifact should be attached after the
next all-buttons or real-mic run.
```

## 27. Meeting Cockpit Overview Rows Are Now Clickable Navigation

User concern:

```text
The left column still looked like a column with no real function.
```

Product review:

```text
The left column is real session state, not mock data. However, before this
change the top `本场会议` rows were static status metrics. They explained how
many transcript/reminder/suggestion/minutes artifacts existed, but they did not
help the user get to those artifacts. That made the cockpit feel decorative.
```

Decision:

```text
Keep the left column as the Meeting Cockpit, but make the six `本场会议` rows
actionable overview jumps:

- 文字记录 -> realtime transcript
- 实时提醒 -> realtime reminder panel
- AI 建议 -> suggestion card panel
- 方案分析 -> approach analysis panel
- 录音保存 -> audio export action when audio exists
- 会后复盘 -> minutes panel

If the target has no content yet, the click still gives an explicit toast/title
explaining why there is no content instead of acting like a dead metric.
```

Implementation:

```text
code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html
code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
code/web_mvp/backend/tests/test_workbench.py
code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
tests/test_workbench_all_buttons_smoke.py
```

Verification:

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_meeting_overview_rows_are_actionable_navigation -q
failed because data-overview-target="transcript" was missing

GREEN:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_meeting_overview_rows_are_actionable_navigation -q
1 passed, 2 warnings

Focused regression:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_route_serves_html \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_left_cockpit_disables_empty_focus_filters_and_names_business_role \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_meeting_overview_rows_are_actionable_navigation -q
3 passed, 2 warnings

python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
8 passed, 1 warning

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
exit 0
```

Browser evidence:

```text
artifact=artifacts/tmp/ui_screenshots/workbench-all-buttons-overview-jump-focus-20260711-122831
status=go_workbench_all_buttons_smoke
imported_session_id=file_b9aa838fa23a
fake_llm_request_count=10
screenshot_count=23

overview_jump_coverage:
  transcript=clicked_navigation
  reminders=clicked_navigation
  suggestions=clicked_navigation
  approach=clicked_navigation
  audio=clicked_navigation
  minutes=clicked_navigation

overview_jump_focus_state:
  transcript/reminders/suggestions/approach/audio/minutes all active_element_matches=true
  transcript/reminders/suggestions/approach/audio/minutes all target_in_viewport=true
  transcript/reminders/suggestions/approach/audio/minutes all toast_after_click_matches=true

screenshots:
  11-overview_jump_transcript.png
  12-overview_jump_reminders.png
  13-overview_jump_suggestions.png
  14-overview_jump_approach.png
  15-overview_jump_audio.png
  16-overview_jump_minutes.png
```

Boundary:

```text
This is a P0 usability and navigation closure. It does not make the product
production-ready by itself. It does not improve Chinese ASR semantic quality,
does not reduce final latency, does not add paid ASR, and does not count as
production LLM evidence because the browser smoke uses a local fake
OpenAI-compatible gateway.

P1 remains: turn selected reminders into concrete action handling, such as
copying a follow-up question, marking handled/dismissed/wrong, and jumping to
the exact source quote with accessibility coverage.
```

## 28. Post-Overview-Jump Real Mic Recheck And Normalizer v5

After the left meeting overview rows were made actionable, I ran a fresh
real-browser-microphone Workbench chain with local macOS `say -v Tingting`
playback. This rechecks that the latest UI/navigation changes did not break
the real microphone path.

Evidence:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-post-overview-jump-3min-nocost-20260711-123349
session_id=rec_mrfvdof3
input_mode=real_browser_mic
ui_coverage=headless_chrome
health_status=audio_capture_health_passed
chunk_count=600
asr_final_count=1
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=354
audio_export_http_status=200
audio_file_size_bytes=5759020
audio_sha256_matches_session=true
workbench_same_session_visible=true
frontend_utterance_count=12
frontend_card_count=4
frontend_minutes_visible=true
meeting_cockpit_stage=已复盘/reviewed
meeting_cockpit_counts:
  transcript=12
  realtime_reminders=21
  ai_suggestions=3
  approach=1
  audio=已保存
  minutes=已生成
first_text_after_audio_active_latency_ms=7195
first_final_after_audio_active_latency_ms=180336
partial_visible_count=17
final_visible_count=1
browser_console_error_count=0
network_error_count=0
recording_phase_ui_samples=37
live_reminder_drift_status=passed
```

Recording-phase evidence:

```text
before_stop:
  cockpit_stage=录音中/recording
  cockpit_counts.transcript=41
  cockpit_counts.realtime_reminders=5
  partial_draft_count=41
  live_partial_exists=true
```

Interpretation:

- The Workbench does show realtime text while the meeting is running. It is not waiting until the meeting ends to show the transcript.
- The same session reaches suggestion cards, approach cards, minutes, saved audio, and exportable audio.
- This is still no-cost deterministic derivation, so it does not count as production LLM evidence.
- `first_final_after_audio_active_latency_ms=180336` means final ASR still arrives near stop for this provider/profile. Realtime UX currently depends on partial/revision projection, not fast final segments.

The same run exposed new bounded Chinese technical near-misses:

```text
flow看板 -> SLO看板
ure fly / feature fly / featureflag -> feature flag
roll backlist / ll back checklist -> rollback checklist
ononor -> owner
```

TDD evidence:

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py::test_normalize_recovers_post_overview_jump_real_mic_release_review_variants -q

GREEN:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py::test_normalize_recovers_post_overview_jump_real_mic_release_review_variants -q
1 passed, 1 warning

Regression:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py -q
22 passed, 1 warning
```

Boundary:

```text
This recheck is useful post-change evidence, but not production readiness.
Remaining blockers after this section: production LLM recheck, 10/20-minute real
mic soak after normalizer v5, ASR final latency, and a real user meeting
acceptance pass.
```

## 29. 10-Minute Real Mic No-Cost Soak And Normalizer v6

After normalizer v5, I ran a bounded 10-minute real-browser-microphone soak
using the same Workbench route, Chrome `getUserMedia`, and local macOS
`say -v Tingting` playback. This is the current strongest no-cost evidence for
the long-meeting UI/business chain.

Evidence:

```text
artifact=artifacts/tmp/browser_live_mic/real-mic-normalizer-v5-10min-nocost-20260711-135007
session_id=rec_mrfy3swm
input_mode=real_browser_mic
ui_coverage=headless_chrome
health_status=audio_capture_health_passed
chunk_count=2000
asr_final_count=1
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=402
audio_export_http_status=200
audio_file_size_bytes=19193900
audio_sha256_matches_session=true
workbench_same_session_visible=true
frontend_utterance_count=37
frontend_card_count=4
frontend_minutes_visible=true
meeting_cockpit_stage=已复盘/reviewed
meeting_cockpit_counts:
  transcript=37
  realtime_reminders=46
  ai_suggestions=3
  approach=1
  audio=已保存
  minutes=已生成
first_text_after_audio_active_latency_ms=6694
first_final_after_audio_active_latency_ms=288627
partial_visible_count=252
final_visible_count=3
browser_console_error_count=0
network_error_count=0
recording_phase_ui_samples=121
live_reminder_drift_status=passed
session_audio_duration_ms=599808
exported_audio_artifact=artifacts/tmp/browser_live_mic/real-mic-normalizer-v5-10min-nocost-20260711-135007/exported-audio.wav
```

Recording-phase evidence:

```text
mid-run:
  partial_draft_count=41
  live_partial_exists=true

before_stop:
  cockpit_stage=录音中/recording
  cockpit_counts.transcript=43
  cockpit_counts.realtime_reminders=11
  partial_draft_count=41
  live_partial_exists=true
  backend_reminders=7
```

Interpretation:

- The long meeting route is not just a post-meeting transcript generator. Text
  and reminder counts are visible while the meeting is still recording.
- The same session reaches suggestion cards, approach cards, minutes, saved
  audio, and exportable audio.
- The audio file is preserved locally and its SHA matches the session export.
- This is still a no-cost deterministic derivation run, so it is not production
  LLM evidence.
- ASR final latency is still a release blocker:
  `first_final_after_audio_active_latency_ms=288627`.

The 10-minute artifact exposed remaining bounded release-review near-misses:

```text
picture er
row backst
raw/rall/ll back checklist
dicloster / closter
havect / haveg如超
orourer
featurelag
honor要在 / 那or要在
ror 超过
low看板
```

Implemented normalizer v6 bounded fixes:

```text
picture er / featurelag -> feature flag
row/raw/rall/ll back checklist -> rollback checklist
dicloster/closter/ster + 缓存穿透 -> Redis cluster
havect/haveg + 通知值班群上下文 -> Kafka lag
orourer/order + 消费堆积 -> order-worker
下一步 honor/那or -> owner
ror 超过百分之零点一 -> error_rate超过百分之零点一
负责 low看板 -> SLO看板
```

TDD evidence:

```text
RED:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py::test_normalize_recovers_ten_minute_v5_real_mic_remaining_variants -q

GREEN:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py::test_normalize_recovers_ten_minute_v5_real_mic_remaining_variants -q
1 passed, 1 warning

Regression:
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_transcript_normalizer.py -q
23 passed, 1 warning
```

Artifact spot-check after v6:

```text
near misses removed:
  picture er=false
  row backst=false
  raw back checklist=false
  rall back checklist=false
  dicloster=false
  havect=false
  haveg如超=false
  orourer=false
  featurelag=false
  ll back checklist=false
  honor要在=false
  那or要在=false
  ror 超过=false
  low看板=false

target terms present:
  SLO看板=true
  feature flag=true
  rollback checklist=true
  Redis cluster=true
  Kafka lag=true
  order-worker=true
  owner=true
```

Regression checks after v6:

```text
PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_workbench.py -q
105 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend python3 -m pytest code/web_mvp/backend/tests/test_asr_stream.py -q
23 passed, 2 warnings

python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q
8 passed, 1 warning

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
git diff --check
all exit 0
```

Boundary:

```text
This closes the 10-minute no-cost real-mic UI/business-chain recheck after the
latest left-cockpit and normalizer changes. It does not close production
readiness.

Remaining blockers:
  production LLM recheck after latest ASR/UI changes
  20-minute real microphone gate
  ASR final latency
  ASR semantic quality in natural noisy multi-speaker Chinese meetings
  real user meeting acceptance
```

## 30. Realtime Text Experience Gate And Short Real-Mic Recheck

The browser-live-mic verifier now classifies the user-visible realtime text
experience independently from final ASR latency. This is a product-level gate:
having a transcript only after stop is a failure, while visible partial/revision
text within the SLO can keep the realtime UX usable even when finalization is
still slow.

Default classification:

```text
text latency SLO: 15000 ms after effective audio becomes active
final latency observation line: 60000 ms

failed_realtime_text_not_visible:
  no partial/final became visible during measurement
failed_realtime_text_slow:
  first visible text exceeded the text latency SLO
passed_realtime_partial_final_missing:
  realtime text passed, but no final was visible during measurement
passed_realtime_partial_final_slow:
  realtime text passed, but final exceeded the observation line
passed_realtime_full:
  realtime text and final both met their respective lines
```

Fresh real-microphone evidence:

```text
artifact=artifacts/tmp/browser_live_mic/realtime-experience-gate-real-mic-20260711
session_id=rec_mrg0po1o
input_mode=real_browser_mic
health_status=audio_capture_health_passed
chunk_count=150
asr_final_count=1
derivation_mode=no_cost_deterministic
counts_as_production_llm_evidence=false
suggestion_card_count=3
approach_card_count=1
minutes_char_count=252
audio_export_http_status=200
audio_file_size_bytes=1433644
audio_sha256_matches_session=true
frontend_utterance_count=4
frontend_card_count=4
frontend_minutes_visible=true
first_text_after_audio_active_latency_ms=8247
first_final_after_audio_active_latency_ms=45059
partial_visible_count=2
final_visible_count=1
realtime_experience_status=passed_realtime_full
live_reminder_drift_status=passed
browser_console_error_count=0
network_error_count=0
```

The same run reached the reviewed stage with transcript, realtime reminders,
three suggestions, one approach, saved audio, and generated minutes in the same
session. The screenshot is stored beside the machine-readable evidence as
`workbench-browser-live-mic.png`.

A separate fake-audio-file diagnostic run correctly failed with
`blocked_audio_too_quiet` and `failed_realtime_text_not_visible`. That result is
useful verifier evidence, not a real-microphone product failure.

Boundary:

```text
The new gate closes ambiguity in reporting realtime text UX.
It does not close production readiness.

Still open:
  production LLM recheck after latest ASR/UI/verifier changes
  20-minute real microphone gate
  final latency optimization, especially the 10-minute 288627 ms result
  natural noisy multi-speaker Chinese meeting quality
  real user meeting acceptance
```

## 31. Fail-Closed Full-Mainline Gate

Independent review found that the initial realtime gate could still pass text
that appeared only after stop, could ignore unhealthy audio, and did not fail a
run where the realtime transcript worked but suggestions, approach, and minutes
were all missing.

The verifier now imports a pure gate module with executable Node tests. A run
is accepted only when both layers pass:

```text
realtime_experience_status:
  healthy audio
  recording-phase samples exist
  text is visible before stop
  first text meets the configured SLO

mainline_completion_status:
  ASR acceptance eligible
  suggestions >= 1
  approaches >= 1
  minutes generated
  cards retain evidence
  same session is visible and reviewed
  recording export returns 200 and matches session SHA
  no browser console/network errors
  production mode additionally has remote non-mock LLM usage evidence
```

Behavior tests:

```text
node --test code/web_mvp/e2e/workbench_browser_live_mic_gate.test.mjs
12 passed
```

Strict offline reclassification preserved the earlier successful evidence:

```text
45-second real mic:
  realtime=passed_realtime_full
  mainline=passed_no_cost_mainline

10-minute real mic:
  realtime=passed_realtime_partial_final_slow
  mainline=passed_no_cost_mainline
```

Two fresh diagnostics correctly failed closed:

```text
real mic with strong environmental speech interference:
  artifact=realtime-experience-gate-v2-real-mic-success-20260711
  realtime=passed_realtime_partial_final_slow
  ASR semantic quality=blocked
  mainline=failed_mainline_completion
  exit=1

Chrome fake-audio-file route:
  artifact=realtime-mainline-gate-v2-fake-audio-20260711
  health=blocked_audio_too_quiet
  realtime=failed_audio_capture_health
  reminder probe=missing
  mainline=failed_mainline_completion
  exit=1
```

The real-mic No-Go captured environmental discussion about crypto and US stock
trading instead of the intended technical-meeting TTS. Refusing to generate AI
advice in that run is correct product behavior. A clean real-microphone rerun is
still required when the acoustic environment can isolate the target meeting.

## 32. Realtime Auto-Suggestion Reliability

The formal suggestion path was reviewed separately from deterministic realtime
reminders. The production rule remains: a formal card needs a persisted,
acceptance-eligible final transcript event.

Implemented reliability changes:

```text
one /auto-suggestions/run-once request -> at most one remote candidate execution
partial_hint_event -> local reminder only, no premature formal LLM call
END/finalize final -> persist session before sending final to Workbench
trigger while request is in flight -> coalesce into one pending retry
Workbench JS cache key -> 20260711-auto-suggestion-reliability1
```

This prevents a long historical session from causing a burst of many remote LLM
calls in one synchronous request, avoids partial-only requests that are guaranteed
to fail the final acceptance gate, and removes the stop-recording race where the
browser could receive final before the backend record existed.

The remaining product limitation is explicit: continuous speech with no provider
final and no 900 ms VAD silence endpoint can still delay formal cards. Fixing that
requires either stronger endpointing or provisional cards with confirm/retract
semantics; low-quality partial text is not silently promoted to a formal card.

Fresh verification after these changes:

```text
Python related suites: 172 passed, 2 warnings
Node verifier gate tests: 12 passed
Workbench all-buttons browser E2E: go_workbench_all_buttons_smoke
Browser screenshots: 23
Node syntax checks: passed
git diff --check: passed
```

Runtime note: the current configured 8765 process serves the updated static JS
file, but its `/workbench` HTML was loaded at process start and still references
the previous cache key. A controlled restart that preserves the production LLM
environment is required before the latest backend Python and HTML cache key are
considered active on that long-running process.

## 33. Production Realtime AI Suggestion Gate

The configured 8765 service was restarted in controlled `inherit` mode while
preserving the already supplied production provider environment. Health evidence
after restart reported a non-mock OpenAI-compatible provider with model
`gpt-5.5`; no secret value was copied into artifacts or documentation.

The verifier now treats recording-time formal AI advice as a separate product
gate. A production run only passes this gate when at least one recording-phase UI
sample reports `cockpit_counts.ai_suggestions > 0`.

```text
summary.json:
  realtime_ai_suggestion_status
  realtime_ai_suggestion_report
  max_recording_ai_suggestions
  first_ai_suggestion_visible_latency_ms

no_cost_deterministic:
  status=not_required_no_cost

production_enabled with no visible card during recording:
  status=failed_realtime_ai_suggestion_not_visible_during_recording
  verifier exit=1
```

Strict offline reclassification of the three latest production real-microphone
runs produced the same No-Go result:

```text
production-existing-server-real-mic-20260711:
  recording samples=18
  max recording AI suggestions=0

production-general-tech-policy-v2-real-mic-20260711:
  recording samples=19
  max recording AI suggestions=0

production-general-tech-single-group-v2-real-mic-20260711:
  recording samples=25
  max recording AI suggestions=0
```

These runs still prove real microphone capture, recording-time text visibility,
and saved audio for their respective samples. They do not prove the product's
core production value because no formal AI card was visible before recording
stopped.

The derivation backend was validated separately without another microphone run:

```text
artifact=artifacts/tmp/production_file_chain/general-tech-policy-v2-20260711
input=66-second generated Chinese technical meeting WAV
ASR=local offline FunASR
semantic_quality=passed
real suggestion cards=1
real approach cards=3
minutes chars=909
all cards have evidence=true
captured suggestion/approach/minutes tokens=15188
session and audio deleted=true
```

This separation localizes the remaining failure: the remote LLM derivation layer
works, but a real microphone meeting has not yet produced and displayed a formal
card during recording. The next run must use a controlled Chinese technical
speaker-to-microphone source and pass this gate. If stable final events remain too
late, the next design review is either stronger endpointing or explicitly
provisional cards with confirm/update/retract semantics. Arbitrary partial text
will not be promoted directly into permanent formal advice.

Related changes also broadened the semantic gate to
`general_chinese_technical_meeting.v2`, covering ordinary SDK/toolkit/tooling,
architecture, development workflow, client/product, permission and bug
discussion while still blocking isolated technical words and non-technical
conversation. OpenQuestion state extraction and candidate emission now use
aligned engineering context tables.

Chrome fake-file microphone capture is closed as an environment No-Go for this
machine: both visible and headless diagnostics saw zero RMS/peak despite a healthy
source WAV, and no virtual input device is installed. It is no longer a mainline
acceptance step.

## 35. Canonical Transcript Recovery Verification - 2026-07-12

This iteration did not create a new microphone recording. It verified the new transcript architecture against the persisted affected real-microphone session and against a deterministic browser full-chain run.

Affected real session:

```text
session_id=rec_mrh7w0eb
provider=funasr_realtime
event_count=91
canonical_segment_count=53
canonical_active_tail_count=1
canonical_committed_char_count=2065
canonical_full_char_count=2068
visible_duplicate_发言_label_count=0
audio_saved=false
```

The Workbench now restores this session as a complete continuous document instead of an empty idle page. Because the real microphone stream has no normal `end_of_stream`, the UI reports:

```text
已恢复最近会议。录音连接已中断，已保留截至断开时的文字；本场历史会话未保存录音。
```

The recovery wording is conditional on `audio.saved`; this historical session has `audio_saved=false`, so the UI explicitly states that no recording was saved.

Deterministic browser acceptance:

```text
status=go_workbench_all_buttons_smoke
uploaded local audio -> local FunASR -> canonical transcript -> fake OpenAI-compatible gateway suggestions -> approach -> minutes -> exports -> delete
scroll-up preserved=true
new-content button resumes follow=true
reload restored same session=true
reload canonical full text identical=true
active tail count <= 1
revision segment count unchanged=true
runtime exceptions=0
console errors=0
HTTP 5xx=0
screenshots=25
```

Evidence:

- `artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/workbench_all_buttons_report.json`
- `artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/05-reload_recovery.png`
- `artifacts/tmp/ui_screenshots/canonical-transcript-live-8767.png`

Conclusion:

- Canonical transcript display, in-place correction, scroll-follow control and latest-session recovery are implemented and browser-verified.
- This closes the repeated/incomplete/empty transcript UI defect.
- It does not change the existing No-Go for production Chinese ASR accuracy or recording-time formal AI suggestion visibility.
- A fresh controlled real-microphone run remains required before a production release claim.
