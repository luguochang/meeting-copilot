# Audio Capture Production Readiness Plan

> Decision: DEC-206  
> Date: 2026-07-04  
> Stage: M1 completed, M2 next  
> Scope: PC desktop audio input readiness before more ASR/provider work

## 1. Why This Exists

The product value is not "record audio and convert it to text". The product value is:

```text
usable meeting audio
  -> reliable realtime Chinese technical transcript
  -> meeting state extraction
  -> timely engineering suggestions
  -> reviewable transcript/audio evidence after the meeting
```

Recent full-chain tests proved that the Web product flow can consume live ASR-style events, create suggestion candidates, and generate preview reports. The blocker is now earlier in the chain: the captured audio and current zero-cost local ASR quality do not yet produce reliable Chinese technical meeting text.

This document turns that finding into an execution gate so the project does not spend more cycles optimizing UI or suggestion logic on bad input.

## 2. Production Principle

External speaker playback captured by the laptop microphone is not a production-grade test path.

It is useful only as a quick local smoke path because it proves that the app can hear something. It does not represent real meeting capture quality:

- the speaker and microphone add echo, room noise, compression, and device gain changes;
- meeting software audio is already digital, so routing it through air is avoidable quality loss;
- ASR errors from that path cannot tell whether the provider is weak or whether the capture path is weak;
- realtime suggestions depend on stable text timing, and unstable capture causes the downstream LLM to reason over broken context.

Therefore, before the next ASR bake-off, the product must first separate:

```text
microphone track quality
system audio digital track quality
mixed/dual-track quality
ASR provider quality
LLM suggestion quality
```

## 3. Current M1 Deliverable

M1 adds a local no-remote healthcheck:

```text
tools/audio_capture_healthcheck.py
tests/test_audio_capture_healthcheck.py
```

It analyzes approved local WAV files and returns deterministic metrics plus a gate status. It does not:

- call remote ASR;
- call an LLM;
- upload raw audio;
- read local provider secrets;
- read private local config;
- read old user recordings;
- download models;
- create a paid dependency.

The healthcheck can optionally record a short Mac microphone sample through `ffmpeg avfoundation`, but that mode is explicit and not invoked by default.
When that mode is used, the output path is validated before any directory is created or `ffmpeg` is started.
The production-readiness pass contract is intentionally strict: `16 kHz`, `mono`, `16-bit PCM WAV`, and at least `10 seconds` of usable audio.

## 4. Health Gate Schema

The report includes:

```text
report_mode
health_status
audio_path
duration_seconds
sample_rate
channel_count
sample_width_bytes
frame_count
rms
peak
active_sample_ratio
silence_ratio
clipping_ratio
validation_errors
recommendations
privacy_cost_flags
```

Allowed health statuses:

```text
audio_capture_health_passed
blocked_by_path_guard
blocked_by_wav_read_error
blocked_audio_too_short
blocked_audio_too_quiet
blocked_no_clear_speech
blocked_audio_clipping
blocked_unsupported_wav_format
blocked_missing_audio_path
blocked_by_microphone_capture_path_guard
blocked_by_microphone_capture_timeout
blocked_by_microphone_capture_error
```

Optional microphone capture statuses:

```text
recorded_from_real_microphone
blocked_by_microphone_capture_path_guard
blocked_by_microphone_capture_timeout
blocked_by_microphone_capture_error
```

## 5. Metric Meaning

`duration_seconds`

Short samples are blocked because one or two words cannot prove meeting capture readiness. Real validation should use longer samples with pauses, turn-taking, and technical terms.
The M1 gate currently requires at least 10 seconds before a sample can be used as provider bake-off evidence.

`rms`

Rough loudness level. Very low RMS means the capture is too quiet and should not be sent to ASR/provider selection.

`peak`

Highest normalized sample level. Very low peak usually means unclear speech or a wrong input source.

`active_sample_ratio`

Approximate share of samples above the active threshold. Very low values indicate mostly silence.

`silence_ratio`

Approximate inverse of active ratio. High silence can mean no input, wrong device, or a capture setup problem.

`clipping_ratio`

Share of samples near digital full scale. High clipping means the input is distorted, and ASR results are not trustworthy.

## 6. Approved Local Audio Roots

The tool only accepts WAV files under approved ignored runtime roots:

