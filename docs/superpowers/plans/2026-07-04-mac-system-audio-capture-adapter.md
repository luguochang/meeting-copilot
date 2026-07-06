# Mac System Audio Capture Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit-only Mac system-audio capture adapter that can preflight the production capture route, optionally record from a selected virtual system-audio input device, and feed the result into the existing M1 audio health gate.

**Architecture:** Create one focused Python tool under `tools/` that defaults to preflight-only and never requests permissions, records, uploads audio, reads secrets, or calls remote ASR/LLM unless explicit capture arguments are supplied. Reuse `audio_capture_healthcheck.build_audio_capture_health_report` for WAV analysis so M2 strengthens the existing mainline instead of creating a parallel health contract.

**Tech Stack:** Python standard library (`argparse`, `json`, `subprocess`, `platform`, `pathlib`), ffmpeg `avfoundation` command contract for explicitly selected Mac audio input devices, pytest.

---

### Task 1: M2 Adapter Contract Tests

**Files:**
- Create: `tests/test_mac_system_audio_capture_adapter.py`
- Create: `tools/mac_system_audio_capture_adapter.py`

- [ ] **Step 1: Write failing tests**

Add tests that import `tools/mac_system_audio_capture_adapter.py` and assert these behaviors:

```python
def test_preflight_defaults_to_no_capture_and_no_paid_or_remote_side_effects():
    tool = load_tool_module()
    report = tool.build_mac_system_audio_capture_preflight()
    assert report["report_mode"] == "mac_system_audio_capture_adapter"
    assert report["capture_adapter_status"] == "preflight_only_not_capturing"
    assert report["capture_backend"] == "ffmpeg_avfoundation_explicit_device"
    assert report["recommended_route"] == "virtual_system_audio_device_first"
    assert report["requires_explicit_device_index"] is True
    assert report["safe_to_capture_system_audio_now"] is False
    assert report["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }
```

Add path guard and process tests:

```python
def test_recording_path_guard_blocks_before_ffmpeg(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    forbidden_path = repo_root / "configs/local/system.wav"

    def fake_run(command, **kwargs):
        raise AssertionError("ffmpeg must not run for blocked output paths")

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    result = tool.record_system_audio_sample(
        audio_path=forbidden_path,
        record_seconds=3,
        audio_device_index=2,
        repo_root=repo_root,
    )
    assert result["capture_status"] == "blocked_by_system_audio_capture_path_guard"
    assert result["audio_path"] == "<redacted_invalid_path>"
    assert not forbidden_path.parent.exists()
```

Add existing WAV health-gate and CLI tests:

