"""File-ASR admission and owned-process cleanup, without model execution."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
import time
import wave

import pytest

from meeting_copilot_web_mvp import asr_resource_guard as guard
from meeting_copilot_web_mvp import batch_transcribe as batch


def _snapshot(commit_gib=12, physical_gib=10):
    return guard.MemorySnapshot(int(commit_gib * guard.GIB), int(physical_gib * guard.GIB), 123)


class FakeProcess:
    def __init__(self, command, **kwargs):
        self.args = command
        self.options = kwargs
        self.returncode = None
        self.exited = threading.Event()
        self.wait_for_monitor = False
        self.timeout_first = False
        self.fail_cleanup = False
        self.calls = []
        self.stdout_text = json.dumps({"items": [{"text": "公开测试文字。", "status": "ok"}]}, ensure_ascii=False)

    def finish(self, code=0):
        self.returncode = code
        self.exited.set()

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        self.calls.append(("communicate", timeout))
        if self.timeout_first and sum(name == "communicate" for name, _ in self.calls) == 1:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if self.wait_for_monitor:
            if not self.exited.wait(3):
                raise subprocess.TimeoutExpired(self.args, timeout)
        elif self.returncode is None:
            self.finish()
        return self.stdout_text, ""

    def terminate(self):
        self.calls.append(("terminate", None))
        if not self.fail_cleanup:
            self.finish(-15)

    def kill(self):
        self.calls.append(("kill", None))
        if self.fail_cleanup:
            raise OSError("private cleanup diagnostic must not become a public error")
        self.finish(-9)

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        if self.returncode is None:
            if timeout is not None or not self.exited.wait(3):
                raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode


@pytest.fixture
def file_case(monkeypatch, tmp_path):
    monkeypatch.setattr(batch, "_windows_resource_guard_enabled", lambda: True)
    monkeypatch.setattr(batch, "_last_resource_error_code", None)
    monkeypatch.setattr(batch, "_last_resource_check", None)
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: _snapshot())
    components = {name: {"path": tmp_path / name} for name in batch._COMPONENT_ENV}
    monkeypatch.setattr(batch, "_resolve_runtime_components", lambda: {"packaged_mode": False, "components": components})
    monkeypatch.setattr(batch, "capability_status", lambda: {"available": True})
    original = tmp_path / "recording.wav"
    original.write_bytes(b"preserved recording")
    normalized = tmp_path / "recording.16k.wav"
    conversions = []

    def convert(path):
        conversions.append(path)
        with wave.open(str(normalized), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16_000)
            audio.writeframes(b"\0" * 640)
        return normalized

    monkeypatch.setattr(batch, "ensure_wav_16k_mono", convert)
    processes = []

    def spawn(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    return original, normalized, conversions, processes


@pytest.mark.parametrize("reading", [lambda: _snapshot(1), lambda: None, lambda: (_ for _ in ()).throw(OSError("private memory error"))])
def test_rejects_capacity_or_unknown_before_conversion(monkeypatch, file_case, reading):
    original, normalized, conversions, processes = file_case
    monkeypatch.setattr(guard, "read_memory_snapshot", reading)
    with pytest.raises(guard.AsrResourceError) as raised:
        batch.transcribe_file_report(original)
    assert raised.value.code in {"asr_capacity_unavailable", "asr_resource_probe_failed"}
    assert raised.value.execution_phase == "before_spawn" and raised.value.owned_cleanup_lease is None
    assert "private" not in raised.value.safe_message
    assert not conversions and not processes and not normalized.exists()
    assert original.read_bytes() == b"preserved recording"
    assert batch.runtime_status()["resource"]["last_error_code"] == raised.value.code


@pytest.mark.parametrize("preserve", [False, True])
def test_rechecks_cold_capacity_after_conversion_and_preserves_cleanup_contract(monkeypatch, file_case, preserve):
    original, normalized, conversions, processes = file_case
    readings = iter([_snapshot(), _snapshot(7)])
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: next(readings))
    with pytest.raises(guard.AsrResourceError, match="内存") as raised:
        batch.transcribe_file_report(original, preserve_preprocessed=preserve)
    assert raised.value.code == "asr_capacity_unavailable"
    assert raised.value.execution_phase == "before_spawn" and raised.value.owned_cleanup_lease is None
    assert conversions == [original] and not processes
    assert normalized.exists() is preserve
    assert original.exists()
    status = batch.runtime_status()["resource"]
    assert status["last_check"]["input_bytes"] == 640
    assert status["last_check"]["required_commit_bytes"] == 8 * guard.GIB + 640 * 8
    with guard.cold_start_lease():
        pass


def test_success_uses_header_pcm_budget_clears_error_and_releases_lease(monkeypatch, file_case):
    original, normalized, _, processes = file_case
    monkeypatch.setattr(batch, "_last_resource_error_code", "asr_capacity_unavailable")
    monkeypatch.setenv("EXAMPLE_API_KEY", "synthetic secret")
    result = batch.transcribe_file_report(original)
    assert result["text"] == "公开测试文字。"
    assert not normalized.exists() and original.exists()
    process = processes[0]
    assert process.options["encoding"] == "utf-8" and process.options["errors"] == "replace"
    assert "EXAMPLE_API_KEY" not in process.options["env"]
    assert not any(name in {"terminate", "kill"} for name, _ in process.calls)
    cached = batch.runtime_status()["resource"]
    assert cached["last_error_code"] is None and cached["last_check"]["input_bytes"] == 640
    cached["last_check"]["input_bytes"] = 99
    assert batch.runtime_status()["resource"]["last_check"]["input_bytes"] == 640
    with guard.cold_start_lease():
        pass


@pytest.mark.parametrize("failure", ["capacity", "probe"])
def test_live_guard_first_cause_beats_killed_process_exit(monkeypatch, file_case, failure):
    original, normalized, _, processes = file_case
    calls = 0

    def read():
        nonlocal calls
        calls += 1
        if calls <= 2:
            return _snapshot()
        if failure == "probe":
            raise OSError("private probe diagnostic")
        return _snapshot(1)

    monkeypatch.setattr(guard, "read_memory_snapshot", read)
    base_spawn = batch.subprocess.Popen

    def spawn(*args, **kwargs):
        process = base_spawn(*args, **kwargs)
        process.wait_for_monitor = True
        return process

    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    with pytest.raises(guard.AsrResourceError) as raised:
        batch.transcribe_file_report(original)
    expected = "asr_resource_probe_failed" if failure == "probe" else "asr_resource_guard_triggered"
    assert raised.value.code == expected
    assert raised.value.execution_phase == "inference_or_cleanup" and raised.value.owned_cleanup_lease is None
    assert batch.runtime_status()["resource"]["last_error_code"] == expected
    assert processes[0].poll() == -15
    assert sum(name == "terminate" for name, _ in processes[0].calls) == 1
    assert not normalized.exists() and original.exists()
    with guard.cold_start_lease():
        pass


def test_timeout_stops_only_owned_handle_drains_and_releases(monkeypatch, file_case):
    original, normalized, _, processes = file_case
    base_spawn = batch.subprocess.Popen

    def spawn(*args, **kwargs):
        process = base_spawn(*args, **kwargs)
        process.timeout_first = True
        return process

    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    with pytest.raises(subprocess.TimeoutExpired):
        batch.transcribe_file_report(original, timeout=9)
    assert processes[0].poll() == -15
    assert [name for name, _ in processes[0].calls].count("communicate") == 2
    assert [name for name, _ in processes[0].calls].count("terminate") == 1
    assert not normalized.exists() and original.exists()
    with guard.cold_start_lease():
        pass


def test_busy_shared_cold_lease_does_not_spawn_or_release_other_owner(file_case):
    original, normalized, _, processes = file_case
    owner = guard.cold_start_lease()
    try:
        with pytest.raises(guard.AsrResourceError) as raised:
            batch.transcribe_file_report(original)
        assert raised.value.code == "asr_cold_start_busy"
        assert raised.value.execution_phase == "before_spawn" and raised.value.owned_cleanup_lease is None
        assert not processes and not normalized.exists()
        with pytest.raises(guard.AsrResourceError, match="加载"):
            guard.cold_start_lease()
    finally:
        owner.release()


def test_cleanup_failure_holds_lease_until_exact_owned_late_exit(monkeypatch, file_case):
    original, normalized, _, processes = file_case
    base_spawn = batch.subprocess.Popen
    released = threading.Event()
    original_release = guard.ColdStartLease.release

    def observed_release(lease):
        original_release(lease)
        released.set()

    monkeypatch.setattr(guard.ColdStartLease, "release", observed_release)

    def spawn(*args, **kwargs):
        process = base_spawn(*args, **kwargs)
        process.timeout_first = True
        process.fail_cleanup = True
        return process

    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    with pytest.raises(guard.AsrResourceError) as raised:
        batch.transcribe_file_report(original)
    assert raised.value.code == "asr_resource_guard_triggered"
    owned_lease = raised.value.owned_cleanup_lease
    assert raised.value.execution_phase == "inference_or_cleanup"
    assert owned_lease is not None and not owned_lease.is_released
    assert "private" not in str(raised.value)
    assert processes[0].poll() is None and not released.is_set()
    with pytest.raises(guard.AsrResourceError) as busy:
        guard.cold_start_lease()
    assert busy.value.code == "asr_cold_start_busy"
    assert not normalized.exists() and original.exists()
    processes[0].finish(-9)
    assert released.wait(2)
    assert owned_lease.is_released
    with guard.cold_start_lease():
        assert owned_lease.is_released  # another owner's admission is not this job's cleanup


def test_runtime_status_get_only_reads_cache(monkeypatch):
    monkeypatch.setattr(batch, "_last_resource_error_code", "asr_cold_start_busy")
    monkeypatch.setattr(guard, "check_capacity", lambda *_args, **_kwargs: pytest.fail("GET must not probe"))
    monkeypatch.setattr(batch.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("GET must not spawn"))
    assert batch.runtime_status()["resource"]["last_error_code"] == "asr_cold_start_busy"


def test_locked_converted_file_cannot_hide_owned_cleanup_failure(monkeypatch, file_case):
    original, normalized, _, processes = file_case
    base_spawn, unlink = batch.subprocess.Popen, Path.unlink
    def spawn(*args, **kwargs):
        process = base_spawn(*args, **kwargs)
        process.timeout_first = process.fail_cleanup = True
        return process
    def locked_unlink(path, *args, **kwargs):
        if path == normalized and processes and processes[0].poll() is None:
            raise PermissionError("synthetic owned file lock")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    monkeypatch.setattr(Path, "unlink", locked_unlink)
    try:
        with pytest.raises(guard.AsrResourceError) as observed:
            batch.transcribe_file_report(original)
        lease = observed.value.owned_cleanup_lease
        assert lease is not None and not lease.is_released
        assert observed.value.code == "asr_resource_guard_triggered" and normalized.exists()
    finally:
        if processes:
            processes[0].finish(-9)
    deadline = time.monotonic() + 2
    while normalized.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert lease.is_released and not normalized.exists() and original.exists()


@pytest.mark.parametrize("resource_failure", ["capacity", "probe"])
def test_failed_cleanup_cannot_replace_first_monitor_cause(monkeypatch, file_case, resource_failure):
    original, _, _, processes = file_case
    failed = threading.Event()
    released = threading.Event()
    readings = 0
    original_monitor = guard.OwnedPopenMonitor
    original_spawn = batch.subprocess.Popen
    original_release = guard.ColdStartLease.release

    def reader():
        nonlocal readings
        readings += 1
        if readings <= 2:
            return _snapshot()
        if resource_failure == "probe":
            raise OSError("private probe failure")
        return _snapshot(1)

    monkeypatch.setattr(guard, "read_memory_snapshot", reader)
    monkeypatch.setattr(guard, "OwnedPopenMonitor", lambda process: original_monitor(
        process, interval_s=0.001, on_failure=lambda _error: failed.set(),
    ))

    def release(lease):
        original_release(lease)
        released.set()

    monkeypatch.setattr(guard.ColdStartLease, "release", release)

    def spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        process.fail_cleanup = True

        def communicate(timeout=None):
            assert failed.wait(2)
            raise subprocess.TimeoutExpired(process.args, timeout)

        process.communicate = communicate
        return process

    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    with pytest.raises(guard.AsrResourceError) as raised:
        batch.transcribe_file_report(original)
    expected = "asr_resource_probe_failed" if resource_failure == "probe" else "asr_resource_guard_triggered"
    assert raised.value.code == expected
    assert batch.runtime_status()["resource"]["last_error_code"] == expected
    assert not released.is_set()
    processes[0].finish(-9)
    assert released.wait(2)


def test_unstopped_watcher_prevents_success_and_release_until_watch_exits(monkeypatch, file_case):
    original, _, _, processes = file_case
    closed = threading.Event()
    released = threading.Event()
    original_release = guard.ColdStartLease.release

    class LateMonitor:
        def __init__(self, process):
            self.process = process

        @property
        def is_alive(self):
            return not closed.is_set()

        def start(self):
            return self

        def close(self):
            return not self.is_alive

        def raise_if_failed(self):
            pass

        def stop_owned(self):
            pytest.fail("the owned process already exited")

        def wait_closed(self):
            assert closed.wait(2)

    def release(lease):
        original_release(lease)
        released.set()

    monkeypatch.setattr(guard.ColdStartLease, "release", release)
    monkeypatch.setattr(guard, "OwnedPopenMonitor", LateMonitor)
    with pytest.raises(guard.AsrResourceError) as raised:
        batch.transcribe_file_report(original)
    assert raised.value.code == "asr_resource_guard_triggered" and processes[0].poll() == 0
    assert not released.is_set()
    with pytest.raises(guard.AsrResourceError):
        guard.cold_start_lease()
    closed.set()
    assert released.wait(2)


def test_spawn_error_releases_lease_and_keeps_original_recording(monkeypatch, file_case):
    original, normalized, _, _ = file_case

    def spawn(*_args, **_kwargs):
        raise OSError("fake spawn error")

    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    with pytest.raises(OSError, match="fake spawn"):
        batch.transcribe_file_report(original)
    assert not normalized.exists() and original.exists()
    with guard.cold_start_lease():
        pass


def test_pcm_estimate_never_reads_audio_samples(tmp_path, monkeypatch):
    class HeaderOnly:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def getnframes(self):
            return 1_000_000_000

        def getnchannels(self):
            return 1

        def getsampwidth(self):
            return 2

        def readframes(self, *_args):
            pytest.fail("must not allocate complete audio")

    monkeypatch.setattr(batch.wave, "open", lambda *_args: HeaderOnly())
    assert batch._normalized_pcm_bytes(Path(tmp_path / "huge.wav")) == 2_000_000_000
