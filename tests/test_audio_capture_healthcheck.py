import importlib.util
import io
import json
import math
import struct
import subprocess
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "audio_capture_healthcheck.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "audio_capture_healthcheck",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_wav(
    path: Path,
    samples: list[int],
    *,
    sample_rate: int = 16_000,
    channel_count: int = 1,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channel_count)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = b"".join(struct.pack("<h", sample) for sample in samples)
        handle.writeframes(frames)


def sine_samples(
    *,
    duration_seconds: float,
    amplitude: float,
    sample_rate: int = 16_000,
    frequency: float = 440.0,
) -> list[int]:
    frame_count = int(duration_seconds * sample_rate)
    return [
        int(32767 * amplitude * math.sin(2 * math.pi * frequency * frame / sample_rate))
        for frame in range(frame_count)
    ]


def test_clean_speech_like_wav_passes_health_gate(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/clean.wav"
    samples = sine_samples(duration_seconds=10.2, amplitude=0.18)
    write_wav(audio_path, samples)

    report = tool.build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )

    assert report["health_status"] == "audio_capture_health_passed"
    assert report["audio_path"] == "artifacts/tmp/audio_health/clean.wav"
    assert report["sample_rate"] == 16_000
    assert report["channel_count"] == 1
    assert report["duration_seconds"] == 10.2
    assert report["rms"] > 0.1
    assert report["peak"] > 0.17
    assert report["active_sample_ratio"] > 0.8
    assert report["silence_ratio"] < 0.2
    assert report["clipping_ratio"] == 0
    assert report["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "user_audio_committed_to_repo": False,
    }


def test_quiet_wav_blocks_before_asr(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/quiet.wav"
    samples = sine_samples(duration_seconds=10.1, amplitude=0.002)
    write_wav(audio_path, samples)

    report = tool.build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )

    assert report["health_status"] == "blocked_audio_too_quiet"
    assert report["rms"] < tool.MIN_RMS
    assert "move_closer_to_microphone_or_use_system_audio_capture" in report["recommendations"]


def test_clipped_wav_blocks_before_asr(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/clipped.wav"
    samples = [32767 if index % 2 == 0 else -32768 for index in range(160_000)]
    write_wav(audio_path, samples)

    report = tool.build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )

    assert report["health_status"] == "blocked_audio_clipping"
    assert report["clipping_ratio"] > tool.MAX_CLIPPING_RATIO
    assert "lower_input_gain_or_move_away_from_microphone" in report["recommendations"]


def test_short_wav_blocks_readiness_gate_before_asr(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/short.wav"
    samples = sine_samples(duration_seconds=1.5, amplitude=0.18)
    write_wav(audio_path, samples)

    report = tool.build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )

    assert report["health_status"] == "blocked_audio_too_short"
    assert report["duration_seconds"] == 1.5
    assert "record_at_least_10_seconds_for_real_validation" in report["recommendations"]


def test_wrong_sample_rate_blocks_unsupported_wav_format(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/wrong-rate.wav"
    samples = sine_samples(duration_seconds=10.1, amplitude=0.18, sample_rate=8_000)
    write_wav(audio_path, samples, sample_rate=8_000)

    report = tool.build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )

    assert report["health_status"] == "blocked_unsupported_wav_format"
    assert report["sample_rate"] == 8_000
    assert "capture_16khz_mono_s16_wav_for_asr_provider_test" in report["recommendations"]


def test_stereo_wav_blocks_unsupported_wav_format(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/stereo.wav"
    mono_samples = sine_samples(duration_seconds=10.1, amplitude=0.18)
    stereo_samples = [sample for mono_sample in mono_samples for sample in (mono_sample, mono_sample)]
    write_wav(audio_path, stereo_samples, channel_count=2)

    report = tool.build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )

    assert report["health_status"] == "blocked_unsupported_wav_format"
    assert report["channel_count"] == 2
    assert "capture_16khz_mono_s16_wav_for_asr_provider_test" in report["recommendations"]


def test_path_guard_blocks_forbidden_or_private_audio_paths(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    forbidden_path = repo_root / "configs/local/private.wav"
    m4a_path = repo_root / "artifacts/tmp/audio_health/private.m4a"

    forbidden_report = tool.build_audio_capture_health_report(
        audio_path=forbidden_path,
        repo_root=repo_root,
    )
    m4a_report = tool.build_audio_capture_health_report(
        audio_path=m4a_path,
        repo_root=repo_root,
    )

    assert forbidden_report["health_status"] == "blocked_by_path_guard"
    assert forbidden_report["audio_path"] == "<redacted_invalid_path>"
    assert forbidden_report["validation_errors"] == ["audio_path is blocked: configs/local"]
    assert m4a_report["health_status"] == "blocked_by_path_guard"
    assert m4a_report["validation_errors"] == ["audio_path must be a WAV file"]


def test_recording_timeout_returns_structured_blocker(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/audio.wav"

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    report = tool.record_microphone_sample(
        audio_path=audio_path,
        record_seconds=3,
        audio_device_index=0,
        repo_root=repo_root,
    )

    assert report["capture_status"] == "blocked_by_microphone_capture_timeout"
    assert report["timeout_seconds"] == 13
    assert report["audio_file_size_bytes"] == 0


def test_recording_path_guard_blocks_before_ffmpeg(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    forbidden_path = repo_root / "configs/local/private.wav"

    def fake_run(command, **kwargs):
        raise AssertionError("ffmpeg must not run for blocked output paths")

    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    report = tool.record_microphone_sample(
        audio_path=forbidden_path,
        record_seconds=3,
        audio_device_index=0,
        repo_root=repo_root,
    )

    assert report["capture_status"] == "blocked_by_microphone_capture_path_guard"
    assert report["audio_path"] == "<redacted_invalid_path>"
    assert report["validation_errors"] == ["audio_path is blocked: configs/local"]
    assert report["audio_file_size_bytes"] == 0
    assert not forbidden_path.parent.exists()


def test_cli_recording_timeout_returns_standard_health_report(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/timeout.wav"

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    out = io.StringIO()

    exit_code = tool.main(
        [
            "--repo-root",
            str(repo_root),
            "--record-seconds",
            "3",
            "--output-audio-path",
            str(audio_path),
        ],
        out=out,
    )

    payload = json.loads(out.getvalue())
    assert exit_code == 1
    assert payload["report_mode"] == "audio_capture_healthcheck"
    assert payload["health_status"] == "blocked_by_microphone_capture_timeout"
    assert payload["audio_path"] == "artifacts/tmp/audio_health/timeout.wav"
    assert payload["validation_errors"] == ["ffmpeg avfoundation capture timed out"]
    assert payload["privacy_cost_flags"]["remote_asr_called"] is False
    assert payload["capture"]["capture_status"] == "blocked_by_microphone_capture_timeout"


def test_cli_writes_json_report_for_existing_wav(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/cli.wav"
    samples = sine_samples(duration_seconds=10.1, amplitude=0.15)
    write_wav(audio_path, samples)
    out = io.StringIO()

    exit_code = tool.main(
        [
            "--audio-path",
            str(audio_path),
            "--repo-root",
            str(repo_root),
        ],
        out=out,
    )

    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert payload["health_status"] == "audio_capture_health_passed"
    assert payload["audio_path"] == "artifacts/tmp/audio_health/cli.wav"
