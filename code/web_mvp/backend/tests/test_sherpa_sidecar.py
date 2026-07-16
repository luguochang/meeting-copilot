"""Tests for the SherpaSidecarRecognizer (real ASR sidecar, mocked subprocess)."""
import threading
import time
import queue
import pytest
from meeting_copilot_web_mvp import asr_stream


class _FakeStdin:
    def __init__(self):
        self.written: list[bytes] = []
        self.closed = False

    def write(self, b):
        self.written.append(b)
        return len(b)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeStdout:
    def __init__(self, stdin):
        self._stdin = stdin
        self.done = threading.Event()

    def __iter__(self):
        yield b'{"event_type":"partial","text":"hi","segment_id":"x","sample_rate":16000}\n'
        while not self._stdin.closed:
            time.sleep(0.01)
        yield b'{"event_type":"final","text":"hi there","segment_id":"x","sample_rate":16000}\n'
        self.done.set()


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(self.stdin)

    def wait(self, timeout=None):
        # block until the reader has consumed the final (mimics real worker exit)
        self.stdout.done.wait(timeout or 10)
        return 0

    def poll(self):
        return None

    def kill(self):
        pass


class _ControlledStream:
    _EOF = object()

    def __init__(self):
        self._items = queue.Queue()
        self.finished = threading.Event()

    def __iter__(self):
        try:
            while True:
                item = self._items.get()
                if item is self._EOF:
                    return
                yield item
        finally:
            self.finished.set()

    def close(self):
        self._items.put(self._EOF)


class _ControlledStdin:
    def __init__(self, proc, *, block_first_write=False):
        self._proc = proc
        self._block_first_write = block_first_write
        self._write_gate = threading.Event()
        self.write_started = threading.Event()
        self.write_seen = threading.Event()
        self.writes: list[tuple[int, bytes]] = []
        self.closed = False

    def write(self, payload):
        self.write_started.set()
        if self._block_first_write:
            assert self._write_gate.wait(2), "blocked writer was not released"
            self._block_first_write = False
        self.writes.append((threading.get_ident(), payload))
        self.write_seen.set()
        return len(payload)

    def flush(self):
        pass

    def close(self):
        self.closed = True
        self._proc.exit(self._proc.close_exit_code)

    def release_write(self):
        self._write_gate.set()


class _ControlledProc:
    def __init__(self, *, block_first_write=False, close_exit_code=0):
        self.close_exit_code = close_exit_code
        self.stdout = _ControlledStream()
        self.stderr = _ControlledStream()
        self.stdin = _ControlledStdin(self, block_first_write=block_first_write)
        self._exit_code = None
        self.exited = threading.Event()

    def exit(self, exit_code):
        if self._exit_code is not None:
            return
        self._exit_code = exit_code
        self.stdout.close()
        self.stderr.close()
        self.exited.set()

    def wait(self, timeout=None):
        assert self.exited.wait(timeout), "process did not exit"
        return self._exit_code

    def poll(self):
        return self._exit_code

    def kill(self):
        self.exit(-9)


class _DelayedFinalStdout:
    def __init__(self, final_line):
        self._final_line = final_line
        self.release_final = threading.Event()
        self.finished = threading.Event()
        self.closed = False

    def __iter__(self):
        try:
            self.release_final.wait()
            if self.closed:
                return
            yield self._final_line
        finally:
            self.finished.set()

    def close(self):
        self.closed = True
        self.release_final.set()


class _WaitReturnsBeforeFinalProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _DelayedFinalStdout(
            b'{"event_type":"final","text":"late sherpa final","segment_id":"late","sample_rate":16000}\n'
        )
        self.stderr = []
        self.wait_returned = threading.Event()

    def wait(self, timeout=None):
        self.wait_returned.set()
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass


