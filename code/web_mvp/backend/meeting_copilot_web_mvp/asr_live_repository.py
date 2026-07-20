from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
import time
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised through the platform fallback test.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - unavailable on non-Windows platforms.
    msvcrt = None  # type: ignore[assignment]

from meeting_copilot_web_mvp.repository import SESSION_ID_PATTERN
from meeting_copilot_web_mvp.storage_governance import (
    ensure_private_directory,
    harden_private_file,
)


_JSON_REPOSITORY_LOCKS: dict[Path, RLock] = {}
_JSON_REPOSITORY_LOCKS_GUARD = RLock()


def _now_epoch_ms() -> int:
    return time.time_ns() // 1_000_000


def _coerce_epoch_ms(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _stamp_created_record(record: dict[str, Any]) -> dict[str, Any]:
    stamped = deepcopy(record)
    now_ms = _now_epoch_ms()
    created_at_ms = _coerce_epoch_ms(stamped.get("created_at_epoch_ms"), now_ms)
    last_activity_at_ms = max(
        created_at_ms,
        _coerce_epoch_ms(stamped.get("last_activity_at_epoch_ms"), now_ms),
    )
    stamped["created_at_epoch_ms"] = created_at_ms
    stamped["last_activity_at_epoch_ms"] = last_activity_at_ms
    return stamped


def _stamp_updated_record(existing: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
    stamped = deepcopy(updated)
    created_at_ms = _coerce_epoch_ms(
        existing.get("created_at_epoch_ms"),
        _coerce_epoch_ms(stamped.get("created_at_epoch_ms"), _now_epoch_ms()),
    )
    previous_activity_ms = _coerce_epoch_ms(
        existing.get("last_activity_at_epoch_ms"),
        created_at_ms,
    )
    requested_activity_ms = _coerce_epoch_ms(
        stamped.get("last_activity_at_epoch_ms"),
        previous_activity_ms,
    )
    stamped["created_at_epoch_ms"] = created_at_ms
    stamped["last_activity_at_epoch_ms"] = max(
        created_at_ms,
        previous_activity_ms,
        requested_activity_ms,
    )
    return stamped


def _with_legacy_file_timestamps(record: dict[str, Any], path: Path) -> dict[str, Any]:
    if record.get("created_at_epoch_ms") and record.get("last_activity_at_epoch_ms"):
        return record
    fallback_ms = path.stat().st_mtime_ns // 1_000_000
    hydrated = deepcopy(record)
    created_at_ms = _coerce_epoch_ms(hydrated.get("created_at_epoch_ms"), fallback_ms)
    hydrated["created_at_epoch_ms"] = created_at_ms
    hydrated["last_activity_at_epoch_ms"] = max(
        created_at_ms,
        _coerce_epoch_ms(hydrated.get("last_activity_at_epoch_ms"), fallback_ms),
    )
    return hydrated


def _shared_repository_lock(records_dir: Path) -> RLock:
    resolved_dir = records_dir.resolve()
    with _JSON_REPOSITORY_LOCKS_GUARD:
        return _JSON_REPOSITORY_LOCKS.setdefault(resolved_dir, RLock())


def _repository_lock_capability() -> str:
    if fcntl is not None:
        return "fcntl"
    if msvcrt is not None:
        return "msvcrt"
    return "process_only"


class InMemoryAsrLiveSessionRepository:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            session_id = str(record["session_id"])
            if session_id in self._records:
                raise ValueError(f"ASR live session already exists: {session_id}")
            stamped = _stamp_created_record(record)
            self._records[session_id] = deepcopy(stamped)
            return deepcopy(stamped)

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                return deepcopy(self._records[session_id])
            except KeyError as exc:
                raise KeyError(f"ASR live session not found: {session_id}") from exc

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                deepcopy(record)
                for _session_id, record in sorted(self._records.items())
            ]

    def replace(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            session_id = str(record["session_id"])
            if session_id not in self._records:
                raise KeyError(f"ASR live session not found: {session_id}")
            stamped = _stamp_updated_record(self._records[session_id], record)
            self._records[session_id] = deepcopy(stamped)
            return deepcopy(stamped)

    def update(
        self,
        session_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            try:
                current = deepcopy(self._records[session_id])
            except KeyError as exc:
                raise KeyError(f"ASR live session not found: {session_id}") from exc
            updated = mutator(current)
            if str(updated.get("session_id") or "") != session_id:
                raise ValueError("ASR live session update cannot change session_id")
            stamped = _stamp_updated_record(current, updated)
            self._records[session_id] = deepcopy(stamped)
            return deepcopy(stamped)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._records.pop(session_id, None) is not None


class JsonFileAsrLiveSessionRepository:
    def __init__(self, data_dir: str | Path) -> None:
        self._records_dir = Path(data_dir) / "live_asr_sessions"
        self._lock = _shared_repository_lock(self._records_dir)

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._locked():
            session_id = str(record["session_id"])
            path = self._record_path(session_id)
            if path.exists():
                raise ValueError(f"ASR live session already exists: {session_id}")
            stamped = _stamp_created_record(record)
            self._write_record(path, stamped)
            return deepcopy(stamped)

    def get(self, session_id: str) -> dict[str, Any]:
        with self._locked():
            path = self._record_path(session_id)
            if not path.exists():
                raise KeyError(f"ASR live session not found: {session_id}")
            record = json.loads(path.read_text(encoding="utf-8"))
            return _with_legacy_file_timestamps(record, path)

    def list(self) -> list[dict[str, Any]]:
        with self._locked():
            if not self._records_dir.exists():
                return []
            records: list[dict[str, Any]] = []
            for path in sorted(self._records_dir.glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                records.append(_with_legacy_file_timestamps(record, path))
            return records

    def replace(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._locked():
            session_id = str(record["session_id"])
            path = self._record_path(session_id)
            if not path.exists():
                raise KeyError(f"ASR live session not found: {session_id}")
            current = _with_legacy_file_timestamps(
                json.loads(path.read_text(encoding="utf-8")),
                path,
            )
            stamped = _stamp_updated_record(current, record)
            self._write_record(path, stamped)
            return deepcopy(stamped)

    def update(
        self,
        session_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._locked():
            path = self._record_path(session_id)
            if not path.exists():
                raise KeyError(f"ASR live session not found: {session_id}")
            current = _with_legacy_file_timestamps(
                json.loads(path.read_text(encoding="utf-8")),
                path,
            )
            updated = mutator(current)
            if str(updated.get("session_id") or "") != session_id:
                raise ValueError("ASR live session update cannot change session_id")
            stamped = _stamp_updated_record(current, updated)
            self._write_record(path, stamped)
            return deepcopy(stamped)

    def delete(self, session_id: str) -> bool:
        with self._locked():
            path = self._record_path(session_id)
            if not path.exists():
                return False
            path.unlink()
            return True

    def _record_path(self, session_id: str) -> Path:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(f"unsafe session_id: {session_id}")
        return self._records_dir / f"{session_id}.json"

    @contextmanager
    def _locked(self):
        with self._lock:
            ensure_private_directory(self._records_dir)
            capability = _repository_lock_capability()
            if capability == "process_only":
                yield
                return
            lock_path = self._records_dir / ".repository.lock"
            with lock_path.open("a+b") as lock_file:
                if capability == "fcntl":
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                else:
                    lock_file.seek(0, os.SEEK_END)
                    if lock_file.tell() == 0:
                        lock_file.write(b"\0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    if capability == "fcntl":
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    else:
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

    def _write_record(self, path: Path, record: dict[str, Any]) -> None:
        ensure_private_directory(self._records_dir)
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._records_dir,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            harden_private_file(temp_path)
            os.replace(temp_path, path)
            harden_private_file(path)
            temp_path = None
            directory_fd = os.open(self._records_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
