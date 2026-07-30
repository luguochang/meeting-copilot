import asyncio
import os

from meeting_copilot_web_mvp import desktop_parent_watchdog


def test_configured_parent_pid_rejects_empty_invalid_and_self_values():
    assert desktop_parent_watchdog.configured_parent_pid("", current_pid=100) is None
    assert desktop_parent_watchdog.configured_parent_pid("invalid", current_pid=100) is None
    assert desktop_parent_watchdog.configured_parent_pid("1", current_pid=100) is None
    assert desktop_parent_watchdog.configured_parent_pid("100", current_pid=100) is None
    assert desktop_parent_watchdog.configured_parent_pid("99", current_pid=100) == 99


def test_parent_process_requires_the_original_direct_parent():
    signals = []

    assert desktop_parent_watchdog.parent_process_is_alive(
        42,
        get_parent_pid=lambda: 42,
        signal_process=lambda pid, signal: signals.append((pid, signal)),
        require_direct_parent=True,
    ) is True
    assert signals == [(42, 0)]
    assert desktop_parent_watchdog.parent_process_is_alive(
        42,
        get_parent_pid=lambda: 1,
        signal_process=lambda pid, signal: None,
        require_direct_parent=True,
    ) is False


def test_windows_batch_launcher_allows_an_indirect_live_desktop_parent():
    probes = []

    def process_is_alive(pid: int) -> bool:
        probes.append(pid)
        return True

    assert desktop_parent_watchdog.parent_process_is_alive(
        42,
        get_parent_pid=lambda: 99,
        process_is_alive=process_is_alive,
        require_direct_parent=False,
    ) is True
    assert probes == [42]


def test_windows_pid_probe_distinguishes_live_and_missing_processes():
    if os.name != "nt":
        return

    assert desktop_parent_watchdog._windows_process_is_alive(os.getpid()) is True
    assert desktop_parent_watchdog._windows_process_is_alive(0x7FFFFFFF) is False


def test_parent_process_reports_missing_pid_as_dead():
    def missing(_pid, _signal):
        raise ProcessLookupError

    assert desktop_parent_watchdog.parent_process_is_alive(
        42,
        get_parent_pid=lambda: 42,
        signal_process=missing,
        require_direct_parent=True,
    ) is False


def test_monitor_parent_terminates_after_parent_disappears():
    checks = iter([True, True, False])
    terminated = []

    asyncio.run(desktop_parent_watchdog.monitor_parent(
        42,
        poll_interval_seconds=0,
        is_alive=lambda _pid: next(checks),
        terminate=lambda: terminated.append(True),
    ))

    assert terminated == [True]
