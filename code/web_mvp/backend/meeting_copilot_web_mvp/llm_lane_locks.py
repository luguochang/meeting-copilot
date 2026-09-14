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

    def is_active(self, meeting_id: str, lane: str) -> bool:
        """Return whether a lane is held without creating or acquiring it."""

        key = (str(meeting_id), str(lane))
        with self._guard:
            lock = self._locks.get(key)
            return bool(lock is not None and lock.locked())

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


class ProviderLaneRegistry:
    """Independent in-process admission for realtime, deep, and correction work.

    Each lane has its own guard and concurrency budget. A slow correction can
    therefore never hold a process-local lock needed by Pi, while each lane
    still has bounded concurrency. This registry does not claim to isolate a
    shared upstream account, gateway quota, or billing boundary.

    Realtime/deep reservations remain observable before a durable job is claimed.
    They are lifecycle bookkeeping only and do not block the correction lane.
    """

    def __init__(
        self,
        *,
        realtime_max_active: int = 1,
        deep_max_active: int = 1,
        correction_max_active: int = 1,
    ) -> None:
        if realtime_max_active < 1:
            raise ValueError("realtime_max_active must be positive")
        if deep_max_active < 1:
            raise ValueError("deep_max_active must be positive")
        if correction_max_active < 1:
            raise ValueError("correction_max_active must be positive")
        self._realtime_guard = Lock()
        self._deep_guard = Lock()
        self._correction_guard = Lock()
        self._realtime_max_active = int(realtime_max_active)
        self._deep_max_active = int(deep_max_active)
        self._correction_max_active = int(correction_max_active)
        self._pending_realtime: set[str] = set()
        self._unbound_realtime: set[str] = set()
        self._active_realtime: set[str] = set()
        self._pending_deep: set[str] = set()
        self._active_deep: set[str] = set()
        self._active_correction = 0

    @property
    def realtime_max_active(self) -> int:
        return self._realtime_max_active

    @property
    def deep_max_active(self) -> int:
        return self._deep_max_active

    @property
    def correction_max_active(self) -> int:
        return self._correction_max_active

    @property
    def active_realtime_count(self) -> int:
        with self._realtime_guard:
            return len(self._active_realtime)

    @property
    def active_deep_count(self) -> int:
        with self._deep_guard:
            return len(self._active_deep)

    @property
    def pending_realtime_count(self) -> int:
        with self._realtime_guard:
            return len(self._pending_realtime)

    @property
    def pending_deep_count(self) -> int:
        with self._deep_guard:
            return len(self._pending_deep)

    @property
    def active_background_count(self) -> int:
        """Compatibility name for the correction lane count."""

        return self.active_correction_count

    @property
    def active_correction_count(self) -> int:
        with self._correction_guard:
            return self._active_correction

    def reserve_realtime(self, work_id: str) -> str:
        """Register a realtime job before its durable worker is awakened."""

        normalized = str(work_id or "").strip()
        if not normalized:
            raise ValueError("realtime work_id must not be empty")
        with self._realtime_guard:
            if normalized not in self._active_realtime:
                self._pending_realtime.add(normalized)
        return normalized

    def reserve_unbound_realtime(self, work_id: str) -> str:
        """Reserve the commit window before a durable job id exists."""

        normalized = self.reserve_realtime(work_id)
        with self._realtime_guard:
            self._unbound_realtime.add(normalized)
        return normalized

    def sync_realtime_work(self, work_ids: set[str] | list[str] | tuple[str, ...]) -> None:
        """Reconcile pending reservations with durable intelligence jobs.

        Startup recovery and jobs cancelled before their worker starts can
        leave only an in-memory reservation.  The durable open-job set is the
        source of truth for pending work; active leases are never pruned here.
        """

        normalized = {
            str(work_id).strip()
            for work_id in work_ids
            if str(work_id or "").strip()
        }
        with self._realtime_guard:
            durable_bound = self._pending_realtime.difference(self._unbound_realtime)
            durable_bound.intersection_update(normalized)
            self._pending_realtime.intersection_update(
                normalized.union(self._unbound_realtime)
            )
            self._pending_realtime.update(durable_bound)
            self._pending_realtime.update(
                work_id
                for work_id in normalized
                if work_id not in self._active_realtime
            )

    def replace_realtime_reservation(self, old_work_id: str, new_work_id: str) -> None:
        """Bind a pre-commit reservation to the durable intelligence job id."""

        old_id = str(old_work_id or "").strip()
        new_id = str(new_work_id or "").strip()
        if not new_id:
            raise ValueError("new realtime work_id must not be empty")
        with self._realtime_guard:
            if old_id:
                self._pending_realtime.discard(old_id)
                self._unbound_realtime.discard(old_id)
            if new_id not in self._active_realtime:
                self._pending_realtime.add(new_id)

    def release_realtime_reservation(self, work_id: str) -> None:
        normalized = str(work_id or "").strip()
        if not normalized:
            return
        with self._realtime_guard:
            self._pending_realtime.discard(normalized)
            self._unbound_realtime.discard(normalized)
            self._active_realtime.discard(normalized)

    def try_acquire_realtime(self, work_id: str) -> LaneLease | None:
        """Activate realtime work within its own concurrency budget."""

        normalized = self.reserve_realtime(work_id)
        with self._realtime_guard:
            if (
                normalized in self._active_realtime
                or len(self._active_realtime) >= self._realtime_max_active
            ):
                return None
            self._pending_realtime.discard(normalized)
            self._unbound_realtime.discard(normalized)
            self._active_realtime.add(normalized)

        def release() -> None:
            with self._realtime_guard:
                self._active_realtime.discard(normalized)

        return LaneLease(release)

    def reserve_deep(self, work_id: str) -> str:
        normalized = str(work_id or "").strip()
        if not normalized:
            raise ValueError("deep work_id must not be empty")
        with self._deep_guard:
            if normalized not in self._active_deep:
                self._pending_deep.add(normalized)
        return normalized

    def sync_deep_work(self, work_ids: set[str] | list[str] | tuple[str, ...]) -> None:
        normalized = {
            str(work_id).strip()
            for work_id in work_ids
            if str(work_id or "").strip()
        }
        with self._deep_guard:
            self._pending_deep.intersection_update(normalized)
            self._pending_deep.update(
                work_id for work_id in normalized if work_id not in self._active_deep
            )

    def release_deep(self, work_id: str) -> None:
        normalized = str(work_id or "").strip()
        if not normalized:
            return
        with self._deep_guard:
            self._pending_deep.discard(normalized)
            self._active_deep.discard(normalized)

    def try_acquire_deep(self, work_id: str) -> LaneLease | None:
        normalized = self.reserve_deep(work_id)
        with self._deep_guard:
            if (
                normalized in self._active_deep
                or len(self._active_deep) >= self._deep_max_active
            ):
                return None
            self._pending_deep.discard(normalized)
            self._active_deep.add(normalized)

        def release() -> None:
            with self._deep_guard:
                self._active_deep.discard(normalized)

        return LaneLease(release)

    def try_acquire_correction(self) -> LaneLease | None:
        """Activate correction work within its own concurrency budget."""

        with self._correction_guard:
            if self._active_correction >= self._correction_max_active:
                return None
            self._active_correction += 1

        released = False
        release_guard = Lock()

        def release() -> None:
            nonlocal released
            with release_guard:
                if released:
                    return
                released = True
            with self._correction_guard:
                self._active_correction = max(0, self._active_correction - 1)

        return LaneLease(release)

    def try_acquire_background(self) -> LaneLease | None:
        """Compatibility alias for callers not yet renamed to correction."""

        return self.try_acquire_correction()


# Keep the old import name for downstream callers while removing its shared
# priority/mutual-exclusion semantics.
ProviderPriorityArbiter = ProviderLaneRegistry
