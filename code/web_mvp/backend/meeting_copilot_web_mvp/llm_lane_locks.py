from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Callable


@dataclass
class LaneLease:
    _release_callback: Callable[[], None]
    _released: bool = field(default=False, init=False)
    _guard: Lock = field(default_factory=Lock, init=False, repr=False)

    def release(self) -> None:
        with self._guard:
            if self._released:
                return
            self._released = True
        self._release_callback()

    def __enter__(self) -> "LaneLease":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


class LaneLockRegistry:
    """Single-flight per meeting and lane without coupling independent lanes."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[tuple[str, str], Lock] = {}

    @property
    def active_lock_count(self) -> int:
        with self._guard:
            return len(self._locks)

    def try_acquire(self, meeting_id: str, lane: str) -> LaneLease | None:
        key = (str(meeting_id), str(lane))
        with self._guard:
            lock = self._locks.setdefault(key, Lock())
        if not lock.acquire(blocking=False):
            return None

        def release() -> None:
            lock.release()
            with self._guard:
                if self._locks.get(key) is lock and not lock.locked():
                    self._locks.pop(key, None)

        return LaneLease(release)