```python
def test_existing_approved_wav_runs_m1_health_gate(tmp_path):
    report = tool.build_system_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )
    assert report["capture_adapter_status"] == "existing_system_audio_wav_analyzed"
    assert report["audio_health"]["health_status"] == "audio_capture_health_passed"
```

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py -q -p no:cacheprovider
```

Expected: fail because `tools/mac_system_audio_capture_adapter.py` does not exist.

### Task 2: Minimal M2 Adapter Implementation

**Files:**
- Create: `tools/mac_system_audio_capture_adapter.py`

- [ ] **Step 1: Implement preflight and explicit capture**

Implement:

```python
def build_mac_system_audio_capture_preflight(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
```

It returns a no-capture report with:

- `capture_adapter_status="preflight_only_not_capturing"`;
- `capture_backend="ffmpeg_avfoundation_explicit_device"`;
- `recommended_route="virtual_system_audio_device_first"`;
- `screen_capturekit_status="future_native_path_not_implemented"`;
- `requires_virtual_system_audio_device=True`;
- `requires_explicit_device_index=True`;
- `requires_user_permission=True`;
- `safe_to_capture_system_audio_now=False`;
- all privacy/cost flags false.

Implement:

```python
def record_system_audio_sample(
    *,
    audio_path: Path,
    record_seconds: int,
    audio_device_index: int,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
```

It validates the output path before creating directories or starting `ffmpeg`, then runs:

```text
ffmpeg -hide_banner -nostdin -y -f avfoundation -i :<index> -t <seconds> -ac 1 -ar 16000 -sample_fmt s16 <audio_path>
```

Structured statuses:

```text
recorded_from_system_audio_device
blocked_by_system_audio_capture_path_guard
blocked_by_system_audio_capture_timeout
blocked_by_system_audio_capture_error
```

- [ ] **Step 2: Implement M1 healthcheck wrapper and CLI**

Implement:

```python
def build_system_audio_capture_health_report(...)
```

CLI contract:

```text
python3 tools/mac_system_audio_capture_adapter.py
python3 tools/mac_system_audio_capture_adapter.py --preflight-only
python3 tools/mac_system_audio_capture_adapter.py --audio-path artifacts/tmp/audio_health/example.wav
python3 tools/mac_system_audio_capture_adapter.py --record-seconds 12 --audio-device-index 2 --output-audio-path artifacts/tmp/audio_health/system-audio.wav
```

No-arg and `--preflight-only` exit `0` and do not capture. Existing approved WAV exits according to the M1 health gate. Explicit capture exits `0` only when capture succeeds and M1 health passes.

- [ ] **Step 3: Verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py -q -p no:cacheprovider
```

Expected: pass.

### Task 3: Mainline Runner Integration And Decision Record

**Files:**
- Modify: `tools/mainline_usable_e2e_runner.py`
- Modify: `tests/test_mainline_usable_e2e_runner.py`
- Create: `docs/mac-system-audio-capture-m2-plan-2026-07-04.md`
- Modify: `docs/decision-log.md`

- [ ] **Step 1: Add runner M2 status injection**

Extend `run_mainline_usable_e2e_selftest` with an optional `system_audio_capture` report parameter. When not supplied, generate the M2 preflight report. Add `system_audio_capture` to the final report.

Gap status rule:

```text
implemented_and_verified
```

only when `system_audio_capture.audio_health.health_status == "audio_capture_health_passed"` or `capture_adapter_status == "system_audio_capture_health_passed"`.

Otherwise keep:

```text
blocked_requires_m2_system_audio_capture
```

with detail from `capture_adapter_status`.

- [ ] **Step 2: Verify mainline regression**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py tests/test_mac_system_audio_capture_adapter.py tests/test_audio_capture_healthcheck.py -q -p no:cacheprovider
```

Expected: pass.

- [ ] **Step 3: Document DEC-209**

Append to `docs/decision-log.md`:

```text
DEC-209: M2 Mac system audio capture adapter is explicit-only, local-only, and health-gated.
```

Document that M2 does not add a paid item. It prepares the digital system-audio route so later provider bake-off does not compare ASR providers using degraded speaker-to-microphone audio.

### Task 4: Full Local Verification

**Files:**
- No new files

- [ ] **Step 1: Syntax check**

Run:

```bash
python3 -m py_compile tools/mac_system_audio_capture_adapter.py tests/test_mac_system_audio_capture_adapter.py tools/mainline_usable_e2e_runner.py tests/test_mainline_usable_e2e_runner.py
```

- [ ] **Step 2: Focused regression**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_mac_system_audio_capture_adapter.py \
  tests/test_audio_capture_healthcheck.py \
  tests/test_mainline_usable_e2e_runner.py \
  -q -p no:cacheprovider
```

- [ ] **Step 3: M2 dry-run CLI**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mac_system_audio_capture_adapter.py --preflight-only
```

Expected: JSON report with `capture_adapter_status=preflight_only_not_capturing`.

- [ ] **Step 4: Sensitive scan**

Run a scan over docs, tools, tests, code, configs, data, results, README, and `.codex` for API-key shaped tokens, relay domains, private audio names, and local model cache paths.

Expected: no matches.

### Self-Review

Spec coverage: covers default no-capture behavior, explicit capture only, ffmpeg avfoundation command contract, path guards before side effects, M1 health-gate reuse, mainline runner gap integration, docs, tests, and sensitive scan.

Placeholder scan: no TBD/TODO placeholders.

Type consistency: status names, report field names, function names, and CLI options are consistent across tasks.
