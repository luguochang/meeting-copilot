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
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdout = _QueueLineStream()
        self.stderr = _QueueLineStream()
        self.stdin = _ProtocolStdin(self)
        self.commands: list[dict[str, Any]] = []
        self._session_text: dict[str, list[str]] = {}
        self._active_session_id: str | None = None
        self._returncode: int | None = None
        self._exit_event = threading.Event()
        self._exit_lock = threading.Lock()
        self.wait_calls = 0
        self.kill_calls = 0

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


def test_concurrent_start_fails_closed_with_exactly_one_active_session(manager_and_factory):
    manager, factory = manager_and_factory
    barrier = threading.Barrier(3)
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
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], funasr_resident.FunasrResidentBusyError)
    assert successes[0].wait_ready(1.0) is True
    assert manager.process_start_count == 1
    assert len(factory.processes) == 1
    successes[0].abort()


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
