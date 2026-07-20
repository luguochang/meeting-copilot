from __future__ import annotations

from dataclasses import asdict, dataclass
import errno
import math
import os
from pathlib import Path
import re
import shutil
import stat
import threading
from typing import Any, Callable, NamedTuple


KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB

PCM_SAMPLE_RATE_HZ = 16_000
PCM_SAMPLE_WIDTH_BYTES = 2
RESERVED_FREE_BYTES = 2 * GIB
PRODUCT_SOFT_LIMIT_BYTES = 10 * GIB
AVAILABLE_SPACE_QUOTA_DIVISOR = 5
LOG_MAX_BYTES = 10 * MIB
LOG_BACKUP_COUNT = 5
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PRIVATE_STORAGE_PERMISSIONS_MARKER = ".storage-permissions-v1"

_LOG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.log$")
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}


class UnsafeManagedPathError(ValueError):
    """Raised when managed storage contains a path that could escape ownership."""


class DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


@dataclass(frozen=True)
class ManagedStorageScan:
    total_bytes: int
    file_count: int
    directory_count: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class StoragePreflightResult:
    allowed: bool
    decision: str
    reason_code: str
    user_message: str
    expected_duration_seconds: float
    track_count: int
    estimated_meeting_bytes: int
    managed_usage_bytes: int
    projected_managed_usage_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    reserved_free_bytes: int
    available_after_reserve_bytes: int
    product_soft_limit_bytes: int
    available_space_quota_bytes: int
    product_quota_bytes: int
    product_quota_remaining_bytes: int
    writable_capacity_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogWriteResult:
    path: str
    bytes_written: int
    file_size_bytes: int
    rotated: bool
    max_bytes: int
    backup_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _chmod_private(path: Path, mode: int) -> None:
    """Apply owner-only permissions where the host filesystem exposes them."""

    if os.name == "nt":
        # Windows app-data ACLs are inherited from the per-user app-data root.
        # The POSIX mode bits are not an ACL boundary there.
        return
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except TypeError:  # pragma: no cover - older Python/platform combination.
        os.chmod(path, mode)


def ensure_private_directory(path: str | Path) -> Path:
    """Create or tighten a managed directory without following symlinks."""

    requested = Path(path).expanduser()
    if requested.is_symlink():
        raise UnsafeManagedPathError("managed private directory must not be a symbolic link")
    directory = requested.resolve(strict=False)
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    if directory.is_symlink() or not directory.is_dir():
        raise UnsafeManagedPathError("managed private path must be a directory")
    _chmod_private(directory, PRIVATE_DIRECTORY_MODE)
    return directory


def harden_private_file(path: str | Path) -> Path:
    """Tighten an existing managed regular file and reject symlinks."""

    file_path = Path(path).expanduser()
    if file_path.is_symlink():
        raise UnsafeManagedPathError("managed private file must not be a symbolic link")
    if not file_path.exists() or not file_path.is_file():
        raise UnsafeManagedPathError("managed private path must be a regular file")
    _chmod_private(file_path, PRIVATE_FILE_MODE)
    return file_path


def harden_sqlite_files(database_path: str | Path) -> None:
    """Tighten SQLite's database and any live journal sidecars."""

    database = Path(database_path)
    for candidate in (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
    ):
        if candidate.exists():
            harden_private_file(candidate)


