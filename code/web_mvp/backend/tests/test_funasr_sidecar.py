"""Tests for FunasrSidecarRecognizer (G2, mocked subprocess)."""
import threading
import time
import queue
from pathlib import Path
from types import SimpleNamespace

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
        yield '{"event_type":"partial","text":"灰度","segment_id":"x","sample_rate":16000}\n'.encode("utf-8")
        while not self._stdin.closed:
            time.sleep(0.01)
        yield '{"event_type":"final","text":"灰度 5%","segment_id":"x","sample_rate":16000}\n'.encode("utf-8")
        self.done.set()


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(self.stdin)

    def wait(self, timeout=None):
        self.stdout.done.wait(timeout or 15)
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
            b'{"event_type":"final","text":"late funasr final","segment_id":"late","sample_rate":16000}\n'
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


def _wait_for_generation_switch(recognizer, previous_generation, timeout=1.0):
    deadline = time.monotonic() + timeout
    while True:
        with recognizer._state_lock:
            generation = recognizer._generation
            if generation is not previous_generation:
                return generation
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("sidecar restart did not publish a new generation")
        time.sleep(min(0.001, remaining))


def test_funasr_sidecar_feeds_chunks_and_reads_final(monkeypatch):
    fake = _FakeProc()
    popen_calls = []
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)) or fake)
    rec = asr_stream.FunasrSidecarRecognizer("sess_f")
    rec.recognize_chunk(b"\x00" * 9600)
    rec.recognize_chunk(b"\x00" * 9600)
    time.sleep(0.15)
    finals = rec.finalize()
    final = finals[-1]
    assert final["event_type"] == "final"
    assert final["segment_id"] == "sess_f_x"
    assert final["text"] == "灰度 5%"
    assert len(fake.stdin.written) == 2
    cmd = popen_calls[0][0][0]
    assert "--chunk-size" in cmd
    assert cmd[cmd.index("--chunk-size") + 1] == "0,30,15"
    assert rec.asr_profile == "balanced_chinese_meeting"


def test_funasr_graceful_drain_timeout_scales_with_burst_audio():
    class ShortStream:
        _seq = 1

    class BurstStream:
        _seq = 60

    short_timeout = asr_stream._sidecar_graceful_drain_timeout_s(ShortStream())
    burst_timeout = asr_stream._sidecar_graceful_drain_timeout_s(BurstStream())

    assert short_timeout == asr_stream.SIDECAR_PROCESS_WAIT_TIMEOUT_S
    assert burst_timeout > short_timeout
    assert burst_timeout <= asr_stream.SIDECAR_GRACEFUL_DRAIN_MAX_S


def test_sidecar_shutdown_reuses_one_total_deadline_across_stages(monkeypatch):
    generation = SimpleNamespace(
        number=1,
        proc=SimpleNamespace(
            stdin=SimpleNamespace(close=lambda: None),
            stdout=SimpleNamespace(close=lambda: None),
            stderr=SimpleNamespace(close=lambda: None),
        ),
        write_q=queue.Queue(),
        writer=object(),
        reader=None,
        stderr_reader=None,
        terminal=False,
        accepting_audio=True,
        accepting_events=True,
        audio_chunks_enqueued=12,
        audio_bytes_enqueued=12_000,
        audio_chunks_written=10,
        audio_bytes_written=10_000,
        max_write_queue_depth=3,
    )
    recognizer = SimpleNamespace(
        session_id="sess_total_deadline",
        provider="funasr_realtime",
        _seq=12,
        _state_lock=threading.Lock(),
        _shutdown_complete=False,
    )
    process_wait_timeouts = []

    monkeypatch.setattr(asr_stream, "_sidecar_graceful_drain_timeout_s", lambda _recognizer: 0.08)

    def join_thread(thread, timeout=None):
        if thread is generation.writer:
            time.sleep(0.03)
        return True

    monkeypatch.setattr(asr_stream, "_join_sidecar_thread", join_thread)
    monkeypatch.setattr(
        asr_stream,
        "_wait_and_reap_sidecar_process",
        lambda _proc, *, timeout_s=None: process_wait_timeouts.append(timeout_s),
    )
    monkeypatch.setattr(asr_stream, "_wait_for_sidecar_reader_drain", lambda *_args, **_kwargs: True)

    asr_stream._shutdown_sidecar_generation(recognizer, generation, abort=False)

    assert len(process_wait_timeouts) == 1
    assert 0 < process_wait_timeouts[0] < 0.07
    assert recognizer.shutdown_diagnostics["write_queue_depth_at_end"] == 0
    assert recognizer.shutdown_diagnostics["audio_chunks_enqueued"] == 12
    assert recognizer.shutdown_diagnostics["audio_chunks_written"] == 10
    assert recognizer.shutdown_diagnostics["unprocessed_chunks"] == 2
    assert recognizer.shutdown_diagnostics["total_ms"] < 120


