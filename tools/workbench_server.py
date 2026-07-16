#!/usr/bin/env python3
"""Start, stop, and inspect the local Meeting Copilot Workbench backend."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TextIO
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"
DEFAULT_PORT = 8765
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 8
STOP_TIMEOUT_SECONDS = GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS + 4
DEFAULT_RUN_ROOT = REPO_ROOT / "artifacts" / "tmp" / "workbench_server"
DEFAULT_DATA_DIR = REPO_ROOT / "artifacts" / "tmp" / "web_mvp_data"
DEFAULT_PID_FILE = DEFAULT_RUN_ROOT / "workbench_server.pid"
DEFAULT_LOG_FILE = DEFAULT_RUN_ROOT / "workbench_server.log"
SECRET_PROVIDER_ENV_KEYS = {
    "LLM_GATEWAY_BASE_URL",
    "LLM_GATEWAY_API_KEY",
    "LLM_GATEWAY_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
}


def build_uvicorn_command(*, port: int) -> list[str]:
    return [
        "uvicorn",
        "meeting_copilot_web_mvp.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--timeout-graceful-shutdown",
        str(GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS),
    ]


def build_child_env(*, data_dir: Path, provider_mode: str = "safe") -> dict[str, str]:
    env = dict(os.environ)
    if provider_mode == "safe":
        for key in SECRET_PROVIDER_ENV_KEYS:
            env.pop(key, None)
    elif provider_mode != "inherit":
        raise ValueError("provider_mode must be safe or inherit")

    existing_pythonpath = env.get("PYTHONPATH", "")
    pythonpath_parts = [str(WEB_BACKEND_ROOT), str(CORE_ROOT)]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    resolved_data_dir = (
        data_dir if data_dir.is_absolute() else REPO_ROOT / data_dir
    ).resolve()
    env["MEETING_COPILOT_DATA_DIR"] = str(resolved_data_dir)
    return env


def read_pid(pid_file: Path) -> int | None:
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def check_health(port: int, timeout_seconds: float = 1.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 - local-only URL.
            body = response.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return {"ok": True, "body": parsed}
    except (OSError, URLError) as exc:
        return {"ok": False, "error": str(exc)}


def status_report(*, port: int, pid_file: Path) -> dict[str, Any]:
    pid = read_pid(pid_file)
    health = check_health(port)
    running = bool(health.get("ok"))
    return {
        "status": "running" if running else "not_running",
        "port": port,
        "pid": pid,
        "pid_running": bool(pid and pid_running(pid)),
        "health_ok": running,
        "health": health,
        "health_url": f"http://127.0.0.1:{port}/health",
        "workbench_url": f"http://127.0.0.1:{port}/workbench",
        "pid_file": str(pid_file),
    }


def start_server(
    *,
    port: int,
    pid_file: Path,
    log_file: Path,
    data_dir: Path,
    provider_mode: str = "safe",
) -> dict[str, Any]:
    data_dir = (
        data_dir if data_dir.is_absolute() else REPO_ROOT / data_dir
    ).resolve()
    current = status_report(port=port, pid_file=pid_file)
    if current["health_ok"]:
        return {**current, "status": "already_running"}
    if is_port_open(port):
        return {
            "status": "blocked_port_in_use",
            "port": port,
            "health_url": f"http://127.0.0.1:{port}/health",
            "workbench_url": f"http://127.0.0.1:{port}/workbench",
            "safe_to_kill_existing_process": False,
            "reason": "port is open but /health is not a Meeting Copilot backend",
        }

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    command = build_uvicorn_command(port=port)
    env = build_child_env(data_dir=data_dir, provider_mode=provider_mode)
    log_handle = log_file.open("ab")
    process = subprocess.Popen(
        command,
        cwd=WEB_BACKEND_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_file.write_text(str(process.pid), encoding="utf-8")

    deadline = time.time() + 20
    health = {"ok": False, "error": "not checked"}
    while time.time() < deadline:
        health = check_health(port)
        if health.get("ok"):
            break
        if process.poll() is not None:
            break
        time.sleep(0.2)
    if not health.get("ok"):
        return {
            "status": "blocked_start_failed",
            "port": port,
            "pid": process.pid,
            "returncode": process.poll(),
            "health": health,
            "log_file": str(log_file),
            "pid_file": str(pid_file),
        }
    return {
        "status": "started",
        "port": port,
        "pid": process.pid,
        "health_ok": True,
        "health": health,
        "health_url": f"http://127.0.0.1:{port}/health",
        "workbench_url": f"http://127.0.0.1:{port}/workbench",
        "data_dir": str(data_dir),
        "log_file": str(log_file),
        "pid_file": str(pid_file),
        "provider_mode": provider_mode,
    }


def stop_server(
    *,
    pid_file: Path,
    timeout_seconds: float = STOP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    pid = read_pid(pid_file)
    if pid is None:
        return {"status": "not_running", "pid": None, "pid_file": str(pid_file)}
    if not pid_running(pid):
        pid_file.unlink(missing_ok=True)
        return {"status": "not_running", "pid": pid, "pid_file": str(pid_file)}
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not pid_running(pid):
            pid_file.unlink(missing_ok=True)
            return {"status": "stopped", "pid": pid, "pid_file": str(pid_file)}
        time.sleep(0.2)
    return {
        "status": "blocked_stop_timeout",
        "pid": pid,
        "pid_file": str(pid_file),
        "safe_to_force_kill": False,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "status"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_PID_FILE)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--provider-mode", choices=["safe", "inherit"], default="safe")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "status":
        report = status_report(port=args.port, pid_file=args.pid_file)
    elif args.command == "start":
        report = start_server(
            port=args.port,
            pid_file=args.pid_file,
            log_file=args.log_file,
            data_dir=args.data_dir,
            provider_mode=args.provider_mode,
        )
    else:
        report = stop_server(pid_file=args.pid_file)
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if not str(report.get("status", "")).startswith("blocked_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