class _BlockingWriteStdin:
    def __init__(self, proc):
        self._proc = proc
        self._state_lock = threading.Lock()
        self.write_started = threading.Event()
        self.unblock_write = threading.Event()
        self.writing = False
        self.closed = False
        self.close_while_writing = False

    def write(self, payload):
        with self._state_lock:
            self.writing = True
        self.write_started.set()
        self.unblock_write.wait()
        with self._state_lock:
            self.writing = False
        if self._proc.poll() not in (None, 0):
            raise BrokenPipeError("child exited")
        return len(payload)

    def flush(self):
        pass

    def close(self):
        with self._state_lock:
            self.close_while_writing = self.writing
            self.closed = True
        if self._proc.poll() is None:
            self._proc.exit(0)


class _BlockingWriteProc:
    def __init__(self):
        self.stdout = _ControlledStream()
        self.stderr = _ControlledStream()
        self.stdin = _BlockingWriteStdin(self)
        self._exit_code = None
        self.exited = threading.Event()
        self.kill_calls = 0
        self.wait_calls = 0

    def exit(self, exit_code):
        if self._exit_code is not None:
            return
        self._exit_code = exit_code
        self.stdout.close()
        self.stderr.close()
        self.exited.set()

    def wait(self, timeout=None):
        self.wait_calls += 1
        if not self.exited.wait(timeout):
            raise asr_stream.subprocess.TimeoutExpired("blocking-worker", timeout)
        return self._exit_code

    def poll(self):
        return self._exit_code

    def kill(self):
        self.kill_calls += 1
        self.exit(-9)
        self.stdin.unblock_write.set()


class _DegradationSpy:
    def __init__(self):
        self.calls = []
        self.called = threading.Event()

    def set_level(self, level, reason):
        self.calls.append((level, reason))
        self.called.set()


def test_sherpa_sidecar_feeds_chunks_and_reads_final(monkeypatch):
    fake = _FakeProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: fake)
    rec = asr_stream.SherpaSidecarRecognizer("sess", asr_stream.Path("/tmp/model"))
    rec.recognize_chunk(b"\x00" * 6400)
    rec.recognize_chunk(b"\x00" * 6400)
    time.sleep(0.15)  # let the reader thread drain the partial into the queue
    finals = rec.finalize()
    final = finals[-1]
    assert final["event_type"] == "final"
    assert final["segment_id"] == "sess_x"
    assert final["text"] == "hi there"
    # chunks were fed to the sidecar stdin
    assert len(fake.stdin.written) == 2
    assert fake.stdin.written[0] == b"\x00" * 6400


def test_sherpa_sidecar_preserves_worker_segment_ids(monkeypatch):
    fake = _FakeProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: fake)
    rec = asr_stream.SherpaSidecarRecognizer("sess", asr_stream.Path("/tmp/model"))

    time.sleep(0.15)
    partials = rec.recognize_chunk(b"\x00" * 6400)
    rec.finalize()

    partial = next(event for event in partials if event["event_type"] == "partial")
    assert partial["segment_id"] == "sess_x"


def test_sherpa_finalize_waits_for_reader_before_draining_late_final(monkeypatch):
    proc = _WaitReturnsBeforeFinalProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    rec = asr_stream.SherpaSidecarRecognizer("sess_late_final", asr_stream.Path("/tmp/model"))
    result = {}

    finalizer = threading.Thread(target=lambda: result.setdefault("events", rec.finalize()))
    finalizer.start()
    assert proc.wait_returned.wait(1)
    proc.stdout.release_final.set()
    finalizer.join(timeout=1)

    assert not finalizer.is_alive()
    assert result["events"][-1]["text"] == "late sherpa final"
    assert result["events"][-1]["segment_id"] == "sess_late_final_late"


def test_sherpa_finalize_reader_timeout_returns_safely_and_records_degradation(monkeypatch):
    proc = _WaitReturnsBeforeFinalProc()
    degradation = _DegradationSpy()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)
    monkeypatch.setattr(asr_stream, "SIDECAR_READER_DRAIN_TIMEOUT_S", 0.01, raising=False)
    rec = asr_stream.SherpaSidecarRecognizer("sess_reader_timeout", asr_stream.Path("/tmp/model"))
    generation = rec._generation
    result = {}

    finalizer = threading.Thread(target=lambda: result.setdefault("events", rec.finalize()))
    finalizer.start()
    finalizer.join(timeout=1)

    assert not finalizer.is_alive(), "reader drain timeout must remain bounded"
    assert degradation.calls == [
        (asr_stream.LEVEL_HEAVY, "asr_sidecar_reader_drain_timeout: generation=1")
    ]
    assert result["events"][-1]["text"] == ""
    proc.stdout.release_final.set()
    assert proc.stdout.finished.wait(1)
    assert getattr(generation, "accepting_events", True) is False
    assert rec._q.empty()
    assert not generation.reader.is_alive()


def test_sherpa_finalize_kills_blocked_writer_then_reaps_without_concurrent_stdin_close(monkeypatch):
    proc = _BlockingWriteProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(asr_stream, "SIDECAR_WRITER_DRAIN_TIMEOUT_S", 0.01, raising=False)
    monkeypatch.setattr(asr_stream, "SIDECAR_THREAD_JOIN_TIMEOUT_S", 0.2, raising=False)
    monkeypatch.setattr(asr_stream, "SIDECAR_PROCESS_WAIT_TIMEOUT_S", 0.05, raising=False)
    rec = asr_stream.SherpaSidecarRecognizer("sess_blocked_finalize", asr_stream.Path("/tmp/model"))
    rec.recognize_chunk(b"blocked-pcm")
    assert proc.stdin.write_started.wait(1)
    finished = threading.Event()

    def finalize():
        rec.finalize()
        finished.set()

    finalizer = threading.Thread(target=finalize)
    finalizer.start()
    returned_without_manual_release = finished.wait(0.5)
    proc.stdin.unblock_write.set()
    finalizer.join(timeout=1)

    assert returned_without_manual_release
    assert not finalizer.is_alive()
    assert proc.kill_calls == 1
    assert proc.wait_calls >= 1
    assert proc.stdin.closed is True
    assert proc.stdin.close_while_writing is False
    assert not rec._writer.is_alive()
    assert not rec._reader.is_alive()
    assert not rec._stderr_reader.is_alive()
    assert not rec._reader.is_alive()
    assert not rec._stderr_reader.is_alive()


def test_sherpa_abort_kills_blocked_writer_and_returns_bounded(monkeypatch):
    proc = _BlockingWriteProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(asr_stream, "SIDECAR_THREAD_JOIN_TIMEOUT_S", 0.2, raising=False)
    monkeypatch.setattr(asr_stream, "SIDECAR_PROCESS_WAIT_TIMEOUT_S", 0.05, raising=False)
    rec = asr_stream.SherpaSidecarRecognizer("sess_blocked_abort", asr_stream.Path("/tmp/model"))
    rec.recognize_chunk(b"blocked-pcm")
    assert proc.stdin.write_started.wait(1)
    finished = threading.Event()

    def abort():
        rec.abort()
        finished.set()

    aborter = threading.Thread(target=abort)
    aborter.start()
    returned_without_manual_release = finished.wait(0.5)
    proc.stdin.unblock_write.set()
    aborter.join(timeout=1)

    assert returned_without_manual_release
    assert proc.kill_calls == 1
    assert proc.wait_calls >= 1
    assert proc.stdin.close_while_writing is False
    assert not rec._writer.is_alive()


def test_sherpa_unexpected_exit_zero_rejects_future_pcm_without_queue_growth(monkeypatch):
    proc = _ControlledProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    rec = asr_stream.SherpaSidecarRecognizer("sess_dead_exit_zero", asr_stream.Path("/tmp/model"))
    generation = rec._generation
    proc.exit(0)
    generation.reader.join(timeout=1)
    queued_before = generation.write_q.qsize()

    with pytest.raises(RuntimeError, match="sidecar"):
        rec.recognize_chunk(b"rejected-pcm")

    assert generation.write_q.qsize() == queued_before
    rec.abort()


def test_sherpa_second_generation_crash_rejects_future_pcm(monkeypatch):
    first_proc = _ControlledProc()
    second_proc = _ControlledProc()
    processes = iter([first_proc, second_proc])
    second_spawned = threading.Event()
    degradation = _DegradationSpy()

    def popen(*args, **kwargs):
        proc = next(processes)
        if proc is second_proc:
            second_spawned.set()
        return proc

    monkeypatch.setattr(asr_stream.subprocess, "Popen", popen)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)
    rec = asr_stream.SherpaSidecarRecognizer("sess_second_crash", asr_stream.Path("/tmp/model"))
    first_proc.exit(7)
    assert second_spawned.wait(1)
    with rec._state_lock:
        generation = rec._generation
        assert generation.proc is second_proc
    second_proc.exit(9)
    generation.reader.join(timeout=1)
    queued_before = generation.write_q.qsize()

    with pytest.raises(RuntimeError, match="sidecar"):
        rec.recognize_chunk(b"rejected-pcm")

    assert generation.write_q.qsize() == queued_before
    rec.abort()


def test_sherpa_restart_spawn_failure_rejects_future_pcm(monkeypatch):
    proc = _ControlledProc()
    popen_calls = 0
    degradation = _DegradationSpy()

    def popen(*args, **kwargs):
        nonlocal popen_calls
        popen_calls += 1
        if popen_calls == 1:
            return proc
        raise RuntimeError("restart spawn failed")

    monkeypatch.setattr(asr_stream.subprocess, "Popen", popen)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)
    rec = asr_stream.SherpaSidecarRecognizer("sess_spawn_fail", asr_stream.Path("/tmp/model"))
    generation = rec._generation
    proc.exit(7)
    generation.reader.join(timeout=1)
    queued_before = generation.write_q.qsize()

    with pytest.raises(RuntimeError, match="sidecar"):
        rec.recognize_chunk(b"rejected-pcm")

    assert generation.write_q.qsize() == queued_before
    rec.abort()


def test_sherpa_full_write_queue_rejects_pcm_without_growth(monkeypatch):
    proc = _BlockingWriteProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(asr_stream, "SIDECAR_WRITE_QUEUE_MAX_CHUNKS", 1, raising=False)
    rec = asr_stream.SherpaSidecarRecognizer("sess_queue_full", asr_stream.Path("/tmp/model"))
    rec.recognize_chunk(b"blocked-pcm")
    assert proc.stdin.write_started.wait(1)
    rec.recognize_chunk(b"queued-pcm")
    generation = rec._generation

    with pytest.raises(RuntimeError, match="sidecar"):
        rec.recognize_chunk(b"overflow-pcm")

    assert generation.write_q.qsize() == 1
    proc.kill()
    rec.abort()


def test_sherpa_restart_retires_old_writer_and_routes_future_pcm_to_new_generation(monkeypatch):
    first_proc = _ControlledProc(block_first_write=True)
    second_proc = _ControlledProc()
    processes = iter([first_proc, second_proc])
    second_spawned = threading.Event()
    degradation = _DegradationSpy()

    def popen(*args, **kwargs):
        proc = next(processes)
        if proc is second_proc:
            second_spawned.set()
        return proc

    monkeypatch.setattr(asr_stream.subprocess, "Popen", popen)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.SherpaSidecarRecognizer("sess_generation", asr_stream.Path("/tmp/model"))
    old_write_q = rec._write_q
    old_writer = rec._writer
    rec.recognize_chunk(b"old-generation-pcm")
    assert first_proc.stdin.write_started.wait(1)

    first_proc.exit(7)
    assert second_spawned.wait(1), "non-zero exit did not trigger the single restart"

    assert rec._write_q is not old_write_q
    first_proc.stdin.release_write()
    old_writer.join(timeout=1)
    assert not old_writer.is_alive(), "old generation writer was not retired"

    rec.recognize_chunk(b"new-generation-pcm")
    assert second_proc.stdin.write_seen.wait(1)
    rec.finalize()

    assert [payload for _, payload in second_proc.stdin.writes] == [b"new-generation-pcm"]
    assert degradation.called.wait(1)
    assert len(degradation.calls) == 1