```text
artifacts/tmp/audio_health/
artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/
artifacts/tmp/real_mic_shadow_tests/
```

It blocks sensitive or non-product paths before reading content:

```text
configs/local/
data/asr_eval/local_samples/
data/local_runtime/
outputs/
```

It also blocks non-WAV inputs for this health gate. This is intentional: M1 is a deterministic PCM WAV gate, not a general media ingestion tool.

## 7. Recommended Mainline From Here

### M2: Mac System Audio Digital Capture

Goal:

```text
meeting app audio -> digital local WAV chunks -> healthcheck report
```

Why:

System audio capture is the production path for remote participants. It avoids the quality loss of external speaker playback.

Implementation boundary:

- Mac-first.
- No remote ASR or LLM during M2.
- No provider secret reads.
- Capture output only to approved ignored artifact roots.
- Add tests around command contracts, path guards, and report schema before enabling real capture.

### M3: Dual-Track Capture

Goal:

```text
local microphone track
remote/system audio track
optional mixed preview track
```

Why:

Production meeting copilot needs to distinguish local speaker, remote speaker, silence, and interruptions. A single mixed track is simpler but weak for debugging and future speaker attribution.

Implementation boundary:

- Store track metadata separately from raw audio.
- Keep raw chunks local.
- Do not assume speaker diarization is solved.
- Keep deletion and retention controls explicit.

### M4: ASR Provider Bake-Off On Clean Tracks

Goal:

Run provider comparison only after the audio health gate passes.

Candidates remain:

- local/offline first where quality is acceptable;
- OpenAI-compatible remote ASR only if explicitly approved;
- Chinese realtime ASR providers only if the quality/cost tradeoff is justified.

Decision rule:

Do not compare providers using noisy speaker-to-microphone samples. A provider bake-off on bad input creates false conclusions.

### M5: Production ASR Provider Decision

Goal:

Pick the minimum-cost ASR strategy that can support realtime Chinese technical meetings.

The current evidence says zero-cost local ASR is not yet enough for production Chinese technical meeting value. That does not mean the product is invalid; it means provider/capture strategy is the next critical product risk.

### M6: Resume Realtime Suggestions

Goal:

Once transcript quality is good enough, continue improving:

- rolling meeting state;
- action/risk/design-decision extraction;
- suggestion candidate timing;
- LLM request gating;
- post-meeting transcript/report/audio replay.

This should not proceed as the mainline until M2-M5 have removed the audio/transcript blocker.

## 8. Cost Policy

Default policy:

```text
spend only on the configured LLM relay unless a later decision explicitly approves ASR provider cost
```

Implications:

- M1 and M2 should be local-only.
- Provider credentials stay out of repository files.
- Paid realtime ASR is not the default.
- If paid ASR becomes necessary, it must be justified by a bake-off report with latency, accuracy, cost, privacy, and failure-mode evidence.

## 9. Pass/Fail Gate For The Next Mainline Step

M2 can start when M1 healthcheck is available and tested.

M4 cannot start until at least one clean capture path passes M1:

```text
audio_capture_health_passed
```

Any of the following blocks ASR/provider comparison:

```text
blocked_audio_too_short
blocked_unsupported_wav_format
blocked_audio_too_quiet
blocked_no_clear_speech
blocked_audio_clipping
blocked_by_path_guard
blocked_by_wav_read_error
blocked_by_microphone_capture_path_guard
blocked_by_microphone_capture_timeout
blocked_by_microphone_capture_error
```

## 10. Verification Commands

Focused M1 verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_audio_capture_healthcheck.py -q -p no:cacheprovider
```

Adjacent full-chain regression:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_audio_capture_healthcheck.py tests/test_real_mic_full_chain_runner.py tests/test_real_mic_shadow_test_report_schema.py -q -p no:cacheprovider
```

Sensitive scan:

Run a repository sensitive scan for API-key shaped tokens, relay domains, private audio filenames, and local model cache paths. Do not record the raw sensitive patterns in project docs, because that creates self-matching scan noise.

## 11. Current Decision

Proceed to M2 next:

```text
Mac system audio digital capture spike
```

This is the most direct way to answer the user's core concern: whether the product can produce fast, accurate enough realtime Chinese meeting text in a production-like capture path.