def test_funasr_bundle_paths_can_be_injected_without_changing_default_layout(monkeypatch, tmp_path):
    configured = tmp_path / "Meeting Copilot.app" / "Contents" / "Resources" / "models" / "funasr"
    monkeypatch.setenv("MEETING_COPILOT_FUNASR_MODEL_DIR", str(configured))

    assert asr_stream._configured_local_path("MEETING_COPILOT_FUNASR_MODEL_DIR", Path("fallback")) == configured

    monkeypatch.delenv("MEETING_COPILOT_FUNASR_MODEL_DIR")
    assert asr_stream._configured_local_path("MEETING_COPILOT_FUNASR_MODEL_DIR", Path("fallback")) == Path("fallback")


def test_funasr_child_environment_switches_python_home_and_path(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/bundle/runtime/backend-python")
    monkeypatch.setenv("PYTHONPATH", "/bundle/backend")
    monkeypatch.setenv("MEETING_COPILOT_FUNASR_PYTHON_HOME", "/bundle/runtime/funasr-python")
    monkeypatch.setenv("MEETING_COPILOT_FUNASR_PYTHONPATH", "/bundle/runtime/funasr-site:/bundle/asr")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-must-not-reach-asr")
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", "a" * 64)

    environment = asr_stream._funasr_process_environment()

    assert environment["PYTHONHOME"] == "/bundle/runtime/funasr-python"
    assert environment["PYTHONPATH"] == "/bundle/runtime/funasr-site:/bundle/asr"
    assert "LLM_GATEWAY_API_KEY" not in environment
    assert "MEETING_COPILOT_LOCAL_API_TOKEN" not in environment


def test_funasr_sidecar_uses_local_model_and_exposes_worker_ready(monkeypatch, tmp_path):
    model_dir = tmp_path / "funasr-online-model"
    model_dir.mkdir()
    (model_dir / "model.pt").write_bytes(b"model")
    (model_dir / "config.yaml").write_text("model: local\n", encoding="utf-8")
    proc = _ControlledProc()
    monkeypatch.setattr(asr_stream, "_FUNASR_MODEL_DIR", model_dir)
    popen_calls = []
    monkeypatch.setattr(
        asr_stream.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)) or proc,
    )

    rec = asr_stream.FunasrSidecarRecognizer("sess_ready")
    proc.stdout._items.put(
        b'{"event_type":"ready","provider":"funasr_realtime","sample_rate":16000}\n'
    )

    assert rec.wait_ready(1.0) is True
    command = popen_calls[0][0][0]
    assert command[command.index("--model") + 1] == str(model_dir)
    rec.abort()


def test_funasr_sidecar_does_not_treat_asr_event_as_worker_ready(monkeypatch):
    proc = _ControlledProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *args, **kwargs: proc)
    rec = asr_stream.FunasrSidecarRecognizer("sess_ready_contract")

    proc.stdout._items.put(
        '{"event_type":"partial","text":"先灰度","segment_id":"seg-1"}\n'.encode()
    )
    assert rec.wait_ready(0.05) is False

    proc.stdout._items.put(
        b'{"event_type":"ready","provider":"funasr_realtime","sample_rate":16000}\n'
    )
    assert rec.wait_ready(1.0) is True
    rec.abort()


def test_funasr_finalize_waits_for_reader_before_draining_late_final(monkeypatch):
    proc = _WaitReturnsBeforeFinalProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    rec = asr_stream.FunasrSidecarRecognizer("sess_late_final")
    result = {}

    finalizer = threading.Thread(target=lambda: result.setdefault("events", rec.finalize()))
    finalizer.start()
    assert proc.wait_returned.wait(1)
    proc.stdout.release_final.set()
    finalizer.join(timeout=1)

    assert not finalizer.is_alive()
    assert result["events"][-1]["text"] == "late funasr final"
    assert result["events"][-1]["segment_id"] == "sess_late_final_late"


