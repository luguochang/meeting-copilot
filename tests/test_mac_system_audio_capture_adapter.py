import importlib.util
import io
import json
import math
import struct
import subprocess
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "mac_system_audio_capture_adapter.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "mac_system_audio_capture_adapter",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_wav(
    path: Path,
    *,
    duration_seconds: float = 10.2,
    amplitude: float = 0.18,
    sample_rate: int = 16_000,
) -> None:
    frame_count = int(duration_seconds * sample_rate)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = b"".join(
            struct.pack(
                "<h",
                int(
                    32767
                    * amplitude
                    * math.sin(2 * math.pi * 440.0 * frame / sample_rate)
                ),
            )
            for frame in range(frame_count)
        )
        handle.writeframes(frames)


def test_preflight_defaults_to_no_capture_and_no_paid_or_remote_side_effects():
    tool = load_tool_module()

    report = tool.build_mac_system_audio_capture_preflight()

    assert report["report_mode"] == "mac_system_audio_capture_adapter"
    assert report["schema_version"] == "mac_system_audio_capture_adapter.v1"
    assert report["capture_adapter_status"] == "preflight_only_not_capturing"
    assert report["capture_backend"] == "ffmpeg_avfoundation_explicit_device"
    assert report["recommended_route"] == "virtual_system_audio_device_first"
    assert report["screen_capturekit_status"] == "future_native_path_not_implemented"
    assert report["requires_virtual_system_audio_device"] is True
    assert report["requires_explicit_device_index"] is True
    assert report["requires_user_permission"] is True
    assert report["safe_to_capture_system_audio_now"] is False
    assert report["safe_to_request_system_audio_permission_now"] is False
    assert report["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }


def test_recording_path_guard_blocks_before_ffmpeg_and_before_mkdir(monkeypatch, tmp_path):
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
    assert result["validation_errors"] == ["audio_path is blocked: configs/local"]
    assert result["audio_file_size_bytes"] == 0
    assert result["privacy_cost_flags"]["remote_asr_called"] is False
    assert not forbidden_path.parent.exists()


def test_recording_timeout_returns_structured_blocker(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/system-timeout.wav"

    def fake_run(command, **kwargs):
        assert command[:8] == [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "avfoundation",
            "-i",
            ":4",
        ]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    result = tool.record_system_audio_sample(
        audio_path=audio_path,
        record_seconds=5,
        audio_device_index=4,
        repo_root=repo_root,
    )

    assert result["capture_status"] == "blocked_by_system_audio_capture_timeout"
    assert result["audio_path"] == "artifacts/tmp/audio_health/system-timeout.wav"
    assert result["timeout_seconds"] == 15
    assert result["audio_file_size_bytes"] == 0
    assert result["validation_errors"] == ["ffmpeg avfoundation system audio capture timed out"]


def test_recording_process_error_redacts_local_paths_and_old_audio_markers(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/system-error.wav"

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr=(
                "failed opening /Users/chase/tmp/private-audio.m4a\n"
                "temp path /private/var/folders/fr/example/audio-cache\n"
                "legacy extension .m4a is not accepted"
            ),
        )

    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    result = tool.record_system_audio_sample(
        audio_path=audio_path,
        record_seconds=5,
        audio_device_index=4,
        repo_root=repo_root,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["capture_status"] == "blocked_by_system_audio_capture_error"
    assert "<redacted_user_path>" in serialized
    assert "<redacted_temp_path>" in serialized
    assert "<redacted_audio_extension>" in serialized
    assert "/Users/" not in serialized
    assert "/private/var" not in serialized
    assert "se/tmp" not in serialized
    assert "s/fr/example" not in serialized
    assert ".m4a" not in serialized


def test_existing_approved_wav_runs_m1_health_gate(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/system.wav"
    write_wav(audio_path)

    report = tool.build_system_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )

    assert report["report_mode"] == "mac_system_audio_capture_adapter"
    assert report["capture_adapter_status"] == "existing_system_audio_wav_analyzed"
    assert report["audio_health"]["health_status"] == "audio_capture_health_passed"
    assert report["audio_path"] == "artifacts/tmp/audio_health/system.wav"
    assert report["capture"] is None
    assert report["privacy_cost_flags"]["paid_provider_used"] is False


def test_cli_preflight_writes_json_without_capture():
    tool = load_tool_module()
    out = io.StringIO()

    exit_code = tool.main(["--preflight-only"], out=out)

    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert payload["capture_adapter_status"] == "preflight_only_not_capturing"
    assert payload["safe_to_capture_system_audio_now"] is False


def test_cli_explicit_capture_timeout_returns_standard_report(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/system-timeout.wav"

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    out = io.StringIO()

    exit_code = tool.main(
        [
            "--repo-root",
            str(repo_root),
            "--record-seconds",
            "4",
            "--audio-device-index",
            "3",
            "--output-audio-path",
            str(audio_path),
        ],
        out=out,
    )

    payload = json.loads(out.getvalue())
    assert exit_code == 1
    assert payload["report_mode"] == "mac_system_audio_capture_adapter"
    assert payload["capture_adapter_status"] == "blocked_by_system_audio_capture_timeout"
    assert payload["audio_path"] == "artifacts/tmp/audio_health/system-timeout.wav"
    assert payload["capture"]["capture_status"] == "blocked_by_system_audio_capture_timeout"
    assert payload["audio_health"] is None
    assert payload["privacy_cost_flags"]["raw_audio_uploaded"] is False


def test_report_serialization_has_no_private_paths_or_remote_side_effects(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/system.wav"
    write_wav(audio_path)

    report = tool.build_system_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )

    serialized = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in serialized
    assert "configs/local" not in serialized
    assert "data/local_runtime" not in serialized
    assert "remote_asr_called" in serialized
    assert report["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }
