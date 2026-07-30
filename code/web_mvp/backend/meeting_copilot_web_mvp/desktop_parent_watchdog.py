"""Stop a bundled backend when its desktop parent process disappears."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable


PARENT_PID_ENV = "MEETING_COPILOT_PARENT_PID"


def _windows_process_is_alive(process_id: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, process_id)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def configured_parent_pid(value: str | None = None, *, current_pid: int | None = None) -> int | None:
    raw_value = os.environ.get(PARENT_PID_ENV, "") if value is None else value
    try:
        parent_pid = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return None
    own_pid = os.getpid() if current_pid is None else current_pid
    if parent_pid <= 1 or parent_pid == own_pid:
        return None
    return parent_pid


def parent_process_is_alive(
    parent_pid: int,
    *,
    get_parent_pid: Callable[[], int] = os.getppid,
    signal_process: Callable[[int, int], None] = os.kill,
    process_is_alive: Callable[[int], bool] | None = None,
    require_direct_parent: bool | None = None,
) -> bool:
    if require_direct_parent is None:
        # Windows batch launchers keep cmd.exe between the desktop process and
        # Python. The configured desktop PID is still the lifecycle owner.
        require_direct_parent = os.name != "nt"
    if require_direct_parent and get_parent_pid() != parent_pid:
        return False
    if process_is_alive is not None:
        return process_is_alive(parent_pid)
    if os.name == "nt" and signal_process is os.kill:
        return _windows_process_is_alive(parent_pid)
    try:
        signal_process(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_orphaned_backend() -> None:
    os._exit(0)


async def monitor_parent(
    parent_pid: int,
    *,
    poll_interval_seconds: float = 0.5,
    is_alive: Callable[[int], bool] = parent_process_is_alive,
    terminate: Callable[[], None] = terminate_orphaned_backend,
) -> None:
    while True:
        await asyncio.sleep(poll_interval_seconds)
        if is_alive(parent_pid):
            continue
        terminate()
        return