def test_funasr_finalize_reader_timeout_returns_safely_and_records_degradation(monkeypatch):
    proc = _WaitReturnsBeforeFinalProc()
    degradation = _DegradationSpy()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)
    monkeypatch.setattr(asr_stream, "SIDECAR_READER_DRAIN_TIMEOUT_S", 0.01, raising=False)
    rec = asr_stream.FunasrSidecarRecognizer("sess_reader_timeout")
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


def test_funasr_finalize_kills_blocked_writer_then_reaps_without_concurrent_stdin_close(monkeypatch):
    proc = _BlockingWriteProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(asr_stream, "SIDECAR_WRITER_DRAIN_TIMEOUT_S", 0.01, raising=False)
    monkeypatch.setattr(asr_stream, "SIDECAR_THREAD_JOIN_TIMEOUT_S", 0.2, raising=False)
    monkeypatch.setattr(asr_stream, "SIDECAR_PROCESS_WAIT_TIMEOUT_S", 0.05, raising=False)
    rec = asr_stream.FunasrSidecarRecognizer("sess_blocked_finalize")
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
    assert proc.kill_calls == 1
    assert proc.wait_calls >= 1
    assert proc.stdin.closed is True
    assert proc.stdin.close_while_writing is False
    assert not rec._writer.is_alive()
    assert not rec._reader.is_alive()
    assert not rec._stderr_reader.is_alive()
    assert not rec._reader.is_alive()
    assert not rec._stderr_reader.is_alive()


def test_funasr_abort_kills_blocked_writer_and_returns_bounded(monkeypatch):
    proc = _BlockingWriteProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(asr_stream, "SIDECAR_THREAD_JOIN_TIMEOUT_S", 0.2, raising=False)
    monkeypatch.setattr(asr_stream, "SIDECAR_PROCESS_WAIT_TIMEOUT_S", 0.05, raising=False)
    rec = asr_stream.FunasrSidecarRecognizer("sess_blocked_abort")
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


def test_funasr_unexpected_exit_zero_rejects_future_pcm_without_queue_growth(monkeypatch):
    proc = _ControlledProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    rec = asr_stream.FunasrSidecarRecognizer("sess_dead_exit_zero")
    generation = rec._generation
    proc.exit(0)
    generation.reader.join(timeout=1)
    queued_before = generation.write_q.qsize()

    with pytest.raises(RuntimeError, match="sidecar"):
        rec.recognize_chunk(b"rejected-pcm")

    assert generation.write_q.qsize() == queued_before
    rec.abort()


def test_funasr_second_generation_crash_rejects_future_pcm(monkeypatch):
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
    rec = asr_stream.FunasrSidecarRecognizer("sess_second_crash")
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


def test_funasr_full_write_queue_rejects_pcm_without_growth(monkeypatch):
    proc = _BlockingWriteProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(asr_stream, "SIDECAR_WRITE_QUEUE_MAX_CHUNKS", 1, raising=False)
    rec = asr_stream.FunasrSidecarRecognizer("sess_queue_full")
    rec.recognize_chunk(b"blocked-pcm")
    assert proc.stdin.write_started.wait(1)
    rec.recognize_chunk(b"queued-pcm")
    generation = rec._generation

    with pytest.raises(RuntimeError, match="sidecar"):
        rec.recognize_chunk(b"overflow-pcm")

    assert generation.write_q.qsize() == 1
    proc.kill()
    rec.abort()