def harden_managed_storage_permissions(data_dir: str | Path) -> dict[str, int]:
    """Tighten an existing local data tree once, without following links."""

    root = ensure_private_directory(data_dir)
    marker = root / PRIVATE_STORAGE_PERMISSIONS_MARKER
    if marker.exists():
        harden_private_file(marker)
        return {"directory_count": 0, "file_count": 0, "already_hardened": 1}
    directory_count = 0
    file_count = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in os.scandir(directory):
            candidate = Path(entry.path)
            if entry.is_symlink():
                raise UnsafeManagedPathError(
                    f"managed storage contains a symbolic link: {candidate.name}"
                )
            if entry.is_dir(follow_symlinks=False):
                _chmod_private(candidate, PRIVATE_DIRECTORY_MODE)
                directory_count += 1
                pending.append(candidate)
            elif entry.is_file(follow_symlinks=False):
                harden_private_file(candidate)
                file_count += 1
            else:
                raise UnsafeManagedPathError(
                    f"managed storage contains an unsupported entry: {candidate.name}"
                )
    marker.write_text("owner-only storage permissions enforced\n", encoding="ascii")
    harden_private_file(marker)
    return {
        "directory_count": directory_count,
        "file_count": file_count,
        "already_hardened": 0,
    }


def estimate_pcm16_storage_bytes(
    *,
    duration_seconds: int | float,
    track_count: int,
    sample_rate_hz: int = PCM_SAMPLE_RATE_HZ,
) -> int:
    """Return a conservative raw PCM16 budget for a meeting recording."""

    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
    ):
        raise ValueError("duration_seconds must be a positive finite number")
    if isinstance(track_count, bool) or not isinstance(track_count, int) or track_count <= 0:
        raise ValueError("track_count must be a positive integer")
    if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, int) or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be a positive integer")

    sample_count_per_track = math.ceil(duration_seconds * sample_rate_hz)
    return sample_count_per_track * track_count * PCM_SAMPLE_WIDTH_BYTES


def scan_managed_storage(data_dir: str | Path) -> ManagedStorageScan:
    """Count regular files under data_dir without following indirection."""

    root = _absolute_path(data_dir)
    if root.is_symlink():
        raise UnsafeManagedPathError("managed data root must not be a symbolic link")
    if not root.exists():
        return ManagedStorageScan(total_bytes=0, file_count=0, directory_count=0)
    if not root.is_dir():
        raise UnsafeManagedPathError("managed data root must be a directory")

    resolved_root = root.resolve(strict=True)
    total_bytes = 0
    file_count = 0
    directory_count = 0
    pending = [root]

    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise UnsafeManagedPathError(
                f"managed storage cannot be scanned safely: {directory}"
            ) from exc

        for entry in entries:
            candidate = Path(entry.path)
            if entry.is_symlink():
                raise UnsafeManagedPathError(
                    f"managed storage contains a symbolic link: {candidate.name}"
                )
            _assert_resolves_within(candidate, resolved_root)
            try:
                if entry.is_dir(follow_symlinks=False):
                    directory_count += 1
                    pending.append(candidate)
                elif entry.is_file(follow_symlinks=False):
                    metadata = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise UnsafeManagedPathError(
                            f"managed storage contains a non-regular file: {candidate.name}"
                        )
                    total_bytes += metadata.st_size
                    file_count += 1
                else:
                    raise UnsafeManagedPathError(
                        f"managed storage contains an unsupported entry: {candidate.name}"
                    )
            except OSError as exc:
                raise UnsafeManagedPathError(
                    f"managed storage entry changed during scan: {candidate.name}"
                ) from exc

    return ManagedStorageScan(
        total_bytes=total_bytes,
        file_count=file_count,
        directory_count=directory_count,
    )


