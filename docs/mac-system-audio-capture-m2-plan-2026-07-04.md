# Mac System Audio Capture M2 Plan

> Date: 2026-07-04  
> Decision: DEC-209  
> Stage: M2 implemented as explicit-only local adapter  
> Scope: Mac PC端系统音频数字采集预检、显式短录制、M1 health gate 接入

## 1. Product Decision

M2 does not add a new paid ASR provider.

The purpose of M2 is to remove the next product-chain uncertainty before provider bake-off:

```text
meeting app/system audio
  -> local digital WAV health sample
  -> M1 audio_capture_healthcheck
  -> ASR/provider evaluation on clean input
```

This matters because speaker playback captured by the laptop microphone is not a reliable production path. It mixes room noise, echo, speaker gain, microphone gain, and meeting compression. If provider tests run on that degraded path, the project cannot tell whether poor transcript quality comes from the ASR provider or from bad audio capture.

## 2. Implementation Boundary

Implemented tool:

```text
tools/mac_system_audio_capture_adapter.py
tests/test_mac_system_audio_capture_adapter.py
```

The adapter defaults to no-capture preflight:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mac_system_audio_capture_adapter.py --preflight-only
```

No-arg mode is also preflight-only. It does not:

- request microphone or system-audio permissions;
- record audio;
- enumerate private audio files;
- read `configs/local/**`;
- read `data/local_runtime/**`;
- read `outputs/**`;
- upload audio;
- call remote ASR;
- call an LLM;
- use a paid provider.

Explicit capture requires caller-supplied recording arguments:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mac_system_audio_capture_adapter.py \
  --record-seconds 12 \
  --audio-device-index 2 \
  --output-audio-path artifacts/tmp/audio_health/system-audio-health.wav
```

That command is only a local health sample. It is not real meeting Go evidence.

## 3. Mac System Audio Route

The current M2 backend is:

```text
ffmpeg avfoundation explicit device
```

The command contract is:

```text
ffmpeg -hide_banner -nostdin -y -f avfoundation -i :<device_index> \
  -t <seconds> -ac 1 -ar 16000 -sample_fmt s16 <audio_path>
```

On macOS, true digital system-audio capture usually requires a virtual audio input route such as BlackHole, Loopback, or an Aggregate Device. M2 does not install or configure those tools. The adapter is intentionally explicit: it records only from the selected avfoundation audio input index provided by the caller.

Apple ScreenCaptureKit is documented as the future native route, but it is not implemented in this M2 slice because it would require a separate native permission/runtime path. For the current Mac MVP, the virtual-system-audio-device route is the smallest useful production step.

## 4. Report Contract

Adapter report:

```text
report_mode=mac_system_audio_capture_adapter
schema_version=mac_system_audio_capture_adapter.v1
capture_backend=ffmpeg_avfoundation_explicit_device
recommended_route=virtual_system_audio_device_first
screen_capturekit_status=future_native_path_not_implemented
m2_go_evidence_status=not_real_meeting_go_evidence
```

Default preflight status:

```text
capture_adapter_status=preflight_only_not_capturing
safe_to_capture_system_audio_now=false
safe_to_request_system_audio_permission_now=false
```

Explicit capture statuses:

```text
recorded_from_system_audio_device
blocked_by_system_audio_capture_path_guard
blocked_by_system_audio_capture_timeout
blocked_by_system_audio_capture_error
```

Health wrapper statuses:

```text
existing_system_audio_wav_analyzed
existing_system_audio_wav_health_failed
system_audio_capture_health_passed
<M1 health_status when blocked>
```

The adapter always separates:

```text
capture_status
audio_health.health_status
m2_go_evidence_status
```

So a short system-audio sample can prove capture readiness without pretending to prove real meeting product readiness.

## 5. Mainline Runner Integration

Modified runner:

```text
tools/mainline_usable_e2e_runner.py
tests/test_mainline_usable_e2e_runner.py
```

The runner now includes:

```text
system_audio_capture
```

Default behavior:

```text
system_audio_capture.capture_adapter_status=preflight_only_not_capturing
mac_system_audio_capture gap=blocked_requires_m2_system_audio_capture
```

If an injected or future explicit M2 report has:

```text
audio_health.health_status=audio_capture_health_passed
```

then only this gap changes:

```text
mac_system_audio_capture -> implemented_and_verified
```

The runner still keeps:

```text
production_asr_quality -> blocked_by_asr_quality
real_meeting_go_evidence -> blocked_requires_explicit_user_approval
```

This is deliberate. M2 proves clean audio input readiness, not ASR production quality and not real meeting Go evidence.

M2.1 adds an explicit mainline runner capture entry:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id <session_id> \
  --system-audio-record-seconds 12 \
  --system-audio-device-index <avfoundation_audio_input_index>
```

Optional output override:

```text
--system-audio-output-path artifacts/tmp/audio_health/<session>.system-audio-health.wav
```

Default mainline behavior remains no-capture. The new CLI arguments only call the M2 adapter when `--system-audio-record-seconds` is greater than zero.
Tests monkeypatch the recorder and health wrapper; no real device is touched by automated regression.

## 6. Safety And Cost Policy

M2 keeps the existing cost policy:

```text
do not add paid ASR provider cost by default
```

All privacy/cost flags remain false:

```text
raw_audio_uploaded=false
remote_asr_called=false
llm_called=false
configs_local_read=false
private_user_audio_read=false
paid_provider_used=false
```

Audio paths are guarded before directories are created or `ffmpeg` is started. Approved health-sample output remains under:

```text
artifacts/tmp/audio_health/
```

Forbidden roots remain blocked:

```text
configs/local/
data/asr_eval/local_samples/
data/local_runtime/
outputs/
```

Process error summaries are redacted for user paths and old audio markers before they enter JSON.

## 7. Verification To Date

TDD red:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py -q -p no:cacheprovider
Result: 7 failed because tools/mac_system_audio_capture_adapter.py did not exist
```

Focused green:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py -q -p no:cacheprovider
Result: 8 passed, 1 warning
```

Runner integration red:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 2 failed, 2 passed because runner had no system_audio_capture report/parameter
```

Runner integration green:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 9 passed, 2 warnings
```

Final adjacent regression:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py tests/test_audio_capture_healthcheck.py tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 28 passed, 2 warnings
```

Syntax check:

```text
python3 -m py_compile tools/mac_system_audio_capture_adapter.py tests/test_mac_system_audio_capture_adapter.py tools/mainline_usable_e2e_runner.py tests/test_mainline_usable_e2e_runner.py
Result: exit 0
```

M2 preflight CLI:

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mac_system_audio_capture_adapter.py --preflight-only
Result: capture_adapter_status=preflight_only_not_capturing, safe_to_capture_system_audio_now=false
```

Mainline CLI with browser smoke:

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py --session-id m21_default_mainline_selftest_20260704 --run-browser-smoke
Result: exit 0, overall_status=mainline_product_chain_exercised_with_expected_blockers, browser_smoke_status=passed
```

Final mainline gap summary:

```text
implemented_and_verified=5
blocked_by_asr_quality=1
blocked_requires_m2_system_audio_capture=1
blocked_requires_explicit_user_approval=1
```

Sensitive scan:

```text
Result: no matches for API-key shaped tokens, relay domain, old Voice Memos marker, old recording filename, or local ModelScope cache path
```

Read-only review closure:

```text
Finding 1 fixed: mainline runner now requires audio_health.health_status=audio_capture_health_passed before clearing mac_system_audio_capture.
Finding 2 fixed: outside-repo artifact paths serialize as <redacted_outside_repo_path>.
Finding 3 fixed: M2-pass test now asserts production_asr_quality remains blocked_by_asr_quality.
```

M2.1 explicit mainline capture entry:

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_runner_explicit_system_audio_capture_uses_adapter_and_health_gate tests/test_mainline_usable_e2e_runner.py::test_cli_explicit_system_audio_capture_calls_recorder_without_remote_services -q -p no:cacheprovider
Result: 2 failed because runner did not normalize capture result and CLI did not recognize system-audio args

GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_runner_explicit_system_audio_capture_uses_adapter_and_health_gate tests/test_mainline_usable_e2e_runner.py::test_cli_explicit_system_audio_capture_calls_recorder_without_remote_services -q -p no:cacheprovider
Result: 2 passed, 2 warnings
```

## 8. Remaining Work

M2 implemented the safe adapter, runner integration, and explicit mainline capture entry. Remaining production work:

- Run a real Mac virtual-system-audio health sample with user approval.
- Compare local/offline ASR and any approved remote ASR only after clean capture health passes.
- Keep raw health samples local and ignored; define retention/deletion controls before real meeting capture.
- Do not remove the real meeting Go evidence blocker until a user-approved real meeting shadow test is run and reviewed.
