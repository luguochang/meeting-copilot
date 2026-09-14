"""Process protocol and lifecycle tests for the resident FunASR worker."""

from __future__ import annotations

import base64
import json
import queue
import subprocess
import threading
import time
from typing import Any

import pytest

from meeting_copilot_web_mvp import funasr_resident


_EOF = object()


class _QueueLineStream:
    def __init__(self) -> None:
        self._items: queue.Queue[bytes | object] = queue.Queue()
        self._closed = False
        self._lock = threading.Lock()

    def __iter__(self):
        while True:
            item = self._items.get()
            if item is _EOF:
                return
            yield item

    def emit(self, payload: dict[str, Any] | bytes | str) -> None:
        if isinstance(payload, dict):
            line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        elif isinstance(payload, str):
            line = payload.encode("utf-8")
        else:
            line = payload
        with self._lock:
            if self._closed:
                return
            self._items.put(line)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._items.put(_EOF)


class _ProtocolStdin:
    def __init__(self, process: "_FakePopen") -> None:
        self._process = process
        self._buffer = b""
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> int:
        if self._process.poll() is not None:
            raise BrokenPipeError("resident worker has exited")
        self.writes.append(payload)
        self._buffer += payload
        while b"\n" in self._buffer:
            raw_line, self._buffer = self._buffer.split(b"\n", 1)
            if raw_line:
                self._process.handle_command(json.loads(raw_line.decode("utf-8")))
        return len(payload)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    _next_pid = 20_000

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdout = _QueueLineStream()
        self.stderr = _QueueLineStream()
        self.stdin = _ProtocolStdin(self)
        self.commands: list[dict[str, Any]] = []
        self._session_text: dict[str, list[str]] = {}
        self._active_session_id: str | None = None
        self.flush_events: dict[str, list[dict[str, Any]]] = {}
        self.flush_ack_mode = "immediate"
        self.flush_ack_boundary_id: str | None = None
        self._returncode: int | None = None
        self._exit_event = threading.Event()
        self._exit_lock = threading.Lock()
        self.wait_calls = 0
        self.kill_calls = 0
        self.pid = self.__class__._next_pid
        self.__class__._next_pid += 1

    def handle_command(self, command: dict[str, Any]) -> None:
        self.commands.append(command)
        command_name = command.get("command")
        session_id = str(command.get("session_id") or "")
        if command_name == "start_session":
            self._active_session_id = session_id
            self._session_text[session_id] = []
            self.emit_event({"event_type": "session_started", "session_id": session_id})
            return
        if command_name == "audio":
            assert session_id == self._active_session_id
            pcm = base64.b64decode(str(command["pcm_base64"]))
            self._session_text[session_id].append(pcm.decode("utf-8"))
            return
        if command_name == "flush_utterance":
            assert session_id == self._active_session_id
            boundary_id = str(command["boundary_id"])
            for event in self.flush_events.get(boundary_id, []):
                self.emit_event(event)
            if self.flush_ack_mode == "immediate":
                self.emit_event({
                    "event_type": "utterance_boundary_complete",
                    "session_id": session_id,
                    "boundary_id": self.flush_ack_boundary_id or boundary_id,
                    "duplicate": False,
                })
            elif self.flush_ack_mode == "wrong_token":
                self.emit_event({
                    "event_type": "utterance_boundary_complete",
                    "session_id": session_id,
                    "boundary_id": self.flush_ack_boundary_id or f"{boundary_id}-other",
                    "duplicate": False,
                })
            return
        if command_name == "end_session":
            assert session_id == self._active_session_id
            self.emit_event({
                "event_type": "final",
                "session_id": session_id,
                "segment_id": "worker-segment",
                "text": "".join(self._session_text.pop(session_id, [])),
            })
            self.emit_event({"event_type": "session_ended", "session_id": session_id})
            self._active_session_id = None
            return
        if command_name == "abort_session":
            assert session_id == self._active_session_id
            self._session_text.pop(session_id, None)
            self._active_session_id = None
            self.emit_event({"event_type": "session_aborted", "session_id": session_id})
            return
        if command_name == "shutdown":
            self._finish(0)

    def emit_event(self, event: dict[str, Any]) -> None:
        self.stdout.emit(event)

    def crash(self, exit_code: int) -> None:
        self._finish(exit_code)

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if not self._exit_event.wait(timeout):
            raise subprocess.TimeoutExpired(self.command, timeout)
        assert self._returncode is not None
        return self._returncode

    def kill(self) -> None:
        self.kill_calls += 1
        self._finish(-9)

    def _finish(self, exit_code: int) -> None:
        with self._exit_lock:
            if self._returncode is not None:
                return
            self._returncode = exit_code
            self._exit_event.set()
            self.stdout.close()
            self.stderr.close()


class _FakePopenFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.processes: list[_FakePopen] = []

    def __call__(self, *args: Any, **kwargs: Any) -> _FakePopen:
        self.calls.append((args, kwargs))
        process = _FakePopen(list(args[0]))
        self.processes.append(process)
        return process


