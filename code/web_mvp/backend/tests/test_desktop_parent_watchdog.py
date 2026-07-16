import asyncio

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
    ) is True
    assert signals == [(42, 0)]
    assert desktop_parent_watchdog.parent_process_is_alive(
        42,
        get_parent_pid=lambda: 1,
        signal_process=lambda pid, signal: None,
    ) is False


def test_parent_process_reports_missing_pid_as_dead():
    def missing(_pid, _signal):
        raise ProcessLookupError

    assert desktop_parent_watchdog.parent_process_is_alive(
        42,
        get_parent_pid=lambda: 42,
        signal_process=missing,
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
