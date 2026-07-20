"""Bounded process-resident FunASR pool and per-stream session adapter.

Each worker owns one expensive model and at most one active streaming cache.
Two lazy process slots allow microphone and system audio to run concurrently
without sharing model state.  The protocol is newline-delimited JSON; raw PCM
is base64 encoded in ``audio`` commands to keep boundaries recoverable.
"""

from __future__ import annotations

import base64
import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


WRITE_QUEUE_MAX_COMMANDS = 256
PROCESS_WAIT_TIMEOUT_S = 5.0
SESSION_ABORT_TIMEOUT_S = 2.0
SESSION_FINALIZE_MAX_TIMEOUT_S = 30.0
DEFAULT_RESIDENT_WORKER_COUNT = 2
MAX_RESIDENT_WORKER_COUNT = 2
MAX_SESSION_HOTWORDS = 50
MAX_SESSION_HOTWORD_CHARACTERS = 64


class FunasrResidentBusyError(RuntimeError):
    """Raised when all bounded resident workers already own a session."""


class FunasrResidentUnavailableError(RuntimeError):
    """Raised when the resident worker cannot safely accept more audio."""


@dataclass
class _WorkerGeneration:
    number: int
    process: Any
    write_queue: "queue.Queue[dict[str, Any] | None]"
    ready_event: threading.Event = field(default_factory=threading.Event)
    terminal: bool = False
    writer: threading.Thread | None = None
    reader: threading.Thread | None = None
    stderr_reader: threading.Thread | None = None
    stderr_lines: list[str] = field(default_factory=list)
    started_at_monotonic: float = field(default_factory=time.monotonic)
    ready_at_monotonic: float | None = None
    exit_code: int | None = None


class FunasrResidentSession:
    """StreamRecognizer-compatible view of one resident-worker session."""

    provider = "funasr_realtime"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons: list[str] = []

    def __init__(self, manager: "_FunasrResidentWorkerSlot", session_id: str):
        self._manager = manager
        self.session_id = session_id
        self.worker_id = manager.worker_id
        self._events: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._ready_event = threading.Event()
        self._ended_event = threading.Event()
        self._aborted_event = threading.Event()
        self._state_lock = threading.Lock()
        self._terminal = False
        self._finalize_started = False
        self._abort_started = False
        self._error: str | None = None
        self._seq = 0
        self.worker_diagnostics: dict[str, Any] = {}
        self.shutdown_diagnostics: dict[str, Any] = {}

    def _receive(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "")
        if event_type == "session_started":
            self._ready_event.set()
            return
        if event_type == "telemetry":
            self.worker_diagnostics = {
                key: value
                for key, value in event.items()
                if key not in {"event_type", "session_id"}
            }
            return
        if event_type == "session_ended":
            self._ended_event.set()
            return
        if event_type == "session_aborted":
            self._aborted_event.set()
            return
        if event_type == "error":
            self._fail(str(event.get("message") or event.get("error_code") or "resident worker error"))
            return
        if event_type in {"partial", "final"}:
            self._events.put(event)

    def _fail(self, message: str) -> None:
        with self._state_lock:
            if self._error is None:
                self._error = message
            self._terminal = True
        self._ready_event.set()
        self._ended_event.set()
        self._aborted_event.set()

    def _raise_if_failed(self) -> None:
        with self._state_lock:
            error = self._error
        if error:
            raise FunasrResidentUnavailableError(error)

    def wait_ready(self, timeout: float | None = None) -> bool:
        if not self._ready_event.wait(timeout):
            return False
        with self._state_lock:
            return self._error is None and not self._terminal

    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]:
        self._raise_if_failed()
        with self._state_lock:
            if self._terminal or self._finalize_started or self._abort_started:
                raise FunasrResidentUnavailableError("FunASR resident session is closed")
            self._seq += 1
            sequence = self._seq
        self._manager.send_audio(self, pcm)
        events = self._drain_events(default_confidence=0.8)
        if events:
            return events
        return [{
            "event_type": "partial",
            "segment_id": f"stream_seg_{self.session_id}",
            "text": "",
            "start_ms": (sequence - 1) * 300,
            "end_ms": sequence * 300,
            "confidence": 0.7,
        }]

    def finalize(self) -> list[dict[str, Any]]:
        with self._state_lock:
            if self._finalize_started or self._abort_started:
                return []
            self._finalize_started = True
        started_at = time.monotonic()
        self._manager.end_session(self)
        self._raise_if_failed()
        events = self._drain_events(default_confidence=0.9)
        if not events:
            events.append({
                "event_type": "final",
                "segment_id": f"stream_seg_{self.session_id}",
                "text": "",
                "confidence": 0.9,
            })
        with self._state_lock:
            self._terminal = True
        self.shutdown_diagnostics = {
            "abort": False,
            "process_reused": True,
            "audio_chunks_enqueued": self._seq,
            "total_ms": round((time.monotonic() - started_at) * 1_000, 2),
            **({"worker": dict(self.worker_diagnostics)} if self.worker_diagnostics else {}),
        }
        return events

    def abort(self) -> None:
        with self._state_lock:
            if self._abort_started or self._finalize_started:
                return
            self._abort_started = True
        started_at = time.monotonic()
        self._manager.abort_session(self)
        with self._state_lock:
            self._terminal = True
        self.shutdown_diagnostics = {
            "abort": True,
            "process_reused": True,
            "audio_chunks_enqueued": self._seq,
            "total_ms": round((time.monotonic() - started_at) * 1_000, 2),
        }

    def _drain_events(self, *, default_confidence: float) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            try:
                event = dict(self._events.get_nowait())
            except queue.Empty:
                return events
            raw_segment_id = str(event.get("segment_id") or "").strip()
            if raw_segment_id.startswith(f"{self.session_id}_"):
                segment_id = raw_segment_id
            elif raw_segment_id:
                segment_id = f"{self.session_id}_{raw_segment_id}"
            else:
                segment_id = f"stream_seg_{self.session_id}"
            event["segment_id"] = segment_id
            event.setdefault("confidence", default_confidence)
            events.append(event)


