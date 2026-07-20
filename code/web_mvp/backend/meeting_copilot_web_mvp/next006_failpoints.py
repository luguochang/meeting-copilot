from __future__ import annotations

import errno
import threading
from typing import Any


_FAILURE_ERRNOS = {
    "enospc": errno.ENOSPC,
    "eio": errno.EIO,
    "erofs": errno.EROFS,
}
_SCOPES = frozenset(
    {"sqlite_transaction", "meeting_title_transaction", "audio_chunk"}
)


class InjectedStorageWriteError(OSError):
    """Bounded NEXT-006 failure raised at a real storage write boundary."""

    def __init__(self, *, scope: str, failure: str) -> None:
        super().__init__(_FAILURE_ERRNOS[failure], "injected Meeting Copilot storage write failure")
        self.scope = scope
        self.failure = failure


class StorageWriteFailpoint:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scope: str | None = None
        self._failure: str | None = None
        self._remaining = 0
        self._hit_count = 0

    def arm(self, *, scope: str, failure: str, count: int) -> dict[str, Any]:
        if scope not in _SCOPES:
            raise ValueError(f"scope must be one of {sorted(_SCOPES)}")
        if failure not in _FAILURE_ERRNOS:
            raise ValueError(f"failure must be one of {sorted(_FAILURE_ERRNOS)}")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
            raise ValueError("count must be between 1 and 10")
        with self._lock:
            self._scope = scope
            self._failure = failure
            self._remaining = count
            self._hit_count = 0
            return self._snapshot_locked()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._scope = None
            self._failure = None
            self._remaining = 0
            self._hit_count = 0
            return self._snapshot_locked()

    def maybe_raise(self, scope: str) -> None:
        with self._lock:
            if self._scope != scope or self._remaining <= 0 or self._failure is None:
                return
            failure = self._failure
            self._remaining -= 1
            self._hit_count += 1
        raise InjectedStorageWriteError(scope=scope, failure=failure)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "schema_version": "meeting_copilot.next006_storage_failpoint.v1",
            "scope": self._scope,
            "failure": self._failure,
            "remaining": self._remaining,
            "hit_count": self._hit_count,
            "supported_scopes": sorted(_SCOPES),
            "supported_failures": sorted(_FAILURE_ERRNOS),
        }


storage_write_failpoint = StorageWriteFailpoint()
