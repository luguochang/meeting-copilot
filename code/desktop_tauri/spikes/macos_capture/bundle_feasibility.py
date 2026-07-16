#!/usr/bin/env python3
"""Inventory and harmlessly probe a self-contained backend/ASR bundle candidate."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = SCRIPT_DIR.parents[3]


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file() and not candidate.is_symlink():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def command_probe(command: list[str], *, cwd: Path, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "status": "passed" if completed.returncode == 0 else "failed",
            "return_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": completed.stdout[-1000:],
            "stderr_tail": completed.stderr[-1000:],
        }
    except subprocess.TimeoutExpired as error:
        return {
            "status": "timed_out",
            "return_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": (error.stdout or b"").decode(errors="replace")[-1000:]
            if isinstance(error.stdout, bytes)
            else (error.stdout or "")[-1000:],
            "stderr_tail": (error.stderr or b"").decode(errors="replace")[-1000:]
            if isinstance(error.stderr, bytes)
            else (error.stderr or "")[-1000:],
        }
    except OSError as error:
        return {
            "status": "failed_to_start",
            "return_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": "",
            "stderr_tail": str(error),
        }


def backend_probe(python: Path, backend_root: Path, core_root: Path, timeout: float) -> dict[str, Any]:
    port = free_port()
    command = [
        str(python),
        "-m",
        "uvicorn",
        "meeting_copilot_web_mvp.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "error",
    ]
    environment = os.environ.copy()
    environment["MEETING_COPILOT_DATA_DIR"] = str(SCRIPT_DIR / ".build" / "bundle-probe-data")
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(core_root), existing_python_path) if part
    )
    started = time.monotonic()
    process: subprocess.Popen[str] | None = None
    result: dict[str, Any] = {
        "status": "failed_to_start",
        "duration_seconds": 0,
        "health_status": None,
        "return_code": None,
        "stderr_tail": "",
    }
    try:
        process = subprocess.Popen(
            command,
            cwd=backend_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = started + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                result["status"] = "exited_before_health"
                result["return_code"] = process.returncode
                break
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.25)
                connection.request("GET", "/health")
                response = connection.getresponse()
                body = response.read(2048).decode(errors="replace")
                result["health_status"] = response.status
                result["health_body"] = body
                result["status"] = "passed" if response.status == 200 else "health_failed"
                connection.close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            result["status"] = "timed_out_waiting_for_health"
    except OSError as error:
        result["stderr_tail"] = str(error)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.75)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
        if process is not None and process.stderr is not None:
            result["stderr_tail"] = process.stderr.read()[-1500:]
            result["return_code"] = process.returncode
        result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def build_report(repo_root: Path, timeout: float) -> dict[str, Any]:
    backend_root = repo_root / "code/web_mvp/backend"
    core_root = repo_root / "code/core"
    backend_entry = backend_root / "meeting_copilot_web_mvp/app.py"
    pyproject = backend_root / "pyproject.toml"
    backend_lock = backend_root / "uv.lock"
    asr_root = repo_root / "code/asr_runtime"
    asr_entry = asr_root / "scripts/funasr_stream_worker.py"
    requirements = asr_root / "requirements-funasr.lock"
    asr_python = asr_root / ".venv-funasr/bin/python"
    backend_python = Path(sys.executable).resolve()
    model_root = asr_root / "models"

    backend_start = backend_probe(backend_python, backend_root, core_root, timeout)
    asr_help = command_probe(
        [str(asr_python), str(asr_entry), "--help"],
        cwd=repo_root,
        timeout=timeout,
    ) if asr_python.is_file() else {
        "status": "missing_runtime",
        "return_code": None,
        "duration_seconds": 0,
        "stdout_tail": "",
        "stderr_tail": f"missing {asr_python}",
    }

    blockers: list[str] = []
    if backend_start["status"] != "passed":
        blockers.append("backend health startup probe did not pass")
    if asr_help["status"] != "passed":
        blockers.append("FunASR worker entrypoint probe did not pass")
    blockers.extend([
        "Python runtime and native wheels are not yet copied into a relocatable bundle",
        "FunASR model weights are not present under the repository model root",
        "No clean-Mac codesign/notarization execution has been performed",
    ])

    return {
        "schema_version": "meeting-copilot.bundle-feasibility-spike.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result": "feasible_with_blockers" if backend_start["status"] == "passed" and asr_help["status"] == "passed" else "probe_failed",
        "host": {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python": str(backend_python),
            "python_version": platform.python_version(),
        },
        "constraints": {
            "copies_model_files": False,
            "loads_asr_model": False,
            "downloads_dependencies": False,
            "calls_remote_services": False,
            "per_probe_timeout_seconds": timeout,
        },
        "inventory": {
            "backend": {
                "root": relative(backend_root, repo_root),
                "entrypoint": relative(backend_entry, repo_root),
                "entrypoint_exists": backend_entry.is_file(),
                "pyproject": relative(pyproject, repo_root),
                "pyproject_sha256": sha256(pyproject),
                "source_tree_bytes": directory_size(backend_root / "meeting_copilot_web_mvp")
                + directory_size(core_root),
                "existing_generated_artifacts_bytes_excluded": directory_size(backend_root / "artifacts"),
                "dependency_lock": relative(backend_lock, repo_root),
                "dependency_lock_exists": backend_lock.is_file(),
                "dependency_lock_sha256": sha256(backend_lock),
                "required_local_module_root": relative(core_root, repo_root),
                "required_local_module_root_exists": core_root.is_dir(),
                "candidate_command": ["python", "-m", "uvicorn", "meeting_copilot_web_mvp.app:app"],
            },
            "asr": {
                "root": relative(asr_root, repo_root),
                "entrypoint": relative(asr_entry, repo_root),
                "entrypoint_exists": asr_entry.is_file(),
                "requirements_lock": relative(requirements, repo_root),
                "requirements_sha256": sha256(requirements),
                "current_venv_python": relative(asr_python, repo_root),
                "current_venv_bytes": directory_size(asr_root / ".venv-funasr"),
                "repository_model_root": relative(model_root, repo_root),
                "repository_model_bytes": directory_size(model_root),
                "candidate_command": ["python", relative(asr_entry, repo_root)],
            },
        },
        "probes": {
            "backend_health": backend_start,
            "asr_entrypoint_help": asr_help,
            "asr_model_start": {
                "status": "not_attempted",
                "reason": "loading model weights is intentionally outside the <=3 second harmless Phase 0 probe",
            },
        },
        "minimum_bundle_layout": {
            "bin/meeting-copilot-backend": "frozen or embedded Python backend launcher",
            "bin/meeting-copilot-asr-worker": "frozen FunASR worker launcher",
            "runtime/python": "relocatable pinned Python runtime if freezing is not used",
            "resources/models/funasr": "separately licensed streaming model weights",
            "resources/licenses": "application, dependency, native wheel, ffmpeg, and model notices",
            "manifest.json": "hash, architecture, version, entrypoint, and provenance for every bundled artifact",
        },
        "blockers": blockers,
        "next_decision": "Build one arm64 relocatable sidecar candidate in a separate implementation slice; this spike proves entrypoint viability, not distributable packaging.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=2.5)
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 3:
        parser.error("--timeout must be greater than 0 and no more than 3 seconds")

    report = build_report(args.repo_root.resolve(), args.timeout)
    output = args.output or SCRIPT_DIR / ".build/evidence/bundle-feasibility.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["result"] == "feasible_with_blockers" else 1


if __name__ == "__main__":
    raise SystemExit(main())