def preflight_meeting_storage(
    *,
    data_dir: str | Path,
    expected_duration_seconds: int | float,
    track_count: int,
    disk_usage_provider: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
) -> StoragePreflightResult:
    """Decide whether a recording may start under local storage policy."""

    estimated_bytes = estimate_pcm16_storage_bytes(
        duration_seconds=expected_duration_seconds,
        track_count=track_count,
    )
    scan = scan_managed_storage(data_dir)
    usage_path = _nearest_existing_path(_absolute_path(data_dir))
    raw_usage = disk_usage_provider(usage_path)
    disk_usage = _validated_disk_usage(raw_usage)

    available_after_reserve = max(0, disk_usage.free - RESERVED_FREE_BYTES)
    available_space_quota = disk_usage.free // AVAILABLE_SPACE_QUOTA_DIVISOR
    product_quota = min(PRODUCT_SOFT_LIMIT_BYTES, available_space_quota)
    product_quota_remaining = max(0, product_quota - scan.total_bytes)
    writable_capacity = min(available_after_reserve, product_quota_remaining)
    projected_usage = scan.total_bytes + estimated_bytes

    if estimated_bytes > available_after_reserve:
        allowed = False
        reason_code = "disk_reserve_required"
        user_message = (
            "无法开始会议：预计录音会使磁盘剩余空间低于必须保留的 2 GiB。"
            "请先释放磁盘空间或缩短预计会议时长。"
        )
    elif projected_usage > product_quota:
        allowed = False
        reason_code = "product_quota_exceeded"
        user_message = (
            "无法开始会议：本地会议数据将超过产品存储上限。"
            "该上限取 10 GiB 与当前可用空间 20% 中的较小值，请先清理旧会议。"
        )
    else:
        allowed = True
        reason_code = "storage_available"
        user_message = (
            "存储空间充足，可以开始会议。"
            f"预计新增 {_format_bytes(estimated_bytes)}，并保留至少 2 GiB 磁盘空间。"
        )

    return StoragePreflightResult(
        allowed=allowed,
        decision="allow" if allowed else "block",
        reason_code=reason_code,
        user_message=user_message,
        expected_duration_seconds=float(expected_duration_seconds),
        track_count=track_count,
        estimated_meeting_bytes=estimated_bytes,
        managed_usage_bytes=scan.total_bytes,
        projected_managed_usage_bytes=projected_usage,
        disk_total_bytes=disk_usage.total,
        disk_free_bytes=disk_usage.free,
        reserved_free_bytes=RESERVED_FREE_BYTES,
        available_after_reserve_bytes=available_after_reserve,
        product_soft_limit_bytes=PRODUCT_SOFT_LIMIT_BYTES,
        available_space_quota_bytes=available_space_quota,
        product_quota_bytes=product_quota,
        product_quota_remaining_bytes=product_quota_remaining,
        writable_capacity_bytes=writable_capacity,
    )


