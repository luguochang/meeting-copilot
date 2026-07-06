#!/usr/bin/env python3
"""Preflight and explicitly capture Mac system-audio health samples."""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audio_capture_healthcheck  # noqa: E402


REPORT_MODE = "mac_system_audio_capture_adapter"
SCHEMA_VERSION = "mac_system_audio_capture_adapter.v1"
CAPTURE_BACKEND = "ffmpeg_avfoundation_explicit_device"
RECOMMENDED_ROUTE = "virtual_system_audio_device_first"
SCREEN_CAPTUREKIT_STATUS = "future_native_path_not_implemented"
DEFAULT_OUTPUT_AUDIO_PATH = Path("artifacts/tmp/audio_health/system-audio-health.wav")


def build_mac_system_audio_capture_preflight(
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    return {
        **_base_report(
            capture_adapter_status="preflight_only_not_capturing",
            audio_path="<not_recorded>",
            capture=None,
            audio_health=None,
        ),
        "platform": platform.system().lower(),
        "repo_root_status": "not_serialized",
        "requires_virtual_system_audio_device": True,
        "requires_explicit_device_index": True,
        "requires_user_permission": True,
        "safe_to_capture_system_audio_now": False,
        "safe_to_request_system_audio_permission_now": False,
        "recommended_next_action": (
            "configure_virtual_system_audio_device_then_run_explicit_short_health_capture"
        ),
    }


def build_system_audio_capture_health_report(
    *,
    audio_path: Path,
    repo_root: Path = REPO_ROOT,
    capture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audio_health = audio_capture_healthcheck.build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )
    status = (
        "system_audio_capture_health_passed"
        if audio_health.get("health_status") == "audio_capture_health_passed"
        else str(audio_health.get("health_status", "blocked_by_system_audio_health_gate"))
    )
    display_path = str(audio_health.get("audio_path", "<redacted_invalid_path>"))
    if capture is None:
        status = (
            "existing_system_audio_wav_analyzed"
            if audio_health.get("health_status") == "audio_capture_health_passed"
            else "existing_system_audio_wav_health_failed"
        )
    return _base_report(
        capture_adapter_status=status,
        audio_path=display_path,
        capture=capture,
        audio_health=audio_health,
    )


def record_system_audio_sample(
    *,
    audio_path: Path,
    record_seconds: int,
    audio_device_index: int,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    path_display, path_errors = audio_capture_healthcheck._validate_audio_path(  # noqa: SLF001
        audio_path,
        repo_root,
    )
    if path_errors:
        return _capture_report(
            capture_status="blocked_by_system_audio_capture_path_guard",
            audio_path=path_display,
            validation_errors=path_errors,
            record_seconds=record_seconds,
            timeout_seconds=record_seconds + 10,
            audio_device_index=audio_device_index,
            audio_file_size_bytes=0,
            error_summary="system audio output path failed preflight guard",
        )
    if record_seconds <= 0:
        return _capture_report(
            capture_status="blocked_by_system_audio_capture_error",
            audio_path=path_display,
            validation_errors=["record_seconds must be greater than zero"],
            record_seconds=record_seconds,
            timeout_seconds=record_seconds + 10,
            audio_device_index=audio_device_index,
            audio_file_size_bytes=0,
            error_summary="invalid record_seconds",
        )
    if audio_device_index < 0:
        return _capture_report(
            capture_status="blocked_by_system_audio_capture_error",
            audio_path=path_display,
            validation_errors=["audio_device_index must be non-negative"],
            record_seconds=record_seconds,
            timeout_seconds=record_seconds + 10,
            audio_device_index=audio_device_index,
            audio_file_size_bytes=0,
            error_summary="invalid audio_device_index",
        )

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
        return _capture_report(
            capture_status="blocked_by_system_audio_capture_timeout",
            audio_path=path_display,
            validation_errors=["ffmpeg avfoundation system audio capture timed out"],
            record_seconds=record_seconds,
            timeout_seconds=timeout_seconds,
            audio_device_index=audio_device_index,
            audio_file_size_bytes=_file_size(resolved_audio_path),
            error_summary="ffmpeg avfoundation system audio capture timed out",
        )

    if completed.returncode != 0:
        error_summary = _redact_process_text(_summarize_stderr(completed.stderr))
        return _capture_report(
            capture_status="blocked_by_system_audio_capture_error",
            audio_path=path_display,
            validation_errors=[error_summary],
            record_seconds=record_seconds,
            timeout_seconds=timeout_seconds,
            audio_device_index=audio_device_index,
            audio_file_size_bytes=_file_size(resolved_audio_path),
            error_summary=error_summary,
            ffmpeg_returncode=completed.returncode,
        )

    return _capture_report(
        capture_status="recorded_from_system_audio_device",
        audio_path=path_display,
        validation_errors=[],
        record_seconds=record_seconds,
        timeout_seconds=timeout_seconds,
        audio_device_index=audio_device_index,
        audio_file_size_bytes=_file_size(resolved_audio_path),
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--audio-path", type=Path)
    parser.add_argument("--record-seconds", type=int, default=0)
    parser.add_argument("--audio-device-index", type=int, default=0)
    parser.add_argument("--output-audio-path", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.preflight_only or (args.audio_path is None and args.record_seconds <= 0):
        report = build_mac_system_audio_capture_preflight(repo_root=args.repo_root)
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 0

    audio_path = args.audio_path
    capture: dict[str, Any] | None = None
    if args.record_seconds > 0:
        audio_path = args.output_audio_path or args.repo_root / DEFAULT_OUTPUT_AUDIO_PATH
        capture = record_system_audio_sample(
            audio_path=audio_path,
            record_seconds=args.record_seconds,
            audio_device_index=args.audio_device_index,
            repo_root=args.repo_root,
        )
        if capture["capture_status"] != "recorded_from_system_audio_device":
            report = _base_report(
                capture_adapter_status=str(capture["capture_status"]),
                audio_path=str(capture.get("audio_path", "<redacted_invalid_path>")),
                capture=capture,
                audio_health=None,
            )
            json.dump(report, out, ensure_ascii=False, indent=2)
            out.write("\n")
            return 1

    if audio_path is None:
        report = _base_report(
            capture_adapter_status="blocked_missing_audio_path",
            audio_path="<not_provided>",
            capture=capture,
            audio_health=None,
        )
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 1

    report = build_system_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=args.repo_root,
        capture=capture,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return (
        0
        if report.get("audio_health", {}).get("health_status") == "audio_capture_health_passed"
        else 1
    )


def _base_report(
    *,
    capture_adapter_status: str,
    audio_path: str,
    capture: dict[str, Any] | None,
    audio_health: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "report_mode": REPORT_MODE,
        "schema_version": SCHEMA_VERSION,
        "capture_adapter_status": capture_adapter_status,
        "capture_backend": CAPTURE_BACKEND,
        "recommended_route": RECOMMENDED_ROUTE,
        "screen_capturekit_status": SCREEN_CAPTUREKIT_STATUS,
        "audio_path": audio_path,
        "capture": capture,
        "audio_health": audio_health,
        "m2_go_evidence_status": "not_real_meeting_go_evidence",
        "privacy_cost_flags": _privacy_cost_flags(),
    }


def _capture_report(
    *,
    capture_status: str,
    audio_path: str,
    validation_errors: list[str],
    record_seconds: int,
    timeout_seconds: int,
    audio_device_index: int,
    audio_file_size_bytes: int,
    error_summary: str | None = None,
    ffmpeg_returncode: int | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "capture_status": capture_status,
        "audio_path": audio_path,
        "validation_errors": [_redact_process_text(error) for error in validation_errors],
        "record_seconds": record_seconds,
        "timeout_seconds": timeout_seconds,
        "audio_device_index": audio_device_index,
        "audio_file_size_bytes": audio_file_size_bytes,
        "privacy_cost_flags": _privacy_cost_flags(),
    }
    if error_summary is not None:
        report["error_summary"] = _redact_process_text(error_summary)
    if ffmpeg_returncode is not None:
        report["ffmpeg_returncode"] = ffmpeg_returncode
    return report


def _privacy_cost_flags() -> dict[str, bool]:
    return {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _summarize_stderr(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "process exited without stderr"
    return " | ".join(lines[-4:])[:800]


def _redact_process_text(text: str) -> str:
    redacted = re.sub(r"/Users/[^\s|\"']+", "<redacted_user_path>", text)
    redacted = re.sub(r"/private/var/[^\s|\"']+", "<redacted_temp_path>", redacted)
    redacted = redacted.replace("Voice" + "Memos", "<redacted_audio_source>")
    redacted = redacted.replace(".m4a", "<redacted_audio_extension>")
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())
