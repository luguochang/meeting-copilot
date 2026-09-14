"""Bounded process-resident FunASR pool and per-stream session adapter.

Each worker owns one expensive model and at most one active streaming cache.
Two lazy process slots allow microphone and system audio to run concurrently
without sharing model state.  The protocol is newline-delimited JSON; raw PCM
is base64 encoded in ``audio`` commands to keep boundaries recoverable.
"""

from __future__ import annotations

import base64
from collections import deque
import json
import math
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


WRITE_QUEUE_MAX_COMMANDS = 256
WRITE_QUEUE_CONTROL_RESERVE = 4
WRITE_QUEUE_MAX_AUDIO_COMMANDS = WRITE_QUEUE_MAX_COMMANDS - WRITE_QUEUE_CONTROL_RESERVE
PROCESS_WAIT_TIMEOUT_S = 5.0
SESSION_ABORT_TIMEOUT_S = 2.0
SESSION_FINALIZE_MAX_TIMEOUT_S = 30.0
FUNASR_BOUNDARY_ACK_TIMEOUT_S = 1.25
DEFAULT_RESIDENT_WORKER_COUNT = 2
MAX_RESIDENT_WORKER_COUNT = 2
MAX_SESSION_HOTWORDS = 50
MAX_SESSION_HOTWORD_CHARACTERS = 64
MAX_BOUNDARY_DIAGNOSTICS = 128
FUNASR_CONFIDENCE_SOURCE_REPORTED = "funasr_worker_reported_score"
FUNASR_CONFIDENCE_SOURCE_UNAVAILABLE = "funasr_worker_no_score"


class FunasrResidentBusyError(RuntimeError):
    """Raised when all bounded resident workers already own a session."""


class FunasrResidentUnavailableError(RuntimeError):
    """Raised when the resident worker cannot safely accept more audio."""


class FunasrResidentBoundaryTimeoutError(FunasrResidentUnavailableError):
    """Raised when a worker does not acknowledge an utterance boundary in time."""


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


