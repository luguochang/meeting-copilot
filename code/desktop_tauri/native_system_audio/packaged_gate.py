#!/usr/bin/env python3
"""Run the real packaged ScreenCaptureKit helper; script stand-ins never pass."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
from typing import Any


EXPECTED_APP_IDENTIFIER = "com.meetingcopilot.desktop"
HELPER_RELATIVE = Path(
    "Contents/Resources/MeetingCopilotRuntime.bundle/bin/meeting-copilot-native-system-audio"
)
MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}
PROTOCOL_TIMEOUT_SECONDS = 60
AUDIBLE_RMS_THRESHOLD = 0.001


def inspect_packaged_helper(app_path: Path) -> dict[str, Any]:
    app = app_path.resolve()
    if app.name != "Meeting Copilot.app" or not app.is_dir():
        raise ValueError("packaged gate requires Meeting Copilot.app")
    info_path = app / "Contents/Info.plist"
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError(f"packaged app Info.plist is invalid: {exc}") from exc
    if info.get("CFBundleIdentifier") != EXPECTED_APP_IDENTIFIER:
        raise ValueError("packaged app identity is not com.meetingcopilot.desktop")

    helper = app / HELPER_RELATIVE
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise ValueError("packaged system-audio helper is missing or not executable")
    try:
        helper.resolve().relative_to(app)
    except ValueError as exc:
        raise ValueError("packaged system-audio helper escapes the app bundle") from exc
    if helper.read_bytes()[:4] not in MACHO_MAGICS:
        raise ValueError("packaged system-audio helper must be a real Mach-O executable")

    try:
        completed = subprocess.run(
            [str(helper), "--describe"],
            cwd=app,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=PROTOCOL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("packaged system-audio helper protocol timed out") from exc
    if completed.returncode != 0:
        raise ValueError(f"packaged system-audio helper protocol failed: {completed.stderr[-500:]}")
    try:
        protocol = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("packaged system-audio helper protocol is invalid") from exc
    if protocol.get("schema_version") != "meeting_copilot.native_system_audio_protocol.v1":
        raise ValueError("packaged system-audio helper protocol schema is invalid")
    if protocol.get("accepts_remote_websocket") is not False:
        raise ValueError("packaged system-audio helper does not enforce the no-upload boundary")
    return {"app_path": app, "helper_path": helper, "protocol": protocol}


def _last_json_line(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def run_packaged_gate(
    app_path: Path,
    *,
    duration_seconds: float,
    display_id: int | None,
    request_permission: bool,
) -> dict[str, Any]:
    inspected = inspect_packaged_helper(app_path)
    command = [
        str(inspected["helper_path"]),
        "--probe",
        "--duration",
        str(duration_seconds),
        "--request-permission" if request_permission else "--no-request-permission",
    ]
    if display_id is not None:
        command.extend(["--display-id", str(display_id)])
    with tempfile.TemporaryDirectory(prefix="meeting-copilot-system-audio-gate-") as home:
        completed = subprocess.run(
            command,
            cwd=inspected["app_path"],
            env={
                "HOME": home,
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
            capture_output=True,
            text=True,
            timeout=duration_seconds + 120,
            check=False,
        )
    probe = _last_json_line(completed.stdout)
    error = _last_json_line(completed.stderr)
    passed = bool(
        completed.returncode == 0
        and probe
        and probe.get("schema_version") == "meeting_copilot.native_system_audio_probe.v1"
        and probe.get("permission") == "authorized"
        and int(probe.get("pcm_event_count") or 0) > 0
        and int(probe.get("frames") or 0) > 0
        and float(probe.get("peak_rms") or 0) >= AUDIBLE_RMS_THRESHOLD
        and probe.get("raw_audio_files_written") is False
        and probe.get("remote_upload_attempted") is False
    )
    return {
        "schema_version": "meeting_copilot.packaged_system_audio_gate.v1",
        "status": "passed" if passed else "blocked",
        "app_identifier": EXPECTED_APP_IDENTIFIER,
        "helper_relative_path": HELPER_RELATIVE.as_posix(),
        "helper_is_macho": True,
        "protocol": inspected["protocol"],
        "probe": probe,
        "error": error,
        "return_code": completed.returncode,
        "counts_as_real_packaged_helper_capture": passed,
        "counts_as_tauri_ipc_backend_asr_recording_gate": False,
        "raw_audio_file_created": False,
        "audible_rms_threshold": AUDIBLE_RMS_THRESHOLD,
        "mock_or_script_helper_accepted": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-path", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--display-id", type=int)
    parser.add_argument("--request-permission", action="store_true")
    parser.add_argument("--evidence", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    evidence = run_packaged_gate(
        args.app_path,
        duration_seconds=args.duration,
        display_id=args.display_id,
        request_permission=args.request_permission,
    )
    serialized = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if evidence["counts_as_real_packaged_helper_capture"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
