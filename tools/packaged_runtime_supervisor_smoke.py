#!/usr/bin/env python3
"""Verify that a packaged macOS app starts and reaps its bundled backend."""

from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import subprocess
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/packaged_runtime_supervisor_smoke"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PORT_PATTERN = re.compile(r"(?:^|\s)--port\s+(\d+)(?:\s|$)")


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


def find_backend_process(processes: list[dict[str, Any]], *, app_pid: int, app_path: Path) -> dict[str, Any] | None:
    app_marker = str(app_path / "Contents/Resources/MeetingCopilotRuntime.bundle")
    for process in processes:
        command = str(process["command"])
        port_match = PORT_PATTERN.search(command)
        if (
            int(process["ppid"]) == app_pid
            and app_marker in command
            and "backend-python/bin/python3.12" in command
            and "-m uvicorn" in command
            and port_match is not None
        ):
            return {**process, "port": int(port_match.group(1))}
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


def http_status(port: int, path: str) -> int | None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path)
        return connection.getresponse().status
    except OSError:
        return None
    finally:
        connection.close()


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
    app_process = subprocess.Popen(
        [str(binary)],
        cwd=app_path.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    backend: dict[str, Any] | None = None
    responses: dict[str, int | None] = {}
    app_exited = False
    backend_exited = False
    port_closed = False
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
            if backend and http_status(int(backend["port"]), "/health") == 200:
                break
            time.sleep(0.1)
        if backend is not None:
            port = int(backend["port"])
            responses = {
                "health": http_status(port, "/health"),
                "workbench": http_status(port, "/workbench"),
                "providers": http_status(port, "/providers/health"),
            }
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
            if backend_exited and port_closed:
                break
            time.sleep(0.1)
    finally:
        if app_process.poll() is None:
            app_process.kill()
            app_process.wait(timeout=5)
        if backend is not None and pid_exists(int(backend["pid"])):
            os.kill(int(backend["pid"]), signal.SIGTERM)

    passed = (
        backend is not None
        and responses == {"health": 200, "workbench": 200, "providers": 200}
        and app_exited
        and backend_exited
        and port_closed
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
        "responses": responses,
        "app_exited_after_sigterm": app_exited,
        "backend_exited_after_parent": backend_exited,
        "backend_port_closed": port_closed,
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = smoke_packaged_app(
        repo_root=args.repo_root,
        app_path=args.app_path,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"]["counts_as_packaged_runtime_supervisor_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