def test_sherpa_finalize_exit_zero_does_not_degrade_or_restart(monkeypatch):
    proc = _ControlledProc()
    popen_calls = []
    degradation = _DegradationSpy()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)) or proc)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.SherpaSidecarRecognizer("sess_finalize", asr_stream.Path("/tmp/model"))
    reader = rec._reader
    rec.finalize()
    reader.join(timeout=1)

    assert not reader.is_alive()
    assert len(popen_calls) == 1
    assert degradation.calls == []


def test_sherpa_finalize_nonzero_exit_does_not_degrade_or_restart(monkeypatch):
    proc = _ControlledProc(close_exit_code=9)
    popen_calls = []
    degradation = _DegradationSpy()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)) or proc)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.SherpaSidecarRecognizer("sess_finalize_nonzero", asr_stream.Path("/tmp/model"))
    reader = rec._reader
    rec.finalize()
    reader.join(timeout=1)

    assert not reader.is_alive()
    assert len(popen_calls) == 1
    assert degradation.calls == []


def test_sherpa_nonfinalize_exit_zero_does_not_degrade_or_restart(monkeypatch):
    proc = _ControlledProc()
    popen_calls = []
    degradation = _DegradationSpy()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)) or proc)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.SherpaSidecarRecognizer("sess_clean_exit", asr_stream.Path("/tmp/model"))
    reader = rec._reader
    proc.exit(0)
    reader.join(timeout=1)

    assert not reader.is_alive()
    assert len(popen_calls) == 1
    assert degradation.calls == []
    rec.finalize()


def test_sherpa_replacement_generation_nonzero_exit_is_blocked_by_restart_budget(monkeypatch):
    first_proc = _ControlledProc()
    second_proc = _ControlledProc()
    unused_third_proc = _ControlledProc()
    processes = iter([first_proc, second_proc, unused_third_proc])
    popen_calls = []
    degradation = _DegradationSpy()

    def popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return next(processes)

    monkeypatch.setattr(asr_stream.subprocess, "Popen", popen)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)
    rec = asr_stream.SherpaSidecarRecognizer("sess_restart_budget", asr_stream.Path("/tmp/model"))
    first_generation = rec._generation
    rec._handle_crash(7, generation=first_generation)
    replacement_generation = rec._generation

    rec._handle_crash(9, generation=replacement_generation)

    assert len(popen_calls) == 2
    assert degradation.calls == [(asr_stream.LEVEL_HEAVY, "asr_sidecar_crashed: exit_code=7")]
    first_proc.exit(0)
    rec.finalize()


def test_sherpa_stale_generation_crash_report_cannot_restart_or_degrade_again(monkeypatch):
    first_proc = _ControlledProc()
    second_proc = _ControlledProc()
    unused_third_proc = _ControlledProc()
    processes = iter([first_proc, second_proc, unused_third_proc])
    popen_calls = []
    degradation = _DegradationSpy()

    def popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return next(processes)

    monkeypatch.setattr(asr_stream.subprocess, "Popen", popen)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.SherpaSidecarRecognizer("sess_stale_generation", asr_stream.Path("/tmp/model"))
    first_generation = rec._generation
    rec._handle_crash(7, generation=first_generation)

    assert len(popen_calls) == 2
    assert degradation.calls == [(asr_stream.LEVEL_HEAVY, "asr_sidecar_crashed: exit_code=7")]

    # Isolate the generation guard from the global one-restart budget.
    with rec._state_lock:
        rec._restart_attempted = False
    rec._handle_crash(9, generation=first_generation)

    assert len(popen_calls) == 2
    assert degradation.calls == [(asr_stream.LEVEL_HEAVY, "asr_sidecar_crashed: exit_code=7")]

    first_proc.exit(0)
    rec.finalize()


def test_get_recognizer_falls_back_to_fake_when_sherpa_absent(monkeypatch):
    # FunASR + sherpa both unavailable -> Fake
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: None)
    monkeypatch.setattr(asr_stream, "_SHERPA_VENV_PY", asr_stream.Path("/nonexistent/python"))
    rec = asr_stream.get_recognizer("sess_fb")
    assert isinstance(rec, asr_stream.FakeStreamRecognizer)
