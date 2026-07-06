#!/usr/bin/env python3
"""Analyze local WAV capture health before running ASR."""

from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]

REPORT_MODE = "audio_capture_healthcheck"
APPROVED_CAPTURE_ROOTS = (
    Path("artifacts/tmp/audio_health"),
    Path("artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"),
    Path("artifacts/tmp/real_mic_shadow_tests"),
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)

REQUIRED_SAMPLE_RATE = 16_000
REQUIRED_CHANNEL_COUNT = 1
REQUIRED_SAMPLE_WIDTH_BYTES = 2
MIN_DURATION_SECONDS = 10.0
MIN_RMS = 0.01
MIN_PEAK = 0.05
ACTIVE_SAMPLE_THRESHOLD = 0.005
MIN_ACTIVE_SAMPLE_RATIO = 0.08
CLIPPING_THRESHOLD = 0.98
MAX_CLIPPING_RATIO = 0.01


def build_audio_capture_health_report(
    *,
    audio_path: Path,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    path_display, path_errors = _validate_audio_path(audio_path, repo_root)
    if path_errors:
        return _base_report(
            health_status="blocked_by_path_guard",
            audio_path=path_display,
            validation_errors=path_errors,
        )

    resolved = audio_path if audio_path.is_absolute() else repo_root / audio_path
    try:
        wav_info = _read_wav_metrics(resolved)
    except (OSError, EOFError, wave.Error, ValueError, struct.error) as exc:
        return _base_report(
            health_status="blocked_by_wav_read_error",
            audio_path=path_display,
            validation_errors=[_safe_error_message(exc)],
        )

    status, recommendations = _classify_health(wav_info)
    return {
        **_base_report(
            health_status=status,
            audio_path=path_display,
            validation_errors=[],
        ),
        **wav_info,
        "recommendations": recommendations,
    }


def record_microphone_sample(
    *,
    audio_path: Path,
    record_seconds: int,
    audio_device_index: int,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    path_display, path_errors = _validate_audio_path(audio_path, repo_root)
    if path_errors:
        return {
            "capture_status": "blocked_by_microphone_capture_path_guard",
            "audio_path": path_display,
            "validation_errors": path_errors,
            "record_seconds": record_seconds,
            "timeout_seconds": record_seconds + 10,
            "audio_device_index": audio_device_index,
            "audio_file_size_bytes": 0,
            "error_summary": "microphone output path failed preflight guard",
        }

    resolved_audio_path = audio_path if audio_path.is_absolute() else repo_root / audio_path
    resolved_audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "avfoundation",
        "-i",
        f":{audio_device_index}",
        "-t",
        str(record_seconds),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(resolved_audio_path),
    ]
    timeout_seconds = record_seconds + 10
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "capture_status": "blocked_by_microphone_capture_timeout",
            "audio_path": path_display,
            "validation_errors": ["ffmpeg avfoundation capture timed out"],
            "record_seconds": record_seconds,
            "timeout_seconds": timeout_seconds,
            "audio_device_index": audio_device_index,
            "audio_file_size_bytes": resolved_audio_path.stat().st_size if resolved_audio_path.exists() else 0,
            "error_summary": "ffmpeg avfoundation capture timed out",
        }

    if completed.returncode != 0:
        error_summary = _summarize_stderr(completed.stderr)
        return {
            "capture_status": "blocked_by_microphone_capture_error",
            "audio_path": path_display,
            "validation_errors": [error_summary],
            "record_seconds": record_seconds,
            "timeout_seconds": timeout_seconds,
            "audio_device_index": audio_device_index,
            "audio_file_size_bytes": resolved_audio_path.stat().st_size if resolved_audio_path.exists() else 0,
            "ffmpeg_returncode": completed.returncode,
            "error_summary": error_summary,
        }

    return {
        "capture_status": "recorded_from_real_microphone",
        "audio_path": path_display,
        "validation_errors": [],
        "record_seconds": record_seconds,
        "timeout_seconds": timeout_seconds,
        "audio_device_index": audio_device_index,
        "audio_file_size_bytes": resolved_audio_path.stat().st_size if resolved_audio_path.exists() else 0,
    }