@dataclass
class _BoundaryAttempt:
    """State for one enqueued boundary command.

    A timeout is only a caller-side wait expiry.  The worker may still be
    draining the command, so the attempt must remain addressable until its
    matching ACK (or a terminal worker failure) arrives.  Keeping this state
    separate from the bounded ACK cache prevents a retry from accidentally
    issuing a second command for the same boundary token.
    """

    waiter: threading.Event
    enqueued_at_monotonic: float | None = None
    command_enqueued: bool = False
    timed_out_count: int = 0
    acknowledged_at_monotonic: float | None = None
    consumed_at_monotonic: float | None = None
    ack_event_sequence: int | None = None


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
        self._started_at_monotonic = time.monotonic()
        self._events: "queue.Queue[tuple[int, dict[str, Any]]]" = queue.Queue()
        self._deferred_events: "deque[tuple[int, dict[str, Any]]]" = deque()
        self._event_drain_lock = threading.Lock()
        self._receive_sequence = 0
        self._ready_event = threading.Event()
        self._ended_event = threading.Event()
        self._aborted_event = threading.Event()
        self._state_lock = threading.Lock()
        self._terminal = False
        self._finalize_started = False
        self._abort_started = False
        self._error: str | None = None
        self._seq = 0
        self._audio_chunk_attempts = 0
        self._audio_chunks_dropped = 0
        self._preview_bytes_dropped = 0
        # Boundary commands are causal barriers.  Serialize calls on one
        # session so two producers cannot enqueue the same token while the
        # first caller is still waiting for its ACK.  An attempt remains
        # inflight after a timeout: the command may still be in the worker
        # FIFO, and a retry must wait on the same waiter rather than enqueue a
        # duplicate command.
        self._boundary_flush_lock = threading.Lock()
        self._boundary_inflight: dict[str, _BoundaryAttempt] = {}
        # Keep the event map as a small compatibility/debug view.  Events in
        # this map intentionally survive timeout and are removed only after a
        # matching ACK is consumed or enqueue fails.
        self._boundary_waiters: dict[str, threading.Event] = {}
        self._boundary_acks: dict[str, dict[str, Any]] = {}
        self._completed_boundaries: dict[str, None] = {}
        self._boundary_diagnostics: dict[str, dict[str, Any]] = {}
        self.worker_diagnostics: dict[str, Any] = {}
        self.shutdown_diagnostics: dict[str, Any] = {}

    @property
    def boundary_diagnostics(self) -> list[dict[str, Any]]:
        """Return a bounded, content-free snapshot of boundary lifecycle data."""

        with self._state_lock:
            return self._boundary_diagnostics_snapshot_locked()

    def _boundary_diagnostics_snapshot_locked(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._boundary_diagnostics.values()]

    def _boundary_timestamp_ms(self, timestamp: float | None) -> float | None:
        if timestamp is None:
            return None
        return round(max(0.0, timestamp - self._started_at_monotonic) * 1_000, 2)

    def _get_boundary_diagnostic_locked(self, boundary_id: str) -> dict[str, Any]:
        diagnostic = self._boundary_diagnostics.get(boundary_id)
        if diagnostic is not None:
            return diagnostic
        # Keep this cache bounded even when a worker sends an unsolicited ACK.
        # Insert the new item after evicting the oldest entry so the current
        # boundary is never immediately removed from the returned snapshot.
        while len(self._boundary_diagnostics) >= MAX_BOUNDARY_DIAGNOSTICS:
            self._boundary_diagnostics.pop(next(iter(self._boundary_diagnostics)))
        diagnostic = {
            "boundary_id": boundary_id,
            "status": "pending",
            "enqueued_at_ms": None,
            "ack_received_at_ms": None,
            "ack_consumed_at_ms": None,
            "ack_latency_ms": None,
            "timeout_count": 0,
            "duplicate": None,
            "utterance_index": None,
            "drain_ms": None,
            "skipped_preview_bytes": 0,
            "skipped_silence_bytes": 0,
        }
        self._boundary_diagnostics[boundary_id] = diagnostic
        return diagnostic

    def _refresh_boundary_latency_locked(
        self,
        diagnostic: dict[str, Any],
        attempt: _BoundaryAttempt | None,
    ) -> None:
        if attempt is not None:
            diagnostic["enqueued_at_ms"] = self._boundary_timestamp_ms(
                attempt.enqueued_at_monotonic
            )
            if attempt.acknowledged_at_monotonic is not None:
                diagnostic["ack_received_at_ms"] = self._boundary_timestamp_ms(
                    attempt.acknowledged_at_monotonic
                )
                if attempt.enqueued_at_monotonic is not None:
                    diagnostic["ack_latency_ms"] = round(
                        max(
                            0.0,
                            attempt.acknowledged_at_monotonic
                            - attempt.enqueued_at_monotonic,
                        )
                        * 1_000,
                        2,
                    )

    def _record_boundary_ack_locked(
        self,
        boundary_id: str,
        event: Mapping[str, Any],
        attempt: _BoundaryAttempt | None,
        received_at: float,
    ) -> None:
        diagnostic = self._boundary_diagnostics.get(boundary_id)
        # An ACK for a boundary this session never issued is stale or
        # malformed. Keep it in the bounded ACK compatibility cache, but do
        # not let worker-controlled tokens create misleading diagnostics.
        if diagnostic is None and attempt is None:
            return
        if diagnostic is None:
            diagnostic = self._get_boundary_diagnostic_locked(boundary_id)
        diagnostic["status"] = "acknowledged"
        if attempt is not None and attempt.acknowledged_at_monotonic is None:
            attempt.acknowledged_at_monotonic = received_at
        if attempt is not None:
            self._refresh_boundary_latency_locked(diagnostic, attempt)
        elif diagnostic["ack_received_at_ms"] is None:
            diagnostic["ack_received_at_ms"] = self._boundary_timestamp_ms(received_at)
        duplicate = event.get("duplicate")
        if isinstance(duplicate, bool):
            diagnostic["duplicate"] = duplicate
        utterance_index = event.get("utterance_index")
        if (
            isinstance(utterance_index, int)
            and not isinstance(utterance_index, bool)
            and utterance_index >= 0
        ):
            diagnostic["utterance_index"] = utterance_index
        drain_ms = event.get("drain_ms")
        if (
            isinstance(drain_ms, (int, float))
            and not isinstance(drain_ms, bool)
            and math.isfinite(float(drain_ms))
            and drain_ms >= 0
        ):
            diagnostic["drain_ms"] = round(float(drain_ms), 2)
        skipped_silence_bytes = event.get("skipped_silence_bytes")
        if (
            isinstance(skipped_silence_bytes, int)
            and not isinstance(skipped_silence_bytes, bool)
            and skipped_silence_bytes >= 0
        ):
            diagnostic["skipped_silence_bytes"] = skipped_silence_bytes
        skipped_preview_bytes = event.get("skipped_preview_bytes")
        if (
            isinstance(skipped_preview_bytes, int)
            and not isinstance(skipped_preview_bytes, bool)
            and skipped_preview_bytes >= 0
        ):
            diagnostic["skipped_preview_bytes"] = skipped_preview_bytes

    def _snapshot_boundary_diagnostics(self) -> list[dict[str, Any]]:
        with self._state_lock:
            return self._boundary_diagnostics_snapshot_locked()

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
        if event_type == "utterance_boundary_complete":
            boundary_id = str(event.get("boundary_id") or "").strip()
            if not boundary_id:
                return
            received_at = time.monotonic()
            with self._state_lock:
                self._receive_sequence += 1
                ack_event_sequence = self._receive_sequence
                attempt = self._boundary_inflight.get(boundary_id)
                if attempt is None:
                    # A legitimate ACK can only follow a command whose attempt
                    # was registered before enqueue.  Do not let an unsolicited
                    # or stale worker token satisfy a future boundary request.
                    # A duplicate ACK for an already consumed token may still
                    # enrich its bounded diagnostics, but it is never cached as
                    # consumable evidence again.
                    if boundary_id in self._completed_boundaries:
                        self._record_boundary_ack_locked(
                            boundary_id,
                            event,
                            None,
                            received_at,
                        )
                    return
                # The first ACK is the causal one. A duplicate event for the
                # same token must not overwrite its payload (or turn a
                # successful non-duplicate into a later duplicate) before the
                # waiting caller consumes it.
                first_ack = boundary_id not in self._boundary_acks
                if first_ack:
                    self._boundary_acks[boundary_id] = dict(event)
                    attempt.ack_event_sequence = ack_event_sequence
                self._record_boundary_ack_locked(
                    boundary_id,
                    event,
                    attempt,
                    received_at,
                )
                attempt.waiter.set()
                # Boundary tokens are unique per utterance. Keep only a small
                # bounded cache so a late ACK after timeout can satisfy a safe
                # retry without allowing unbounded worker-controlled growth.
                if len(self._boundary_acks) > 128:
                    oldest = next(iter(self._boundary_acks))
                    self._boundary_acks.pop(oldest, None)
            return
        if event_type == "error":
            self._fail(str(event.get("message") or event.get("error_code") or "resident worker error"))
            return
        if event_type in {"partial", "final"}:
            with self._state_lock:
                self._receive_sequence += 1
                event_sequence = self._receive_sequence
            self._events.put((event_sequence, event))

    def _fail(self, message: str) -> None:
        with self._state_lock:
            if self._error is None:
                self._error = message
            self._terminal = True
            for boundary_id, attempt in self._boundary_inflight.items():
                if boundary_id not in self._boundary_acks:
                    diagnostic = self._get_boundary_diagnostic_locked(boundary_id)
                    diagnostic["status"] = "failed"
                    diagnostic["timeout_count"] = attempt.timed_out_count
                    self._refresh_boundary_latency_locked(diagnostic, attempt)
                attempt.waiter.set()
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
            self._audio_chunk_attempts += 1
            sequence = self._audio_chunk_attempts
        accepted = self._manager.send_audio(self, pcm)
        with self._state_lock:
            if accepted:
                self._seq += 1
            else:
                self._audio_chunks_dropped += 1
                self._preview_bytes_dropped += len(pcm)
        events = self._drain_events()
        if events:
            return events
        return [{
            "event_type": "partial",
            "segment_id": f"stream_seg_{self.session_id}",
            "text": "",
            "start_ms": (sequence - 1) * 300,
            "end_ms": sequence * 300,
            "confidence": None,
            "confidence_source": FUNASR_CONFIDENCE_SOURCE_UNAVAILABLE,
        }]

    def flush_utterance(
        self,
        boundary_id: str,
        timeout: float = FUNASR_BOUNDARY_ACK_TIMEOUT_S,
    ) -> list[dict[str, Any]]:
        """Drain one worker utterance and wait for its causal boundary ACK.

        The worker emits any residual incremental partial before the ACK. A
        timeout is fail-closed: no text is promoted and the session remains
        usable so a later ``END`` can retry over the retained PCM.
        """

        if (
            not isinstance(boundary_id, str)
            or not boundary_id
            or len(boundary_id) > 192
            or any(ord(character) < 33 or ord(character) > 126 for character in boundary_id)
        ):
            raise ValueError("boundary_id must be printable ASCII and <= 192 characters")
        try:
            timeout_s = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout must be a positive number") from exc
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout must be a finite positive number")
        timeout_s = max(0.001, timeout_s)

        # The lock covers the enqueue/wait/consume sequence.  It is separate
        # from ``_state_lock`` so the reader thread can deliver the ACK while
        # this call is blocked.  A second caller therefore waits for the first
        # attempt and observes the completed token instead of issuing a second
        # flush concurrently.
        with self._boundary_flush_lock:
            with self._state_lock:
                if self._terminal or self._finalize_started or self._abort_started:
                    raise FunasrResidentUnavailableError("FunASR resident session is closed")
                if self._error:
                    raise FunasrResidentUnavailableError(self._error)
                if boundary_id in self._completed_boundaries:
                    return []
                if self._boundary_inflight and boundary_id not in self._boundary_inflight:
                    raise FunasrResidentUnavailableError(
                        "FunASR resident session has an unresolved utterance boundary"
                    )
                attempt = self._boundary_inflight.get(boundary_id)
                if attempt is None:
                    attempt = _BoundaryAttempt(waiter=threading.Event())
                    self._boundary_inflight[boundary_id] = attempt
                    self._boundary_waiters[boundary_id] = attempt.waiter
                    self._get_boundary_diagnostic_locked(boundary_id)
                waiter = attempt.waiter
                # A late ACK can arrive after a previous timeout.  Consume it
                # without enqueueing a duplicate command.
                already_acknowledged = boundary_id in self._boundary_acks

            if not already_acknowledged:
                # ``enqueued_at_monotonic`` is initialized when the attempt is
                # created.  Only the first caller writes the worker command;
                # retries reuse the persistent attempt state.
                should_enqueue = not attempt.command_enqueued
            else:
                should_enqueue = False

            if should_enqueue:
                # Register the command as enqueuing before handing control to
                # the manager. A synchronous test double, or an exceptionally
                # fast writer/reader pair, may deliver the ACK before the
                # manager call returns; the ACK must already have an attempt
                # and a causal enqueue timestamp to attach to.
                with self._state_lock:
                    attempt.command_enqueued = True
                    attempt.enqueued_at_monotonic = time.monotonic()
                    diagnostic = self._get_boundary_diagnostic_locked(boundary_id)
                    if diagnostic["status"] == "pending":
                        diagnostic["status"] = "enqueuing"
                    self._refresh_boundary_latency_locked(diagnostic, attempt)
                try:
                    self._manager.flush_utterance(self, boundary_id)
                    with self._state_lock:
                        diagnostic = self._get_boundary_diagnostic_locked(boundary_id)
                        if diagnostic["status"] == "enqueuing":
                            diagnostic["status"] = "enqueued"
                        self._refresh_boundary_latency_locked(diagnostic, attempt)
                except Exception:
                    with self._state_lock:
                        self._boundary_inflight.pop(boundary_id, None)
                        self._boundary_waiters.pop(boundary_id, None)
                        # If a synchronous manager delivered an ACK and then
                        # reported enqueue failure, that ACK is not safe to
                        # reuse for a future command.
                        self._boundary_acks.pop(boundary_id, None)
                        attempt.command_enqueued = False
                        diagnostic = self._get_boundary_diagnostic_locked(boundary_id)
                        diagnostic["status"] = "enqueue_failed"
                        diagnostic["timeout_count"] = attempt.timed_out_count
                    raise

            if not already_acknowledged:
                waiter.wait(timeout_s)

            with self._state_lock:
                error = self._error
                ack = self._boundary_acks.get(boundary_id)
                ack_event_sequence = attempt.ack_event_sequence
                diagnostic = self._get_boundary_diagnostic_locked(boundary_id)
                if ack is not None:
                    self._boundary_inflight.pop(boundary_id, None)
                    self._boundary_waiters.pop(boundary_id, None)
                    self._boundary_acks.pop(boundary_id, None)
                    attempt.consumed_at_monotonic = time.monotonic()
                    diagnostic["ack_consumed_at_ms"] = self._boundary_timestamp_ms(
                        attempt.consumed_at_monotonic
                    )
                    diagnostic["timeout_count"] = attempt.timed_out_count
                    diagnostic["status"] = "acknowledged"
                    self._completed_boundaries[boundary_id] = None
                    if len(self._completed_boundaries) > 128:
                        oldest = next(iter(self._completed_boundaries))
                        self._completed_boundaries.pop(oldest, None)
                elif error:
                    # A failure wake-up is not an ACK. Remove terminal state so
                    # diagnostics remain truthful, but never stamp it as
                    # consumed or completed.
                    self._boundary_inflight.pop(boundary_id, None)
                    self._boundary_waiters.pop(boundary_id, None)
                    diagnostic["status"] = "failed"
                    diagnostic["timeout_count"] = attempt.timed_out_count
                    self._refresh_boundary_latency_locked(diagnostic, attempt)
                else:
                    # Check under the same lock used by the reader. An ACK that
                    # races the waiter deadline is therefore observed as
                    # success; a genuine timeout keeps the attempt addressable
                    # for a later ACK and idempotent retry.
                    attempt.timed_out_count += 1
                    diagnostic["status"] = "timeout"
                    diagnostic["timeout_count"] = attempt.timed_out_count
                    self._refresh_boundary_latency_locked(diagnostic, attempt)
                    waiter.clear()
            if ack is None and error:
                raise FunasrResidentUnavailableError(error)
            if ack is None:
                raise FunasrResidentBoundaryTimeoutError(
                    f"FunASR utterance boundary ACK timed out: {boundary_id}"
                )
            if error:
                raise FunasrResidentUnavailableError(error)
            # Reader dispatch is ordered: all partials written before the ACK
            # have already entered ``_events`` by this point.
            return self._drain_events(max_sequence=ack_event_sequence)

    def finalize(self) -> list[dict[str, Any]]:
        with self._state_lock:
            if self._finalize_started or self._abort_started:
                return []
            self._finalize_started = True
        started_at = time.monotonic()
        self._manager.end_session(self)
        self._raise_if_failed()
        events = self._drain_events()
        if not events:
            events.append({
                "event_type": "final",
                "segment_id": f"stream_seg_{self.session_id}",
                "text": "",
                "confidence": None,
                "confidence_source": FUNASR_CONFIDENCE_SOURCE_UNAVAILABLE,
            })
        with self._state_lock:
            self._terminal = True
        self.shutdown_diagnostics = {
            "abort": False,
            "process_reused": True,
            "audio_chunks_enqueued": self._seq,
            "audio_chunks_attempted": self._audio_chunk_attempts,
            "audio_chunks_dropped": self._audio_chunks_dropped,
            "preview_bytes_dropped": self._preview_bytes_dropped,
            "total_ms": round((time.monotonic() - started_at) * 1_000, 2),
            "boundary_diagnostics": self._snapshot_boundary_diagnostics(),
            **({"worker": dict(self.worker_diagnostics)} if self.worker_diagnostics else {}),
        }
        return events

    def abort(self) -> None:
        with self._state_lock:
            if self._abort_started or self._finalize_started:
                return
            self._abort_started = True
        started_at = time.monotonic()
        abort_error: Exception | None = None
        process_reused = False
        try:
            process_reused = self._manager.abort_session(self)
        except Exception as exc:  # cleanup remains terminal even if transport fails
            abort_error = exc
        finally:
            with self._state_lock:
                self._terminal = True
            self.shutdown_diagnostics = {
                "abort": True,
                "process_reused": abort_error is None and process_reused,
                "audio_chunks_enqueued": self._seq,
                "audio_chunks_attempted": self._audio_chunk_attempts,
                "audio_chunks_dropped": self._audio_chunks_dropped,
                "preview_bytes_dropped": self._preview_bytes_dropped,
                "total_ms": round((time.monotonic() - started_at) * 1_000, 2),
                "boundary_diagnostics": self._snapshot_boundary_diagnostics(),
                **(
                    {"abort_error": type(abort_error).__name__}
                    if abort_error is not None
                    else {}
                ),
            }

    def _drain_events(self, *, max_sequence: int | None = None) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        with self._event_drain_lock:
            while True:
                if self._deferred_events:
                    event_sequence, raw_event = self._deferred_events.popleft()
                else:
                    try:
                        event_sequence, raw_event = self._events.get_nowait()
                    except queue.Empty:
                        return events
                if max_sequence is not None and event_sequence >= max_sequence:
                    self._deferred_events.appendleft((event_sequence, raw_event))
                    return events
                event = dict(raw_event)
                raw_segment_id = str(event.get("segment_id") or "").strip()
                if raw_segment_id.startswith(f"{self.session_id}_"):
                    segment_id = raw_segment_id
                elif raw_segment_id:
                    segment_id = f"{self.session_id}_{raw_segment_id}"
                else:
                    segment_id = f"stream_seg_{self.session_id}"
                event["segment_id"] = segment_id
                if event.get("confidence") is None:
                    event["confidence"] = None
                    event.setdefault(
                        "confidence_source",
                        FUNASR_CONFIDENCE_SOURCE_UNAVAILABLE,
                    )
                else:
                    event.setdefault(
                        "confidence_source",
                        FUNASR_CONFIDENCE_SOURCE_REPORTED,
                    )
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

    def send_audio(self, session: FunasrResidentSession, pcm: bytes) -> bool:
        if not pcm:
            return True
        with self._lock:
            generation = self._require_active_generation_locked(session)
            # Audio is a lossy preview plane; the backend retains complete raw
            # PCM for authoritative refinement. Keep capacity for FLUSH/abort so
            # sustained input cannot make lifecycle control itself fail.
            if generation.write_queue.qsize() >= WRITE_QUEUE_MAX_AUDIO_COMMANDS:
                return False
            self._enqueue_locked(generation, {
                "command": "audio",
                "session_id": session.session_id,
                "pcm_base64": base64.b64encode(pcm).decode("ascii"),
            })
            return True

    def flush_utterance(
        self,
        session: FunasrResidentSession,
        boundary_id: str,
    ) -> None:
        with self._lock:
            generation = self._require_active_generation_locked(session)
            self._enqueue_locked(generation, {
                "command": "flush_utterance",
                "session_id": session.session_id,
                "boundary_id": boundary_id,
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

    def abort_session(self, session: FunasrResidentSession) -> bool:
        recycle = False
        with self._lock:
            if self._active_session is not session:
                return True
            generation = self._generation
            if generation is None or generation.terminal:
                self._active_session = None
                return False
            try:
                self._enqueue_locked(generation, {
                    "command": "abort_session",
                    "session_id": session.session_id,
                })
            except FunasrResidentUnavailableError:
                recycle = True
        if not recycle and not session._aborted_event.wait(SESSION_ABORT_TIMEOUT_S):
            recycle = True
        if recycle:
            self._recycle_unresponsive_generation(generation, session)
        with self._lock:
            if self._active_session is session:
                self._active_session = None
        return not recycle

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
            self._handle_writer_failure(generation)

    def _handle_writer_failure(self, generation: _WorkerGeneration) -> None:
        active: FunasrResidentSession | None = None
        with self._lock:
            if self._generation is not generation or generation.terminal:
                return
            generation.terminal = True
            self._last_error = "worker_stdin_write_failed"
            active = self._active_session
            self._active_session = None
            try:
                generation.write_queue.put_nowait(None)
            except queue.Full:
                pass
        if active is not None:
            active._fail("FunASR resident worker stdin write failed")
        self._terminate_process(generation.process)

    def _reader_loop(self, generation: _WorkerGeneration) -> None:
        process = generation.process
        try:
            for raw_line in process.stdout:
                if isinstance(raw_line, bytes):
                    raw_line = raw_line.decode("utf-8")
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
                    line = raw_line.decode("utf-8").rstrip()
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
            # Leave the dead generation attached to this slot. The next
            # explicit create_session call starts a replacement lazily; abort
            # itself must not create a second process behind the user's back.

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
