from __future__ import annotations

import subprocess
import threading
from types import SimpleNamespace

import pytest

from meeting_copilot_web_mvp import asr_resource_guard as guard


def memory(commit=16 * guard.GIB, physical=16 * guard.GIB):
    return guard.MemorySnapshot(commit, physical, 7)


def test_provisional_cold_and_real_pcm_allocation_budgets_are_enforced():
    result = guard.check_capacity(guard.COLD_LOAD_PROFILE, 100, reader=lambda: memory())
    assert result.required_commit_bytes == 8 * guard.GIB + 800
    assert result.required_physical_bytes == 6 * guard.GIB + 800
    assert result.as_dict()["input_bytes"] == 100
    for snapshot in (memory(8 * guard.GIB - 1), memory(physical=6 * guard.GIB - 1)):
        with pytest.raises(guard.AsrResourceError) as observed:
            guard.check_capacity(guard.COLD_LOAD_PROFILE, reader=lambda: snapshot)
        assert observed.value.code == "asr_capacity_unavailable"
        assert observed.value.capacity_check["checked"] is True
    with pytest.raises(guard.AsrResourceError):
        guard.check_capacity(guard.WARM_INFERENCE_PROFILE, 100, reader=lambda: memory(2 * guard.GIB + 799))
    assert guard.check_capacity(guard.WARM_INFERENCE_PROFILE, 100, reader=lambda: memory(2 * guard.GIB + 800)).checked


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_input_budget_is_rejected_before_probe(value):
    with pytest.raises(ValueError):
        guard.check_capacity(guard.COLD_LOAD_PROFILE, value, reader=lambda: pytest.fail("must not probe"))


def test_probe_failure_is_closed_on_windows_and_old_behavior_is_preserved_elsewhere(monkeypatch):
    monkeypatch.setattr(guard, "os", SimpleNamespace(name="nt"))
    with pytest.raises(guard.AsrResourceError, match="无法确认") as observed:
        guard.check_capacity(guard.COLD_LOAD_PROFILE, reader=lambda: None)
    assert observed.value.code == "asr_resource_probe_failed"
    def private_failure():
        raise OSError("private exception path")
    with pytest.raises(guard.AsrResourceError) as observed:
        guard.check_capacity(guard.COLD_LOAD_PROFILE, reader=private_failure)
    assert "private" not in str(observed.value)
    monkeypatch.setattr(guard, "os", SimpleNamespace(name="posix"))
    assert guard.check_capacity(guard.COLD_LOAD_PROFILE, reader=lambda: None).checked is False


def test_windows_stdlib_reader_maps_available_commit_and_physical(monkeypatch):
    class Probe:
        def __call__(self, pointer):
            pointer._obj.page_available = 9 * guard.GIB
            pointer._obj.available = 6 * guard.GIB
            return 1
    monkeypatch.setattr(guard, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guard.ctypes, "WinDLL", lambda *_args, **_kwargs: SimpleNamespace(GlobalMemoryStatusEx=Probe()), raising=False)
    result = guard.read_memory_snapshot()
    assert result.commit_available_bytes == 9 * guard.GIB
    assert result.physical_available_bytes == 6 * guard.GIB


def test_error_messages_are_fixed_and_mapping_is_immutable():
    for code in guard.RESOURCE_ERROR_CODES:
        assert str(guard.AsrResourceError(code)) == guard.RESOURCE_ERROR_MESSAGES[code]
        with pytest.raises(ValueError):
            guard.AsrResourceError(code, "private raw log")
    with pytest.raises(TypeError):
        guard.RESOURCE_ERROR_MESSAGES["asr_capacity_unavailable"] = "private"


def test_shared_cold_start_lease_is_nonblocking_and_release_is_idempotent():
    first = guard.cold_start_lease()
    try:
        errors = []
        def contend():
            try:
                guard.cold_start_lease()
            except guard.AsrResourceError as error:
                errors.append(error.code)
        thread = threading.Thread(target=contend)
        thread.start()
        thread.join(1)
        assert not thread.is_alive() and errors == ["asr_cold_start_busy"]
    finally:
        first.release()
        first.release()
    with guard.cold_start_lease():
        pass


class OwnedProcess:
    def __init__(self, *, stubborn=False):
        self.returncode = None
        self.terminated = 0
        self.killed = 0
        self.stubborn = stubborn

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        if not self.stubborn:
            self.returncode = -15

    def kill(self):
        self.killed += 1

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("owned", timeout)
        return self.returncode


@pytest.mark.parametrize("probe_failed", [False, True])
def test_monitor_wakes_consumer_before_stopping_only_owned_handle(probe_failed):
    owned, other = OwnedProcess(), OwnedProcess()
    notified = threading.Event()
    seen = []
    def reader():
        if probe_failed:
            raise RuntimeError("private")
        return memory(guard.GIB)
    def notify(error):
        seen.append((error.code, owned.poll()))
        notified.set()
    monitor = guard.OwnedPopenMonitor(owned, reader=reader, on_failure=notify, interval_s=.001).start()
    assert notified.wait(1)
    monitor.wait_closed()
    expected = "asr_resource_probe_failed" if probe_failed else "asr_resource_guard_triggered"
    assert seen == [(expected, None)]
    assert owned.terminated == 1 and other.terminated == 0
    assert monitor.close() and not monitor.is_alive
    with pytest.raises(guard.AsrResourceError) as observed:
        monitor.raise_if_failed()
    assert observed.value.code == expected


def test_close_during_slow_probe_cannot_claim_finished_and_never_turns_success_into_failure():
    entered, release = threading.Event(), threading.Event()
    def slow_reader():
        entered.set()
        release.wait(1)
        return memory(guard.GIB)
    owned = OwnedProcess()
    monitor = guard.OwnedPopenMonitor(owned, reader=slow_reader, interval_s=.001).start()
    assert entered.wait(1)
    assert monitor.close(timeout_s=.001) is False and monitor.is_alive
    release.set()
    monitor.wait_closed()
    assert monitor.close() and monitor.failure is None and owned.terminated == 0
    monitor.raise_if_failed()


def test_failed_owned_cleanup_retains_first_cause_and_can_retry_same_handle():
    owned = OwnedProcess(stubborn=True)
    monitor = guard.OwnedPopenMonitor(owned, reader=lambda: memory(guard.GIB), interval_s=.001).start()
    monitor.wait_closed()
    assert monitor.failure.code == "asr_resource_guard_triggered"
    with pytest.raises(guard.AsrResourceError) as observed:
        monitor.stop_owned()
    assert observed.value is monitor.failure
    assert "owned" not in str(observed.value)
    owned.returncode = -9
    assert monitor.stop_owned() == -9


def test_normal_child_exit_and_close_preserve_success():
    owned = OwnedProcess()
    owned.returncode = 0
    monitor = guard.OwnedPopenMonitor(owned, reader=lambda: pytest.fail("exited child must not probe"), interval_s=.001).start()
    monitor.wait_closed()
    assert monitor.close() and monitor.failure is None and owned.terminated == 0
