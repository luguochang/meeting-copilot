#!/usr/bin/env python3
"""Run a packaged real-microphone helper against the packaged local backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import subprocess
import time
from typing import Any

from packaged_runtime_supervisor_smoke import (
    bootstrap_cookie,
    find_backend_process,
    health_proof,
    http_response,
    pid_exists,
    port_is_listening,
    post_json,
    read_process_table,
    resolve_output_root,
    validate_run_id,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/packaged_native_mic_smoke"


def native_helper_path(app_path: Path) -> Path:
    return (
        app_path
        / "Contents/Resources/MeetingCopilotRuntime.bundle/bin/meeting-copilot-native-mic"
    )


def native_helper_command(
    *, helper: Path, ws_url: str, meeting_id: str, ready_file: Path, duration_seconds: float
) -> list[str]:
    return [
        str(helper),
        "--ws-url",
        ws_url,
        "--session-id",
        meeting_id,
        "--ready-file",
        str(ready_file),
        "--duration",
        str(duration_seconds),
    ]


def native_helper_environment(*, home: Path, cookie: str) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "MEETING_COPILOT_SESSION_COOKIE": cookie,
    }


def _json_response(port: int, path: str, cookie: str) -> tuple[int | None, dict[str, Any]]:
    response = http_response(port, path, {"Cookie": cookie})
    try:
        payload = json.loads(response["body"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return response["status"], payload if isinstance(payload, dict) else {}


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_native_helper(
    *,
    app_path: Path,
    run_root: Path,
    port: int,
    cookie: str,
    meeting_id: str,
    duration_seconds: float,
    playback_audio: Path | None = None,
    ready_timeout_seconds: float = 35.0,
) -> dict[str, Any]:
    helper = native_helper_path(app_path)
    if not helper.is_file():
        raise FileNotFoundError(helper)
    if playback_audio is not None and not playback_audio.is_file():
        raise FileNotFoundError(playback_audio)
    ready_file = run_root / "native-mic-ready.json"
    stdout_path = run_root / "native-mic.stdout.log"
    stderr_path = run_root / "native-mic.stderr.log"
    home = run_root / "native-mic-home"
    home.mkdir(parents=True, exist_ok=True)
    command = native_helper_command(
        helper=helper,
        ws_url=f"ws://127.0.0.1:{port}/live/asr/stream/ws/{meeting_id}?audio_source=packaged_native_mic_smoke",
        meeting_id=meeting_id,
        ready_file=ready_file,
        duration_seconds=duration_seconds,
    )
    started_at = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=native_helper_environment(home=home, cookie=cookie),
            start_new_session=True,
        )
        deadline = time.monotonic() + ready_timeout_seconds
        while time.monotonic() < deadline and process.poll() is None and not ready_file.is_file():
            time.sleep(0.1)
        ready_payload: dict[str, Any] = {}
        if ready_file.is_file():
            try:
                parsed = json.loads(ready_file.read_text(encoding="utf-8"))
                ready_payload = parsed if isinstance(parsed, dict) else {}
            except (OSError, json.JSONDecodeError):
                ready_payload = {}
        ready = (
            ready_payload.get("status") == "ready"
            and ready_payload.get("session_id") == meeting_id
            and ready_payload.get("sample_rate_hz") == 16_000
            and ready_payload.get("channels") == 1
            and ready_payload.get("sample_format") == "pcm_f32le"
        )
        playback_process: subprocess.Popen[bytes] | None = None
        if ready and playback_audio is not None:
            playback_process = subprocess.Popen(
                ["/usr/bin/afplay", str(playback_audio)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if not ready and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=duration_seconds + 20.0 if ready else 10.0)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if playback_process is not None and playback_process.poll() is None:
            playback_process.terminate()
            playback_process.wait(timeout=5)

    return {
        "helper_path": str(helper),
        "command_without_credentials": command,
        "pid": process.pid,
        "ready": ready,
        "ready_payload": ready_payload,
        "exit_code": process.returncode,
        "duration_seconds": round(time.monotonic() - started_at, 3),
        "stdout_tail": _tail(stdout_path),
        "stderr_tail": _tail(stderr_path),
        "credential_in_command": cookie in " ".join(command),
        "playback_audio": (
            {
                "path": str(playback_audio),
                "sha256": _sha256(playback_audio),
                "source": "controlled_synthetic_speaker_playback_into_real_microphone",
            }
            if playback_audio is not None
            else None
        ),
    }


def smoke_packaged_native_mic(
    *,
    repo_root: Path,
    app_path: Path,
    output_root: Path,
    run_id: str,
    duration_seconds: float = 30.0,
    playback_audio: Path | None = None,
    startup_timeout_seconds: float = 60.0,
    projection_timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    app_path = app_path.resolve()
    output_root = resolve_output_root(repo_root, output_root)
    validate_run_id(run_id)
    if not 5.0 <= duration_seconds <= 300.0:
        raise ValueError("duration_seconds must be between 5 and 300")
    binary = app_path / "Contents/MacOS/meeting-copilot-desktop"
    if not binary.is_file():
        raise FileNotFoundError(binary)

    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    token = secrets.token_hex(32)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().endswith("_API_KEY")
        and key.upper() not in {"AUTHORIZATION", "MEETING_COPILOT_LOCAL_API_TOKEN"}
    }
    environment.update(
        {
            "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE": "1",
            "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE": token,
        }
    )
    app_process = subprocess.Popen(
        [str(binary)],
        cwd=app_path.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    backend: dict[str, Any] | None = None
    helper_result: dict[str, Any] | None = None
    responses: dict[str, int | None] = {}
    projection: dict[str, Any] = {}
    app_exited = False
    backend_exited = False
    port_closed = False
    started_at = time.monotonic()
    try:
        deadline = time.monotonic() + startup_timeout_seconds
        while time.monotonic() < deadline and app_process.poll() is None:
            backend = find_backend_process(
                read_process_table(), app_pid=app_process.pid, app_path=app_path
            )
            if backend is not None:
                health = http_response(int(backend["port"]), "/health")
                if health["status"] == 200 and health_proof(token).encode("ascii") in health["body"]:
                    break
            time.sleep(0.1)
        if backend is None:
            raise RuntimeError("packaged backend did not become ready")

        port = int(backend["port"])
        bootstrap_status, cookie = bootstrap_cookie(port, token)
        if bootstrap_status != 303 or not cookie:
            raise RuntimeError("packaged bootstrap authentication failed")
        meeting_id = f"native_mic_{run_id}"
        created = post_json(
            port,
            "/v2/meetings",
            cookie,
            {"meeting_id": meeting_id, "expected_duration_seconds": 300, "track_count": 1},
        )
        responses["create_meeting"] = created["status"]
        if created["status"] != 201:
            raise RuntimeError("native microphone smoke meeting creation failed")

        helper_result = run_native_helper(
            app_path=app_path,
            run_root=run_root,
            port=port,
            cookie=cookie,
            meeting_id=meeting_id,
            duration_seconds=duration_seconds,
            playback_audio=playback_audio,
        )
        ended = post_json(
            port,
            f"/v2/meetings/{meeting_id}/end",
            cookie,
            {"action": "end_and_review"},
        )
        responses["end_meeting"] = ended["status"]

        projection_deadline = time.monotonic() + projection_timeout_seconds
        while time.monotonic() < projection_deadline:
            snapshot_status, snapshot = _json_response(
                port, f"/v2/meetings/{meeting_id}/snapshot", cookie
            )
            transcript_status, transcript = _json_response(
                port,
                f"/v2/meetings/{meeting_id}/transcript?after_transcript_seq=0&limit=500",
                cookie,
            )
            audio_status, audio = _json_response(
                port, f"/v2/meetings/{meeting_id}/audio", cookie
            )
            legacy_status, legacy = _json_response(
                port, f"/live/asr/sessions/{meeting_id}/events", cookie
            )
            segments = list(transcript.get("segments") or [])
            audio_chunks = int(audio.get("chunk_count") or 0)
            projection = {
                "meeting_id": meeting_id,
                "snapshot_status": snapshot_status,
                "transcript_status": transcript_status,
                "audio_status": audio_status,
                "legacy_events_status": legacy_status,
                "segment_count": len(segments),
                "transcript_texts": [
                    str(segment.get("normalized_text") or segment.get("text") or "")
                    for segment in segments
                ],
                "suggestion_count": len(list(snapshot.get("suggestions") or [])),
                "audio": audio,
                "legacy_event_types": [
                    str(event.get("event_type") or "") for event in list(legacy.get("events") or [])
                ],
            }
            if len(segments) > 0 and audio_chunks > 0:
                break
            time.sleep(0.5)
        responses.update(
            {
                "snapshot": projection.get("snapshot_status"),
                "transcript": projection.get("transcript_status"),
                "audio": projection.get("audio_status"),
                "legacy_events": projection.get("legacy_events_status"),
            }
        )
    finally:
        if app_process.poll() is None:
            os.killpg(app_process.pid, signal.SIGTERM)
            try:
                app_process.wait(timeout=15)
                app_exited = True
            except subprocess.TimeoutExpired:
                os.killpg(app_process.pid, signal.SIGKILL)
                app_process.wait(timeout=5)
        else:
            app_exited = True
        cleanup_deadline = time.monotonic() + 15
        while backend is not None and time.monotonic() < cleanup_deadline:
            backend_exited = not pid_exists(int(backend["pid"]))
            port_closed = not port_is_listening(int(backend["port"]))
            if backend_exited and port_closed:
                break
            time.sleep(0.1)

    audio = dict(projection.get("audio") or {})
    passed = (
        helper_result is not None
        and helper_result.get("ready") is True
        and helper_result.get("exit_code") == 0
        and helper_result.get("credential_in_command") is False
        and responses.get("create_meeting") == 201
        and responses.get("end_meeting") in {200, 202}
        and responses.get("snapshot") == 200
        and responses.get("transcript") == 200
        and responses.get("audio") == 200
        and int(projection.get("segment_count") or 0) > 0
        and int(audio.get("chunk_count") or 0) > 0
        and int(audio.get("file_size_bytes") or 0) > 0
        and app_exited
        and backend_exited
        and port_closed
    )
    evidence = {
        "schema_version": "meeting_copilot.packaged_native_mic_smoke.v1",
        "run_id": run_id,
        "host_platform": platform.platform(),
        "architecture": platform.machine(),
        "app_path": str(app_path.relative_to(repo_root)),
        "app_pid": app_process.pid,
        "backend_pid": backend.get("pid") if backend else None,
        "backend_port": backend.get("port") if backend else None,
        "duration_seconds": round(time.monotonic() - started_at, 3),
        "requested_capture_seconds": duration_seconds,
        "helper": helper_result,
        "responses": responses,
        "projection": projection,
        "cleanup": {
            "app_exited": app_exited,
            "backend_exited": backend_exited,
            "backend_port_closed": port_closed,
        },
        "decision": {
            "status": (
                "go_packaged_real_native_mic_helper_not_ui_not_public_release"
                if passed
                else "no_go_packaged_real_native_mic_helper"
            ),
            "counts_as_real_native_microphone_evidence": passed,
            "counts_as_tauri_ipc_evidence": False,
            "counts_as_ui_evidence": False,
            "counts_as_public_release_package": False,
        },
        "privacy_cost_flags": {
            "remote_asr_called": False,
            "remote_llm_called": False,
            "user_private_recording_read": False,
            "configs_local_read": False,
        },
    }
    evidence_path = run_root / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence | {"evidence_path": str(evidence_path.relative_to(repo_root))}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--app-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--playback-audio", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = smoke_packaged_native_mic(
        repo_root=args.repo_root,
        app_path=args.app_path,
        output_root=args.output_root,
        run_id=args.run_id,
        duration_seconds=args.duration_seconds,
        playback_audio=args.playback_audio,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"]["counts_as_real_native_microphone_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
