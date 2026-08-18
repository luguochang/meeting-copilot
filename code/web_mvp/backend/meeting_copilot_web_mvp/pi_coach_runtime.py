"""Bounded JSONL bridge to the optional Pi realtime-coach runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Mapping

from meeting_copilot_web_mvp.coach_skills import coach_skill_payload


PROTOCOL = "talktrace-pi-coach-jsonl.v1"
PI_RUNTIME_ENV = "MEETING_COPILOT_REALTIME_COACH_RUNTIME"
PI_BRIDGE_ENTRY_ENV = "MEETING_COPILOT_PI_BRIDGE_ENTRY"
PI_NODE_EXECUTABLE_ENV = "MEETING_COPILOT_NODE_EXECUTABLE"
DEFAULT_TIMEOUT_SECONDS = 12.0
REALTIME_PROVIDER_TIMEOUT_SECONDS = 10.0
MAX_REQUEST_BYTES = 200_000
MAX_RESPONSE_BYTES = 200_000

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_BRIDGE_ENTRY = _REPO_ROOT / "code/agent_runtime/pi_coach_bridge/src/bridge.mjs"


class PiCoachRuntimeError(RuntimeError):
    """The optional Pi process, provider, or protocol could not produce a result."""

    def __init__(self, message: str, *, code: str = "pi_runtime_error") -> None:
        super().__init__(message)
        self.code = code


def configured_coach_runtime(value: Any = None) -> str:
    normalized = str(value if value is not None else os.environ.get(PI_RUNTIME_ENV, "pi")).strip().lower()
    return normalized if normalized in {"direct", "pi"} else "direct"


def default_pi_bridge_command() -> list[str] | None:
    configured_entry = str(os.environ.get(PI_BRIDGE_ENTRY_ENV) or "").strip()
    entry = Path(configured_entry) if configured_entry else _DEFAULT_BRIDGE_ENTRY
    if not entry.is_absolute() or not entry.is_file():
        return None
    configured_node = str(os.environ.get(PI_NODE_EXECUTABLE_ENV) or "").strip()
    node = configured_node or shutil.which("node")
    if not node:
        return None
    node_path = Path(node)
    if configured_node and (not node_path.is_absolute() or not node_path.is_file()):
        return None
    return [str(node), str(entry)]


def build_pi_coach_request(
    request: Any,
    *,
    request_id: str,
    base_url: str,
    api_key: str,
    model: str,
    api_style: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Serialize the already-validated intelligence request for the Pi bridge."""

    return {
        "request_id": str(request_id),
        "session_id": str(request.meeting_id),
        "provider": {
            "base_url": str(base_url),
            "api_key": str(api_key),
            "model": str(model),
            "api_style": str(api_style),
            "timeout_ms": int(
                max(1.0, min(float(timeout_seconds), REALTIME_PROVIDER_TIMEOUT_SECONDS)) * 1_000
            ),
        },
        "context": {
            "state_revision": int(request.state_revision),
            "new_paragraphs": [asdict(item) for item in request.new_paragraphs],
            # Pi is a decision lane, not the transcript index. Keep its
            # prompt small and let the bounded search tool retrieve older
            # evidence only when the current decision needs it.
            "context_paragraphs": [asdict(item) for item in request.context_paragraphs[-2:]],
            # Historical evidence is available through Pi's bounded search
            # tool. Keep the initial prompt small; replaying a long meeting
            # here made later coach turns pay for the same text repeatedly.
            "retrieval_paragraphs": [asdict(item) for item in request.retrieval_paragraphs[-8:]],
            "semantic_windows": [asdict(item) for item in request.semantic_windows[-2:]],
            "rolling_state": dict(request.rolling_state),
            "meeting_goal": request.meeting_goal,
            "coach_skill": coach_skill_payload(request.coach_skill_id),
        },
    }


