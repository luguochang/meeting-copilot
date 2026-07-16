"""Stop a bundled backend when its desktop parent process disappears."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable


PARENT_PID_ENV = "MEETING_COPILOT_PARENT_PID"


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
) -> bool:
    if get_parent_pid() != parent_pid:
        return False
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