class _BlockingProtocolStdin(_ProtocolStdin):
    """Let START through, then emulate a child that stopped reading stdin."""

    def __init__(self, process: "_FakePopen") -> None:
        super().__init__(process)
        self.blocked = threading.Event()
        self.release = threading.Event()

    def write(self, payload: bytes) -> int:
        command = json.loads(payload.decode("utf-8"))
        if command.get("command") != "start_session":
            self.blocked.set()
            while self._process.poll() is None and not self.release.wait(0.005):
                pass
        return super().write(payload)


class _BlockingPopen(_FakePopen):
    def __init__(self, command: list[str]) -> None:
        super().__init__(command)
        self.stdin = _BlockingProtocolStdin(self)


class _BlockingPopenFactory(_FakePopenFactory):
    def __call__(self, *args: Any, **kwargs: Any) -> _FakePopen:
        self.calls.append((args, kwargs))
        process = _BlockingPopen(list(args[0]))
        self.processes.append(process)
        return process


@pytest.fixture
def manager_and_factory():
    factory = _FakePopenFactory()
    manager = funasr_resident.FunasrResidentWorkerManager(
        ["fake-funasr-worker"],
        environment={"FUNASR_OFFLINE": "1"},
        popen_factory=factory,
    )
    yield manager, factory
    manager.shutdown()


def _final_text(events: list[dict[str, Any]]) -> str:
    return next(event["text"] for event in events if event.get("event_type") == "final")


