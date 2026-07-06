# ASR Mainchain Fullflow Self-Test Report

> Date: 2026-07-04  
> Decision: DEC-201  
> Scope: Run the real program chain on existing synthetic Chinese technical meeting audio, without microphone, user recordings, remote ASR, remote LLM, model download, or paid providers.

## User Request

The user asked to stop deep boundary testing and run the real main flow first. In this report, "real main flow" means the implemented local execution chain:

```text
synthetic Chinese technical meeting audio
  -> local FunASR streaming provider/events
  -> transcript report + technical-term normalization
  -> single-scenario smoke evidence
  -> DRV-046 batch evidence assembler
  -> DRV-032 ASR quality decision gate
  -> product-side ASR event replay / shadow pipeline
```

This does not mean microphone capture yet. The microphone path remains blocked until ASR quality exit passes or a formal degraded-pilot acceptance record exists.

## What Ran

Hotword runtime artifacts from the local FunASR batch runner were postprocessed through the same evidence chain:

- `chunk10_hotword`: `chunk_size=[0,10,5]`
- `chunk20_hotword`: `chunk_size=[0,20,10]`

Generated or refreshed artifacts:

- `artifacts/tmp/asr_reports/*.funasr.batch-chunk10_hotword.transcript-report.json`
- `artifacts/tmp/asr_reports/*.funasr.batch-chunk10_hotword.smoke-report.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly-chunk10_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk10_hotword.json`
- `artifacts/tmp/asr_reports/*.funasr.batch-chunk20_hotword.transcript-report.json`
- `artifacts/tmp/asr_reports/*.funasr.batch-chunk20_hotword.smoke-report.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly-chunk20_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.shadow-pipeline-chunk10_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.shadow-pipeline-chunk20_hotword.json`
- `artifacts/tmp/asr_reports/simulated-shadow-pipeline.mock-control.latest.json`

## ASR Quality Result

| Candidate | Scenario | RTF | Raw recall | Normalized recall |
| --- | --- | ---: | ---: | ---: |
| `chunk10_hotword` | `api-review-001` | `0.670562` | `0.00` | `1.00` |
| `chunk10_hotword` | `architecture-review-001` | `0.669519` | `0.20` | `0.80` |
| `chunk10_hotword` | `incident-review-001` | `0.667928` | `0.25` | `0.25` |
| `chunk10_hotword` | `release-review-001` | `0.672214` | `0.00` | `0.50` |
| `chunk10_hotword` | `non-engineering-control-001` | `0.693573` | `0.00` | `0.00` |
| `chunk20_hotword` | `api-review-001` | `0.354784` | `0.00` | `0.50` |
| `chunk20_hotword` | `architecture-review-001` | `0.356219` | `0.20` | `0.60` |
| `chunk20_hotword` | `incident-review-001` | `0.357595` | `0.25` | `0.25` |
| `chunk20_hotword` | `release-review-001` | `0.359742` | `0.00` | `0.50` |
| `chunk20_hotword` | `non-engineering-control-001` | `0.362909` | `0.00` | `0.00` |

Gate result:

```text
chunk10_hotword:
assembly_status=drv044_batch_evidence_blocked
quality_exit_status=not_exited
blocked_by=rtf > 0.6 and normalized_recall < 0.8

chunk20_hotword:
assembly_status=drv044_batch_evidence_blocked
quality_exit_status=not_exited
blocked_by=normalized_recall < 0.8
```

Interpretation:

- `chunk10_hotword` is closer on content for some scenarios, but it misses both speed and recall gates.
- `chunk20_hotword` meets the RTF target, but technical entity recall regresses and remains below the product threshold.
- The controlled hotword manifest did not materially improve FunASR recognition for the tested Chinese technical meeting set.

## Product Replay Result

Using real FunASR event files, the product-side replay/shadow pipeline behaved as follows:

