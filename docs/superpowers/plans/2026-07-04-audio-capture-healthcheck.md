# Audio Capture Healthcheck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, no-remote audio healthcheck that tells whether a captured WAV is usable for ASR before provider tests run.

**Architecture:** Add a focused Python tool that can analyze approved local WAV files and optionally record a short Mac microphone sample through `ffmpeg avfoundation`. The tool reports deterministic metrics and a gate status; it does not call ASR, LLM, remote providers, or read secrets.

**Tech Stack:** Python standard library (`wave`, `struct`, `math`, `json`, `subprocess`), pytest, existing ignored `artifacts/tmp/**` runtime roots.

---

### Task 1: Test Audio Health Metrics And Gate Status

**Files:**
- Create: `tests/test_audio_capture_healthcheck.py`
- Create: `tools/audio_capture_healthcheck.py`

- [x] **Step 1: Write failing tests**

Create tests that generate local PCM WAV fixtures and assert:

```python
def test_clean_speech_like_wav_passes_health_gate(tmp_path):
    # Write 16 kHz mono PCM with sustained non-silent samples.
    # Expect audio_capture_health_passed with rms, peak, active ratios populated.
```

```python
def test_quiet_wav_blocks_before_asr(tmp_path):
    # Write a nearly silent WAV.
    # Expect blocked_audio_too_quiet and recommendation to move closer or use system audio.
```

```python
def test_clipped_wav_blocks_before_asr(tmp_path):
    # Write a heavily clipped WAV.
    # Expect blocked_audio_clipping.
```

```python
def test_path_guard_blocks_forbidden_or_private_audio_paths(tmp_path):
    # Analyze path under configs/local and a .m4a path.
    # Expect blocked_by_path_guard before reading.
```

- [x] **Step 2: Verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_audio_capture_healthcheck.py -q -p no:cacheprovider
```

Expected: failure because `tools/audio_capture_healthcheck.py` does not exist.

- [x] **Step 3: Implement minimal tool**

Implement:

```python
build_audio_capture_health_report(audio_path: Path, repo_root: Path = REPO_ROOT) -> dict
```

The report must include:

```text
health_status
audio_path
duration_seconds
sample_rate
channel_count
sample_width_bytes
rms
peak
active_sample_ratio
silence_ratio
clipping_ratio
validation_errors
recommendations
privacy_cost_flags
```

Allowed statuses:

```text
audio_capture_health_passed
blocked_by_path_guard
blocked_by_wav_read_error
blocked_audio_too_short
blocked_audio_too_quiet
blocked_no_clear_speech
blocked_audio_clipping
blocked_unsupported_wav_format
```

- [x] **Step 4: Verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_audio_capture_healthcheck.py -q -p no:cacheprovider
```

Expected: all tests pass.

### Task 2: Add Optional Real Mic Recording Wrapper

**Files:**
- Modify: `tools/audio_capture_healthcheck.py`
- Modify: `tests/test_audio_capture_healthcheck.py`

- [x] **Step 1: Write failing timeout/CLI tests**

Add tests for:

```python
def test_recording_timeout_returns_structured_blocker(monkeypatch, tmp_path):
    # monkeypatch subprocess.run to raise TimeoutExpired.
    # Expect blocked_by_microphone_capture_timeout.
```

```python
def test_cli_writes_json_report_for_existing_wav(tmp_path):
    # Run main([...], out=io.StringIO()).
    # Expect JSON with health_status.
```

- [x] **Step 2: Implement minimal recording wrapper**

Implement:

```python
record_microphone_sample(audio_path: Path, record_seconds: int, audio_device_index: int, repo_root: Path = REPO_ROOT) -> dict
```

It must call:

```text
ffmpeg -hide_banner -nostdin -y -f avfoundation -i :<index> -t <seconds> -ac 1 -ar 16000 -sample_fmt s16 <audio_path>
```

with timeout `record_seconds + 10`.

- [x] **Step 3: Verify**

Run the focused healthcheck tests plus the existing real mic runner tests.

### Task 3: Document Production Audio Input Gate

**Files:**
- Create: `docs/audio-capture-production-readiness-plan.md`
- Modify: `docs/decision-log.md`

- [x] **Step 1: Record the decision**

Document that production validation must move from "speaker-to-microphone" testing to:

```text
mic healthcheck
system audio digital capture
dual-track capture
ASR provider bake-off on clean tracks
```

- [x] **Step 2: Verify docs do not contain secrets**

Run the repository sensitive scan for API-key shaped tokens, relay domains, private audio filenames, and local model cache paths. Keep the raw pattern out of docs so the scan does not match its own instructions.

Expected: no matches.