class ManagedLogRotator:
    """Append-only logger constrained to data_dir/logs with bounded backups."""

    def __init__(
        self,
        *,
        data_dir: str | Path,
        log_name: str,
        max_bytes: int = LOG_MAX_BYTES,
        backup_count: int = LOG_BACKUP_COUNT,
    ) -> None:
        if not isinstance(log_name, str) or not _LOG_NAME_PATTERN.fullmatch(log_name):
            raise ValueError("log_name must be a simple filename ending in .log")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if (
            isinstance(backup_count, bool)
            or not isinstance(backup_count, int)
            or backup_count <= 0
        ):
            raise ValueError("backup_count must be a positive integer")

        self._data_dir = _absolute_path(data_dir)
        self._logs_dir = self._data_dir / "logs"
        self._path = self._logs_dir / log_name
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = _process_lock_for(self._path)

    @property
    def path(self) -> Path:
        return self._path

    def backup_path(self, index: int) -> Path:
        if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
            raise ValueError("backup index must be a positive integer")
        return self._logs_dir / f"{self._path.name}.{index}"

    def append(self, payload: str | bytes) -> LogWriteResult:
        if isinstance(payload, str):
            encoded = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            encoded = payload
        else:
            raise TypeError("log payload must be str or bytes")
        if len(encoded) > self._max_bytes:
            raise ValueError("one log payload must not exceed max_bytes")

        with self._lock:
            self._prepare_managed_logs()
            current_size = _regular_file_size(self._path)
            rotated = bool(
                current_size
                and current_size + len(encoded) > self._max_bytes
            )
            if rotated:
                self._rotate()
            self._append_bytes(encoded)
            file_size = _regular_file_size(self._path)

        return LogWriteResult(
            path=str(self._path),
            bytes_written=len(encoded),
            file_size_bytes=file_size,
            rotated=rotated,
            max_bytes=self._max_bytes,
            backup_count=self._backup_count,
        )

    def _prepare_managed_logs(self) -> None:
        if self._data_dir.is_symlink():
            raise UnsafeManagedPathError("managed data root must not be a symbolic link")
        if self._data_dir.exists() and not self._data_dir.is_dir():
            raise UnsafeManagedPathError("managed data root must be a directory")
        self._data_dir.mkdir(parents=True, exist_ok=True)

        if self._logs_dir.is_symlink():
            raise UnsafeManagedPathError("managed logs directory must not be a symbolic link")
        if self._logs_dir.exists() and not self._logs_dir.is_dir():
            raise UnsafeManagedPathError("managed logs path must be a directory")
        self._logs_dir.mkdir(mode=0o700, exist_ok=True)

        resolved_data_dir = self._data_dir.resolve(strict=True)
        _assert_resolves_within(self._logs_dir, resolved_data_dir)
        for path in [
            self._path,
            *(self.backup_path(index) for index in range(1, self._backup_count + 1)),
        ]:
            _validate_optional_regular_file(path, resolved_data_dir)

    def _rotate(self) -> None:
        oldest = self.backup_path(self._backup_count)
        if oldest.exists():
            oldest.unlink()
        for index in range(self._backup_count - 1, 0, -1):
            source = self.backup_path(index)
            if source.exists():
                os.replace(source, self.backup_path(index + 1))
        os.replace(self._path, self.backup_path(1))
        _fsync_directory(self._logs_dir)

    def _append_bytes(self, payload: bytes) -> None:
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EMLINK}:
                raise UnsafeManagedPathError(
                    "managed log file must not be a symbolic link"
                ) from exc
            raise
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(self._logs_dir)


def _validated_disk_usage(raw_usage: Any) -> DiskUsage:
    try:
        total = raw_usage.total
        used = raw_usage.used
        free = raw_usage.free
    except AttributeError as exc:
        raise ValueError("disk usage provider returned an invalid result") from exc
    for name, value in (("total", total), ("used", used), ("free", free)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"disk usage {name} must be a non-negative integer")
    if used + free > total:
        raise ValueError("disk usage used and free must not exceed total")
    return DiskUsage(total=total, used=used, free=free)


def _absolute_path(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _nearest_existing_path(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise ValueError("data_dir has no existing filesystem parent")
        current = parent
    return current


def _assert_resolves_within(path: Path, resolved_root: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise UnsafeManagedPathError(
            f"managed storage path cannot be resolved safely: {path.name}"
        ) from exc
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise UnsafeManagedPathError(
            f"managed storage path escapes data_dir: {path.name}"
        )


def _validate_optional_regular_file(path: Path, resolved_root: Path) -> None:
    if path.is_symlink():
        raise UnsafeManagedPathError(
            f"managed log path must not be a symbolic link: {path.name}"
        )
    if not path.exists():
        return
    _assert_resolves_within(path, resolved_root)
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeManagedPathError(
            f"managed log path must be a regular file: {path.name}"
        )


def _regular_file_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_symlink():
        raise UnsafeManagedPathError(
            f"managed log path must not be a symbolic link: {path.name}"
        )
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeManagedPathError(
            f"managed log path must be a regular file: {path.name}"
        )
    return metadata.st_size


def _process_lock_for(path: Path) -> threading.Lock:
    key = os.fspath(path)
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _format_bytes(value: int) -> str:
    if value >= GIB:
        return f"{value / GIB:.2f} GiB"
    if value >= MIB:
        return f"{value / MIB:.2f} MiB"
    if value >= KIB:
        return f"{value / KIB:.2f} KiB"
    return f"{value} B"