class PiCoachSidecar:
    """Lazy, persistent Pi process with one bounded in-flight JSONL request."""

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        process_factory: Any = subprocess.Popen,
    ) -> None:
        self.command = list(command) if command is not None else default_pi_bridge_command()
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self._process_factory = process_factory
        self._process: Any | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._responses: queue.Queue[Mapping[str, Any]] = queue.Queue(maxsize=8)
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self.stderr_bytes_drained = 0

    @property
    def available(self) -> bool:
        return bool(self.command)

    async def evaluate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._evaluate_sync, dict(payload))

    def _evaluate_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise PiCoachRuntimeError("Pi request exceeds the byte budget", code="pi_request_too_large")
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            raise PiCoachRuntimeError("Pi request_id is required", code="pi_invalid_request")
        with self._lock:
            self._ensure_started_unlocked()
            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                raise PiCoachRuntimeError("Pi bridge is not running", code="pi_unavailable")
            try:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._stop_unlocked()
                raise PiCoachRuntimeError("Pi bridge input closed", code="pi_transport_error") from exc
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stop_unlocked()
                    raise PiCoachRuntimeError("Pi bridge response timed out", code="pi_timeout")
                try:
                    response = dict(self._responses.get(timeout=remaining))
                except queue.Empty as exc:
                    self._stop_unlocked()
                    raise PiCoachRuntimeError("Pi bridge response timed out", code="pi_timeout") from exc
                bridge_error = response.get("_bridge_error")
                if bridge_error:
                    self._stop_unlocked()
                    raise PiCoachRuntimeError(str(bridge_error), code="pi_transport_error")
                if str(response.get("request_id") or "") != request_id:
                    self._stop_unlocked()
                    raise PiCoachRuntimeError("Pi response request_id mismatch", code="pi_protocol_error")
                return self._validate_response(response)

    def _ensure_started_unlocked(self) -> None:
        if self._process is not None and self._process.poll() is None and self._ready.is_set():
            return
        self._stop_unlocked()
        if not self.command:
            raise PiCoachRuntimeError(
                "Pi bridge or Node.js is not installed",
                code="pi_unavailable",
            )
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = self._process_factory(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            self._process = None
            raise PiCoachRuntimeError("Pi bridge failed to start", code="pi_spawn_failed") from exc
        self._ready.clear()
        self._responses = queue.Queue(maxsize=8)
        self._reader = threading.Thread(target=self._read_stdout, name="pi-coach-stdout", daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="pi-coach-stderr", daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        if not self._ready.wait(timeout=min(8.0, self.timeout_seconds)):
            self._stop_unlocked()
            raise PiCoachRuntimeError("Pi bridge readiness timed out", code="pi_startup_timeout")

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                if len(line.encode("utf-8", errors="replace")) > MAX_RESPONSE_BYTES:
                    self._put_response({"_bridge_error": "Pi bridge response exceeded the byte budget"})
                    return
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self._put_response({"_bridge_error": "Pi bridge returned invalid JSON"})
                    return
                if not isinstance(payload, Mapping) or payload.get("protocol") != PROTOCOL:
                    self._put_response({"_bridge_error": "Pi bridge protocol mismatch"})
                    return
                if payload.get("event") == "ready":
                    self._ready.set()
                    continue
                self._put_response(payload)
        finally:
            if not self._ready.is_set():
                self._ready.set()
            self._put_response({"_bridge_error": "Pi bridge output closed"})

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for chunk in iter(lambda: process.stderr.read(4096), ""):
            self.stderr_bytes_drained += len(chunk.encode("utf-8", errors="replace"))

    def _put_response(self, payload: Mapping[str, Any]) -> None:
        try:
            self._responses.put_nowait(payload)
        except queue.Full:
            pass

    @staticmethod
    def _validate_response(response: dict[str, Any]) -> dict[str, Any]:
        if response.get("protocol") != PROTOCOL:
            raise PiCoachRuntimeError("Pi response protocol mismatch", code="pi_protocol_error")
        if response.get("ok") is not True:
            error = response.get("error") if isinstance(response.get("error"), Mapping) else {}
            code = str(error.get("code") or "pi_agent_error")[:80]
            message = str(error.get("message") or "Pi agent failed")[:500]
            raise PiCoachRuntimeError(message, code=code)
        if response.get("action") not in {"intervention", "silent"}:
            raise PiCoachRuntimeError("Pi response action is invalid", code="pi_protocol_error")
        if response.get("action") == "intervention" and not isinstance(response.get("intervention"), Mapping):
            raise PiCoachRuntimeError("Pi intervention is missing", code="pi_protocol_error")
        return response

    def close(self) -> None:
        with self._lock:
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        process = self._process
        self._process = None
        self._ready.clear()
        if process is None:
            return
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