def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def test_flush_utterance_sends_boundary_and_drains_late_partial_after_matching_ack(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-adapter")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    boundary_id = "flush-adapter:utterance:1:boundary-1"
    process.flush_events[boundary_id] = [{
        "event_type": "partial",
        "session_id": "flush-adapter",
        "segment_id": "late-worker-segment",
        "text": "迟到增量",
    }]

    session.recognize_chunk(b"audio")
    events = session.flush_utterance(boundary_id, timeout=0.25)

    assert [command["command"] for command in process.commands] == [
        "start_session",
        "audio",
        "flush_utterance",
    ]
    assert process.commands[-1] == {
        "command": "flush_utterance",
        "session_id": "flush-adapter",
        "boundary_id": boundary_id,
    }
    assert len(events) == 1
    assert events[0]["event_type"] == "partial"
    assert events[0]["text"] == "迟到增量"
    assert events[0]["segment_id"] == "flush-adapter_late-worker-segment"
    session.abort()


def test_flush_only_drains_events_before_matching_ack(manager_and_factory):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-event-fence")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    process.flush_ack_mode = "none"
    boundary_id = "flush-event-fence:utterance:1:boundary-1"
    results: list[list[dict[str, Any]]] = []

    flush_thread = threading.Thread(
        target=lambda: results.append(session.flush_utterance(boundary_id, timeout=0.5))
    )
    flush_thread.start()
    _wait_until(
        lambda: any(
            command.get("command") == "flush_utterance"
            for command in process.commands
        )
    )

    # Hold the drain lock so both events are queued before the waiting caller
    # can run. The matching ACK is the exact causal cut-off.
    with session._event_drain_lock:
        session._receive({
            "event_type": "partial",
            "session_id": session.session_id,
            "segment_id": "before-boundary",
            "text": "before",
        })
        session._receive({
            "event_type": "utterance_boundary_complete",
            "session_id": session.session_id,
            "boundary_id": boundary_id,
            "duplicate": False,
        })
        session._receive({
            "event_type": "partial",
            "session_id": session.session_id,
            "segment_id": "after-boundary",
            "text": "after",
        })

    flush_thread.join(timeout=1.0)
    assert not flush_thread.is_alive()
    assert [event["text"] for event in results[0]] == ["before"]

    next_events = session.recognize_chunk(b"next-utterance")
    assert [event["text"] for event in next_events if event.get("text")] == ["after"]
    session.abort()


def test_flush_utterance_ignores_other_boundary_token_and_times_out_bounded(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-token")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    process.flush_ack_mode = "wrong_token"
    started = time.monotonic()

    with pytest.raises(funasr_resident.FunasrResidentBoundaryTimeoutError):
        session.flush_utterance("flush-token:utterance:1:boundary-1", timeout=0.04)

    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert len([command for command in process.commands if command["command"] == "flush_utterance"]) == 1
    assert session._terminal is False
    process.flush_ack_mode = "immediate"
    session.abort()


def test_flush_timeout_keeps_session_usable_for_end_session(manager_and_factory):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-end")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    process.flush_ack_mode = "wrong_token"
    boundary_id = "flush-end:utterance:1:boundary-1"

    with pytest.raises(funasr_resident.FunasrResidentBoundaryTimeoutError):
        session.flush_utterance(boundary_id, timeout=0.03)

    process.flush_ack_mode = "immediate"
    session.recognize_chunk(b"retained-audio")
    events = session.finalize()

    assert _final_text(events) == "retained-audio"
    assert process.commands[-1] == {
        "command": "end_session",
        "session_id": "flush-end",
    }


def test_flush_late_ack_retry_consumes_ack_without_second_command(manager_and_factory):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-retry")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    boundary_id = "flush-retry:utterance:1:boundary-1"
    process.flush_ack_mode = "wrong_token"

    with pytest.raises(funasr_resident.FunasrResidentBoundaryTimeoutError):
        session.flush_utterance(boundary_id, timeout=0.03)
    flush_count = len([command for command in process.commands if command["command"] == "flush_utterance"])

    process.emit_event({
        "event_type": "utterance_boundary_complete",
        "session_id": "flush-retry",
        "boundary_id": boundary_id,
        "duplicate": False,
    })
    _wait_until(lambda: boundary_id in session._boundary_acks)
    events = session.flush_utterance(boundary_id, timeout=0.1)

    assert events == []
    # The consumed token remains idempotent for all subsequent reads even
    # though the one-shot ACK payload has left the compatibility cache.
    assert session.flush_utterance(boundary_id, timeout=0.1) == []
    assert session.flush_utterance(boundary_id, timeout=0.1) == []
    assert boundary_id not in session._boundary_acks
    assert len([command for command in process.commands if command["command"] == "flush_utterance"]) == flush_count
    session.abort()


def test_flush_registers_attempt_before_synchronous_ack_and_ignores_unsolicited_ack():
    class SynchronousAckManager:
        worker_id = 17

        def __init__(self):
            self.flush_calls: list[str] = []

        def flush_utterance(self, session, boundary_id):
            attempt = session._boundary_inflight.get(boundary_id)
            assert attempt is not None
            assert attempt.command_enqueued is True
            assert attempt.enqueued_at_monotonic is not None
            self.flush_calls.append(boundary_id)
            session._receive(
                {
                    "event_type": "utterance_boundary_complete",
                    "session_id": session.session_id,
                    "boundary_id": boundary_id,
                    "duplicate": False,
                }
            )

    manager = SynchronousAckManager()
    session = funasr_resident.FunasrResidentSession(manager, "flush-sync-ack")
    boundary_id = "flush-sync-ack:utterance:1:boundary-1"

    # A token not causally linked to a locally registered command cannot be
    # cached and used to forge a future successful flush.
    session._receive(
        {
            "event_type": "utterance_boundary_complete",
            "session_id": session.session_id,
            "boundary_id": boundary_id,
            "duplicate": False,
        }
    )
    assert boundary_id not in session._boundary_acks

    assert session.flush_utterance(boundary_id, timeout=0.1) == []

    assert manager.flush_calls == [boundary_id]
    assert boundary_id in session._completed_boundaries
    diagnostic = session.boundary_diagnostics[0]
    assert diagnostic["status"] == "acknowledged"
    assert diagnostic["ack_received_at_ms"] >= diagnostic["enqueued_at_ms"]
    assert diagnostic["ack_consumed_at_ms"] >= diagnostic["ack_received_at_ms"]


def test_flush_enqueue_failure_discards_synchronous_ack_and_allows_no_forged_retry():
    class FailingAfterAckManager:
        worker_id = 18

        def flush_utterance(self, session, boundary_id):
            session._receive(
                {
                    "event_type": "utterance_boundary_complete",
                    "session_id": session.session_id,
                    "boundary_id": boundary_id,
                    "duplicate": False,
                }
            )
            raise RuntimeError("queue write failed after simulated ACK")

    manager = FailingAfterAckManager()
    session = funasr_resident.FunasrResidentSession(manager, "flush-enqueue-failure")
    boundary_id = "flush-enqueue-failure:utterance:1:boundary-1"

    with pytest.raises(RuntimeError, match="queue write failed"):
        session.flush_utterance(boundary_id, timeout=0.1)
    assert boundary_id not in session._boundary_acks
    assert boundary_id not in session._boundary_inflight
    assert boundary_id not in session._completed_boundaries


@pytest.mark.parametrize("invalid_timeout", [float("nan"), float("inf"), -float("inf"), 0])
def test_flush_rejects_nonfinite_or_nonpositive_timeout(manager_and_factory, invalid_timeout):
    manager, _factory = manager_and_factory
    session = manager.create_session("flush-invalid-timeout")
    assert session.wait_ready(1.0) is True

    with pytest.raises(ValueError, match="finite positive"):
        session.flush_utterance("flush-invalid-timeout:utterance:1:boundary-1", invalid_timeout)
    session.abort()


def test_worker_failure_does_not_mark_unacknowledged_boundary_as_consumed(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-worker-failure")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    process.flush_ack_mode = "none"
    boundary_id = "flush-worker-failure:utterance:1:boundary-1"
    failures: list[Exception] = []

    def flush() -> None:
        try:
            session.flush_utterance(boundary_id, timeout=0.8)
        except Exception as exc:  # pragma: no branch - asserted below
            failures.append(exc)

    thread = threading.Thread(target=flush)
    thread.start()
    _wait_until(
        lambda: any(
            command.get("command") == "flush_utterance"
            for command in process.commands
        )
    )
    process.crash(23)
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], funasr_resident.FunasrResidentUnavailableError)
    assert not isinstance(
        failures[0],
        funasr_resident.FunasrResidentBoundaryTimeoutError,
    )
    assert boundary_id not in session._boundary_inflight
    assert boundary_id not in session._boundary_waiters
    assert boundary_id not in session._boundary_acks
    assert boundary_id not in session._completed_boundaries
    diagnostic = session.boundary_diagnostics[0]
    assert diagnostic["status"] == "failed"
    assert diagnostic["ack_received_at_ms"] is None
    assert diagnostic["ack_consumed_at_ms"] is None
    assert diagnostic["ack_latency_ms"] is None


def test_unresolved_boundary_rejects_different_token_without_second_enqueue(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-unresolved")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    process.flush_ack_mode = "none"
    first_boundary = "flush-unresolved:utterance:1:boundary-1"

    with pytest.raises(funasr_resident.FunasrResidentBoundaryTimeoutError):
        session.flush_utterance(first_boundary, timeout=0.02)
    with pytest.raises(
        funasr_resident.FunasrResidentUnavailableError,
        match="unresolved utterance boundary",
    ):
        session.flush_utterance(
            "flush-unresolved:utterance:2:boundary-2",
            timeout=0.02,
        )

    assert [
        command["boundary_id"]
        for command in process.commands
        if command["command"] == "flush_utterance"
    ] == [first_boundary]
    session.abort()


def test_flush_retries_persistent_inflight_attempt_without_reenqueueing(
    manager_and_factory,
):
    """A caller timeout must not turn a still-draining command into a duplicate."""

    manager, factory = manager_and_factory
    session = manager.create_session("flush-persistent-retry")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    boundary_id = "flush-persistent-retry:utterance:1:boundary-1"
    process.flush_ack_mode = "wrong_token"

    with pytest.raises(funasr_resident.FunasrResidentBoundaryTimeoutError):
        session.flush_utterance(boundary_id, timeout=0.02)
    with pytest.raises(funasr_resident.FunasrResidentBoundaryTimeoutError):
        session.flush_utterance(boundary_id, timeout=0.02)

    # The second wait reused the original in-flight command.  Enqueueing a
    # second flush here could cause duplicate inference or reset the worker
    # state twice when the first command eventually completes.
    assert len(
        [command for command in process.commands if command["command"] == "flush_utterance"]
    ) == 1
    attempt = session._boundary_inflight[boundary_id]
    assert attempt.command_enqueued is True
    assert attempt.timed_out_count == 2
    assert session.boundary_diagnostics[0]["status"] == "timeout"
    assert session.boundary_diagnostics[0]["timeout_count"] == 2

    process.emit_event(
        {
            "event_type": "utterance_boundary_complete",
            "session_id": "flush-persistent-retry",
            "boundary_id": boundary_id,
            "duplicate": False,
        }
    )
    _wait_until(lambda: boundary_id in session._boundary_acks)
    assert session.flush_utterance(boundary_id, timeout=0.1) == []
    assert boundary_id not in session._boundary_inflight
    assert boundary_id in session._completed_boundaries
    assert len(
        [command for command in process.commands if command["command"] == "flush_utterance"]
    ) == 1
    session.abort()


def test_boundary_diagnostics_track_timeout_late_ack_and_consumption(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-diagnostics")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    boundary_id = "flush-diagnostics:utterance:1:boundary-1"
    process.flush_ack_mode = "wrong_token"

    with pytest.raises(funasr_resident.FunasrResidentBoundaryTimeoutError):
        session.flush_utterance(boundary_id, timeout=0.02)

    timed_out = session.boundary_diagnostics
    assert len(timed_out) == 1
    assert timed_out[0]["boundary_id"] == boundary_id
    assert timed_out[0]["status"] == "timeout"
    assert timed_out[0]["enqueued_at_ms"] is not None
    assert timed_out[0]["ack_received_at_ms"] is None
    assert timed_out[0]["ack_consumed_at_ms"] is None
    assert timed_out[0]["ack_latency_ms"] is None
    assert timed_out[0]["timeout_count"] == 1

    process.emit_event(
        {
            "event_type": "utterance_boundary_complete",
            "session_id": "flush-diagnostics",
            "boundary_id": boundary_id,
            "utterance_index": 3,
            "duplicate": False,
            "drain_ms": 4.5,
            "skipped_preview_bytes": 128,
            "skipped_silence_bytes": 64,
            # Worker payload fields outside the diagnostics allowlist must not
            # leak into the exported lifecycle evidence.
            "command": "should-not-be-exported",
        }
    )
    _wait_until(lambda: boundary_id in session._boundary_acks)
    process.flush_ack_mode = "immediate"
    assert session.flush_utterance(boundary_id, timeout=0.1) == []

    acknowledged = session.boundary_diagnostics
    assert acknowledged == [
        {
            "boundary_id": boundary_id,
            "status": "acknowledged",
            "enqueued_at_ms": acknowledged[0]["enqueued_at_ms"],
            "ack_received_at_ms": acknowledged[0]["ack_received_at_ms"],
            "ack_consumed_at_ms": acknowledged[0]["ack_consumed_at_ms"],
            "ack_latency_ms": acknowledged[0]["ack_latency_ms"],
            "timeout_count": 1,
            "duplicate": False,
            "utterance_index": 3,
            "drain_ms": 4.5,
            "skipped_preview_bytes": 128,
            "skipped_silence_bytes": 64,
        }
    ]
    item = acknowledged[0]
    assert item["ack_received_at_ms"] >= item["enqueued_at_ms"]
    assert item["ack_consumed_at_ms"] >= item["ack_received_at_ms"]
    assert item["ack_latency_ms"] >= 0
    assert "command" not in item
    assert "environment" not in item
    assert "api_key" not in item

    session.abort()
    assert session.shutdown_diagnostics["boundary_diagnostics"] == acknowledged


def test_boundary_diagnostics_are_bounded_and_worker_payload_free(manager_and_factory):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-diagnostics-bounded")
    assert session.wait_ready(1.0) is True

    for index in range(funasr_resident.MAX_BOUNDARY_DIAGNOSTICS + 7):
        boundary_id = f"boundary-{index}"
        session.flush_utterance(boundary_id, timeout=0.1)
        # The worker's ACK is already consumed above; enrich the tracked item
        # with fields that are deliberately outside the export allowlist.
        session._receive(
            {
                "event_type": "utterance_boundary_complete",
                "session_id": "flush-diagnostics-bounded",
                "boundary_id": boundary_id,
                "utterance_index": index,
                "duplicate": bool(index % 2),
                "drain_ms": 1.0,
                "skipped_silence_bytes": 0,
                "secret_payload": "must-not-be-exported",
            }
        )

    diagnostics = session.boundary_diagnostics
    assert len(diagnostics) == funasr_resident.MAX_BOUNDARY_DIAGNOSTICS
    assert diagnostics[0]["boundary_id"] == "boundary-7"
    assert diagnostics[-1]["boundary_id"] == "boundary-134"
    assert all("secret_payload" not in item for item in diagnostics)
    assert all(set(item) == {
        "boundary_id",
        "status",
        "enqueued_at_ms",
        "ack_received_at_ms",
        "ack_consumed_at_ms",
        "ack_latency_ms",
        "timeout_count",
        "duplicate",
        "utterance_index",
        "drain_ms",
        "skipped_preview_bytes",
        "skipped_silence_bytes",
    } for item in diagnostics)
    session.abort()


def test_same_boundary_concurrent_flushes_share_inflight_attempt_and_completion(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("flush-concurrent")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    process.flush_ack_mode = "none"
    boundary_id = "flush-concurrent:utterance:1:boundary-1"
    results: list[list[dict[str, Any]]] = []
    failures: list[Exception] = []

    def flush() -> None:
        try:
            results.append(session.flush_utterance(boundary_id, timeout=0.8))
        except Exception as exc:  # pragma: no cover - assertion below reports it
            failures.append(exc)

    first = threading.Thread(target=flush)
    second = threading.Thread(target=flush)
    first.start()
    _wait_until(
        lambda: len(
            [command for command in process.commands if command["command"] == "flush_utterance"]
        )
        == 1
    )
    second.start()
    # The second caller must wait on the first in-flight boundary and must not
    # enqueue a duplicate command before the ACK arrives.
    time.sleep(0.03)
    assert len(
        [command for command in process.commands if command["command"] == "flush_utterance"]
    ) == 1

    process.emit_event({
        "event_type": "utterance_boundary_complete",
        "session_id": "flush-concurrent",
        "boundary_id": boundary_id,
        "duplicate": False,
    })
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert len(results) == 2
    assert len(
        [command for command in process.commands if command["command"] == "flush_utterance"]
    ) == 1
    # A completed token is idempotent even after both original callers return.
    assert session.flush_utterance(boundary_id, timeout=0.05) == []
    assert len(
        [command for command in process.commands if command["command"] == "flush_utterance"]
    ) == 1
    session.abort()


def test_sequential_sessions_reuse_process_finalize_keeps_worker_and_text_is_isolated(
    manager_and_factory,
):
    manager, factory = manager_and_factory

    first = manager.create_session("meeting-one")
    assert first.wait_ready(1.0) is True
    first.recognize_chunk("first-meeting-only".encode())
    first_events = first.finalize()

    process = factory.processes[0]
    assert process.poll() is None
    assert manager.completed_session_count == 1

    second = manager.create_session("meeting-two")
    assert second.wait_ready(1.0) is True
    process.emit_event({
        "event_type": "final",
        "session_id": "meeting-one",
        "segment_id": "late-stale-segment",
        "text": "must-not-leak",
    })
    second.recognize_chunk("second-meeting-only".encode())
    second_events = second.finalize()

    assert _final_text(first_events) == "first-meeting-only"
    assert _final_text(second_events) == "second-meeting-only"
    assert all("first-meeting" not in str(event.get("text")) for event in second_events)
    assert all("must-not-leak" not in str(event.get("text")) for event in second_events)
    assert first_events[-1]["segment_id"] == "meeting-one_worker-segment"
    assert second_events[-1]["segment_id"] == "meeting-two_worker-segment"
    assert first_events[-1]["confidence"] is None
    assert (
        first_events[-1]["confidence_source"]
        == funasr_resident.FUNASR_CONFIDENCE_SOURCE_UNAVAILABLE
    )
    assert process.poll() is None
    assert manager.process_start_count == 1
    assert manager.completed_session_count == 2
    assert [command["command"] for command in process.commands] == [
        "start_session",
        "audio",
        "end_session",
        "start_session",
        "audio",
        "end_session",
    ]

    args, kwargs = factory.calls[0]
    assert args[0] == ["fake-funasr-worker", "--resident"]
    assert kwargs == {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": {"FUNASR_OFFLINE": "1"},
    }


def test_worker_reported_confidence_is_preserved_with_explicit_provenance(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("reported-confidence")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    process.emit_event({
        "event_type": "partial",
        "session_id": "reported-confidence",
        "segment_id": "provider-scored",
        "text": "provider supplied this score",
        "confidence": 0.61,
    })
    _wait_until(lambda: not session._events.empty())

    events = session.recognize_chunk(b"pcm")
    session.abort()

    scored = next(event for event in events if event.get("text"))
    assert scored["confidence"] == pytest.approx(0.61)
    assert (
        scored["confidence_source"]
        == funasr_resident.FUNASR_CONFIDENCE_SOURCE_REPORTED
    )


def test_session_hotwords_are_sent_once_on_start_and_bounded(manager_and_factory):
    manager, factory = manager_and_factory

    session = manager.create_session(
        "meeting-hotwords",
        hotwords=("P99", "订单中台", "p99"),
    )
    assert session.wait_ready(1.0) is True
    session.abort()

    assert factory.processes[0].commands[0] == {
        "command": "start_session",
        "session_id": "meeting-hotwords",
        "hotwords": ["P99", "订单中台"],
    }
    with pytest.raises(ValueError, match="bounded"):
        manager.create_session("too-many", hotwords=tuple(f"term-{i}" for i in range(51)))


def test_manager_status_requires_global_worker_ready_and_records_exit(manager_and_factory):
    manager, factory = manager_and_factory

    manager.start()
    process = factory.processes[0]
    before = manager.status()
    assert before["spawned"] is True
    assert before["process_running"] is True
    assert before["process_ready"] is False
    assert before["pid"] == process.pid
    assert before["pool_mode"] == "bounded_process_per_session"
    assert before["max_worker_count"] == 2
    assert before["worker_count"] == 1
    assert before["available_worker_count"] == 2

    process.emit_event({"event_type": "ready", "provider": "funasr_realtime"})
    assert manager.wait_process_ready(1.0) is True
    assert manager.status()["process_ready"] is True

    process.crash(19)
    _wait_until(lambda: manager.status()["last_exit_code"] == 19)
    status = manager.status()
    assert status["last_error"] == "worker_exited_with_code_19"
    assert status["process_ready"] is False


def test_writer_failure_fails_session_releases_slot_and_does_not_restart_eagerly(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("writer-failure")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]

    def broken_write(_payload: bytes) -> int:
        raise BrokenPipeError("private pipe detail")

    process.stdin.write = broken_write
    session.recognize_chunk(b"audio")
    _wait_until(lambda: manager.status()["last_error"] == "worker_stdin_write_failed")

    with pytest.raises(
        funasr_resident.FunasrResidentUnavailableError,
        match="stdin write failed",
    ):
        session.recognize_chunk(b"after-failure")
    assert manager.status()["active_session_count"] == 0
    assert manager.process_start_count == 1
    assert len(factory.processes) == 1
    assert process.kill_calls == 1


def test_abort_releases_session_and_next_session_reuses_worker(manager_and_factory):
    manager, factory = manager_and_factory

    abandoned = manager.create_session("abandoned")
    assert abandoned.wait_ready(1.0) is True
    abandoned.recognize_chunk(b"discarded-text")
    abandoned.abort()

    replacement = manager.create_session("replacement")
    assert replacement.wait_ready(1.0) is True
    replacement.recognize_chunk(b"fresh-text")
    replacement_events = replacement.finalize()

    process = factory.processes[0]
    assert _final_text(replacement_events) == "fresh-text"
    assert abandoned.shutdown_diagnostics["abort"] is True
    assert process.poll() is None
    assert manager.process_start_count == 1
    assert [command["command"] for command in process.commands] == [
        "start_session",
        "audio",
        "abort_session",
        "start_session",
        "audio",
        "end_session",
    ]


def test_audio_backlog_abort_reuses_same_worker_for_next_session(monkeypatch):
    monkeypatch.setattr(funasr_resident, "SESSION_ABORT_TIMEOUT_S", 1.0)
    factory = _BlockingPopenFactory()
    manager = funasr_resident.FunasrResidentWorkerManager(
        ["blocking-funasr-worker"],
        popen_factory=factory,
        max_workers=1,
    )
    try:
        abandoned = manager.create_session("backlogged-abort")
        assert abandoned.wait_ready(1.0) is True
        process = factory.processes[0]
        assert isinstance(process.stdin, _BlockingProtocolStdin)

        attempted = funasr_resident.WRITE_QUEUE_MAX_COMMANDS + 24
        for _ in range(attempted):
            abandoned.recognize_chunk(b"x")
        assert process.stdin.blocked.wait(1.0)

        generation = manager._generation
        assert generation is not None
        assert generation.write_queue.qsize() <= funasr_resident.WRITE_QUEUE_MAX_AUDIO_COMMANDS
        assert abandoned._audio_chunks_dropped > 0

        failures: list[Exception] = []

        def abort() -> None:
            try:
                abandoned.abort()
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        abort_thread = threading.Thread(target=abort)
        abort_thread.start()

        def abort_is_queued() -> bool:
            with generation.write_queue.mutex:
                return any(
                    isinstance(command, dict)
                    and command.get("command") == "abort_session"
                    for command in generation.write_queue.queue
                )

        _wait_until(abort_is_queued)
        process.stdin.release.set()
        abort_thread.join(timeout=1.0)

        assert not abort_thread.is_alive()
        assert failures == []
        assert manager.status()["active_session_count"] == 0
        assert manager.process_start_count == 1
        assert process.kill_calls == 0
        assert process.poll() is None

        replacement = manager.create_session("replacement-after-backlog")
        assert replacement.wait_ready(1.0) is True
        replacement.recognize_chunk(b"fresh-text")
        replacement_events = replacement.finalize()

        assert _final_text(replacement_events) == "fresh-text"
        assert manager.process_start_count == 1
        assert len(factory.processes) == 1
        command_names = [command["command"] for command in process.commands]
        abort_index = command_names.index("abort_session")
        replacement_index = command_names.index("start_session", abort_index + 1)
        assert all(
            command.get("session_id") != abandoned.session_id
            for command in process.commands[abort_index + 1 :]
        )
        assert command_names[replacement_index:] == [
            "start_session",
            "audio",
            "end_session",
        ]
    finally:
        manager.shutdown()


def test_blocked_writer_reserves_control_capacity_and_abort_recycles_lazily(monkeypatch):
    monkeypatch.setattr(funasr_resident, "SESSION_ABORT_TIMEOUT_S", 0.03)
    factory = _BlockingPopenFactory()
    manager = funasr_resident.FunasrResidentWorkerManager(
        ["blocking-funasr-worker"],
        popen_factory=factory,
        max_workers=1,
    )
    try:
        session = manager.create_session("blocked-writer")
        assert session.wait_ready(1.0) is True
        process = factory.processes[0]
        assert isinstance(process.stdin, _BlockingProtocolStdin)

        frame = b"\0" * 6_400  # 100 ms of 16 kHz mono float32 PCM.
        attempted = funasr_resident.WRITE_QUEUE_MAX_COMMANDS + 24
        for _ in range(attempted):
            session.recognize_chunk(frame)
        assert process.stdin.blocked.wait(1.0)

        generation = manager._generation
        assert generation is not None
        assert generation.write_queue.qsize() <= funasr_resident.WRITE_QUEUE_MAX_AUDIO_COMMANDS
        assert session._audio_chunk_attempts == attempted
        assert session._audio_chunks_dropped > 0
        assert session._seq + session._audio_chunks_dropped == attempted

        started_at = time.monotonic()
        session.abort()
        elapsed = time.monotonic() - started_at

        status = manager.status()
        assert elapsed < 0.5
        assert status["active_session_count"] == 0
        assert status["worker_count"] == 1
        assert manager.process_start_count == 1
        assert len(factory.processes) == 1
        assert process.kill_calls == 1
        assert session.shutdown_diagnostics["audio_chunks_attempted"] == attempted
        assert session.shutdown_diagnostics["audio_chunks_dropped"] > 0
        assert session.shutdown_diagnostics["preview_bytes_dropped"] > 0
        assert session.shutdown_diagnostics["process_reused"] is False

        replacement = manager.create_session("replacement-after-backpressure")
        assert replacement.wait_ready(1.0) is True
        assert manager.process_start_count == 2
        assert len(factory.processes) == 2
        replacement.abort()
    finally:
        manager.shutdown()


def test_two_concurrent_sessions_get_distinct_workers_and_third_fails_closed(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    barrier = threading.Barrier(4)
    successes: list[funasr_resident.FunasrResidentSession] = []
    failures: list[Exception] = []

    def start(session_id: str) -> None:
        barrier.wait()
        try:
            successes.append(manager.create_session(session_id))
        except Exception as exc:  # exercised as part of the concurrent result
            failures.append(exc)

    threads = [
        threading.Thread(target=start, args=("concurrent-a",)),
        threading.Thread(target=start, args=("concurrent-b",)),
        threading.Thread(target=start, args=("concurrent-c",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(successes) == 2
    assert len(failures) == 1
    assert isinstance(failures[0], funasr_resident.FunasrResidentBusyError)
    assert all(session.wait_ready(1.0) for session in successes)
    assert len({session.worker_id for session in successes}) == 2
    assert manager.process_start_count == 2
    assert len(factory.processes) == 2
    assert manager.status()["active_session_count"] == 2
    for session in successes:
        session.abort()


def test_duplicate_meeting_ids_route_dual_tracks_to_isolated_workers_and_reuse_capacity(
    manager_and_factory,
):
    manager, factory = manager_and_factory

    microphone = manager.create_session("meeting-dual")
    system_audio = manager.create_session("meeting-dual")
    assert microphone.wait_ready(1.0) is True
    assert system_audio.wait_ready(1.0) is True
    assert microphone.worker_id != system_audio.worker_id
    assert manager.status()["active_session_ids"] == ["meeting-dual", "meeting-dual"]

    microphone.recognize_chunk(b"microphone-only")
    system_audio.recognize_chunk(b"system-audio-only")
    microphone_events = microphone.finalize()
    system_audio_events = system_audio.finalize()

    assert _final_text(microphone_events) == "microphone-only"
    assert _final_text(system_audio_events) == "system-audio-only"
    assert manager.completed_session_count == 2
    assert manager.status()["active_session_count"] == 0
    assert manager.status()["available_worker_count"] == 2

    replacement = manager.create_session("meeting-next")
    assert replacement.wait_ready(1.0) is True
    replacement.abort()
    assert manager.process_start_count == 2
    assert len(factory.processes) == 2


def test_shutdown_aborts_and_reaps_every_active_worker(manager_and_factory):
    manager, factory = manager_and_factory
    sessions = [
        manager.create_session("shutdown-dual"),
        manager.create_session("shutdown-dual"),
    ]
    assert all(session.wait_ready(1.0) for session in sessions)

    manager.shutdown()

    assert len(factory.processes) == 2
    assert all(process.poll() == 0 for process in factory.processes)
    assert all(process.commands[-2]["command"] == "abort_session" for process in factory.processes)
    assert all(process.commands[-1] == {"command": "shutdown"} for process in factory.processes)
    assert all(process.wait_calls >= 1 for process in factory.processes)
    assert all(process.kill_calls == 0 for process in factory.processes)
    for session in sessions:
        with pytest.raises(funasr_resident.FunasrResidentUnavailableError, match="shut down"):
            session.recognize_chunk(b"closed")


def test_crash_fails_active_session_and_automatically_restarts_only_once(manager_and_factory):
    manager, factory = manager_and_factory

    first = manager.create_session("crash-one")
    assert first.wait_ready(1.0) is True
    factory.processes[0].crash(17)

    _wait_until(lambda: manager.process_start_count == 2)
    with pytest.raises(funasr_resident.FunasrResidentUnavailableError, match="code 17"):
        first.recognize_chunk(b"after-crash")

    second = manager.create_session("crash-two")
    assert second.wait_ready(1.0) is True
    factory.processes[1].crash(23)

    _wait_until(lambda: not second.wait_ready(0))
    with pytest.raises(funasr_resident.FunasrResidentUnavailableError, match="code 23"):
        second.recognize_chunk(b"after-second-crash")
    time.sleep(0.05)
    assert manager.process_start_count == 2
    assert len(factory.processes) == 2


def test_shutdown_sends_protocol_command_reaps_process_and_closes_active_session(
    manager_and_factory,
):
    manager, factory = manager_and_factory
    session = manager.create_session("shutdown-active")
    assert session.wait_ready(1.0) is True
    process = factory.processes[0]
    generation = manager._generation
    assert generation is not None

    manager.shutdown()

    assert process.poll() == 0
    assert process.commands[-2] == {"command": "abort_session", "session_id": "shutdown-active"}
    assert process.commands[-1] == {"command": "shutdown"}
    assert process.wait_calls >= 1
    assert process.kill_calls == 0
    assert generation.writer is not None and not generation.writer.is_alive()
    assert generation.reader is not None and not generation.reader.is_alive()
    assert generation.stderr_reader is not None and not generation.stderr_reader.is_alive()
    with pytest.raises(funasr_resident.FunasrResidentUnavailableError, match="shut down"):
        session.recognize_chunk(b"closed")
    with pytest.raises(funasr_resident.FunasrResidentUnavailableError, match="shut down"):
        manager.create_session("after-shutdown")