def test_funasr_restart_retires_old_writer_and_routes_future_pcm_to_new_generation(monkeypatch):
    first_proc = _ControlledProc(block_first_write=True)
    second_proc = _ControlledProc()
    processes = iter([first_proc, second_proc])
    degradation = _DegradationSpy()

    def popen(*args, **kwargs):
        return next(processes)

    monkeypatch.setattr(asr_stream.subprocess, "Popen", popen)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.FunasrSidecarRecognizer("sess_generation")
    old_generation = rec._generation
    old_write_q = rec._write_q
    old_writer = rec._writer
    rec.recognize_chunk(b"old-generation-pcm")
    assert first_proc.stdin.write_started.wait(1)

    try:
        first_proc.exit(7)
        new_generation = _wait_for_generation_switch(rec, old_generation)

        assert new_generation.number == old_generation.number + 1
        assert new_generation.proc is second_proc
        with rec._state_lock:
            assert rec._generation is new_generation
            assert rec._write_q is new_generation.write_q
            assert rec._writer is new_generation.writer
            assert old_generation.terminal is True
            assert old_generation.accepting_audio is False
            assert old_generation.accepting_events is False
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
    finally:
        # Keep a failed assertion from leaking the blocked old writer into the
        # next test, where its delayed crash report would use a new spy.
        first_proc.stdin.release_write()
        old_writer.join(timeout=1)
        rec.abort()


def test_funasr_finalize_exit_zero_does_not_degrade_or_restart(monkeypatch):
    proc = _ControlledProc()
    popen_calls = []
    degradation = _DegradationSpy()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)) or proc)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.FunasrSidecarRecognizer("sess_finalize")
    reader = rec._reader
    rec.finalize()
    reader.join(timeout=1)

    assert not reader.is_alive()
    assert len(popen_calls) == 1
    assert degradation.calls == []


def test_funasr_finalize_nonzero_exit_does_not_degrade_or_restart(monkeypatch):
    proc = _ControlledProc(close_exit_code=9)
    popen_calls = []
    degradation = _DegradationSpy()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)) or proc)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.FunasrSidecarRecognizer("sess_finalize_nonzero")
    reader = rec._reader
    rec.finalize()
    reader.join(timeout=1)

    assert not reader.is_alive()
    assert len(popen_calls) == 1
    assert degradation.calls == []


def test_funasr_nonfinalize_exit_zero_does_not_degrade_or_restart(monkeypatch):
    proc = _ControlledProc()
    popen_calls = []
    degradation = _DegradationSpy()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)) or proc)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    rec = asr_stream.FunasrSidecarRecognizer("sess_clean_exit")
    reader = rec._reader
    proc.exit(0)
    reader.join(timeout=1)

    assert not reader.is_alive()
    assert len(popen_calls) == 1
    assert degradation.calls == []
    rec.finalize()


def test_funasr_stale_generation_crash_report_cannot_restart_or_degrade_again(monkeypatch):
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
    rec = asr_stream.FunasrSidecarRecognizer("sess_stale_generation")
    first_generation = rec._generation
    rec._handle_crash(7, generation=first_generation)

    with rec._state_lock:
        rec._restart_attempted = False
    rec._handle_crash(9, generation=first_generation)

    assert len(popen_calls) == 2
    assert degradation.calls == [(asr_stream.LEVEL_HEAVY, "asr_sidecar_crashed: exit_code=7")]
    first_proc.exit(0)
    rec.finalize()


def test_get_recognizer_uses_funasr_when_sherpa_unavailable(monkeypatch):
    # sherpa preferred but unavailable -> FunASR used
    class _StubRecognizer:
        def __init__(self, sid):
            self.sid = sid
        def recognize_chunk(self, pcm):
            return {"event_type": "partial", "text": "stub", "segment_id": "s", "confidence": 0.8}
        def finalize(self):
            return {"event_type": "final", "text": "stub", "segment_id": "s", "confidence": 0.9}
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda sid: None)
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: _StubRecognizer(sid))
    rec = asr_stream.get_recognizer("sess_pref")
    assert isinstance(rec, _StubRecognizer)


def test_get_recognizer_prefers_funasr_for_chinese_realtime_when_both_available(monkeypatch):
    class _FunasrStubRecognizer:
        provider = "funasr_realtime"

        def __init__(self, sid):
            self.sid = sid

    class _SherpaStubRecognizer:
        provider = "sherpa_onnx_realtime"

        def __init__(self, sid):
            self.sid = sid

    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: _FunasrStubRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda sid: _SherpaStubRecognizer(sid))

    rec = asr_stream.get_recognizer("sess_cn_pref")

    assert isinstance(rec, _FunasrStubRecognizer)


def test_get_recognizer_falls_back_to_sherpa_then_fake(monkeypatch):
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: None)
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda sid: None)
    rec = asr_stream.get_recognizer("sess_fb2")
    assert isinstance(rec, asr_stream.FakeStreamRecognizer)