class _FunasrResidentWorkerSlot:
    """Own one model process and serialize sessions within that process."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        worker_id: int = 1,
    ) -> None:
        self.worker_id = worker_id
        self._command = [*command, "--resident"]
        self._environment = dict(environment) if environment is not None else None
        self._popen_factory = popen_factory
        self._lock = threading.RLock()
        self._generation: _WorkerGeneration | None = None
        self._active_session: FunasrResidentSession | None = None
        self._shutdown = False
        self._automatic_restart_used = False
        self._last_exit_code: int | None = None
        self._last_error: str | None = None
        self.process_start_count = 0
        self.completed_session_count = 0

    def start(self) -> None:
        """Spawn the model process without claiming a meeting session."""
        with self._lock:
            if self._shutdown:
                raise FunasrResidentUnavailableError("FunASR resident worker manager is shut down")
            self._ensure_generation_locked()

    def wait_process_ready(self, timeout: float | None = None) -> bool:
        with self._lock:
            generation = self._generation
            if generation is None or generation.terminal:
                return False
        if not generation.ready_event.wait(timeout):
            return False
        with self._lock:
            return (
                self._generation is generation
                and not generation.terminal
                and generation.process.poll() is None
                and generation.ready_at_monotonic is not None
            )

    def status(self) -> dict[str, Any]:
        with self._lock:
            generation = self._generation
            process_running = bool(
                generation is not None
                and not generation.terminal
                and generation.process.poll() is None
            )
            process_ready = bool(
                process_running
                and generation is not None
                and generation.ready_at_monotonic is not None
            )
            return {
                "schema_version": "funasr_resident_status.v1",
                "worker_id": self.worker_id,
                "spawned": generation is not None,
                "process_running": process_running,
                "process_ready": process_ready,
                "pid": int(getattr(generation.process, "pid", 0) or 0) if process_running and generation else None,
                "generation": generation.number if generation is not None else None,
                "active_session_id": self._active_session.session_id if self._active_session is not None else None,
                "process_start_count": self.process_start_count,
                "completed_session_count": self.completed_session_count,
                "last_exit_code": self._last_exit_code,
                "last_error": self._last_error,
            }

    def create_session(
        self,
        session_id: str,
        *,
        hotwords: Sequence[str] = (),
    ) -> FunasrResidentSession:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        normalized_hotwords = _normalize_session_hotwords(hotwords)
        with self._lock:
            if self._shutdown:
                raise FunasrResidentUnavailableError("FunASR resident worker manager is shut down")
            if self._active_session is not None:
                raise FunasrResidentBusyError(
                    f"FunASR resident worker is already serving {self._active_session.session_id}"
                )
            generation = self._ensure_generation_locked()
            session = FunasrResidentSession(self, session_id)
            self._active_session = session
            try:
                start_command: dict[str, Any] = {
                    "command": "start_session",
                    "session_id": session_id,
                }
                if normalized_hotwords:
                    start_command["hotwords"] = list(normalized_hotwords)
                self._enqueue_locked(generation, start_command)
            except Exception:
                self._active_session = None
                raise
            return session

    def send_audio(self, session: FunasrResidentSession, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            generation = self._require_active_generation_locked(session)
            self._enqueue_locked(generation, {
                "command": "audio",
                "session_id": session.session_id,
                "pcm_base64": base64.b64encode(pcm).decode("ascii"),
            })

    def end_session(self, session: FunasrResidentSession) -> None:
        with self._lock:
            generation = self._require_active_generation_locked(session)
            self._enqueue_locked(generation, {
                "command": "end_session",
                "session_id": session.session_id,
            })
        timeout_s = min(
            SESSION_FINALIZE_MAX_TIMEOUT_S,
            max(PROCESS_WAIT_TIMEOUT_S, 5.0 + session._seq * 0.3 * 2.0),
        )
        if not session._ended_event.wait(timeout_s):
            session._fail("FunASR resident session finalization timed out")
            self._recycle_unresponsive_generation(generation, session)
        with self._lock:
            if self._active_session is session:
                self._active_session = None
                self.completed_session_count += 1
                self._automatic_restart_used = False

    def abort_session(self, session: FunasrResidentSession) -> None:
        with self._lock:
            if self._active_session is not session:
                return
            generation = self._generation
            if generation is None or generation.terminal:
                self._active_session = None
                return
            self._enqueue_locked(generation, {
                "command": "abort_session",
                "session_id": session.session_id,
            })
        if not session._aborted_event.wait(SESSION_ABORT_TIMEOUT_S):
            self._recycle_unresponsive_generation(generation, session)
        with self._lock:
            if self._active_session is session:
                self._active_session = None

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            generation = self._generation
            active = self._active_session
            if generation is None:
                self._active_session = None
                return

            abort_requested = active is not None and not generation.terminal and generation.process.poll() is None
            if abort_requested:
                try:
                    self._enqueue_locked(generation, {
                        "command": "abort_session",
                        "session_id": active.session_id,
                    })
                except FunasrResidentUnavailableError:
                    abort_requested = False

        if abort_requested and active is not None:
            if not active._aborted_event.wait(SESSION_ABORT_TIMEOUT_S):
                active._fail("FunASR resident worker did not acknowledge session abort during shutdown")
                self._recycle_unresponsive_generation(generation, active)
                self._join_and_reap(generation)
                return

        with self._lock:
            if self._active_session is active:
                self._active_session = None
            if active is not None:
                active._fail("FunASR resident worker shut down")
            if generation.terminal or generation.process.poll() is not None:
                return
            generation.terminal = True
            try:
                generation.write_queue.put_nowait({"command": "shutdown"})
            except queue.Full:
                pass
            try:
                generation.write_queue.put_nowait(None)
            except queue.Full:
                pass
        self._join_and_reap(generation)

    def _ensure_generation_locked(self) -> _WorkerGeneration:
        generation = self._generation
        if generation is not None and not generation.terminal and generation.process.poll() is None:
            return generation
        return self._start_generation_locked(1 if generation is None else generation.number + 1)

    def _start_generation_locked(self, number: int) -> _WorkerGeneration:
        process = self._popen_factory(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._environment,
        )
        generation = _WorkerGeneration(
            number=number,
            process=process,
            write_queue=queue.Queue(maxsize=WRITE_QUEUE_MAX_COMMANDS),
        )
        generation.writer = threading.Thread(
            target=self._writer_loop,
            args=(generation,),
            daemon=True,
            name=f"funasr-resident-{self.worker_id}-writer-{number}",
        )
        generation.reader = threading.Thread(
            target=self._reader_loop,
            args=(generation,),
            daemon=True,
            name=f"funasr-resident-{self.worker_id}-reader-{number}",
        )
        generation.stderr_reader = threading.Thread(
            target=self._stderr_loop,
            args=(generation,),
            daemon=True,
            name=f"funasr-resident-{self.worker_id}-stderr-{number}",
        )
        self._generation = generation
        self.process_start_count += 1
        generation.writer.start()
        generation.reader.start()
        generation.stderr_reader.start()
        return generation

    def _enqueue_locked(self, generation: _WorkerGeneration, command: dict[str, Any]) -> None:
        if generation.terminal or generation.process.poll() is not None:
            raise FunasrResidentUnavailableError("FunASR resident worker is not running")
        try:
            generation.write_queue.put_nowait(command)
        except queue.Full as exc:
            raise FunasrResidentUnavailableError("FunASR resident command queue is full") from exc

    def _require_active_generation_locked(
        self,
        session: FunasrResidentSession,
    ) -> _WorkerGeneration:
        if self._active_session is not session:
            raise FunasrResidentUnavailableError("FunASR resident session is no longer active")
        generation = self._generation
        if generation is None or generation.terminal or generation.process.poll() is not None:
            raise FunasrResidentUnavailableError("FunASR resident worker is not running")
        return generation

    def _writer_loop(self, generation: _WorkerGeneration) -> None:
        process = generation.process
        try:
            while True:
                command = generation.write_queue.get()
                if command is None:
                    return
                payload = (json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8")
                process.stdin.write(payload)
                process.stdin.flush()
        except Exception:
            return

    def _reader_loop(self, generation: _WorkerGeneration) -> None:
        process = generation.process
        try:
            for raw_line in process.stdout:
                if isinstance(raw_line, bytes):
                    raw_line = raw_line.decode("utf-8", errors="replace")
                try:
                    event = json.loads(raw_line)
                except (TypeError, json.JSONDecodeError):
                    continue
                self._dispatch_event(generation, event)
        except Exception:
            pass
        exit_code = process.poll()
        if exit_code is None:
            try:
                exit_code = process.wait(timeout=0.1)
            except Exception:
                exit_code = -1
        self._handle_process_exit(generation, int(exit_code))

    def _stderr_loop(self, generation: _WorkerGeneration) -> None:
        try:
            for raw_line in generation.process.stderr:
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                else:
                    line = str(raw_line).rstrip()
                if line:
                    generation.stderr_lines.append(line)
                    if len(generation.stderr_lines) > 100:
                        del generation.stderr_lines[:-100]
        except Exception:
            return

    def _dispatch_event(self, generation: _WorkerGeneration, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "")
        with self._lock:
            if self._generation is not generation or generation.terminal:
                return
            if event_type == "ready" and not event.get("session_id"):
                generation.ready_at_monotonic = time.monotonic()
                generation.ready_event.set()
                return
            session = self._active_session
            event_session_id = str(event.get("session_id") or "")
            if session is None or event_session_id != session.session_id:
                return
        session._receive(event)

    def _handle_process_exit(self, generation: _WorkerGeneration, exit_code: int) -> None:
        with self._lock:
            if self._generation is not generation or generation.terminal:
                return
            generation.terminal = True
            generation.exit_code = exit_code
            generation.ready_event.set()
            self._last_exit_code = exit_code
            self._last_error = f"worker_exited_with_code_{exit_code}"
            try:
                generation.write_queue.put_nowait(None)
            except queue.Full:
                pass
            active = self._active_session
            self._active_session = None
            if active is not None:
                stderr_tail = " | ".join(generation.stderr_lines[-3:])
                detail = f"FunASR resident worker exited with code {exit_code}"
                if stderr_tail:
                    detail += f": {stderr_tail}"
                active._fail(detail)
            if self._shutdown or self._automatic_restart_used:
                return
            self._automatic_restart_used = True
            try:
                self._start_generation_locked(generation.number + 1)
            except Exception:
                return

    def _recycle_unresponsive_generation(
        self,
        generation: _WorkerGeneration,
        session: FunasrResidentSession,
    ) -> None:
        with self._lock:
            if self._generation is not generation:
                return
            generation.terminal = True
            try:
                generation.write_queue.put_nowait(None)
            except queue.Full:
                pass
            if self._active_session is session:
                self._active_session = None
            self._terminate_process(generation.process)
            if not self._shutdown:
                try:
                    self._start_generation_locked(generation.number + 1)
                except Exception:
                    pass

    def _join_and_reap(self, generation: _WorkerGeneration) -> None:
        writer = generation.writer
        if writer is not None:
            writer.join(timeout=PROCESS_WAIT_TIMEOUT_S)
        try:
            generation.process.wait(timeout=PROCESS_WAIT_TIMEOUT_S)
        except Exception:
            self._terminate_process(generation.process)
        for thread in (generation.reader, generation.stderr_reader):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)

    @staticmethod
    def _terminate_process(process: Any) -> None:
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=PROCESS_WAIT_TIMEOUT_S)
        except Exception:
            pass

    def _is_idle(self) -> bool:
        with self._lock:
            return not self._shutdown and self._active_session is None


class FunasrResidentWorkerManager:
    """Bounded pool of single-session resident FunASR model processes.

    A FunASR online model keeps mutable streaming cache, so sharing one model
    instance across microphone and system-audio sessions is unsafe.  The pool
    keeps that process-level isolation, starts the second worker lazily, and
    pins every session object to one slot for its complete lifetime.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        max_workers: int = DEFAULT_RESIDENT_WORKER_COUNT,
    ) -> None:
        if (
            not isinstance(max_workers, int)
            or isinstance(max_workers, bool)
            or not 1 <= max_workers <= MAX_RESIDENT_WORKER_COUNT
        ):
            raise ValueError(
                f"max_workers must be between 1 and {MAX_RESIDENT_WORKER_COUNT}"
            )
        self._command = tuple(command)
        self._environment = dict(environment) if environment is not None else None
        self._popen_factory = popen_factory
        self._max_workers = max_workers
        self._lock = threading.RLock()
        self._slots: list[_FunasrResidentWorkerSlot] = []
        self._shutdown = False

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def process_start_count(self) -> int:
        with self._lock:
            return sum(slot.process_start_count for slot in self._slots)

    @property
    def completed_session_count(self) -> int:
        with self._lock:
            return sum(slot.completed_session_count for slot in self._slots)

    @property
    def _generation(self) -> _WorkerGeneration | None:
        """Compatibility view of the prewarmed/primary worker generation."""
        with self._lock:
            return self._slots[0]._generation if self._slots else None

    def start(self) -> None:
        """Prewarm one worker; the second worker remains lazy until dual-track use."""
        with self._lock:
            if self._shutdown:
                raise FunasrResidentUnavailableError(
                    "FunASR resident worker manager is shut down"
                )
            slot = self._primary_slot_locked()
            slot.start()

    def wait_process_ready(self, timeout: float | None = None) -> bool:
        with self._lock:
            if not self._slots:
                return False
            slot = self._slots[0]
        return slot.wait_process_ready(timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            slot_statuses = [slot.status() for slot in self._slots]
            max_workers = self._max_workers
            shutdown = self._shutdown

        active_session_ids = [
            str(status["active_session_id"])
            for status in slot_statuses
            if status.get("active_session_id") is not None
        ]
        running_worker_count = sum(bool(status["process_running"]) for status in slot_statuses)
        ready_worker_count = sum(bool(status["process_ready"]) for status in slot_statuses)
        primary = slot_statuses[0] if slot_statuses else {}
        latest_failure = next(
            (
                status
                for status in reversed(slot_statuses)
                if status.get("last_exit_code") is not None or status.get("last_error") is not None
            ),
            {},
        )
        return {
            "schema_version": "funasr_resident_status.v1",
            "spawned": any(bool(status["spawned"]) for status in slot_statuses),
            "process_running": running_worker_count > 0,
            "process_ready": ready_worker_count > 0,
            "pid": primary.get("pid"),
            "generation": primary.get("generation"),
            "active_session_id": active_session_ids[0] if active_session_ids else None,
            "process_start_count": sum(
                int(status["process_start_count"]) for status in slot_statuses
            ),
            "completed_session_count": sum(
                int(status["completed_session_count"]) for status in slot_statuses
            ),
            "last_exit_code": latest_failure.get("last_exit_code"),
            "last_error": latest_failure.get("last_error"),
            "pool_mode": "bounded_process_per_session",
            "max_worker_count": max_workers,
            "worker_count": len(slot_statuses),
            "running_worker_count": running_worker_count,
            "ready_worker_count": ready_worker_count,
            "active_session_count": len(active_session_ids),
            "active_session_ids": active_session_ids,
            "available_worker_count": 0 if shutdown else max_workers - len(active_session_ids),
            "workers": slot_statuses,
            "shutdown": shutdown,
        }

    def create_session(
        self,
        session_id: str,
        *,
        hotwords: Sequence[str] = (),
    ) -> FunasrResidentSession:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        normalized_hotwords = _normalize_session_hotwords(hotwords)
        with self._lock:
            if self._shutdown:
                raise FunasrResidentUnavailableError(
                    "FunASR resident worker manager is shut down"
                )
            slot = next((candidate for candidate in self._slots if candidate._is_idle()), None)
            if slot is None:
                if len(self._slots) >= self._max_workers:
                    raise FunasrResidentBusyError(
                        "FunASR resident worker pool has no available capacity"
                    )
                slot = self._new_slot_locked()
            return slot.create_session(session_id, hotwords=normalized_hotwords)

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            slots = tuple(self._slots)
        for slot in slots:
            slot.shutdown()

    def _primary_slot_locked(self) -> _FunasrResidentWorkerSlot:
        if self._slots:
            return self._slots[0]
        return self._new_slot_locked()

    def _new_slot_locked(self) -> _FunasrResidentWorkerSlot:
        if len(self._slots) >= self._max_workers:
            raise FunasrResidentBusyError(
                "FunASR resident worker pool has no available capacity"
            )
        slot = _FunasrResidentWorkerSlot(
            self._command,
            environment=self._environment,
            popen_factory=self._popen_factory,
            worker_id=len(self._slots) + 1,
        )
        self._slots.append(slot)
        return slot


def _normalize_session_hotwords(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or len(values) > MAX_SESSION_HOTWORDS:
        raise ValueError("session hotwords must be a bounded sequence")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_item in values:
        if not isinstance(raw_item, str):
            raise ValueError("session hotwords must contain strings")
        item = raw_item.strip()
        key = item.casefold()
        if (
            not item
            or len(item) > MAX_SESSION_HOTWORD_CHARACTERS
            or any(ord(character) < 32 for character in item)
        ):
            raise ValueError("session hotword is invalid")
        if key not in seen:
            seen.add(key)
            normalized.append(item)
    return tuple(normalized)
