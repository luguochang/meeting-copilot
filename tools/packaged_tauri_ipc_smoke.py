#!/usr/bin/env python3
"""Prove safe Tauri commands are callable from the actual packaged React WebView."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import subprocess
import time
from typing import Any

from packaged_runtime_supervisor_smoke import (
    find_backend_process,
    packaged_app_launch_command,
    pid_exists,
    port_is_listening,
    read_process_table,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/packaged_tauri_ipc_smoke"
PROBE_RELATIVE_PATH = Path("artifacts/tmp/desktop_frontend_probe_runtime/latest-ipc.json")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def resolve_output_root(repo_root: Path, output_root: Path) -> Path:
    resolved = output_root.resolve() if output_root.is_absolute() else (repo_root / output_root).resolve()
    approved = (repo_root / "artifacts/tmp").resolve()
    try:
        resolved.relative_to(approved)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id contains unsafe characters")


def load_ipc_probe(path: Path) -> dict[str, Any]:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"packaged IPC probe is unreadable: {exc}") from exc
    if evidence.get("schema_version") != "desktop_frontend_probe.v1":
        raise ValueError("packaged IPC probe schema is invalid")
    if evidence.get("source") != "tauri_packaged_webview":
        raise ValueError("packaged IPC probe did not originate from the Tauri WebView")
    payload = evidence.get("payload")
    if not isinstance(payload, dict) or payload.get("packaged_ipc_probe") is not True:
        raise ValueError("packaged IPC probe payload is invalid")
    return evidence


def probe_checks(evidence: dict[str, Any]) -> dict[str, bool]:
    payload = evidence.get("payload") or {}
    return {
        "runtime_command_ok": payload.get("runtime_command_status") == "ok",
        "runtime_is_real": payload.get("runtime_implementation_status") == "real",
        "provider_command_ok": payload.get("provider_command_status") == "ok",
        "microphone_prepare_ok": payload.get("microphone_command_status") == "ok",
        "native_helper_present": payload.get("microphone_helper_present") is True,
        "microphone_not_started": payload.get("microphone_captures_audio") is False,
        "consent_not_bypassed": payload.get("consent_bypassed") is False,
        "probe_errors_empty": payload.get("errors") == [],
    }


def smoke_packaged_tauri_ipc(
    *,
    repo_root: Path,
    app_path: Path,
    output_root: Path,
    run_id: str,
    startup_timeout_seconds: float = 90.0,
    cleanup_timeout_seconds: float = 15.0,
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
    probe_path = repo_root / PROBE_RELATIVE_PATH
    probe_path.unlink(missing_ok=True)
    started_at = time.monotonic()
    environment = dict(os.environ)
    environment.update(
        {
            "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE": "1",
            "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE": secrets.token_hex(32),
            "MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE": "1",
        }
    )
    app_process = subprocess.Popen(
        packaged_app_launch_command(binary),
        cwd=app_path.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    backend: dict[str, Any] | None = None
    probe: dict[str, Any] | None = None
    probe_error: str | None = None
    app_exited = False
    backend_exited = False
    port_closed = False
    try:
        deadline = time.monotonic() + startup_timeout_seconds
        while time.monotonic() < deadline:
            if app_process.poll() is not None:
                break
            if backend is None:
                backend = find_backend_process(
                    read_process_table(),
                    app_pid=app_process.pid,
                    app_path=app_path,
                )
            if probe_path.is_file():
                try:
                    probe = load_ipc_probe(probe_path)
                    break
                except ValueError as exc:
                    probe_error = str(exc)
            time.sleep(0.1)
    finally:
        if app_process.poll() is None:
            app_process.send_signal(signal.SIGTERM)
            try:
                app_process.wait(timeout=cleanup_timeout_seconds)
                app_exited = True
            except subprocess.TimeoutExpired:
                app_process.kill()
                app_process.wait(timeout=5)
        else:
            app_exited = True
        cleanup_deadline = time.monotonic() + cleanup_timeout_seconds
        while time.monotonic() < cleanup_deadline and backend is not None:
            backend_exited = not pid_exists(int(backend["pid"]))
            port_closed = not port_is_listening(int(backend["port"]))
            if backend_exited and port_closed:
                break
            time.sleep(0.1)
        if backend is not None and pid_exists(int(backend["pid"])):
            os.kill(int(backend["pid"]), signal.SIGTERM)

    checks = probe_checks(probe) if probe is not None else {}
    passed = (
        backend is not None
        and probe is not None
        and checks
        and all(checks.values())
        and app_exited
        and backend_exited
        and port_closed
    )
    evidence = {
        "schema_version": "meeting_copilot.packaged_tauri_ipc_smoke.v1",
        "run_id": run_id,
        "host_platform": platform.platform(),
        "architecture": platform.machine(),
        "app_path": str(app_path.relative_to(repo_root)),
        "app_pid": app_process.pid,
        "backend_pid": backend.get("pid") if backend else None,
        "backend_port": backend.get("port") if backend else None,
        "probe_path": str(PROBE_RELATIVE_PATH),
        "probe": probe,
        "probe_error": probe_error,
        "checks": checks,
        "app_exited_after_sigterm": app_exited,
        "backend_exited_after_parent": backend_exited,
        "backend_port_closed": port_closed,
        "duration_seconds": round(time.monotonic() - started_at, 3),
        "decision": {
            "status": (
                "go_packaged_tauri_ipc_page_smoke_not_public_release"
                if passed
                else "no_go_packaged_tauri_ipc_page_smoke"
            ),
            "counts_as_packaged_tauri_ipc_page_evidence": passed,
            "counts_as_microphone_capture_evidence": False,
            "counts_as_public_release_package": False,
        },
        "privacy_cost_flags": {
            "microphone_started": False,
            "microphone_permission_bypassed": False,
            "remote_service_called": False,
            "remote_asr_called": False,
            "remote_llm_called": False,
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
    result = smoke_packaged_tauri_ipc(
        repo_root=args.repo_root,
        app_path=args.app_path,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"]["counts_as_packaged_tauri_ipc_page_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
