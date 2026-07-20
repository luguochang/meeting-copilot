#!/usr/bin/env python3
"""Run NEXT-006 resilience gates against a real Meeting Copilot runtime."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import socket
import sqlite3
import ssl
import struct
import subprocess
import threading
import time
from typing import Any, Mapping
from urllib.parse import urlsplit
import wave
import websocket

from packaged_real_provider_mainline_smoke import load_provider_config
from packaged_runtime_supervisor_smoke import (
    bootstrap_cookie,
    find_backend_process,
    find_conflicting_app_instances,
    find_funasr_process,
    health_proof,
    packaged_app_launch_command,
    pcm_float32_chunks,
    pcm_float32_duration_seconds,
    pid_exists,
    read_process_table,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/next006-real-sut"
REPORT_SCHEMA = "meeting_copilot.next006_real_sut_soak.v1"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REQUIRED_AUTOMATED_GATES = (
    "authenticated_health_metrics",
    "persistent_session",
    "recording_state",
    "queue_state",
    "backend_crash_recovery",
    "provider_disconnect_recovery",
    "provider_429_recovery",
    "provider_5xx_recovery",
    "disk_write_recovery",
    "asr_worker_crash_recovery",
    "app_crash_recovery",
    "sqlite_integrity",
    "cleanup",
)
FAULT_NAMES = frozenset(
    {
        "backend-crash",
        "provider-disconnect",
        "provider-429",
        "provider-5xx",
        "disk-write",
        "asr-worker-crash",
        "app-crash",
    }
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_object(raw: bytes) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class HttpResult:
    status_code: int | None
    json_body: dict[str, Any] | None
    latency_ms: float
    error_type: str | None = None


class JsonHttpClient:
    def __init__(self, base_url: str, token: str | None) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be absolute HTTP(S)")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        extra_headers: Mapping[str, str] | None = None,
    ) -> HttpResult:
        parsed = urlsplit(self.base_url)
        connection_cls = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_cls(parsed.hostname, parsed.port, timeout=timeout)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "accept": "application/json",
            "origin": self.base_url,
            **({"content-type": "application/json"} if body is not None else {}),
            **({"x-meeting-copilot-token": self.token} if self.token else {}),
            **dict(extra_headers or {}),
        }
        started = time.monotonic()
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(4 * 1024 * 1024)
            return HttpResult(
                status_code=response.status,
                json_body=_json_object(raw),
                latency_ms=round((time.monotonic() - started) * 1000, 3),
            )
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            return HttpResult(
                status_code=None,
                json_body=None,
                latency_ms=round((time.monotonic() - started) * 1000, 3),
                error_type=type(exc).__name__,
            )
        finally:
            connection.close()


class ProviderFaultRelay:
    """Loopback relay that forwards upstream bytes and injects transport/status faults."""

    MODES = frozenset({"passthrough", "disconnect", "429", "500", "503"})

    def __init__(self, *, upstream_base_url: str) -> None:
        parsed = urlsplit(upstream_base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("upstream_base_url must be absolute HTTP(S)")
        self._upstream = parsed
        self._mode = "passthrough"
        self._lock = threading.Lock()
        self._mode_counts: Counter[str] = Counter()
        self._forwarded = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("provider relay is not started")
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self) -> None:
        if self._server is not None:
            return
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                relay._handle(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="next006-provider-fault-relay",
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._server = None
        self._thread = None

    def set_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(f"provider relay mode must be one of {sorted(self.MODES)}")
        with self._lock:
            self._mode = mode

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": "meeting_copilot.next006_provider_fault_relay.v1",
                "mode": self._mode,
                "mode_counts": dict(sorted(self._mode_counts.items())),
                "forwarded_request_count": self._forwarded,
                "canned_success_response_count": 0,
                "upstream_kind": "remote_https"
                if self._upstream.scheme == "https"
                else "loopback_or_test_http",
            }

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            mode = self._mode
            self._mode_counts[mode] += 1
        size = min(
            max(0, int(handler.headers.get("content-length", "0"))), 4 * 1024 * 1024
        )
        body = handler.rfile.read(size)
        if mode == "disconnect":
            linger = struct.pack("ii", 1, 0)
            try:
                handler.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, linger
                )
            except OSError:
                pass
            handler.connection.close()
            return
        if mode in {"429", "500", "503"}:
            raw = json.dumps(
                {"error": {"type": f"next006_injected_{mode}", "retryable": True}}
            ).encode("utf-8")
            handler.send_response(int(mode))
            handler.send_header("content-type", "application/json")
            handler.send_header("content-length", str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
            return

        connection_cls = (
            http.client.HTTPSConnection
            if self._upstream.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_cls(
            self._upstream.hostname,
            self._upstream.port,
            timeout=60,
        )
        upstream_path = f"{self._upstream.path.rstrip('/')}{handler.path}"
        headers = {
            key: value
            for key, value in handler.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        headers["host"] = self._upstream.netloc
        try:
            connection.request("POST", upstream_path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(4 * 1024 * 1024)
            handler.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {
                    "connection",
                    "transfer-encoding",
                    "content-length",
                }:
                    handler.send_header(key, value)
            handler.send_header("content-length", str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
            with self._lock:
                self._forwarded += 1
        finally:
            connection.close()


def verify_packaged_target(
    app_path: Path,
    package_evidence_path: Path,
    *,
    verify_code_signature: bool = True,
) -> dict[str, Any]:
    app_path = app_path.resolve()
    package_evidence_path = package_evidence_path.resolve()
    binary = app_path / "Contents/MacOS/meeting-copilot-desktop"
    manifest = (
        app_path
        / "Contents/Resources/MeetingCopilotRuntime.bundle/runtime-bundle-manifest.json"
    )
    if (
        not binary.is_file()
        or not manifest.is_file()
        or not package_evidence_path.is_file()
    ):
        raise ValueError("packaged target is incomplete")
    evidence = json.loads(package_evidence_path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != "meeting_copilot.tauri_runtime_package.v1":
        raise ValueError("package evidence schema is invalid")
    expected_hash = str((evidence.get("app_binary") or {}).get("sha256") or "")
    binary_hash = _sha256_file(binary)
    if expected_hash != binary_hash:
        raise ValueError("package evidence binary hash does not match target")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    if evidence.get("packaged_runtime_manifest") != manifest_payload:
        raise ValueError("package evidence runtime manifest does not match target")
    evidence_app = Path(str(evidence.get("app_path") or ""))
    evidence_candidates = {
        evidence_app.resolve(strict=False),
        (REPO_ROOT / evidence_app).resolve(strict=False),
    }
    if app_path not in evidence_candidates:
        raise ValueError("package evidence app path does not match target")
    signature_verified = False
    if verify_code_signature:
        completed = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(app_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError("packaged target code signature verification failed")
        signature_verified = True
    return {
        "target_kind": "meeting_copilot_packaged_app",
        "bundle_name": app_path.name,
        "binary_sha256": binary_hash,
        "runtime_manifest_sha256": _sha256_file(manifest),
        "package_evidence_sha256": _sha256_file(package_evidence_path),
        "code_signature_verified": signature_verified,
        "provenance_verified": True,
    }


def verify_runtime_bundle(runtime_bundle: Path) -> dict[str, Any]:
    runtime_bundle = runtime_bundle.resolve()
    manifest = runtime_bundle / "runtime-bundle-manifest.json"
    launcher = runtime_bundle / "bin/meeting-copilot-backend"
    if not manifest.is_file() or not launcher.is_file():
        raise ValueError("Meeting Copilot runtime bundle is incomplete")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "meeting_copilot.runtime_bundle.v1":
        raise ValueError("Meeting Copilot runtime manifest schema is invalid")
    return {
        "target_kind": "meeting_copilot_runtime_bundle",
        "runtime_manifest_sha256": _sha256_file(manifest),
        "backend_launcher_sha256": _sha256_file(launcher),
        "provenance_verified": True,
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


class MeetingCopilotTarget:
    recovery_owner = "unknown"

    def __init__(self, *, run_root: Path, token: str) -> None:
        self.run_root = run_root
        self.token = token
        self.backend: dict[str, Any] | None = None
        self.app_process: subprocess.Popen[bytes] | None = None
        self.data_dir: Path | None = None

    @property
    def base_url(self) -> str:
        if self.backend is None:
            raise RuntimeError("Meeting Copilot backend is not ready")
        return f"http://127.0.0.1:{self.backend['port']}"

    def client(self) -> JsonHttpClient:
        return JsonHttpClient(self.base_url, self.token)

    def wait_ready(self, timeout_seconds: float = 90.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self.refresh_processes()
            if self.backend is not None:
                health = self.client().request("GET", "/health", timeout=2)
                proof = str((health.json_body or {}).get("instance_proof") or "")
                if health.status_code == 200 and proof == health_proof(self.token):
                    return self.backend
            time.sleep(0.1)
        raise RuntimeError("Meeting Copilot backend did not become ready")

    def refresh_processes(self) -> None:
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError

    def kill_backend_for_fault(self) -> tuple[int, int]:
        self.refresh_processes()
        if self.backend is None:
            raise RuntimeError("backend process is unavailable")
        pid = int(self.backend["pid"])
        pgid = os.getpgid(pid)
        if pgid != pid:
            raise RuntimeError("backend process group identity is not isolated")
        os.killpg(pgid, signal.SIGKILL)
        return pid, pgid

    def recover_backend(
        self, old_pid: int, timeout_seconds: float = 90.0
    ) -> dict[str, Any]:
        raise NotImplementedError

    def find_worker(self) -> dict[str, Any] | None:
        self.refresh_processes()
        if self.backend is None:
            return None
        return self._find_worker_for_backend(int(self.backend["pid"]))

    def _find_worker_for_backend(self, backend_pid: int) -> dict[str, Any] | None:
        raise NotImplementedError

    def stop(self) -> dict[str, Any]:
        raise NotImplementedError


class PackagedAppTarget(MeetingCopilotTarget):
    recovery_owner = "tauri_backend_supervisor"

    def __init__(
        self,
        *,
        app_path: Path,
        package_evidence_path: Path,
        run_root: Path,
        token: str,
    ) -> None:
        super().__init__(run_root=run_root, token=token)
        self.app_path = app_path.resolve()
        self.provenance = verify_packaged_target(self.app_path, package_evidence_path)
        self.binary = self.app_path / "Contents/MacOS/meeting-copilot-desktop"
        self.runtime_bundle = (
            self.app_path / "Contents/Resources/MeetingCopilotRuntime.bundle"
        )
        self.home = run_root / "isolated-home"
        self.data_dir = (
            self.home
            / "Library/Application Support/com.meetingcopilot.desktop/runtime-data"
        )

    def start(self) -> None:
        conflicts = find_conflicting_app_instances(
            read_process_table(), app_path=self.app_path
        )
        if conflicts:
            raise RuntimeError(
                "another Meeting Copilot packaged app instance is running"
            )
        self.home.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(self.home),
                "CFFIXED_USER_HOME": str(self.home),
                "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE": "1",
                "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE": self.token,
                "MEETING_COPILOT_ENABLE_NEXT006_FAILPOINTS": "1",
            }
        )
        self.app_process = subprocess.Popen(
            packaged_app_launch_command(self.binary),
            cwd=self.app_path.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
        self.wait_ready()

    def refresh_processes(self) -> None:
        if self.app_process is None or self.app_process.poll() is not None:
            self.backend = None
            return
        self.backend = find_backend_process(
            read_process_table(),
            app_pid=self.app_process.pid,
            app_path=self.app_path,
        )

    def recover_backend(
        self, old_pid: int, timeout_seconds: float = 90.0
    ) -> dict[str, Any]:
        recovered = self.wait_ready(timeout_seconds)
        if int(recovered["pid"]) == old_pid:
            raise RuntimeError(
                "Tauri supervisor did not create a new backend generation"
            )
        return recovered

    def _find_worker_for_backend(self, backend_pid: int) -> dict[str, Any] | None:
        return find_funasr_process(
            read_process_table(),
            backend_pid=backend_pid,
            app_path=self.app_path,
        )

    def crash_and_relaunch_app(self) -> tuple[int, int]:
        if self.app_process is None:
            raise RuntimeError("packaged app is unavailable")
        old_app_pid = self.app_process.pid
        old_backend_pid = int((self.backend or {}).get("pid") or 0)
        os.kill(old_app_pid, signal.SIGKILL)
        self.app_process.wait(timeout=10)
        deadline = time.monotonic() + 15
        while (
            old_backend_pid
            and pid_exists(old_backend_pid)
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        self.backend = None
        self.start()
        return old_app_pid, int(self.app_process.pid)

    def stop(self) -> dict[str, Any]:
        backend_pid = int((self.backend or {}).get("pid") or 0)
        worker = self.find_worker()
        worker_pid = int((worker or {}).get("pid") or 0)
        if self.app_process is not None and self.app_process.poll() is None:
            self.app_process.send_signal(signal.SIGTERM)
            try:
                self.app_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.app_process.kill()
                self.app_process.wait(timeout=5)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(
            pid and pid_exists(pid) for pid in (backend_pid, worker_pid)
        ):
            time.sleep(0.1)
        return {
            "app_exited": self.app_process is None
            or self.app_process.poll() is not None,
            "backend_exited": not backend_pid or not pid_exists(backend_pid),
            "asr_worker_exited": not worker_pid or not pid_exists(worker_pid),
        }


class RuntimeBundleTarget(MeetingCopilotTarget):
    recovery_owner = "meeting_copilot_runtime_adapter"

    def __init__(
        self,
        *,
        runtime_bundle: Path,
        run_root: Path,
        token: str,
    ) -> None:
        super().__init__(run_root=run_root, token=token)
        self.runtime_bundle = runtime_bundle.resolve()
        self.provenance = verify_runtime_bundle(self.runtime_bundle)
        self.launcher = self.runtime_bundle / "bin/meeting-copilot-backend"
        self.port = _free_port()
        self.process: subprocess.Popen[bytes] | None = None
        self.data_dir = run_root / "runtime-data"

    def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        stdout = (self.run_root / "backend.stdout.log").open("ab")
        stderr = (self.run_root / "backend.stderr.log").open("ab")
        environment = dict(os.environ)
        environment.update(
            {
                "MEETING_COPILOT_PORT": str(self.port),
                "MEETING_COPILOT_DATA_DIR": str(self.data_dir),
                "MEETING_COPILOT_DESKTOP_RUNTIME": "1",
                "MEETING_COPILOT_LOCAL_API_TOKEN": self.token,
                "MEETING_COPILOT_ENABLE_NEXT006_FAILPOINTS": "1",
            }
        )
        environment.pop("MEETING_COPILOT_PARENT_PID", None)
        try:
            self.process = subprocess.Popen(
                ["/bin/sh", str(self.launcher)],
                cwd=self.runtime_bundle,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=environment,
                start_new_session=True,
            )
        finally:
            stdout.close()
            stderr.close()
        self.backend = {"pid": self.process.pid, "ppid": os.getpid(), "port": self.port}
        self.wait_ready()

    def refresh_processes(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.backend = None
        else:
            self.backend = {
                "pid": self.process.pid,
                "ppid": os.getpid(),
                "port": self.port,
            }

    def recover_backend(
        self, old_pid: int, timeout_seconds: float = 90.0
    ) -> dict[str, Any]:
        if self.process is not None:
            self.process.wait(timeout=10)
        self.start()
        recovered = self.wait_ready(timeout_seconds)
        if int(recovered["pid"]) == old_pid:
            raise RuntimeError(
                "runtime adapter did not create a new backend generation"
            )
        return recovered

    def _find_worker_for_backend(self, backend_pid: int) -> dict[str, Any] | None:
        runtime_marker = str(self.runtime_bundle)
        for process in read_process_table():
            command = str(process.get("command") or "")
            if (
                int(process.get("ppid") or 0) == backend_pid
                and runtime_marker in command
                and "funasr_stream_worker.py" in command
                and "--resident" in command
            ):
                return process
        return None

    def stop(self) -> dict[str, Any]:
        backend_pid = int((self.backend or {}).get("pid") or 0)
        worker = self.find_worker()
        worker_pid = int((worker or {}).get("pid") or 0)
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        return {
            "app_exited": True,
            "backend_exited": not backend_pid or not pid_exists(backend_pid),
            "asr_worker_exited": not worker_pid or not pid_exists(worker_pid),
        }


def _controlled_wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        if (
            handle.getsampwidth() != 2
            or handle.getnchannels() != 1
            or handle.getframerate() != 16_000
        ):
            raise ValueError("NEXT-006 audio must be 16-bit mono 16kHz PCM WAV")
        return handle.getnframes() / handle.getframerate()


class ContinuousAudioProtocolError(RuntimeError):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class ContinuousAudioDriver:
    """Continuously exercise the real packaged WebSocket/ASR/recording path."""

    def __init__(
        self,
        *,
        target: MeetingCopilotTarget,
        meeting_id: str,
        audio_path: Path,
        requested_duration_seconds: float,
    ) -> None:
        self.target = target
        self.meeting_id = meeting_id
        self.audio_path = audio_path.resolve()
        if not self.audio_path.is_file():
            raise FileNotFoundError(self.audio_path)
        self.source_duration_seconds = _controlled_wav_duration_seconds(self.audio_path)
        if self.source_duration_seconds <= 0:
            raise ValueError("NEXT-006 controlled audio must be non-empty")
        self.stream_duration_seconds = min(30.0, self.source_duration_seconds)
        self._pcm_chunks = tuple(
            pcm_float32_chunks(
                self.audio_path, max_seconds=self.stream_duration_seconds
            )
        )
        if not self._pcm_chunks:
            raise ValueError("NEXT-006 controlled audio produced no PCM chunks")
        self.requested_duration_seconds = requested_duration_seconds
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_monotonic: float | None = None
        self._stopped_monotonic: float | None = None
        self._attempt_count = 0
        self._bootstrap_success_count = 0
        self._ready_stream_count = 0
        self._non_empty_final_count = 0
        self._estimated_audio_sent_seconds = 0.0
        self._pause_count = 0
        self._pause_started_monotonic: float | None = None
        self._paused_seconds = 0.0
        self._error_types: Counter[str] = Counter()
        self._final_text_sha256: list[str] = []

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("continuous audio driver is already started")
        self._started_monotonic = time.monotonic()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="next006-continuous-real-audio",
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 150.0) -> dict[str, Any]:
        self._stop.set()
        self._pause.clear()
        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)
            if self._thread.is_alive():
                with self._lock:
                    self._error_types["audio_driver_stop_timeout"] += 1
            elif self._stopped_monotonic is None:
                self._stopped_monotonic = time.monotonic()
        return self.snapshot()

    def pause(self, timeout_seconds: float = 150.0) -> bool:
        self._pause.set()
        if not self._paused.wait(timeout=timeout_seconds):
            self._pause.clear()
            return False
        with self._lock:
            self._pause_count += 1
            self._pause_started_monotonic = time.monotonic()
        return True

    def resume(self) -> None:
        with self._lock:
            if self._pause_started_monotonic is not None:
                self._paused_seconds += time.monotonic() - self._pause_started_monotonic
                self._pause_started_monotonic = None
        self._pause.clear()
        deadline = time.monotonic() + 5.0
        while self._paused.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            started = self._started_monotonic
            stopped = self._stopped_monotonic
            active_seconds = (
                max(0.0, (stopped or time.monotonic()) - started)
                if started is not None
                else 0.0
            )
            estimated = self._estimated_audio_sent_seconds
            requested = self.requested_duration_seconds
            continuous = (
                started is not None
                and active_seconds >= requested * 0.98
                and estimated >= requested * 0.80
            )
            return {
                "schema_version": "meeting_copilot.next006_continuous_audio.v1",
                "source_kind": "controlled_local_pcm_wav",
                "source_sha256": _sha256_file(self.audio_path),
                "source_size_bytes": self.audio_path.stat().st_size,
                "source_duration_seconds": round(self.source_duration_seconds, 3),
                "per_stream_cap_seconds": self.stream_duration_seconds,
                "requested_duration_seconds": requested,
                "active_wall_seconds": round(active_seconds, 3),
                "continuous": continuous,
                "attempt_count": self._attempt_count,
                "bootstrap_success_count": self._bootstrap_success_count,
                "ready_stream_count": self._ready_stream_count,
                "non_empty_final_count": self._non_empty_final_count,
                "estimated_audio_sent_seconds": round(estimated, 3),
                "controlled_pause_count": self._pause_count,
                "controlled_pause_seconds": round(self._paused_seconds, 3),
                "error_types": dict(sorted(self._error_types.items())),
                "final_text_sha256": list(self._final_text_sha256[:20]),
                "raw_audio_uploaded": False,
                "transcript_text_in_evidence": False,
            }

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self._pause.is_set():
                    self._wait_while_paused()
                    continue
                with self._lock:
                    self._attempt_count += 1
                try:
                    self.target.refresh_processes()
                    backend = dict(self.target.backend or {})
                    port = int(backend.get("port") or 0)
                    if port <= 0:
                        raise ContinuousAudioProtocolError("backend_unavailable")
                    bootstrap_status, cookie = bootstrap_cookie(port, self.target.token)
                    if bootstrap_status != 303 or not cookie:
                        raise ContinuousAudioProtocolError(
                            f"bootstrap_status_{bootstrap_status}"
                        )
                    with self._lock:
                        self._bootstrap_success_count += 1
                    self._stream_generation(port, cookie)
                except Exception as exc:
                    category = (
                        exc.category
                        if isinstance(exc, ContinuousAudioProtocolError)
                        else type(exc).__name__
                    )
                    with self._lock:
                        self._error_types[category] += 1
                if not self._stop.is_set():
                    self._stop.wait(0.25)
        finally:
            self._paused.clear()
            self._stopped_monotonic = time.monotonic()

    def _wait_while_paused(self) -> None:
        self._paused.set()
        while self._pause.is_set() and not self._stop.is_set():
            self._stop.wait(0.05)
        self._paused.clear()

    def _stream_generation(self, port: int, cookie: str) -> None:
        ws_url = (
            f"ws://127.0.0.1:{port}/live/asr/stream/ws/{self.meeting_id}"
            "?audio_source=next006_continuous_controlled_audio"
        )
        ws = websocket.create_connection(
            ws_url,
            cookie=cookie,
            origin=f"http://127.0.0.1:{port}",
            timeout=45,
        )
        ready = False
        try:
            ready_deadline = time.monotonic() + 45.0
            while (
                not ready
                and not self._stop.is_set()
                and time.monotonic() < ready_deadline
            ):
                ready = self._drain_ws(ws, timeout=0.5, stop_on_ready=True)
            if not ready:
                raise ContinuousAudioProtocolError("asr_ready_timeout")
            with self._lock:
                self._ready_stream_count += 1

            chunk_index = 0
            while not self._stop.is_set():
                if self._pause.is_set():
                    self._wait_while_paused()
                    continue
                chunk = self._pcm_chunks[chunk_index % len(self._pcm_chunks)]
                chunk_index += 1
                chunk_started = time.monotonic()
                ws.send_binary(chunk)
                chunk_seconds = pcm_float32_duration_seconds(chunk)
                with self._lock:
                    self._estimated_audio_sent_seconds += chunk_seconds
                self._drain_ws(ws, timeout=0.02)
                remaining = chunk_seconds - (time.monotonic() - chunk_started)
                if remaining > 0:
                    self._stop.wait(remaining)

            ws.send("END")
            final_count_before = self._non_empty_final_count
            final_deadline = time.monotonic() + 90.0
            while (
                self._non_empty_final_count == final_count_before
                and time.monotonic() < final_deadline
            ):
                self._drain_ws(ws, timeout=0.5)
        finally:
            ws.close()

    def _drain_ws(
        self, ws: Any, *, timeout: float, stop_on_ready: bool = False
    ) -> bool:
        ws.settimeout(timeout)
        ready = False
        while True:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                return ready
            if not isinstance(raw, str):
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event_type = str(event.get("event_type") or "")
            if event_type == "asr_ready" and event.get("ready") is True:
                ready = True
                if stop_on_ready:
                    return True
            elif event_type == "final":
                text = str(
                    event.get("normalized_text") or event.get("text") or ""
                ).strip()
                if text:
                    with self._lock:
                        self._non_empty_final_count += 1
                        self._final_text_sha256.append(
                            hashlib.sha256(text.encode("utf-8")).hexdigest()
                        )
            elif event_type in {"provider_error", "recording_rejected", "error"}:
                raw_error_code = (
                    str(event.get("error_code") or "unknown").strip().lower()
                )
                safe_error_code = re.sub(r"[^a-z0-9_]+", "_", raw_error_code).strip("_")
                raise ContinuousAudioProtocolError(
                    f"asr_stream_{event_type}_{safe_error_code or 'unknown'}"
                )


def _process_metrics(pid: int | None) -> dict[str, Any]:
    if not pid:
        return {"available": False, "pid": pid, "rss_bytes": None, "cpu_percent": None}
    completed = subprocess.run(
        ["ps", "-o", "rss=,%cpu=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    fields = completed.stdout.split()
    if completed.returncode != 0 or len(fields) != 2:
        return {"available": False, "pid": pid, "rss_bytes": None, "cpu_percent": None}
    return {
        "available": True,
        "pid": pid,
        "rss_bytes": int(fields[0]) * 1024,
        "cpu_percent": float(fields[1].replace(",", ".")),
    }


def _gate(status: str, **detail: Any) -> dict[str, Any]:
    return {"status": status, **detail}


def new_report_skeleton(
    *,
    run_id: str,
    duration_seconds: float,
    target_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "run_id": run_id,
        "started_at": _now(),
        "duration_seconds": duration_seconds,
        "target_provenance": dict(target_provenance),
        "automated_gates": {
            name: _gate("not_run") for name in REQUIRED_AUTOMATED_GATES
        },
        "independent_gates": {
            "mac_sleep_wake": _gate(
                "blocked_manual_required",
                reason="requires_dedicated_mac_sleep_wake_and_unlock_evidence",
            ),
            "microphone_device_switch": _gate(
                "blocked_manual_required",
                reason="requires_real_device_and_tcc_observation",
            ),
        },
        "samples": [],
        "faults": [],
        "privacy_cost_flags": {
            "fixture_service_used_as_sut": False,
            "raw_audio_uploaded": False,
            "provider_secret_in_evidence": False,
            "remote_llm_called": False,
        },
    }


def evaluate_report(report: dict[str, Any]) -> dict[str, Any]:
    automated_blockers: list[str] = []
    requested_duration = float(report.get("duration_seconds") or 0)
    if requested_duration not in {3600.0, 10800.0}:
        automated_blockers.append("duration_is_not_1h_or_3h")
    if float(report.get("wall_clock_elapsed_seconds") or 0) < requested_duration:
        automated_blockers.append("wall_clock_elapsed_shorter_than_requested")
    if float(report.get("soak_wall_clock_elapsed_seconds") or 0) < requested_duration:
        automated_blockers.append("soak_wall_clock_elapsed_shorter_than_requested")
    if not bool((report.get("target_provenance") or {}).get("provenance_verified")):
        automated_blockers.append("target_provenance_not_verified")
    automated_gates = dict(report.get("automated_gates") or {})
    for name in REQUIRED_AUTOMATED_GATES:
        if (automated_gates.get(name) or {}).get("status") != "passed":
            automated_blockers.append(f"automated_gate_not_passed:{name}")
    fault_records: dict[str, list[dict[str, Any]]] = {}
    for raw in report.get("faults") or []:
        if isinstance(raw, dict):
            fault_records.setdefault(str(raw.get("fault") or ""), []).append(raw)
    for name in sorted(FAULT_NAMES):
        records = fault_records.get(name) or []
        if not records:
            automated_blockers.append(f"required_fault_missing:{name}")
            continue
        if not any(record.get("observed") is True for record in records):
            automated_blockers.append(f"required_fault_not_observed:{name}")
        if not any(
            record.get("status") == "passed" and record.get("recovered") is True
            for record in records
        ):
            automated_blockers.append(f"required_fault_not_recovered:{name}")
    independent_gates = dict(report.get("independent_gates") or {})
    overall_blockers = [
        f"independent_gate_not_passed:{name}"
        for name in ("mac_sleep_wake", "microphone_device_switch")
        if (independent_gates.get(name) or {}).get("status") != "passed"
    ]
    automated_eligible = not automated_blockers
    return {
        **report,
        "automated_acceptance_eligible": automated_eligible,
        "automated_blockers": sorted(set(automated_blockers)),
        "next006_overall_eligible": automated_eligible and not overall_blockers,
        "overall_blockers": sorted(set(overall_blockers)),
    }


def _recording_summary(
    status_code: int | None,
    audio_body: Mapping[str, Any] | None,
    snapshot_body: Mapping[str, Any],
) -> dict[str, Any]:
    audio = dict(audio_body or {})
    recordings = [
        item for item in audio.get("recordings") or [] if isinstance(item, dict)
    ]
    exports = [item for item in audio.get("exports") or [] if isinstance(item, dict)]
    journal_hashes = sorted(
        {
            str(item.get("journal_sha256"))
            for item in recordings
            if re.fullmatch(r"[0-9a-f]{64}", str(item.get("journal_sha256") or ""))
        }
    )
    snapshot_audio = dict(snapshot_body.get("audio") or {})
    return {
        "status_code": status_code,
        "status": audio.get("status"),
        "assembled": audio.get("assembled") is True,
        "file_size_bytes": int(audio.get("file_size_bytes") or 0),
        "chunk_count": int(audio.get("chunk_count") or 0),
        "duration_ms": int(audio.get("duration_ms") or 0),
        "durable_duration_ms": int(snapshot_audio.get("duration_ms") or 0),
        "tracks": sorted(str(item) for item in audio.get("tracks") or []),
        "recording_statuses": sorted(
            str(item.get("status") or "") for item in recordings
        ),
        "journal_sha256": journal_hashes,
        "export_statuses": sorted(str(item.get("status") or "") for item in exports),
        "active_recording_lease_count": sum(
            1
            for item in recordings
            if item.get("status") == "active"
            and (item.get("lease_owner") or item.get("lease_until_ms"))
        ),
    }


def _recording_gate(
    baseline: Mapping[str, Any],
    final: Mapping[str, Any],
    audio_input: Mapping[str, Any],
    requested_duration_seconds: float,
) -> dict[str, Any]:
    blockers: list[str] = []
    if audio_input.get("continuous") is not True:
        blockers.append("continuous_audio_input_not_observed")
    if int(audio_input.get("ready_stream_count") or 0) <= 0:
        blockers.append("real_asr_stream_not_ready")
    if int(audio_input.get("non_empty_final_count") or 0) <= 0:
        blockers.append("non_empty_asr_final_missing")
    if float(audio_input.get("estimated_audio_sent_seconds") or 0) < (
        requested_duration_seconds * 0.80
    ):
        blockers.append("continuous_audio_coverage_insufficient")
    if final.get("status_code") != 200:
        blockers.append("authenticated_audio_state_unavailable")
    if final.get("assembled") is not True:
        blockers.append("recording_not_assembled")
    if int(final.get("file_size_bytes") or 0) <= 44:
        blockers.append("recording_asset_missing_or_empty")
    if int(final.get("chunk_count") or 0) <= 0:
        blockers.append("durable_audio_chunks_missing")
    if int(final.get("durable_duration_ms") or 0) <= int(
        baseline.get("durable_duration_ms") or 0
    ):
        blockers.append("recording_duration_did_not_grow_during_soak")
    if int(final.get("chunk_count") or 0) <= int(baseline.get("chunk_count") or 0):
        blockers.append("recording_chunks_did_not_grow_during_soak")
    if int(final.get("durable_duration_ms") or 0) < int(
        requested_duration_seconds * 1_000 * 0.80
    ):
        blockers.append("durable_recording_coverage_insufficient")
    if not final.get("journal_sha256"):
        blockers.append("recording_journal_hash_missing")
    if "succeeded" not in set(final.get("export_statuses") or []):
        blockers.append("recording_export_not_succeeded")
    return _gate(
        "passed" if not blockers else "failed",
        blockers=sorted(set(blockers)),
        baseline=dict(baseline),
        final=dict(final),
        audio_input=dict(audio_input),
    )


def _queue_gate(queue: Mapping[str, Any]) -> dict[str, Any]:
    statuses = Counter(
        {
            str(key): int(value)
            for key, value in dict(queue.get("by_status") or {}).items()
        }
    )
    active_count = sum(statuses[name] for name in ("pending", "running", "retry_wait"))
    blockers: list[str] = []
    if queue.get("contract_present") is not True:
        blockers.append("durable_job_contract_missing")
    if int(queue.get("total") or 0) <= 0 or not queue.get("job_ids"):
        blockers.append("durable_job_evidence_missing")
    required_review_kinds = {"minutes", "approach", "index"}
    if not required_review_kinds.issubset(set(queue.get("review_kinds") or [])):
        blockers.append("required_review_jobs_missing")
    if active_count:
        blockers.append("durable_jobs_not_terminal")
    if statuses["failed"]:
        blockers.append("durable_jobs_failed")
    return _gate(
        "passed" if not blockers else "failed",
        blockers=sorted(set(blockers)),
        active_or_leased_status_count=active_count,
        failed_count=statuses["failed"],
        observed=dict(queue),
    )


def _durable_state_checks(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    before_persistence = dict(before.get("persistence") or {})
    after_persistence = dict(after.get("persistence") or {})
    before_recording = dict(before.get("recording") or {})
    after_recording = dict(after.get("recording") or {})
    before_queue = dict(before.get("queue") or {})
    after_queue = dict(after.get("queue") or {})
    checks = {
        "meeting_id_preserved": bool(before_persistence.get("meeting_id"))
        and before_persistence.get("meeting_id") == after_persistence.get("meeting_id")
        and after_persistence.get("meeting_id_matches") is True,
        "title_preserved": before_persistence.get("title")
        == after_persistence.get("title"),
        "last_seq_non_regressing": int(after_persistence.get("last_seq") or 0)
        >= int(before_persistence.get("last_seq") or 0),
        "recording_chunks_non_regressing": int(after_recording.get("chunk_count") or 0)
        >= int(before_recording.get("chunk_count") or 0),
        "recording_duration_non_regressing": int(
            after_recording.get("durable_duration_ms") or 0
        )
        >= int(before_recording.get("durable_duration_ms") or 0),
        "recording_journals_preserved": set(
            before_recording.get("journal_sha256") or []
        ).issubset(set(after_recording.get("journal_sha256") or [])),
        "job_count_non_regressing": int(after_queue.get("total") or 0)
        >= int(before_queue.get("total") or 0),
        "job_ids_preserved": set(before_queue.get("job_ids") or []).issubset(
            set(after_queue.get("job_ids") or [])
        ),
    }
    return {"passed": all(checks.values()), **checks}


def _probe_state(target: MeetingCopilotTarget, meeting_id: str) -> dict[str, Any]:
    client = target.client()
    health = client.request("GET", "/health", timeout=3)
    metrics = client.request("GET", "/metrics", timeout=3)
    asr_runtime = client.request("GET", "/providers/asr/runtime", timeout=3)
    snapshot = client.request("GET", f"/v2/meetings/{meeting_id}/snapshot", timeout=5)
    audio = client.request("GET", f"/v2/meetings/{meeting_id}/audio", timeout=5)
    snapshot_body = snapshot.json_body or {}
    jobs = list(snapshot_body.get("jobs") or [])
    review_jobs = list((snapshot_body.get("review_jobs") or {}).values())
    queue = Counter(str(item.get("status") or "unknown") for item in jobs)
    target.refresh_processes()
    backend_pid = int((target.backend or {}).get("pid") or 0) or None
    worker = target.find_worker()
    return {
        "captured_at": _now(),
        "health": {
            "status_code": health.status_code,
            "identity_verified": (health.json_body or {}).get("instance_proof")
            == health_proof(target.token),
            "latency_ms": health.latency_ms,
        },
        "metrics": {
            "status_code": metrics.status_code,
            "values": {
                str(key): value
                for key, value in (metrics.json_body or {}).items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            },
        },
        "asr_runtime": {
            "status_code": asr_runtime.status_code,
            "resident": (asr_runtime.json_body or {}).get("resident"),
        },
        "persistence": {
            "snapshot_status_code": snapshot.status_code,
            "meeting_id": snapshot_body.get("meeting_id"),
            "meeting_id_matches": snapshot_body.get("meeting_id") == meeting_id,
            "last_seq": snapshot_body.get("last_seq"),
            "segment_count": len(snapshot_body.get("segments") or []),
            "title": snapshot_body.get("title"),
        },
        "recording": {
            **_recording_summary(audio.status_code, audio.json_body, snapshot_body),
        },
        "queue": {
            "contract_present": isinstance(snapshot_body.get("jobs"), list)
            and isinstance(snapshot_body.get("review_jobs"), dict),
            "total": len(jobs),
            "by_status": dict(sorted(queue.items())),
            "job_ids": sorted(
                {
                    str(item.get("id") or "")
                    for item in jobs
                    if str(item.get("id") or "")
                }
            ),
            "kinds": sorted(
                {str(item.get("kind") or "") for item in jobs if item.get("kind")}
            ),
            "review_kinds": sorted(
                {
                    str(item.get("kind") or "")
                    for item in review_jobs
                    if item.get("kind")
                }
            ),
        },
        "processes": {
            "app_pid": target.app_process.pid if target.app_process else None,
            "backend": _process_metrics(backend_pid),
            "asr_worker_pid": int((worker or {}).get("pid") or 0) or None,
        },
    }


def _create_persistent_meeting(
    target: MeetingCopilotTarget,
    meeting_id: str,
    duration_seconds: float,
) -> dict[str, Any]:
    client = target.client()
    created = client.request(
        "POST",
        "/v2/meetings",
        {
            "meeting_id": meeting_id,
            "title": "NEXT-006 real SUT soak",
            "expected_duration_seconds": duration_seconds,
            "track_count": 1,
        },
    )
    if created.status_code != 201:
        raise RuntimeError(f"persistent meeting creation failed: {created.status_code}")
    prepared = client.request(
        "PUT",
        f"/v2/meetings/{meeting_id}/preparation",
        {
            "hotwords": ["NEXT-006", "SLO", "RTO", "RPO"],
            "input_source": "microphone",
            "input_device_id": "next006-controlled-audio",
            "input_device_name": "NEXT-006 controlled audio",
            "notice_acknowledged": True,
        },
    )
    if prepared.status_code != 200:
        raise RuntimeError(f"meeting preparation failed: {prepared.status_code}")
    return {
        "create_status": created.status_code,
        "preparation_status": prepared.status_code,
    }


def _persistent_title_roundtrip(
    target: MeetingCopilotTarget, meeting_id: str, title: str
) -> bool:
    updated = target.client().request(
        "PATCH", f"/v2/meetings/{meeting_id}", {"title": title}
    )
    if updated.status_code != 200:
        return False
    snapshot = target.client().request("GET", f"/v2/meetings/{meeting_id}/snapshot")
    return (
        snapshot.status_code == 200 and (snapshot.json_body or {}).get("title") == title
    )


def _run_backend_crash(
    target: MeetingCopilotTarget,
    meeting_id: str,
    audio_driver: ContinuousAudioDriver,
) -> dict[str, Any]:
    before = _probe_state(target, meeting_id)
    audio_before = audio_driver.snapshot()
    old_pid, old_pgid = target.kill_backend_for_fault()
    started = time.monotonic()
    try:
        recovered = target.recover_backend(old_pid)
        persisted = _probe_state(target, meeting_id)
        durable_checks = _durable_state_checks(before, persisted)
        observed = not pid_exists(old_pid)
        audio_deadline = time.monotonic() + 75.0
        audio_after = audio_driver.snapshot()
        while time.monotonic() < audio_deadline:
            ready_resumed = int(audio_after["ready_stream_count"]) > int(
                audio_before["ready_stream_count"]
            )
            audio_resumed = float(audio_after["estimated_audio_sent_seconds"]) > (
                float(audio_before["estimated_audio_sent_seconds"]) + 0.25
            )
            if ready_resumed and audio_resumed:
                break
            time.sleep(0.1)
            audio_after = audio_driver.snapshot()
        ready_resumed = int(audio_after["ready_stream_count"]) > int(
            audio_before["ready_stream_count"]
        )
        audio_resumed = float(audio_after["estimated_audio_sent_seconds"]) > (
            float(audio_before["estimated_audio_sent_seconds"]) + 0.25
        )
        passed = (
            observed
            and int(recovered["pid"]) != old_pid
            and durable_checks["passed"]
            and ready_resumed
            and audio_resumed
        )
        return {
            "status": "passed" if passed else "failed",
            "observed": observed,
            "recovered": passed,
            "old_pid": old_pid,
            "old_pgid": old_pgid,
            "new_pid": int(recovered["pid"]),
            "recovery_owner": target.recovery_owner,
            "rto_ms": round((time.monotonic() - started) * 1000, 3),
            "pre_fault_last_seq": before["persistence"]["last_seq"],
            "post_fault_last_seq": persisted["persistence"]["last_seq"],
            "durable_state_checks": durable_checks,
            "post_fault_asr_ready_observed": ready_resumed,
            "post_fault_audio_growth_observed": audio_resumed,
            "pre_fault_ready_stream_count": audio_before["ready_stream_count"],
            "post_fault_ready_stream_count": audio_after["ready_stream_count"],
            "pre_fault_audio_sent_seconds": audio_before[
                "estimated_audio_sent_seconds"
            ],
            "post_fault_audio_sent_seconds": audio_after[
                "estimated_audio_sent_seconds"
            ],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "observed": not pid_exists(old_pid),
            "recovered": False,
            "old_pid": old_pid,
            "old_pgid": old_pgid,
            "recovery_owner": target.recovery_owner,
            "error_type": type(exc).__name__,
            "rto_ms": round((time.monotonic() - started) * 1000, 3),
        }


def _configure_provider(
    target: MeetingCopilotTarget,
    relay: ProviderFaultRelay,
    config: Mapping[str, str],
) -> HttpResult:
    return target.client().request(
        "PUT",
        "/desktop/provider/config",
        {
            "base_url": relay.base_url,
            "api_key": config["api_key"],
            "model": config["model"],
            "api_style": config["api_style"],
            "provider_label": "next006_real_upstream_relay",
        },
    )


def _run_provider_fault(
    target: MeetingCopilotTarget,
    relay: ProviderFaultRelay,
    config: Mapping[str, str],
    *,
    mode: str,
) -> dict[str, Any]:
    if _configure_provider(target, relay, config).status_code != 200:
        return _gate(
            "failed", observed=False, recovered=False, reason="provider_config_failed"
        )
    before_count = int(relay.snapshot()["mode_counts"].get(mode, 0))
    relay.set_mode(mode)
    failed = target.client().request(
        "POST",
        "/providers/llm/probe",
        {},
        timeout=90,
        extra_headers={"x-meeting-copilot-verification": "1"},
    )
    relay.set_mode("passthrough")
    _configure_provider(target, relay, config)
    recovered = target.client().request(
        "POST",
        "/providers/llm/probe",
        {},
        timeout=90,
        extra_headers={"x-meeting-copilot-verification": "1"},
    )
    after_count = int(relay.snapshot()["mode_counts"].get(mode, 0))
    observed = failed.status_code == 502 and after_count > before_count
    recovery_observed = recovered.status_code == 200 and bool(
        (recovered.json_body or {}).get("ok")
    )
    passed = observed and recovery_observed
    return _gate(
        "passed" if passed else "failed",
        observed=observed,
        recovered=passed,
        injected_mode=mode,
        failed_probe_status=failed.status_code,
        failed_probe_error_type=failed.error_type,
        recovery_probe_status=recovered.status_code,
        real_upstream_response=bool((recovered.json_body or {}).get("ok")),
    )


def _run_disk_fault(target: MeetingCopilotTarget, meeting_id: str) -> dict[str, Any]:
    client = target.client()
    before = client.request("GET", f"/v2/meetings/{meeting_id}/snapshot")
    before_title = (before.json_body or {}).get("title")
    armed = client.request(
        "PUT",
        "/desktop/test/failpoints/storage-write",
        {
            "scope": "meeting_title_transaction",
            "failure": "enospc",
            "count": 1,
        },
    )
    failed = client.request(
        "PATCH",
        f"/v2/meetings/{meeting_id}",
        {"title": "NEXT-006 injected disk failure must not persist"},
    )
    after_failure = client.request("GET", f"/v2/meetings/{meeting_id}/snapshot")
    recovered_title = "NEXT-006 storage recovered"
    recovered = client.request(
        "PATCH", f"/v2/meetings/{meeting_id}", {"title": recovered_title}
    )
    final = client.request("GET", f"/v2/meetings/{meeting_id}/snapshot")
    failpoint = client.request("GET", "/desktop/test/failpoints/storage-write")
    observed = (
        armed.status_code == 200
        and failed.status_code == 507
        and (after_failure.json_body or {}).get("title") == before_title
        and int((failpoint.json_body or {}).get("hit_count") or 0) == 1
    )
    recovered_observed = (
        recovered.status_code == 200
        and (final.json_body or {}).get("title") == recovered_title
    )
    passed = observed and recovered_observed
    return _gate(
        "passed" if passed else "failed",
        observed=observed,
        recovered=passed,
        injector="authenticated_real_write_path_failpoint",
        write_boundary="V2Persistence.update_meeting_title.BEGIN_IMMEDIATE",
        injected_errno="ENOSPC",
        failed_write_status=failed.status_code,
        recovery_write_status=recovered.status_code,
        failpoint_hit_count=(failpoint.json_body or {}).get("hit_count"),
    )


def _run_asr_worker_crash(target: MeetingCopilotTarget) -> dict[str, Any]:
    worker = target.find_worker()
    if worker is None:
        return _gate(
            "failed",
            observed=False,
            recovered=False,
            reason="real_asr_worker_not_found",
        )
    old_pid = int(worker["pid"])
    os.kill(old_pid, signal.SIGKILL)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        runtime = target.client().request("GET", "/providers/asr/runtime", timeout=3)
        current = target.find_worker()
        if (
            runtime.status_code == 200
            and bool(
                ((runtime.json_body or {}).get("resident") or {}).get("process_ready")
            )
            and current is not None
            and int(current["pid"]) != old_pid
        ):
            observed = not pid_exists(old_pid)
            return _gate(
                "passed" if observed else "failed",
                observed=observed,
                recovered=observed,
                old_pid=old_pid,
                new_pid=int(current["pid"]),
                recovery_owner="meeting_copilot_funasr_resident_manager",
            )
        time.sleep(0.25)
    return _gate(
        "failed",
        observed=not pid_exists(old_pid),
        recovered=False,
        old_pid=old_pid,
        reason="asr_worker_not_restarted",
    )


def _run_app_crash(target: MeetingCopilotTarget, meeting_id: str) -> dict[str, Any]:
    if not isinstance(target, PackagedAppTarget):
        return _gate(
            "not_applicable",
            observed=False,
            recovered=False,
            reason="runtime_bundle_has_no_tauri_app",
        )
    before = _probe_state(target, meeting_id)
    try:
        old_pid, new_pid = target.crash_and_relaunch_app()
        state = _probe_state(target, meeting_id)
        durable_checks = _durable_state_checks(before, state)
        observed = not pid_exists(old_pid)
        passed = observed and new_pid != old_pid and durable_checks["passed"]
        return _gate(
            "passed" if passed else "failed",
            observed=observed,
            recovered=passed,
            old_app_pid=old_pid,
            new_app_pid=new_pid,
            recovery_owner="packaged_app_relaunch_with_isolated_persistent_home",
            durable_state_checks=durable_checks,
        )
    except Exception as exc:
        return _gate(
            "failed", observed=False, recovered=False, error_type=type(exc).__name__
        )


def _sqlite_integrity(data_dir: Path | None) -> dict[str, Any]:
    if data_dir is None:
        return _gate("failed", reason="data_dir_unavailable")
    candidates = list(data_dir.rglob("meeting_copilot.db")) if data_dir.exists() else []
    if not candidates:
        return _gate("failed", reason="meeting_copilot_database_not_found")
    try:
        connection = sqlite3.connect(f"file:{candidates[0]}?mode=ro", uri=True)
        result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        active_job_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'running', 'retry_wait')"
            ).fetchone()[0]
        )
        leased_job_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE lease_owner IS NOT NULL OR lease_until_ms IS NOT NULL"
            ).fetchone()[0]
        )
        active_recording_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM recording_sessions WHERE status = 'active' "
                "OR lease_owner IS NOT NULL OR lease_until_ms IS NOT NULL"
            ).fetchone()[0]
        )
        active_export_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM recording_exports WHERE status IN "
                "('pending', 'running', 'retry_wait') OR lease_owner IS NOT NULL "
                "OR lease_until_ms IS NOT NULL"
            ).fetchone()[0]
        )
        connection.close()
    except sqlite3.Error as exc:
        return _gate("failed", error_type=type(exc).__name__)
    passed = result == "ok" and not any(
        (
            active_job_count,
            leased_job_count,
            active_recording_count,
            active_export_count,
        )
    )
    return _gate(
        "passed" if passed else "failed",
        quick_check=result,
        database_sha256=_sha256_file(candidates[0]),
        active_job_count=active_job_count,
        leased_job_count=leased_job_count,
        active_recording_count=active_recording_count,
        active_export_count=active_export_count,
    )


def _end_meeting_and_wait_for_durable_state(
    target: MeetingCopilotTarget,
    meeting_id: str,
    *,
    timeout_seconds: float = 180.0,
) -> tuple[int | None, dict[str, Any]]:
    ended = target.client().request(
        "POST",
        f"/v2/meetings/{meeting_id}/end",
        {"action": "end_and_review"},
        timeout=30,
    )
    final_state = _probe_state(target, meeting_id)
    if ended.status_code not in {200, 202}:
        return ended.status_code, final_state
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        final_state = _probe_state(target, meeting_id)
        statuses = Counter(final_state["queue"]["by_status"])
        active_jobs = sum(
            int(statuses.get(name) or 0)
            for name in ("pending", "running", "retry_wait")
        )
        review_kinds = set(final_state["queue"]["review_kinds"])
        if (
            final_state["recording"]["assembled"]
            and {"minutes", "approach", "index"}.issubset(review_kinds)
            and active_jobs == 0
        ):
            break
        time.sleep(0.25)
    return ended.status_code, final_state


def _parse_fault(value: str) -> tuple[str, float]:
    name, separator, at_raw = value.partition("@")
    if separator != "@" or name not in FAULT_NAMES:
        raise argparse.ArgumentTypeError(
            f"fault must use name@seconds with name in {sorted(FAULT_NAMES)}"
        )
    try:
        at_seconds = float(at_raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("fault time must be numeric") from exc
    if at_seconds < 0:
        raise argparse.ArgumentTypeError("fault time must be non-negative")
    return name, at_seconds


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run_id contains unsafe characters")
    run_root = args.output_root.resolve() / args.run_id
    run_root.mkdir(parents=True, exist_ok=False)
    token = secrets.token_hex(32)
    if args.target == "packaged-app":
        if args.app_path is None or args.package_evidence is None:
            raise ValueError("packaged-app requires --app-path and --package-evidence")
        target: MeetingCopilotTarget = PackagedAppTarget(
            app_path=args.app_path,
            package_evidence_path=args.package_evidence,
            run_root=run_root,
            token=token,
        )
    else:
        if args.runtime_bundle is None:
            raise ValueError("runtime-bundle requires --runtime-bundle")
        target = RuntimeBundleTarget(
            runtime_bundle=args.runtime_bundle,
            run_root=run_root,
            token=token,
        )
    report = new_report_skeleton(
        run_id=args.run_id,
        duration_seconds=args.duration_seconds,
        target_provenance=target.provenance,
    )
    report["host"] = {
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }
    provider_config = None
    relay = None
    if args.provider_config is not None:
        provider_config = load_provider_config(args.provider_config)
        relay = ProviderFaultRelay(upstream_base_url=provider_config["base_url"])
        relay.start()
    meeting_id = f"next006_{hashlib.sha256(args.run_id.encode()).hexdigest()[:16]}"
    run_started = time.monotonic()
    soak_started: float | None = None
    audio_driver: ContinuousAudioDriver | None = None
    baseline: dict[str, Any] | None = None
    try:
        target.start()
        if relay is not None and provider_config is not None:
            configured = _configure_provider(target, relay, provider_config)
            report["provider_initial_configuration"] = {
                "status_code": configured.status_code,
                "configured_for_real_upstream_relay": configured.status_code == 200,
            }
        created = _create_persistent_meeting(target, meeting_id, args.duration_seconds)
        baseline = _probe_state(target, meeting_id)
        health_passed = (
            baseline["health"]["status_code"] == 200
            and baseline["health"]["identity_verified"]
            and baseline["metrics"]["status_code"] == 200
        )
        report["automated_gates"]["authenticated_health_metrics"] = _gate(
            "passed" if health_passed else "failed",
            health=baseline["health"],
            metrics_status=baseline["metrics"]["status_code"],
            metric_names=sorted(baseline["metrics"]["values"]),
        )
        persistence_passed = _persistent_title_roundtrip(
            target, meeting_id, "NEXT-006 persistent before faults"
        )
        report["automated_gates"]["persistent_session"] = _gate(
            "passed" if persistence_passed else "failed", **created
        )
        baseline = _probe_state(target, meeting_id)
        report["baseline_state"] = baseline
        report["automated_gates"]["recording_state"] = _gate(
            "failed", reason="continuous_audio_soak_not_completed"
        )
        report["automated_gates"]["queue_state"] = _gate(
            "failed", reason="durable_jobs_not_yet_observed"
        )

        audio_driver = ContinuousAudioDriver(
            target=target,
            meeting_id=meeting_id,
            audio_path=args.audio_path,
            requested_duration_seconds=args.duration_seconds,
        )
        audio_driver.start()
        soak_started = time.monotonic()

        pending_faults = sorted(args.fault, key=lambda item: item[1])
        completed_faults: set[str] = set()
        while time.monotonic() - soak_started < args.duration_seconds:
            elapsed = time.monotonic() - soak_started
            for name, at_seconds in pending_faults:
                if name in completed_faults or elapsed < at_seconds:
                    continue
                if name == "backend-crash":
                    result = _run_backend_crash(target, meeting_id, audio_driver)
                    gate_name = "backend_crash_recovery"
                elif name.startswith("provider-"):
                    if relay is None or provider_config is None:
                        result = _gate(
                            "failed",
                            observed=False,
                            recovered=False,
                            reason="real_provider_config_required",
                        )
                    else:
                        mode = {
                            "provider-disconnect": "disconnect",
                            "provider-429": "429",
                            "provider-5xx": "503",
                        }[name]
                        result = _run_provider_fault(
                            target, relay, provider_config, mode=mode
                        )
                    gate_name = {
                        "provider-disconnect": "provider_disconnect_recovery",
                        "provider-429": "provider_429_recovery",
                        "provider-5xx": "provider_5xx_recovery",
                    }[name]
                elif name == "disk-write":
                    audio_paused = audio_driver.pause()
                    if not audio_paused:
                        result = _gate(
                            "failed",
                            observed=False,
                            recovered=False,
                            reason="could_not_quiesce_competing_sqlite_writers",
                        )
                    else:
                        try:
                            result = _run_disk_fault(target, meeting_id)
                            result["continuous_audio_controlled_pause"] = True
                        finally:
                            audio_driver.resume()
                    gate_name = "disk_write_recovery"
                elif name == "asr-worker-crash":
                    result = _run_asr_worker_crash(target)
                    gate_name = "asr_worker_crash_recovery"
                else:
                    result = _run_app_crash(target, meeting_id)
                    gate_name = "app_crash_recovery"
                report["automated_gates"][gate_name] = result
                report["faults"].append(
                    {"fault": name, "scheduled_at_seconds": at_seconds, **result}
                )
                completed_faults.add(name)
            sample = _probe_state(target, meeting_id)
            sample["elapsed_seconds"] = round(elapsed, 3)
            sample["continuous_audio"] = audio_driver.snapshot()
            report["samples"].append(sample)
            time.sleep(
                min(
                    args.sample_interval_seconds,
                    max(0.01, args.duration_seconds - elapsed),
                )
            )

        audio_input = audio_driver.stop()
        report["continuous_audio"] = audio_input
        report["soak_wall_clock_elapsed_seconds"] = round(
            time.monotonic() - soak_started, 3
        )
        end_status, final_state = _end_meeting_and_wait_for_durable_state(
            target, meeting_id
        )
        report["final_state"] = final_state
        report["meeting_end_status"] = end_status
        report["automated_gates"]["recording_state"] = _recording_gate(
            baseline["recording"],
            final_state["recording"],
            audio_input,
            args.duration_seconds,
        )
        report["automated_gates"]["queue_state"] = _queue_gate(final_state["queue"])
    except Exception as exc:
        report["run_error"] = {"error_type": type(exc).__name__}
    finally:
        if audio_driver is not None:
            report["continuous_audio"] = audio_driver.stop()
        if soak_started is not None:
            report["soak_wall_clock_elapsed_seconds"] = round(
                time.monotonic() - soak_started, 3
            )
        if relay is not None:
            report["provider_fault_relay"] = relay.snapshot()
            report["privacy_cost_flags"]["remote_llm_called"] = (
                relay.snapshot()["forwarded_request_count"] > 0
            )
            relay.stop()
        try:
            cleanup_detail = target.stop()
        except Exception as exc:
            cleanup_detail = {
                "app_exited": False,
                "backend_exited": False,
                "asr_worker_exited": False,
                "error_type": type(exc).__name__,
            }
        cleanup = _gate(
            "passed"
            if all(
                cleanup_detail.get(name) is True
                for name in ("app_exited", "backend_exited", "asr_worker_exited")
            )
            else "failed",
            **cleanup_detail,
        )
        report["automated_gates"]["cleanup"] = cleanup
        report["automated_gates"]["sqlite_integrity"] = _sqlite_integrity(
            target.data_dir
        )
        report["finished_at"] = _now()
        report["wall_clock_elapsed_seconds"] = round(time.monotonic() - run_started, 3)
        report = evaluate_report(report)
        report_path = run_root / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report["report_path"] = str(report_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", required=True, choices=("packaged-app", "runtime-bundle")
    )
    parser.add_argument("--app-path", type=Path)
    parser.add_argument("--package-evidence", type=Path)
    parser.add_argument("--runtime-bundle", type=Path)
    parser.add_argument("--provider-config", type=Path)
    parser.add_argument(
        "--audio-path",
        type=Path,
        required=True,
        help="Controlled 16-bit mono 16kHz WAV continuously streamed through real bundled ASR.",
    )
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--sample-interval-seconds", type=float, default=5.0)
    parser.add_argument("--fault", type=_parse_fault, action="append", default=[])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--automated-gate-only",
        action="store_true",
        help="Return success for the automated subset while preserving manual overall blockers.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.duration_seconds <= 0 or args.sample_interval_seconds <= 0:
        raise SystemExit("duration and sample interval must be positive")
    report = run(args)
    print(report["report_path"])
    accepted = (
        report["automated_acceptance_eligible"]
        if args.automated_gate_only
        else report["next006_overall_eligible"]
    )
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
