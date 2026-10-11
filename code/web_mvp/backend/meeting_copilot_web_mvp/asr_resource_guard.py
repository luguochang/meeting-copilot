"""Bound optional local ASR work by current Windows commit availability.

Budgets below are conservative, provisional policy estimates, not measured
minimum hardware requirements. They do not change model precision or the
separate portable acceptance harness. No Torch import, process discovery, or
system setting change is performed by this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
import os
import subprocess
import threading
import time
from types import MappingProxyType
from typing import Any, Callable


GIB = 1024 ** 3
_MESSAGES = {
    "asr_capacity_unavailable": "当前本机可用内存资源不足，无法开始本次离线识别；资源恢复后可手动重试。",
    "asr_resource_probe_failed": "暂时无法确认本机可用内存资源，当前不能进行离线识别；可手动重新检查。",
    "asr_resource_guard_triggered": "本次离线识别触发资源保护，未能完成；可稍后手动重试。",
    "asr_cold_start_busy": "另一个本地识别模型正在加载，请等待其完成后手动重试。",
}
RESOURCE_ERROR_CODES = frozenset(_MESSAGES)
RESOURCE_ERROR_MESSAGES = MappingProxyType(_MESSAGES)


class AsrResourceError(RuntimeError):
    def __init__(self, code: str, safe_message: str | None = None) -> None:
        if code not in RESOURCE_ERROR_CODES:
            raise ValueError("unsupported local ASR resource error code")
        if safe_message is not None and safe_message != _MESSAGES[code]:
            raise ValueError("local ASR resource messages must be fixed")
        self.code = code
        self.safe_message = safe_message or _MESSAGES[code]
        self.execution_phase: str | None = None
        self.owned_cleanup_lease: ColdStartLease | None = None
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class MemorySnapshot:
    commit_available_bytes: int
    physical_available_bytes: int
    sampled_at_ms: int
    total_physical_bytes: int | None = None


@dataclass(frozen=True)
class ResourceProfile:
    name: str
    commit_budget_bytes: int
    physical_budget_bytes: int
    reserve_commit_bytes: int = 2 * GIB
    reserve_physical_bytes: int = 2 * GIB


COLD_LOAD_PROFILE = ResourceProfile("cold_load_provisional_v1", 6 * GIB, 4 * GIB)
WARM_INFERENCE_PROFILE = ResourceProfile("warm_inference_provisional_v1", 0, 0)


@dataclass(frozen=True)
class CapacityCheck:
    checked: bool
    profile: str
    commit_available_bytes: int | None
    physical_available_bytes: int | None
    required_commit_bytes: int
    required_physical_bytes: int
    input_bytes: int
    sampled_at_ms: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


MemoryReader = Callable[[], MemorySnapshot | None]


def read_memory_snapshot() -> MemorySnapshot | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
            *((name, ctypes.c_ulonglong) for name in (
                "total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended",
            )),
        ]

    try:
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        function = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
        function.argtypes = [ctypes.POINTER(MemoryStatus)]
        function.restype = ctypes.c_int
        if not function(ctypes.byref(status)):
            raise AsrResourceError("asr_resource_probe_failed")
        return MemorySnapshot(int(status.page_available), int(status.available), time.time_ns() // 1_000_000,
                              total_physical_bytes=int(status.total))
    except AsrResourceError:
        raise
    except (OSError, AttributeError, TypeError, ValueError):
        raise AsrResourceError("asr_resource_probe_failed") from None


def _read(reader: MemoryReader | None) -> MemorySnapshot | None:
    try:
        snapshot = (reader or read_memory_snapshot)()
        if snapshot is None and os.name == "nt":
            raise AsrResourceError("asr_resource_probe_failed")
        if snapshot is not None and (
            not isinstance(snapshot, MemorySnapshot)
            or type(snapshot.commit_available_bytes) is not int or snapshot.commit_available_bytes < 0
            or type(snapshot.physical_available_bytes) is not int or snapshot.physical_available_bytes < 0
        ):
            raise AsrResourceError("asr_resource_probe_failed")
        return snapshot
    except AsrResourceError:
        raise
    except Exception:
        raise AsrResourceError("asr_resource_probe_failed") from None


def check_capacity(profile: ResourceProfile, input_bytes: int = 0, *, reader: MemoryReader | None = None) -> CapacityCheck:
    if type(input_bytes) is not int or input_bytes < 0:
        raise ValueError("PCM input byte count must be a non-negative integer")
    # Conservatively cover raw PCM, split copies, float32 conversion and the
    # base64/JSON representations used by resident refinement. This is an
    # allocation budget, not an assertion about measured peak memory.
    input_budget = input_bytes * 8
    required_commit = profile.commit_budget_bytes + profile.reserve_commit_bytes + input_budget
    required_physical = profile.physical_budget_bytes + profile.reserve_physical_bytes + input_budget
    snapshot = _read(reader)
    result = CapacityCheck(
        snapshot is not None, profile.name,
        snapshot.commit_available_bytes if snapshot else None,
        snapshot.physical_available_bytes if snapshot else None,
        required_commit, required_physical, input_bytes, snapshot.sampled_at_ms if snapshot else None,
    )
    if snapshot and (snapshot.commit_available_bytes < required_commit or snapshot.physical_available_bytes < required_physical):
        error = AsrResourceError("asr_capacity_unavailable")
        error.capacity_check = result.as_dict()
        raise error
    return result


_COLD_START_LOCK = threading.Lock()


class ColdStartLease:
    def __init__(self) -> None:
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if not self._released:
                self._released = True
                _COLD_START_LOCK.release()

    @property
    def is_released(self) -> bool:
        """Observe this owned admission token, never another model's global lock."""
        with self._lock:
            return self._released

    def __enter__(self) -> ColdStartLease:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.release()