| Candidate | Engineering previews | Negative control | Failed scenario |
| --- | ---: | ---: | --- |
| `chunk10_hotword` | `3/4` | `1/1 blocked, 0 fake candidates` | `incident-review-001` |
| `chunk20_hotword` | `3/4` | `1/1 blocked, 0 fake candidates` | `incident-review-001` |
| mock control | `4/4` | `1/1 blocked, 0 fake candidates` | none |

Interpretation:

- The product replay/card pipeline can consume ASR events and produce useful preview candidates when enough technical context survives.
- The `incident-review-001` failure is caused by ASR text losing enough incident context that no candidate timeline is detected.
- The mock control passing confirms the current blocker is not the EvidenceSpan/state/card pipeline itself.

## Decision

DEC-201 is accepted:

- Keep FunASR hotword manifest support in code because it is a clean, auditable extension point.
- Do not promote either hotword candidate to ASR quality Go.
- Do not start microphone capture from this result.
- Do not keep expanding normalizer rules to hallucinate entities that ASR never produced.
- Do not add paid remote ASR by default.

## Next Mainline

The bounded next move is no longer more edge testing. The project should now choose one of two routes:

1. Build the PC desktop main flow around synthetic/live-event replay while clearly showing ASR quality blocked; this advances UX and integration without claiming real meeting readiness.
2. If the user wants to test real microphone timing despite ASR risk, create an explicit degraded-pilot acceptance artifact first, then run one user-started shadow test that is labeled as timing/feedback evidence only, not ASR quality Go evidence.

Default recommendation: proceed with route 1 for product development, and reserve microphone capture for a clearly labeled degraded pilot or a future ASR-provider improvement.

## Safety And Cost

This run did not:

- access or enumerate microphone devices
- request macOS microphone permission
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

## Addendum: Real Microphone Mainflow Self-Test

> Date: 2026-07-04  
> Decision: DEC-204  
> Scope: User-approved real microphone self-test on the local Mac. The test used local microphone capture, local ASR providers, and the Web Live ASR handoff path. It did not call remote ASR or LLM and did not upload audio.

### What Changed Before The Run

The first real microphone attempt exposed a runner defect: `ffmpeg avfoundation` could hang while opening the microphone input, leaving the runner waiting indefinitely. A regression test was added and the runner now returns `blocked_by_microphone_capture_timeout` after `record_seconds + 10`.

The second defect appeared after Sherpa produced a final event: local file-replayed ASR events used inference elapsed time for `received_at_ms`, for example `received_at_ms=498` while the audio segment ended at `end_ms=17898`. The Web Live ASR contract correctly requires `received_at_ms >= end_ms`. A TDD regression test was added, and `tools/real_mic_full_chain_runner.py` now preserves the raw ASR event file while also writing a Web handoff event file with `received_at_ms` clamped to at least `end_ms`.

Verification:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_full_chain_runner.py -q -p no:cacheprovider
Result: 4 passed, 1 warning

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_report_schema.py -q -p no:cacheprovider
Result: 14 passed, 1 warning
```

### Real Microphone Evidence

Three real microphone runs were executed after user approval:

| Session | Capture | ASR provider | ASR result | Web handoff | Product value result |
| --- | --- | --- | --- | --- | --- |
| `real_mic_20260704_mainflow_002` | WAV written, but very low level | Sherpa ONNX | no final text | blocked before timestamp fix | not usable |
| `real_mic_20260704_mainflow_003` | WAV written, usable level | Sherpa ONNX | `真是麦克风主流承测试` | passed after timestamp adaptation | no engineering state or suggestions |
| `real_mic_20260704_mainflow_004` | WAV written, usable level | Sherpa ONNX | no final text | passed with evaluation summary only | no transcript or suggestions |

Audio level check:

```text
real_mic_20260704_mainflow_003:
duration_seconds=17.899
max_abs=0.113556
rms=0.008856
ratio_gt_0_005=0.232968
ratio_gt_0_02=0.042824

