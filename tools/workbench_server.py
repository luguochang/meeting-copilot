#!/usr/bin/env python3
"""Start, stop, and inspect the local Meeting Copilot Workbench backend."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import signal
import socket
import subprocess
import sys
import time
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from typing import Any, TextIO


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
FRONTEND_V2_DIST_ROOT = REPO_ROOT / "code" / "web_mvp" / "frontend_v2" / "dist"
FRONTEND_V2_INDEX = FRONTEND_V2_DIST_ROOT / "index.html"
RUNTIME_IDENTITY_SCHEMA = "meeting_copilot.workbench_runtime_identity.v1"
APPLICATION_SCHEMA_VERSION = "application-schema-migration-report.v1"
HEALTH_SERVICE = "meeting-copilot-web-mvp"
MAX_JSON_PROBE_BYTES = 64 * 1024
MAX_WORKBENCH_PROBE_BYTES = 2 * 1024 * 1024
MAX_IDENTITY_RECORD_BYTES = 16 * 1024
RUNTIME_SOURCE_ROOTS = (
    ("backend", WEB_BACKEND_ROOT / "meeting_copilot_web_mvp", frozenset({".py"})),
    ("core", CORE_ROOT / "meeting_copilot_core", frozenset({".py"})),
    ("frontend", FRONTEND_V2_DIST_ROOT, None),
)
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


def runtime_identity_file(pid_file: Path) -> Path:
    return pid_file.with_name(f"{pid_file.name}.runtime-identity.json")


def runtime_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for label, root, suffixes in RUNTIME_SOURCE_ROOTS:
        if not root.is_dir():
            raise OSError(f"required runtime source root is unavailable: {label}")
        files = sorted(
            path
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and "__pycache__" not in path.parts
            and (suffixes is None or path.suffix in suffixes)
        )
        if not files:
            raise OSError(f"required runtime source root is empty: {label}")
        for path in files:
            relative = path.relative_to(root).as_posix()
            digest.update(label.encode("ascii"))
            digest.update(b"\0")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def process_start_marker(pid: int) -> str | None:
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        raw_stat = proc_stat.read_text(encoding="utf-8")
        _, separator, trailing = raw_stat.rpartition(")")
        fields = trailing.split()
        if separator and len(fields) > 19:
            return hashlib.sha256(f"proc:{fields[19]}".encode("ascii")).hexdigest()
    except (OSError, UnicodeError):
        pass

    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started_at = result.stdout.strip()
    if result.returncode != 0 or not started_at:
        return None
    return hashlib.sha256(started_at.encode("utf-8")).hexdigest()


def write_runtime_identity(*, pid_file: Path, pid: int, port: int) -> None:
    identity_file = runtime_identity_file(pid_file)
    identity_file.parent.mkdir(parents=True, exist_ok=True)
    start_marker = process_start_marker(pid)
    if start_marker is None:
        raise OSError("process start marker is unavailable")
    payload = {
        "schema_version": RUNTIME_IDENTITY_SCHEMA,
        "pid": pid,
        "port": port,
        "process_start_marker": start_marker,
        "source_fingerprint": runtime_source_fingerprint(),
    }
    temporary = identity_file.with_name(f".{identity_file.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=True, separators=(",", ":"))
            output.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, identity_file)
    finally:
        temporary.unlink(missing_ok=True)


def _read_runtime_identity(pid_file: Path) -> dict[str, Any] | None:
    identity_file = runtime_identity_file(pid_file)
    try:
        if identity_file.stat().st_size > MAX_IDENTITY_RECORD_BYTES:
            return None
        payload = json.loads(identity_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _remove_owned_runtime_records(*, pid_file: Path, pid: int) -> None:
    if read_pid(pid_file) != pid:
        return
    pid_file.unlink(missing_ok=True)
    record = _read_runtime_identity(pid_file)
    if record is not None and record.get("pid") != pid:
        return
    runtime_identity_file(pid_file).unlink(missing_ok=True)


def _terminate_started_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float = STOP_TIMEOUT_SECONDS,
) -> bool:
    if process.poll() is not None:
        return True
    try:
        process.terminate()
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            return False
    except OSError:
        return process.poll() is not None
    return process.poll() is not None


def managed_runtime_provenance(*, port: int | None, pid_file: Path) -> dict[str, Any]:
    checks = {
        "managed_launch_record": False,
        "pid_binding": False,
        "port_binding": False,
        "process_start_binding": False,
        "process_start_binding_available": False,
        "current_source_provenance": False,
    }
    pid = read_pid(pid_file)
    if pid is None:
        return {
            "verified": False,
            "owned": False,
            "reason": "managed_pid_missing",
            "checks": checks,
        }

    record = _read_runtime_identity(pid_file)
    if record is None or record.get("schema_version") != RUNTIME_IDENTITY_SCHEMA:
        return {
            "verified": False,
            "owned": False,
            "reason": "managed_launch_record_missing_or_invalid",
            "checks": checks,
        }
    checks["managed_launch_record"] = True

    record_pid = record.get("pid")
    checks["pid_binding"] = (
        type(record_pid) is int and record_pid == pid and pid_running(pid)
    )
    record_port = record.get("port")
    checks["port_binding"] = (
        type(record_port) is int
        and record_port > 0
        and (port is None or record_port == port)
    )

    recorded_start = record.get("process_start_marker")
    observed_start = process_start_marker(pid) if checks["pid_binding"] else None
    checks["process_start_binding_available"] = bool(
        isinstance(recorded_start, str) and recorded_start and observed_start
    )
    if checks["process_start_binding_available"]:
        checks["process_start_binding"] = hmac.compare_digest(
            recorded_start, observed_start
        )

    owned = all(
        checks[name]
        for name in (
            "managed_launch_record",
            "pid_binding",
            "port_binding",
            "process_start_binding",
        )
    )
    if not owned:
        return {
            "verified": False,
            "owned": False,
            "reason": "managed_process_binding_mismatch",
            "checks": checks,
        }

    try:
        current_fingerprint = runtime_source_fingerprint()
    except OSError:
        return {
            "verified": False,
            "owned": True,
            "reason": "current_source_provenance_unavailable",
            "checks": checks,
        }
    recorded_fingerprint = record.get("source_fingerprint")
    checks["current_source_provenance"] = isinstance(
        recorded_fingerprint, str
    ) and hmac.compare_digest(
        recorded_fingerprint,
        current_fingerprint,
    )
    if not checks["current_source_provenance"]:
        return {
            "verified": False,
            "owned": True,
            "reason": "current_source_provenance_mismatch",
            "checks": checks,
        }
    return {"verified": True, "owned": True, "reason": "verified", "checks": checks}


def _local_response_bytes(
    *, port: int, path: str, timeout_seconds: float, max_bytes: int
) -> bytes:
    connection = HTTPConnection("127.0.0.1", port, timeout=timeout_seconds)
    try:
        try:
            connection.request("GET", path, headers={"Connection": "close"})
            response = connection.getresponse()
        except HTTPException as exc:
            raise OSError("local runtime probe returned malformed HTTP") from exc
        if response.status != 200:
            raise OSError("local runtime probe returned a non-success status")
        body = response.read(max_bytes + 1)
    finally:
        connection.close()
    if len(body) > max_bytes:
        raise OSError("local runtime probe exceeded its response limit")
    return body


def _runtime_identity_diagnostic(
    *,
    verified: bool,
    reason: str,
    checks: dict[str, bool],
) -> dict[str, Any]:
    return {
        "schema_version": RUNTIME_IDENTITY_SCHEMA,
        "verified": verified,
        "reason": reason,
        "checks": checks,
    }


def check_health(port: int, timeout_seconds: float = 1.0) -> dict[str, Any]:
    checks = {
        "loopback_transport": True,
        "health_contract": False,
        "application_schema_contract": False,
        "workbench_asset_provenance": False,
    }
    try:
        health_payload = json.loads(
            _local_response_bytes(
                port=port,
                path="/health",
                timeout_seconds=timeout_seconds,
                max_bytes=MAX_JSON_PROBE_BYTES,
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        identity = _runtime_identity_diagnostic(
            verified=False,
            reason="health_contract_unavailable",
            checks=checks,
        )
        return {"ok": False, "error": identity["reason"], "runtime_identity": identity}

    checks["health_contract"] = (
        isinstance(health_payload, dict)
        and health_payload.get("status") == "ok"
        and (health_payload.get("service") == HEALTH_SERVICE)
    )
    if not checks["health_contract"]:
        identity = _runtime_identity_diagnostic(
            verified=False,
            reason="health_contract_mismatch",
            checks=checks,
        )
        return {
            "ok": False,
            "error": identity["reason"],
            "body": {},
            "runtime_identity": identity,
        }

    safe_health_body: dict[str, Any] = {"status": "ok", "service": HEALTH_SERVICE}

    try:
        application_schema = json.loads(
            _local_response_bytes(
                port=port,
                path="/v2/diagnostics/application-schema",
                timeout_seconds=timeout_seconds,
                max_bytes=MAX_JSON_PROBE_BYTES,
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        identity = _runtime_identity_diagnostic(
            verified=False,
            reason="application_schema_contract_unavailable",
            checks=checks,
        )
        return {
            "ok": False,
            "error": identity["reason"],
            "body": safe_health_body,
            "runtime_identity": identity,
        }
    checks["application_schema_contract"] = (
        isinstance(application_schema, dict)
        and application_schema.get("schema_version") == APPLICATION_SCHEMA_VERSION
    )
    if not checks["application_schema_contract"]:
        identity = _runtime_identity_diagnostic(
            verified=False,
            reason="application_schema_contract_mismatch",
            checks=checks,
        )
        return {
            "ok": False,
            "error": identity["reason"],
            "body": safe_health_body,
            "runtime_identity": identity,
        }

    try:
        expected_workbench = FRONTEND_V2_INDEX.read_bytes()
        served_workbench = _local_response_bytes(
            port=port,
            path="/workbench",
            timeout_seconds=timeout_seconds,
            max_bytes=MAX_WORKBENCH_PROBE_BYTES,
        )
    except OSError:
        identity = _runtime_identity_diagnostic(
            verified=False,
            reason="workbench_asset_provenance_unavailable",
            checks=checks,
        )
        return {
            "ok": False,
            "error": identity["reason"],
            "body": safe_health_body,
            "runtime_identity": identity,
        }
    checks["workbench_asset_provenance"] = hmac.compare_digest(
        hashlib.sha256(served_workbench).digest(),
        hashlib.sha256(expected_workbench).digest(),
    )
    reason = (
        "verified"
        if checks["workbench_asset_provenance"]
        else "workbench_asset_provenance_mismatch"
    )
    identity = _runtime_identity_diagnostic(
        verified=checks["workbench_asset_provenance"],
        reason=reason,
        checks=checks,
    )
    return {
        "ok": identity["verified"],
        **({} if identity["verified"] else {"error": reason}),
        "body": safe_health_body,
        "runtime_identity": identity,
    }


def _bind_health_to_managed_runtime(
    health: dict[str, Any],
    *,
    port: int,
    pid_file: Path,
) -> dict[str, Any]:
    contract_identity = health.get("runtime_identity")
    if not health.get("ok"):
        return health
    if not isinstance(contract_identity, dict):
        reason = "runtime_identity_contract_missing"
        identity = _runtime_identity_diagnostic(
            verified=False,
            reason=reason,
            checks={},
        )
        return {
            **health,
            "ok": False,
            "error": reason,
            "runtime_identity": identity,
        }

    provenance = managed_runtime_provenance(port=port, pid_file=pid_file)
    checks = {
        **dict(contract_identity.get("checks") or {}),
        **dict(provenance.get("checks") or {}),
    }
    verified = bool(contract_identity.get("verified") and provenance.get("verified"))
    reason = (
        "verified"
        if verified
        else str(provenance.get("reason") or "managed_runtime_unverified")
    )
    identity = _runtime_identity_diagnostic(
        verified=verified, reason=reason, checks=checks
    )
    return {
        **health,
        "ok": verified,
        **({} if verified else {"error": reason}),
        "runtime_identity": identity,
    }


def status_report(*, port: int, pid_file: Path) -> dict[str, Any]:
    pid = read_pid(pid_file)
    health = _bind_health_to_managed_runtime(
        check_health(port),
        port=port,
        pid_file=pid_file,
    )
    running = bool(health.get("ok"))
    return {
        "status": "running" if running else "not_running",
        "port": port,
        "pid": pid,
        "pid_running": bool(pid and pid_running(pid)),
        "health_ok": running,
        "runtime_identity_verified": running,
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
    data_dir = (data_dir if data_dir.is_absolute() else REPO_ROOT / data_dir).resolve()
    current = status_report(port=port, pid_file=pid_file)
    if current["health_ok"]:
        return {**current, "status": "already_running"}
    if is_port_open(port):
        identity = current.get("health", {}).get("runtime_identity")
        return {
            "status": "blocked_port_in_use",
            "port": port,
            "health_url": f"http://127.0.0.1:{port}/health",
            "workbench_url": f"http://127.0.0.1:{port}/workbench",
            "safe_to_kill_existing_process": False,
            "reason": "port is open but the current managed runtime identity is not verified",
            **({"runtime_identity": identity} if isinstance(identity, dict) else {}),
        }

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    command = build_uvicorn_command(port=port)
    env = build_child_env(data_dir=data_dir, provider_mode=provider_mode)
    log_handle = log_file.open("ab")
    try:
        process = subprocess.Popen(
            command,
            cwd=WEB_BACKEND_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    pid_file.write_text(str(process.pid), encoding="utf-8")
    try:
        write_runtime_identity(pid_file=pid_file, pid=process.pid, port=port)
    except OSError:
        cleanup_complete = _terminate_started_process(process)
        if cleanup_complete:
            _remove_owned_runtime_records(pid_file=pid_file, pid=process.pid)
        return {
            "status": "blocked_start_failed",
            "port": port,
            "pid": process.pid,
            "reason": "runtime_identity_record_unavailable",
            "owned_process_cleanup": (
                "complete" if cleanup_complete else "blocked_process_still_running"
            ),
            "safe_to_force_kill": False,
        }

    deadline = time.time() + 20
    health = {"ok": False, "error": "not checked"}
    while time.time() < deadline:
        health = _bind_health_to_managed_runtime(
            check_health(port),
            port=port,
            pid_file=pid_file,
        )
        if health.get("ok"):
            break
        if process.poll() is not None:
            break
        time.sleep(0.2)
    if not health.get("ok"):
        cleanup_complete = _terminate_started_process(process)
        if cleanup_complete:
            _remove_owned_runtime_records(pid_file=pid_file, pid=process.pid)
        return {
            "status": "blocked_start_failed",
            "port": port,
            "pid": process.pid,
            "returncode": process.poll(),
            "health": health,
            "owned_process_cleanup": (
                "complete" if cleanup_complete else "blocked_process_still_running"
            ),
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
        runtime_identity_file(pid_file).unlink(missing_ok=True)
        return {"status": "not_running", "pid": pid, "pid_file": str(pid_file)}
    provenance = managed_runtime_provenance(port=None, pid_file=pid_file)
    if not provenance.get("owned"):
        identity = _runtime_identity_diagnostic(
            verified=False,
            reason=str(provenance.get("reason") or "managed_process_unverified"),
            checks=dict(provenance.get("checks") or {}),
        )
        return {
            "status": "blocked_foreign_process",
            "pid": pid,
            "pid_file": str(pid_file),
            "safe_to_kill_existing_process": False,
            "runtime_identity": identity,
        }
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not pid_running(pid):
            pid_file.unlink(missing_ok=True)
            runtime_identity_file(pid_file).unlink(missing_ok=True)
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