def cold_start_lease() -> ColdStartLease:
    if not _COLD_START_LOCK.acquire(blocking=False):
        raise AsrResourceError("asr_cold_start_busy")
    return ColdStartLease()


class OwnedPopenMonitor:
    """Watch only the Popen passed by its creator, never a PID/name search.

    The callback is a nonblocking first-failure notification and is invoked
    before process termination, so consumers can wake response waits without
    holding their inference lock. close() ends monitoring only; stop_owned()
    serializes timeout, cancellation and guard cleanup for this same handle.
    """

    def __init__(self, process: subprocess.Popen[Any], *, reader: MemoryReader | None = None,
                 on_failure: Callable[[AsrResourceError], None] | None = None, interval_s: float = 0.25) -> None:
        self.process = process
        self.reader = reader
        self.on_failure = on_failure
        self.interval_s = max(0.001, float(interval_s))
        self._stop = threading.Event()
        self._failure: AsrResourceError | None = None
        self._failure_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def failure(self) -> AsrResourceError | None:
        with self._failure_lock:
            return self._failure

    def start(self) -> OwnedPopenMonitor:
        if self._thread is None:
            self._thread = threading.Thread(target=self._watch, daemon=True, name="local-asr-resource-guard")
            self._thread.start()
        return self

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def close(self, timeout_s: float = 2.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout_s))
        return not self.is_alive

    def raise_if_failed(self) -> None:
        failure = self.failure
        if failure is not None:
            raise failure

    def wait_closed(self) -> None:
        """Join only from a daemon cleanup path, never an API/inference wait."""
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def stop_owned(self) -> int | None:
        with self._stop_lock:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2.0)
                return self.process.poll()
            except (OSError, subprocess.TimeoutExpired):
                if self.process.poll() is not None:
                    return self.process.poll()
                raise self.failure or AsrResourceError("asr_resource_guard_triggered") from None

    def _watch(self) -> None:
        while not self._stop.wait(self.interval_s):
            if self.process.poll() is not None:
                return
            try:
                check_capacity(WARM_INFERENCE_PROFILE, reader=self.reader)
            except AsrResourceError as observed:
                if self._stop.is_set() or self.process.poll() is not None:
                    return
                error = observed if observed.code == "asr_resource_probe_failed" else AsrResourceError("asr_resource_guard_triggered")
                if hasattr(observed, "capacity_check"):
                    error.capacity_check = observed.capacity_check
                with self._failure_lock:
                    if self._failure is not None:
                        return
                    self._failure = error
                if self.on_failure is not None:
                    try:
                        self.on_failure(error)
                    except Exception:
                        # A notification failure must not disable process cleanup.
                        pass
                try:
                    self.stop_owned()
                except AsrResourceError:
                    # Keep the first resource cause. Owner cleanup can retry the
                    # same handle and must not report this operation as passed.
                    pass
                return
