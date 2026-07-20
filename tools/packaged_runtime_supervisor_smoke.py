#!/usr/bin/env python3
"""Verify that a packaged macOS app starts and reaps its bundled backend."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import socket
import subprocess
import time
import struct
import wave
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/packaged_runtime_supervisor_smoke"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PORT_PATTERN = re.compile(r"(?:^|\s)--port\s+(\d+)(?:\s|$)")
PACKAGED_APP_PROCESS_PATTERN = re.compile(
    r"^(?P<binary>.+?Meeting Copilot\.app/Contents/MacOS/meeting-copilot-desktop)(?:\s|$)"
)


def packaged_app_launch_command(binary: Path) -> list[str]:
    """Prevent AppKit crash-history restoration prompts from blocking setup."""
    return [
        str(binary),
        "-ApplePersistenceIgnoreState",
        "YES",
        "-NSQuitAlwaysKeepsWindows",
        "NO",
    ]


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id contains unsafe characters")


def resolve_output_root(repo_root: Path, output_root: Path) -> Path:
    resolved = output_root.resolve() if output_root.is_absolute() else (repo_root / output_root).resolve()
    approved = (repo_root / "artifacts/tmp").resolve()
    try:
        resolved.relative_to(approved)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def parse_process_table(output: str) -> list[dict[str, Any]]:
    processes = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(.+)$", line)
        if not match:
            continue
        processes.append({
            "pid": int(match.group(1)),
            "ppid": int(match.group(2)),
            "command": match.group(3),
        })
    return processes


def find_conflicting_app_instances(
    processes: list[dict[str, Any]], *, app_path: Path
) -> list[dict[str, Any]]:
    candidate_binary = (
        app_path / "Contents/MacOS/meeting-copilot-desktop"
    ).expanduser().resolve(strict=False)
    conflicts: list[dict[str, Any]] = []
    for process in processes:
        match = PACKAGED_APP_PROCESS_PATTERN.match(str(process.get("command") or ""))
        if match is None:
            continue
        observed_binary = Path(match.group("binary")).expanduser().resolve(strict=False)
        conflicts.append(
            {
                "pid": int(process["pid"]),
                "same_candidate": observed_binary == candidate_binary,
            }
        )
    return conflicts


def backend_executable_from_manifest(app_path: Path) -> str:
    manifest_path = app_path / "Contents/Resources/MeetingCopilotRuntime.bundle/runtime-bundle-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "meeting_copilot.runtime_bundle.v1":
        raise ValueError("runtime bundle manifest schema is invalid")
    executable = str((payload.get("runtimes") or {}).get("backend", {}).get("executable") or "")
    candidate = Path(executable)
    if not executable or candidate.is_absolute() or ".." in candidate.parts or "\\" in executable:
        raise ValueError("runtime bundle backend executable is unsafe")
    return candidate.as_posix()


def find_backend_process(processes: list[dict[str, Any]], *, app_pid: int, app_path: Path) -> dict[str, Any] | None:
    app_marker = str(app_path / "Contents/Resources/MeetingCopilotRuntime.bundle")
    backend_executable = backend_executable_from_manifest(app_path)
    for process in processes:
        command = str(process["command"])
        port_match = PORT_PATTERN.search(command)
        if (
            int(process["ppid"]) == app_pid
            and app_marker in command
            and backend_executable in command
            and "-m uvicorn" in command
            and port_match is not None
        ):
            return {**process, "port": int(port_match.group(1))}
    return None


def find_funasr_process(
    processes: list[dict[str, Any]],
    *,
    backend_pid: int,
    app_path: Path,
) -> dict[str, Any] | None:
    runtime_marker = str(app_path / "Contents/Resources/MeetingCopilotRuntime.bundle")
    for process in processes:
        command = str(process["command"])
        if (
            int(process["ppid"]) == backend_pid
            and runtime_marker in command
            and "funasr_stream_worker.py" in command
            and "--resident" in command
        ):
            return process
    return None


def read_process_table() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return parse_process_table(completed.stdout)


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def http_response(port: int, path: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read(1024 * 1024)
        return {
            "status": response.status,
            "headers": {key.lower(): value for key, value in response.getheaders()},
            "body": body,
        }
    except OSError:
        return {"status": None, "headers": {}, "body": b""}
    finally:
        connection.close()


def health_proof(token: str) -> str:
    return hmac.new(token.encode("utf-8"), b"meeting-copilot-health-v1", hashlib.sha256).hexdigest()


def bootstrap_cookie(port: int, token: str) -> tuple[int | None, str | None]:
    response = http_response(port, f"/desktop/bootstrap?token={token}")
    raw_cookie = str(response["headers"].get("set-cookie") or "")
    cookie = raw_cookie.split(";", 1)[0] if raw_cookie else None
    return response["status"], cookie


def request_json(
    port: int,
    path: str,
    cookie: str,
    payload: dict[str, Any],
    *,
    method: str = "POST",
) -> dict[str, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        body = json.dumps(payload).encode("utf-8")
        connection.request(
            method,
            path,
            body=body,
            headers={
                "Cookie": cookie,
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
            },
        )
        response = connection.getresponse()
        raw = response.read(1024 * 1024)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        return {"status": response.status, "body": parsed}
    except OSError:
        return {"status": None, "body": None}
    finally:
        connection.close()


def post_json(port: int, path: str, cookie: str, payload: dict[str, Any]) -> dict[str, Any]:
    return request_json(port, path, cookie, payload)


def meeting_preparation_payload() -> dict[str, Any]:
    return {
        "hotwords": ["P99", "SLO", "灰度", "回滚"],
        "input_source": "microphone",
        "input_device_id": "packaged-smoke-fixture",
        "input_device_name": "Packaged synthetic acceptance fixture",
        "notice_acknowledged": True,
    }


def pcm_float32_chunks(wav_path: Path, *, chunk_samples: int = 4_800, max_seconds: float = 30.0):
    with wave.open(str(wav_path), "rb") as handle:
        if handle.getsampwidth() != 2 or handle.getnchannels() != 1 or handle.getframerate() != 16_000:
            raise ValueError("packaged ASR fixture must be 16-bit mono 16kHz PCM WAV")
        remaining = min(handle.getnframes(), int(max_seconds * handle.getframerate()))
        while remaining > 0:
            raw = handle.readframes(min(chunk_samples, remaining))
            if not raw:
                break
            values = struct.unpack("<" + "h" * (len(raw) // 2), raw)
            yield b"".join(struct.pack("<f", value / 32768.0) for value in values)
            remaining -= len(values)


def pcm_float32_duration_seconds(chunk: bytes, *, sample_rate_hz: int = 16_000) -> float:
    if len(chunk) % 4 != 0:
        raise ValueError("float32 PCM chunk size must be divisible by four")
    return (len(chunk) // 4) / sample_rate_hz


def stream_packaged_funasr(
    port: int,
    *,
    meeting_id: str,
    cookie: str,
    audio_path: Path,
    audio_source: str = "packaged_synthetic_fixture",
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    import websocket

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", audio_source):
        raise ValueError("audio_source contains unsafe characters")
    ws_url = f"ws://127.0.0.1:{port}/live/asr/stream/ws/{meeting_id}?audio_source={audio_source}"
    ws = websocket.create_connection(
        ws_url,
        cookie=cookie,
        origin=f"http://127.0.0.1:{port}",
        timeout=timeout_seconds,
    )
    events: list[dict[str, Any]] = []
    ready = False
    finals: list[dict[str, Any]] = []
    rejected = False
    transport_error: str | None = None
    post_end_quiet_seconds = 2.0
    ready_wait_started = time.monotonic()
    ready_wait_ms: float | None = None

    def drain(timeout: float) -> None:
        nonlocal ready, rejected
        ws.settimeout(timeout)
        while True:
            try:
                raw = ws.recv()
            except Exception:
                return
            if not isinstance(raw, str):
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events.append(event)
            event_type = str(event.get("event_type") or "")
            if event_type == "asr_ready" and event.get("ready") is True:
                ready = True
                return
            if event_type == "final" and str(event.get("text") or event.get("normalized_text") or "").strip():
                finals.append(event)
            if event_type in {"provider_error", "recording_rejected", "error"}:
                rejected = True
                return

    try:
        ready_deadline = time.monotonic() + min(45.0, timeout_seconds)
        while not ready and not rejected and time.monotonic() < ready_deadline:
            drain(min(0.5, max(0.01, ready_deadline - time.monotonic())))
        ready_wait_ms = round((time.monotonic() - ready_wait_started) * 1_000, 2)
        for chunk in pcm_float32_chunks(audio_path):
            if not ready or rejected:
                break
            chunk_started = time.monotonic()
            try:
                ws.send_binary(chunk)
            except OSError as exc:
                transport_error = type(exc).__name__
                break
            drain(0.05)
            remaining_pacing = pcm_float32_duration_seconds(chunk) - (
                time.monotonic() - chunk_started
            )
            if remaining_pacing > 0:
                time.sleep(remaining_pacing)
        if not rejected and transport_error is None:
            try:
                ws.send("END")
            except OSError as exc:
                transport_error = type(exc).__name__
        deadline = time.monotonic() + timeout_seconds
        last_event_at = time.monotonic()
        while time.monotonic() < deadline and not rejected and transport_error is None:
            event_count = len(events)
            drain(min(0.5, max(0.01, deadline - time.monotonic())))
            if len(events) != event_count:
                last_event_at = time.monotonic()
            if finals and time.monotonic() - last_event_at >= post_end_quiet_seconds:
                break
    finally:
        ws.close()
    return {
        "ready": ready,
        "ready_wait_ms": ready_wait_ms,
        "events": events,
        "non_empty_final_count": len(finals),
        "non_empty_final_texts": [str(event.get("normalized_text") or event.get("text") or "") for event in finals],
        "rejected": rejected,
        "transport_error": transport_error,
    }


def port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def smoke_packaged_app(
    *,
    repo_root: Path,
    app_path: Path,
    output_root: Path,
    run_id: str,
    startup_timeout_seconds: float = 60.0,
    cleanup_timeout_seconds: float = 10.0,
    audio_path: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    app_path = app_path.resolve()
    output_root = resolve_output_root(repo_root, output_root)
    validate_run_id(run_id)
    binary = app_path / "Contents/MacOS/meeting-copilot-desktop"
    if not binary.is_file():
        raise FileNotFoundError(binary)

    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()
    conflicting_instances = find_conflicting_app_instances(
        read_process_table(), app_path=app_path
    )
    if conflicting_instances:
        evidence = {
            "schema_version": "meeting_copilot.packaged_runtime_supervisor_smoke.v1",
            "run_id": run_id,
            "host_platform": platform.platform(),
            "architecture": platform.machine(),
            "app_path": str(app_path.relative_to(repo_root)),
            "app_pid": None,
            "backend_pid": None,
            "backend_port": None,
            "funasr_worker_pid": None,
            "preflight": {
                "conflicting_app_instance_count": len(conflicting_instances),
                "conflicting_app_instances": conflicting_instances,
            },
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "decision": {
                "status": "no_go_conflicting_app_instance",
                "counts_as_packaged_runtime_supervisor_evidence": False,
                "counts_as_packaged_mainline_evidence": False,
                "counts_as_public_release_package": False,
            },
            "privacy_cost_flags": {
                "remote_service_called": False,
                "remote_asr_called": False,
                "llm_called": False,
                "user_audio_read": False,
            },
        }
        evidence_path = run_root / "evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return evidence | {"evidence_path": str(evidence_path.relative_to(repo_root))}
    smoke_token = secrets.token_hex(32)
    environment = dict(os.environ)
    environment.update({
        "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE": "1",
        "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE": smoke_token,
    })
    app_process = subprocess.Popen(
        packaged_app_launch_command(binary),
        cwd=app_path.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    backend: dict[str, Any] | None = None
    responses: dict[str, int | None] = {}
    bootstrap_authenticated = False
    health_identity_verified = False
    resident_ready = False
    asr_stream: dict[str, Any] | None = None
    asr_provider: str | None = None
    app_exited = False
    backend_exited = False
    port_closed = False
    funasr_process: dict[str, Any] | None = None
    funasr_exited = False
    funasr_forced_cleanup = False
    try:
        deadline = time.monotonic() + startup_timeout_seconds
        while time.monotonic() < deadline:
            if app_process.poll() is not None:
                break
            backend = find_backend_process(
                read_process_table(),
                app_pid=app_process.pid,
                app_path=app_path,
            )
            if backend:
                health = http_response(int(backend["port"]), "/health")
                if health["status"] == 200 and health_proof(smoke_token).encode("ascii") in health["body"]:
                    health_identity_verified = True
                    break
            time.sleep(0.1)
        if backend is not None:
            port = int(backend["port"])
            health = http_response(port, "/health")
            bootstrap_status, cookie = bootstrap_cookie(port, smoke_token)
            request_headers = {"Cookie": cookie} if cookie else {}
            bootstrap_authenticated = bootstrap_status == 303 and bool(cookie)
            runtime_status = http_response(port, "/providers/asr/runtime", request_headers)
            if runtime_status["status"] == 200:
                try:
                    runtime_payload = json.loads(runtime_status["body"].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    runtime_payload = {}
                resident = dict(runtime_payload.get("resident") or {})
                resident_ready = resident.get("process_ready") is True
                funasr_process = find_funasr_process(
                    read_process_table(),
                    backend_pid=int(backend["pid"]),
                    app_path=app_path,
                )
            responses = {
                "health": health["status"],
                "bootstrap": bootstrap_status,
                "workbench": http_response(port, "/workbench", request_headers)["status"],
                "providers": http_response(port, "/providers/health", request_headers)["status"],
                "asr_runtime": runtime_status["status"],
            }
            if bootstrap_authenticated and resident_ready and audio_path is not None:
                meeting_id = f"packaged_smoke_{run_id}"
                created = post_json(
                    port,
                    "/v2/meetings",
                    cookie or "",
                    {"meeting_id": meeting_id, "expected_duration_seconds": 60, "track_count": 1},
                )
                if created["status"] == 201:
                    prepared = request_json(
                        port,
                        f"/v2/meetings/{meeting_id}/preparation",
                        cookie or "",
                        meeting_preparation_payload(),
                        method="PUT",
                    )
                    responses["preparation"] = prepared["status"]
                    if prepared["status"] == 200:
                        asr_stream = stream_packaged_funasr(
                            port,
                            meeting_id=meeting_id,
                            cookie=cookie or "",
                            audio_path=audio_path,
                        )
                    record = http_response(port, f"/live/asr/sessions/{meeting_id}/events", request_headers)
                    if record["status"] == 200:
                        try:
                            record_payload = json.loads(record["body"].decode("utf-8"))
                            asr_provider = str(record_payload.get("provider") or "")
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            asr_provider = None
        app_process.send_signal(signal.SIGTERM)
        try:
            app_process.wait(timeout=cleanup_timeout_seconds)
            app_exited = True
        except subprocess.TimeoutExpired:
            app_process.kill()
            app_process.wait(timeout=5)
        cleanup_deadline = time.monotonic() + cleanup_timeout_seconds
        while time.monotonic() < cleanup_deadline and backend is not None:
            backend_exited = not pid_exists(int(backend["pid"]))
            port_closed = not port_is_listening(int(backend["port"]))
            funasr_exited = bool(
                funasr_process is not None
                and not pid_exists(int(funasr_process["pid"]))
            )
            if backend_exited and port_closed and funasr_exited:
                break
            time.sleep(0.1)
    finally:
        if app_process.poll() is None:
            app_process.kill()
            app_process.wait(timeout=5)
        if backend is not None and pid_exists(int(backend["pid"])):
            os.kill(int(backend["pid"]), signal.SIGTERM)
        if funasr_process is not None and pid_exists(int(funasr_process["pid"])):
            funasr_forced_cleanup = True
            os.kill(int(funasr_process["pid"]), signal.SIGTERM)
            cleanup_deadline = time.monotonic() + 3
            while pid_exists(int(funasr_process["pid"])) and time.monotonic() < cleanup_deadline:
                time.sleep(0.05)
            if pid_exists(int(funasr_process["pid"])):
                os.kill(int(funasr_process["pid"]), signal.SIGKILL)
            funasr_exited = not pid_exists(int(funasr_process["pid"]))

    passed = (
        backend is not None
        and responses == {
            "health": 200,
            "bootstrap": 303,
            "workbench": 200,
            "providers": 200,
            "asr_runtime": 200,
            **({"preparation": 200} if audio_path is not None else {}),
        }
        and health_identity_verified
        and bootstrap_authenticated
        and resident_ready
        and asr_stream is not None
        and asr_stream.get("ready") is True
        and asr_stream.get("non_empty_final_count", 0) > 0
        and asr_provider == "funasr_realtime"
        and app_exited
        and backend_exited
        and port_closed
        and funasr_process is not None
        and funasr_exited
        and not funasr_forced_cleanup
    )
    evidence = {
        "schema_version": "meeting_copilot.packaged_runtime_supervisor_smoke.v1",
        "run_id": run_id,
        "host_platform": platform.platform(),
        "architecture": platform.machine(),
        "app_path": str(app_path.relative_to(repo_root)),
        "app_pid": app_process.pid,
        "backend_pid": backend.get("pid") if backend else None,
        "backend_port": backend.get("port") if backend else None,
        "funasr_worker_pid": funasr_process.get("pid") if funasr_process else None,
        "responses": responses,
        "health_identity_verified": health_identity_verified,
        "bootstrap_authenticated": bootstrap_authenticated,
        "resident_ready": resident_ready,
        "asr_stream": asr_stream,
        "asr_provider": asr_provider,
        "audio_fixture": str(audio_path) if audio_path is not None else None,
        "app_exited_after_sigterm": app_exited,
        "backend_exited_after_parent": backend_exited,
        "backend_port_closed": port_closed,
        "funasr_worker_exited_after_parent": funasr_exited,
        "funasr_worker_forced_cleanup": funasr_forced_cleanup,
        "duration_seconds": round(time.monotonic() - started_at, 3),
        "decision": {
            "status": (
                "go_packaged_runtime_supervisor_smoke_not_public_release"
                if passed
                else "no_go_packaged_runtime_supervisor_smoke"
            ),
            "counts_as_packaged_runtime_supervisor_evidence": passed,
            "counts_as_packaged_mainline_evidence": False,
            "counts_as_public_release_package": False,
        },
        "privacy_cost_flags": {
            "remote_service_called": False,
            "remote_asr_called": False,
            "llm_called": False,
            "user_audio_read": False,
        },
    }
    evidence_path = run_root / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence | {"evidence_path": str(evidence_path.relative_to(repo_root))}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--app-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--audio-path", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = smoke_packaged_app(
        repo_root=args.repo_root,
        app_path=args.app_path,
        output_root=args.output_root,
        run_id=args.run_id,
        audio_path=args.audio_path,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"]["counts_as_packaged_runtime_supervisor_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