def _read_wav_metrics(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as handle:
        channel_count = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        raw = handle.readframes(frame_count)
    if sample_width != 2:
        raise ValueError("only 16-bit PCM WAV is supported")
    if channel_count <= 0 or sample_rate <= 0:
        raise ValueError("WAV header has invalid channel count or sample rate")

    sample_count = len(raw) // sample_width
    if sample_count == 0:
        samples: list[int] = []
    else:
        samples = list(struct.unpack(f"<{sample_count}h", raw[: sample_count * sample_width]))
    if channel_count > 1:
        samples = samples[0::channel_count]
    normalized = [sample / 32768 for sample in samples]
    duration_seconds = frame_count / sample_rate if sample_rate else 0.0
    peak = max((abs(sample) for sample in normalized), default=0.0)
    rms = math.sqrt(sum(sample * sample for sample in normalized) / len(normalized)) if normalized else 0.0
    active_ratio = (
        sum(1 for sample in normalized if abs(sample) >= ACTIVE_SAMPLE_THRESHOLD) / len(normalized)
        if normalized
        else 0.0
    )
    clipping_ratio = (
        sum(1 for sample in normalized if abs(sample) >= CLIPPING_THRESHOLD) / len(normalized)
        if normalized
        else 0.0
    )
    return {
        "duration_seconds": round(duration_seconds, 3),
        "sample_rate": sample_rate,
        "channel_count": channel_count,
        "sample_width_bytes": sample_width,
        "frame_count": frame_count,
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "active_sample_ratio": round(active_ratio, 6),
        "silence_ratio": round(1 - active_ratio, 6),
        "clipping_ratio": round(clipping_ratio, 6),
    }


def _classify_health(metrics: dict[str, Any]) -> tuple[str, list[str]]:
    if (
        metrics["sample_rate"] != REQUIRED_SAMPLE_RATE
        or metrics["channel_count"] != REQUIRED_CHANNEL_COUNT
        or metrics["sample_width_bytes"] != REQUIRED_SAMPLE_WIDTH_BYTES
    ):
        return (
            "blocked_unsupported_wav_format",
            ["capture_16khz_mono_s16_wav_for_asr_provider_test"],
        )
    if metrics["duration_seconds"] < MIN_DURATION_SECONDS:
        return "blocked_audio_too_short", ["record_at_least_10_seconds_for_real_validation"]
    if metrics["clipping_ratio"] > MAX_CLIPPING_RATIO:
        return "blocked_audio_clipping", ["lower_input_gain_or_move_away_from_microphone"]
    if metrics["rms"] < MIN_RMS:
        return (
            "blocked_audio_too_quiet",
            ["move_closer_to_microphone_or_use_system_audio_capture"],
        )
    if metrics["peak"] < MIN_PEAK or metrics["active_sample_ratio"] < MIN_ACTIVE_SAMPLE_RATIO:
        return (
            "blocked_no_clear_speech",
            ["speak_louder_or_capture_digital_system_audio_instead_of_speaker_playback"],
        )
    return "audio_capture_health_passed", ["audio_is_usable_for_asr_provider_test"]


def _validate_audio_path(path: Path, repo_root: Path) -> tuple[str, list[str]]:
    errors: list[str] = []
    if path.suffix.casefold() != ".wav":
        errors.append("audio_path must be a WAV file")
    forbidden_errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            forbidden_errors.append(f"audio_path is blocked: {label}")
    resolved = (path if path.is_absolute() else repo_root / path).resolve(strict=False)
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(resolved, suffix_parts):
            message = f"audio_path is blocked: {label}"
            if message not in forbidden_errors:
                forbidden_errors.append(message)
    if forbidden_errors:
        return "<redacted_invalid_path>", errors + forbidden_errors
    relative = _repo_relative_path(resolved, repo_root)
    if relative is None:
        errors.append("audio_path is outside repository")
    elif not _is_under_approved_root(relative):
        errors.append("audio_path is not under an approved capture root")
    if errors:
        display = "<redacted_invalid_path>" if any("blocked:" in error or "outside" in error for error in errors) else (
            relative.as_posix() if relative is not None else "<redacted_invalid_path>"
        )
        return display, errors
    return relative.as_posix(), []


def _repo_relative_path(path: Path, repo_root: Path) -> Path | None:
    try:
        return path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return None


def _is_under_approved_root(relative: Path) -> bool:
    return any(relative == root or root in relative.parents for root in APPROVED_CAPTURE_ROOTS)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _base_report(
    *,
    health_status: str,
    audio_path: str,
    validation_errors: list[str],
) -> dict[str, Any]:
    return {
        "report_mode": REPORT_MODE,
        "health_status": health_status,
        "audio_path": audio_path,
        "validation_errors": validation_errors,
        "recommendations": [],
        "privacy_cost_flags": _privacy_cost_flags(),
    }


def _privacy_cost_flags() -> dict[str, bool]:
    return {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "user_audio_committed_to_repo": False,
    }


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message if message else exc.__class__.__name__


def _summarize_stderr(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "process exited without stderr"
    return " | ".join(lines[-4:])[:800]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-path", type=Path)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--record-seconds", type=int, default=0)
    parser.add_argument("--audio-device-index", type=int, default=0)
    parser.add_argument(
        "--output-audio-path",
        type=Path,
        help="Target WAV path for an explicit microphone capture healthcheck.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    capture_report: dict[str, Any] | None = None
    audio_path = args.audio_path
    if args.record_seconds > 0:
        audio_path = args.output_audio_path or (
            args.repo_root / "artifacts/tmp/audio_health/microphone-healthcheck.wav"
        )
        capture_report = record_microphone_sample(
            audio_path=audio_path,
            record_seconds=args.record_seconds,
            audio_device_index=args.audio_device_index,
            repo_root=args.repo_root,
        )
        if capture_report["capture_status"] != "recorded_from_real_microphone":
            report = _capture_blocker_report(capture_report)
            json.dump(report, out, ensure_ascii=False, indent=2)
            out.write("\n")
            return 1
    if audio_path is None:
        report = {
            **_base_report(
                health_status="blocked_missing_audio_path",
                audio_path="<not_provided>",
                validation_errors=["provide --audio-path or --record-seconds"],
            ),
            "capture": capture_report,
        }
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 1

    report = build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=args.repo_root,
    )
    if capture_report is not None:
        report["capture"] = capture_report
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["health_status"] == "audio_capture_health_passed" else 1


def _capture_blocker_report(capture_report: dict[str, Any]) -> dict[str, Any]:
    status = str(capture_report.get("capture_status", "blocked_by_microphone_capture_error"))
    report = {
        **_base_report(
            health_status=status,
            audio_path=str(capture_report.get("audio_path", "<redacted_invalid_path>")),
            validation_errors=list(capture_report.get("validation_errors", [])),
        ),
        "capture": capture_report,
    }
    if not report["validation_errors"] and capture_report.get("error_summary"):
        report["validation_errors"] = [str(capture_report["error_summary"])]
    return report


if __name__ == "__main__":
    raise SystemExit(main())