real_mic_20260704_mainflow_004:
duration_seconds=18.048
max_abs=0.063965
rms=0.007292
ratio_gt_0_005=0.329365
ratio_gt_0_02=0.022115
```

Artifacts:

- `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/real_mic_20260704_mainflow_003/audio.wav`
- `artifacts/tmp/asr_events/real_mic_20260704_mainflow_003.sherpa.events.json`
- `artifacts/tmp/asr_events/real_mic_20260704_mainflow_003.web.events.json`
- `artifacts/tmp/real_mic_shadow_tests/real_mic_20260704_mainflow_003/timestamp_adapted_web_handoff.json`
- `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/real_mic_20260704_mainflow_004/audio.wav`
- `artifacts/tmp/asr_events/real_mic_20260704_mainflow_004.sherpa.events.json`
- `artifacts/tmp/asr_events/real_mic_20260704_mainflow_004.web.events.json`
- `artifacts/tmp/real_mic_shadow_tests/real_mic_20260704_mainflow_004/full_chain_summary.json`
- `artifacts/tmp/real_mic_shadow_reports/real_mic_20260704_mainflow_004.json`

### Provider Check On The Same Real Audio

Sherpa ONNX was fast on CPU but did not preserve the spoken Chinese technical content:

```text
mainflow_003:
text=真是麦克风主流承测试
partial=3
final=1
technical_entity_recall=0.0

mainflow_004:
text=<empty>
partial=0
final=0
technical_entity_recall=0.0
```

FunASR was also tested on the same user-approved `mainflow_004` WAV:

```text
FunASR streaming:
text=我是我样
latency_ms=15438
rtf=0.855386
final_event_count=3
technical content not preserved

FunASR non-streaming:
text=六 六
latency_ms=9200
technical content not preserved
```

Important boundary note: the FunASR non-streaming test used a local ASR model path, but the script's VAD alias caused ModelScope to fetch a small cached-model README file. No audio was uploaded and no remote ASR/LLM inference was called, but this proves the current FunASR non-streaming script is not a strict offline-only path unless VAD/punctuation dependencies are also fully pinned to explicit local directories.

### Decision

DEC-204 is accepted:

- Real microphone capture on Mac is technically possible with `ffmpeg avfoundation`.
- The Web Live ASR handoff can ingest real-microphone-derived event files after timestamp adaptation.
- The current zero-cost local ASR providers are not good enough for the product's core value. The blocker is not the Web/product pipeline; it is ASR quality on real microphone Chinese technical speech.
- This evidence is not product Go evidence. It is a mainline integration and feasibility finding.
- Do not spend more time polishing UI or suggestion logic until an ASR provider path can reliably preserve Chinese technical meeting content.
- Keep the provider abstraction: local Sherpa/FunASR remain offline smoke providers, but the product MVP needs a stronger ASR route for real meetings. Options are a better local model, a strictly pinned local FunASR stack with improved capture/VAD, or a user-approved remote realtime ASR provider.

### Updated Next Mainline

The next product-significant decision is no longer "can we capture microphone" or "can Web ingest ASR events"; both are proven enough for integration. The next decision is:

```text
Choose the real-meeting ASR provider strategy before building more suggestion UX.
```

Recommended route:

1. Keep the PC app/client shell and Web Live ASR pipeline.
2. Promote `real_mic_full_chain_runner.py` to the main validation harness.
3. Add a strict provider bake-off for real microphone audio:
   - current Sherpa local
   - current FunASR streaming with explicit local dirs only
   - one stronger ASR provider candidate, only if explicitly approved
4. Gate product value on technical entity recall and first useful suggestion, not on "audio file exists" or "Web session exists".

### Safety And Cost

This real microphone run did:

- capture user-approved short microphone WAV files into ignored `artifacts/tmp/**`
- write local ASR event files and Web handoff event files
- mutate temporary Web TestClient data directories under ignored `artifacts/tmp/**`

This real microphone run did not:

- read the old user-provided `.m4a`
- read `configs/local/**`
- read `data/asr_eval/local_samples/**`
- read `data/local_runtime/**`
- upload raw audio
- call remote ASR inference
- call remote LLM inference
- create provider charges

Boundary deviation:

- One FunASR non-streaming check triggered a ModelScope metadata/README fetch for the VAD alias. This must be fixed before claiming strict offline FunASR execution.
