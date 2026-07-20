from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
from typing import Any, Protocol
from uuid import uuid4

try:  # pragma: no cover - selected by platform
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - selected by platform
    import msvcrt
except ImportError:  # pragma: no cover - POSIX only
    msvcrt = None  # type: ignore[assignment]


CURRENT_SCHEMA_VERSION = 1
MAX_SUPPORTED_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
MAX_SCHEMA_VERSION = MAX_SUPPORTED_SCHEMA_VERSION
MIGRATION_HISTORY_TABLE = "meeting_copilot_schema_migrations"
FINGERPRINT_ALGORITHM = "sha256"

_HISTORY_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {MIGRATION_HISTORY_TABLE} (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
)
"""
_LOCK_POLL_SECONDS = 0.025


class SchemaMigrationError(RuntimeError):
    """Base error for SQLite schema migration failures."""


class MigrationPreflightError(SchemaMigrationError):
    """Raised when a database cannot safely enter migration."""


class FutureSchemaVersionError(MigrationPreflightError):
    """Raised when the database was written by a newer application version."""


class MigrationRegistryError(MigrationPreflightError):
    """Raised when the declared migration sequence is invalid or incomplete."""


class MigrationHistoryError(MigrationPreflightError):
    """Raised when durable migration history does not match the registry."""


class MigrationLockTimeout(MigrationPreflightError):
    """Raised when the process or file migration lock cannot be acquired in time."""


class MigrationConnection(Protocol):
    """Restricted connection surface supplied to migration callbacks."""

    def execute(
        self,
        sql: str,
        parameters: Iterable[Any] = (),
    ) -> sqlite3.Cursor: ...

    def executemany(
        self,
        sql: str,
        parameters: Iterable[Iterable[Any]],
    ) -> sqlite3.Cursor: ...


MigrationCallable = Callable[[MigrationConnection], None]


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    """One immutable transition from ``version - 1`` to ``version``."""

    version: int
    name: str
    fingerprint: str
    apply: MigrationCallable

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise ValueError("migration version must be an integer")
        if self.version <= 0:
            raise ValueError("migration version must be positive")
        if not self.name or not self.name.strip() or "\x00" in self.name:
            raise ValueError("migration name must be non-empty")
        if len(self.name) > 200:
            raise ValueError("migration name must not exceed 200 characters")
        expected_prefix = f"{FINGERPRINT_ALGORITHM}:"
        digest = self.fingerprint.removeprefix(expected_prefix)
        if (
            not self.fingerprint.startswith(expected_prefix)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("migration fingerprint must be a sha256 fingerprint")
        if not callable(self.apply):
            raise ValueError("migration apply must be callable")


Migration = SchemaMigration


def migration_fingerprint(identity: str | bytes) -> str:
    """Return a stable fingerprint for a callback's reviewed migration identity."""

    payload = identity.encode("utf-8") if isinstance(identity, str) else bytes(identity)
    return f"{FINGERPRINT_ALGORITHM}:{hashlib.sha256(payload).hexdigest()}"


def sql_migration(
    version: int,
    name: str,
    statements: Iterable[str],
) -> SchemaMigration:
    """Build a fingerprinted migration from individually executed SQL statements."""

    normalized = tuple(statements)
    if not normalized:
        raise ValueError("SQL migration must contain at least one statement")
    if any(not isinstance(statement, str) or not statement.strip() for statement in normalized):
        raise ValueError("SQL migration statements must be non-empty strings")
    fingerprint_payload = json.dumps(
        {"name": name, "statements": normalized, "version": version},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )

    def apply(connection: MigrationConnection) -> None:
        for statement in normalized:
            connection.execute(statement)

    return SchemaMigration(
        version=version,
        name=name,
        fingerprint=migration_fingerprint(fingerprint_payload),
        apply=apply,
    )


class MigrationRegistry(Sequence[SchemaMigration]):
    """Validated version-indexed migration declarations."""

    def __init__(self, migrations: Iterable[SchemaMigration]) -> None:
        entries = tuple(migrations)
        if any(not isinstance(migration, SchemaMigration) for migration in entries):
            raise TypeError("registry entries must be SchemaMigration instances")
        ordered = sorted(entries, key=lambda migration: migration.version)
        by_version: dict[int, SchemaMigration] = {}
        for migration in ordered:
            if migration.version in by_version:
                raise MigrationRegistryError(f"duplicate SQLite migration version {migration.version}")
            by_version[migration.version] = migration
        self._ordered = tuple(ordered)
        self._by_version = by_version

    def __getitem__(self, index: int | slice) -> SchemaMigration | tuple[SchemaMigration, ...]:
        return self._ordered[index]

    def __len__(self) -> int:
        return len(self._ordered)

    def get(self, version: int) -> SchemaMigration | None:
        return self._by_version.get(version)

    def pending(self, source_version: int, target_version: int) -> tuple[SchemaMigration, ...]:
        if source_version >= target_version:
            return ()
        missing = [
            version for version in range(source_version + 1, target_version + 1) if version not in self._by_version
        ]
        if missing:
            rendered = ", ".join(str(version) for version in missing)
            raise MigrationRegistryError(f"SQLite migration registry is missing sequential version(s): {rendered}")
        return tuple(self._by_version[version] for version in range(source_version + 1, target_version + 1))


@dataclass(frozen=True, slots=True)
class SchemaMigrationResult:
    database_path: Path
    source_version: int
    final_version: int
    applied_versions: tuple[int, ...]
    backup_path: Path | None

    @property
    def migrated(self) -> bool:
        return bool(self.applied_versions)


MigrationResult = SchemaMigrationResult
MigrationFailpoint = Callable[[str, SchemaMigration | None], None]


@dataclass(frozen=True, slots=True)
class _HistoryEntry:
    version: int
    name: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class _DatabaseState:
    version: int
    history: tuple[_HistoryEntry, ...]


class _RestrictedMigrationConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(
        self,
        sql: str,
        parameters: Iterable[Any] = (),
    ) -> sqlite3.Cursor:
        return self._connection.execute(sql, tuple(parameters))

    def executemany(
        self,
        sql: str,
        parameters: Iterable[Iterable[Any]],
    ) -> sqlite3.Cursor:
        return self._connection.executemany(
            sql,
            (tuple(row) for row in parameters),
        )


class _PathLock:
    def __init__(self) -> None:
        self.process_lock = threading.RLock()
        self.local = threading.local()


_PATH_LOCKS: dict[str, _PathLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(lock_path: Path) -> _PathLock:
    key = str(lock_path.resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PathLock()
            _PATH_LOCKS[key] = lock
        return lock


def _default_lock_path(database_path: Path) -> Path:
    return database_path.with_name(f"{database_path.name}.schema-migration.lock")


@contextmanager
def sqlite_schema_migration_lock(
    database_path: str | Path,
    *,
    lock_path: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> Iterator[Path]:
    """Serialize schema migration in this process and across cooperating processes."""

    timeout = _validated_timeout(timeout_seconds)
    database = Path(database_path)
    selected_lock_path = Path(lock_path) if lock_path is not None else _default_lock_path(database)
    selected_lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    path_lock = _path_lock(selected_lock_path)
    if not path_lock.process_lock.acquire(timeout=timeout):
        raise MigrationLockTimeout(f"timed out acquiring in-process SQLite migration lock: {selected_lock_path}")

    entered = False
    try:
        depth = int(getattr(path_lock.local, "depth", 0))
        if depth == 0:
            descriptor = _open_owner_only_regular_file(selected_lock_path)
            try:
                _acquire_file_lock(
                    descriptor,
                    selected_lock_path,
                    deadline=deadline,
                )
            except BaseException:
                os.close(descriptor)
                raise
            path_lock.local.descriptor = descriptor
        path_lock.local.depth = depth + 1
        entered = True
        yield selected_lock_path
    finally:
        if entered:
            remaining_depth = int(path_lock.local.depth) - 1
            path_lock.local.depth = remaining_depth
            if remaining_depth == 0:
                descriptor = int(path_lock.local.descriptor)
                try:
                    _release_file_lock(descriptor)
                finally:
                    os.close(descriptor)
                    del path_lock.local.descriptor
                    del path_lock.local.depth
        path_lock.process_lock.release()


class SQLiteSchemaMigrator:
    """Backup, verify, and atomically advance one SQLite schema."""

    def __init__(
        self,
        migrations: Iterable[SchemaMigration] = (),
        *,
        current_version: int = CURRENT_SCHEMA_VERSION,
        max_supported_version: int = MAX_SUPPORTED_SCHEMA_VERSION,
    ) -> None:
        self.current_version = _validated_version(current_version, label="current_version")
        self.max_supported_version = _validated_version(
            max_supported_version,
            label="max_supported_version",
        )
        if self.current_version > self.max_supported_version:
            raise ValueError("current_version must not exceed max_supported_version")
        self.registry = MigrationRegistry(migrations)
        unsupported = [
            migration.version for migration in self.registry if migration.version > self.max_supported_version
        ]
        if unsupported:
            rendered = ", ".join(str(version) for version in unsupported)
            raise MigrationRegistryError(f"SQLite migration version(s) exceed maximum support: {rendered}")

    def migrate(
        self,
        database_path: str | Path,
        *,
        backup_dir: str | Path | None = None,
        lock_path: str | Path | None = None,
        timeout_seconds: float = 30.0,
        failpoint: MigrationFailpoint | None = None,
    ) -> SchemaMigrationResult:
        database = _validated_database_path(database_path)
        timeout = _validated_timeout(timeout_seconds)
        selected_backup_dir = Path(backup_dir) if backup_dir is not None else database.parent / "migration_backups"

        initial_state = _inspect_database(
            database,
            registry=self.registry,
            max_supported_version=self.max_supported_version,
            timeout_seconds=timeout,
        )
        initial_pending = self.registry.pending(
            initial_state.version,
            self.current_version,
        )
        if not initial_pending:
            return SchemaMigrationResult(
                database_path=database,
                source_version=initial_state.version,
                final_version=initial_state.version,
                applied_versions=(),
                backup_path=None,
            )

        with sqlite_schema_migration_lock(
            database,
            lock_path=lock_path,
            timeout_seconds=timeout,
        ):
            locked_state = _inspect_database(
                database,
                registry=self.registry,
                max_supported_version=self.max_supported_version,
                timeout_seconds=timeout,
            )
            pending = self.registry.pending(
                locked_state.version,
                self.current_version,
            )
            if not pending:
                return SchemaMigrationResult(
                    database_path=database,
                    source_version=locked_state.version,
                    final_version=locked_state.version,
                    applied_versions=(),
                    backup_path=None,
                )
            return self._migrate_locked(
                database,
                source_state=locked_state,
                pending=pending,
                backup_dir=selected_backup_dir,
                timeout_seconds=timeout,
                failpoint=failpoint,
            )

    def _migrate_locked(
        self,
        database_path: Path,
        *,
        source_state: _DatabaseState,
        pending: tuple[SchemaMigration, ...],
        backup_dir: Path,
        timeout_seconds: float,
        failpoint: MigrationFailpoint | None,
    ) -> SchemaMigrationResult:
        database_existed = database_path.exists()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            database_path,
            isolation_level=None,
            timeout=float(timeout_seconds),
        )
        backup_path: Path | None = None
        actual_source_version = source_state.version
        try:
            _configure_connection(connection, timeout_seconds=timeout_seconds)
            connection.execute("BEGIN IMMEDIATE")
            try:
                transaction_state = _inspect_open_connection(
                    connection,
                    registry=self.registry,
                    max_supported_version=self.max_supported_version,
                )
                actual_source_version = transaction_state.version
                if transaction_state != source_state:
                    pending = self.registry.pending(
                        transaction_state.version,
                        self.current_version,
                    )
                if not pending:
                    connection.execute("ROLLBACK")
                    return SchemaMigrationResult(
                        database_path=database_path,
                        source_version=transaction_state.version,
                        final_version=transaction_state.version,
                        applied_versions=(),
                        backup_path=None,
                    )

                if database_existed:
                    backup_path = _create_verified_backup(
                        database_path,
                        backup_dir=backup_dir,
                        source_version=transaction_state.version,
                        target_version=self.current_version,
                        timeout_seconds=timeout_seconds,
                    )
                _hit_failpoint(failpoint, "after_backup", None)
                connection.execute(_HISTORY_CREATE_SQL)
                applied_versions: list[int] = []
                expected_version = transaction_state.version
                restricted_connection = _RestrictedMigrationConnection(connection)
                for migration in pending:
                    if migration.version != expected_version + 1:
                        raise MigrationRegistryError("SQLite migration sequence changed while applying")
                    _hit_failpoint(failpoint, "before_migration", migration)
                    _apply_migration(connection, restricted_connection, migration)
                    observed_version = _sqlite_schema_version(connection)
                    if observed_version != expected_version:
                        raise MigrationRegistryError(f"migration {migration.version} modified PRAGMA user_version")
                    connection.execute(f"PRAGMA user_version = {migration.version}")
                    connection.execute(
                        f"INSERT INTO {MIGRATION_HISTORY_TABLE} "
                        "(version, name, fingerprint, applied_at_ms) VALUES (?, ?, ?, ?)",
                        (
                            migration.version,
                            migration.name,
                            migration.fingerprint,
                            time.time_ns() // 1_000_000,
                        ),
                    )
                    expected_version = migration.version
                    applied_versions.append(migration.version)
                    _hit_failpoint(failpoint, "after_migration", migration)

                if expected_version != self.current_version:
                    raise MigrationRegistryError("SQLite migration sequence did not reach current_version")
                _require_integrity(connection, label="migrated database")
                _verify_history(
                    _read_history(connection),
                    database_version=expected_version,
                    registry=self.registry,
                )
                _hit_failpoint(failpoint, "before_commit", None)
                connection.execute("COMMIT")
            except BaseException as exc:
                _rollback_with_note(connection, exc)
                if backup_path is not None:
                    exc.add_note(f"complete pre-migration backup: {backup_path}")
                raise
        finally:
            connection.close()

        return SchemaMigrationResult(
            database_path=database_path,
            source_version=actual_source_version,
            final_version=self.current_version,
            applied_versions=tuple(applied_versions),
            backup_path=backup_path,
        )


def migrate_sqlite_schema(
    database_path: str | Path,
    migrations: Iterable[SchemaMigration] = (),
    *,
    current_version: int = CURRENT_SCHEMA_VERSION,
    max_supported_version: int = MAX_SUPPORTED_SCHEMA_VERSION,
    backup_dir: str | Path | None = None,
    lock_path: str | Path | None = None,
    timeout_seconds: float = 30.0,
    failpoint: MigrationFailpoint | None = None,
) -> SchemaMigrationResult:
    """Convenience API for a single locked schema migration attempt."""

    return SQLiteSchemaMigrator(
        migrations,
        current_version=current_version,
        max_supported_version=max_supported_version,
    ).migrate(
        database_path,
        backup_dir=backup_dir,
        lock_path=lock_path,
        timeout_seconds=timeout_seconds,
        failpoint=failpoint,
    )


def _validated_version(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _validated_timeout(value: float) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout_seconds must be a finite non-negative number")
    return timeout


def _validated_database_path(database_path: str | Path) -> Path:
    if str(database_path) == ":memory:":
        raise ValueError("SQLite schema migration requires a filesystem database")
    path = Path(database_path)
    if path.exists():
        _require_regular_path(path, label="SQLite database")
    return path


def _configure_connection(
    connection: sqlite3.Connection,
    *,
    timeout_seconds: float,
) -> None:
    timeout_ms = max(0, int(float(timeout_seconds) * 1_000))
    connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
    connection.execute("PRAGMA foreign_keys = ON")


def _open_read_only(
    database_path: Path,
    *,
    timeout_seconds: float,
) -> sqlite3.Connection:
    uri = f"{database_path.resolve(strict=True).as_uri()}?mode=ro"
    connection = sqlite3.connect(
        uri,
        uri=True,
        isolation_level=None,
        timeout=float(timeout_seconds),
    )
    _configure_connection(connection, timeout_seconds=timeout_seconds)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _inspect_database(
    database_path: Path,
    *,
    registry: MigrationRegistry,
    max_supported_version: int,
    timeout_seconds: float,
) -> _DatabaseState:
    if not database_path.exists():
        return _DatabaseState(version=0, history=())
    _require_regular_path(database_path, label="SQLite database")
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_read_only(
            database_path,
            timeout_seconds=timeout_seconds,
        )
        return _inspect_open_connection(
            connection,
            registry=registry,
            max_supported_version=max_supported_version,
        )
    except (FutureSchemaVersionError, MigrationHistoryError, MigrationPreflightError):
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise MigrationPreflightError(f"SQLite migration preflight failed: {_safe_error(exc)}") from exc
    finally:
        if connection is not None:
            connection.close()


def _inspect_open_connection(
    connection: sqlite3.Connection,
    *,
    registry: MigrationRegistry,
    max_supported_version: int,
) -> _DatabaseState:
    try:
        version = _sqlite_schema_version(connection)
    except sqlite3.DatabaseError as exc:
        raise MigrationPreflightError(f"could not read SQLite schema version: {_safe_error(exc)}") from exc
    if version > max_supported_version:
        raise FutureSchemaVersionError(
            f"unsupported future SQLite schema version {version}; maximum supported version is {max_supported_version}"
        )
    _require_integrity(connection, label="source database")
    history = _read_history(connection)
    _verify_history(
        history,
        database_version=version,
        registry=registry,
    )
    return _DatabaseState(version=version, history=history)


def _sqlite_schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _require_integrity(connection: sqlite3.Connection, *, label: str) -> None:
    try:
        results = [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]
    except sqlite3.DatabaseError as exc:
        raise MigrationPreflightError(f"{label} integrity_check failed: {_safe_error(exc)}") from exc
    if results != ["ok"]:
        detail = "; ".join(results[:10]) or "no integrity result"
        raise MigrationPreflightError(f"{label} integrity_check failed: {detail}")


def _read_history(connection: sqlite3.Connection) -> tuple[_HistoryEntry, ...]:
    object_row = connection.execute(
        "SELECT type FROM sqlite_master WHERE name = ?",
        (MIGRATION_HISTORY_TABLE,),
    ).fetchone()
    if object_row is None:
        return ()
    if str(object_row[0]) != "table":
        raise MigrationHistoryError(f"reserved migration history name is not a table: {MIGRATION_HISTORY_TABLE}")
    try:
        rows = connection.execute(
            f"SELECT version, name, fingerprint FROM {MIGRATION_HISTORY_TABLE} ORDER BY version"
        ).fetchall()
        return tuple(
            _HistoryEntry(
                version=int(row[0]),
                name=str(row[1]),
                fingerprint=str(row[2]),
            )
            for row in rows
        )
    except (TypeError, ValueError, sqlite3.DatabaseError) as exc:
        raise MigrationHistoryError(f"invalid SQLite migration history: {_safe_error(exc)}") from exc


def _verify_history(
    history: tuple[_HistoryEntry, ...],
    *,
    database_version: int,
    registry: MigrationRegistry,
) -> None:
    for entry in history:
        if entry.version <= 0 or entry.version > database_version:
            raise MigrationHistoryError(
                f"migration history version {entry.version} exceeds database version {database_version}"
            )
        declared = registry.get(entry.version)
        if declared is None:
            raise MigrationHistoryError(f"migration history version {entry.version} has no registered migration")
        if entry.name != declared.name:
            raise MigrationHistoryError(f"migration history name mismatch at version {entry.version}")
        if entry.fingerprint != declared.fingerprint:
            raise MigrationHistoryError(f"migration history fingerprint mismatch at version {entry.version}")


def _apply_migration(
    connection: sqlite3.Connection,
    restricted_connection: _RestrictedMigrationConnection,
    migration: SchemaMigration,
) -> None:
    def authorizer(
        action: int,
        argument_one: str | None,
        _argument_two: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_TRANSACTION:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and str(argument_one or "").casefold() == "user_version":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorizer)
    try:
        migration.apply(restricted_connection)
    finally:
        connection.set_authorizer(None)
    if not connection.in_transaction:
        raise MigrationRegistryError(f"migration {migration.version} ended the enclosing transaction")


def _create_verified_backup(
    database_path: Path,
    *,
    backup_dir: Path,
    source_version: int,
    target_version: int,
    timeout_seconds: float,
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    unique = f"{time.time_ns()}.{uuid4().hex}"
    final_path = backup_dir / (
        f"{database_path.stem}.pre-schema-v{source_version}-to-v{target_version}.{unique}.sqlite3"
    )
    temporary_path = backup_dir / f".{final_path.name}.tmp"
    descriptor = _create_owner_only_regular_file(temporary_path)
    os.close(descriptor)
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = _open_read_only(
            database_path,
            timeout_seconds=timeout_seconds,
        )
        if _sqlite_schema_version(source) != source_version:
            raise MigrationPreflightError("SQLite schema version changed before backup snapshot")
        _require_integrity(source, label="backup source")
        destination = sqlite3.connect(
            temporary_path,
            isolation_level=None,
            timeout=float(timeout_seconds),
        )
        source.backup(destination)
        _require_integrity(destination, label="migration backup")
        if _sqlite_schema_version(destination) != source_version:
            raise MigrationPreflightError("migration backup schema version does not match source")
        destination.close()
        destination = None
        source.close()
        source = None
        os.chmod(temporary_path, 0o600, follow_symlinks=False)
        _require_owner_only_regular_path(
            temporary_path,
            label="temporary migration backup",
        )
        with temporary_path.open("rb") as backup_file:
            os.fsync(backup_file.fileno())
        os.replace(temporary_path, final_path)
        _require_owner_only_regular_path(final_path, label="migration backup")
        _fsync_directory(backup_dir)
        return final_path
    except (MigrationPreflightError, OSError, sqlite3.DatabaseError) as exc:
        if isinstance(exc, MigrationPreflightError):
            raise
        raise MigrationPreflightError(f"could not create verified SQLite migration backup: {_safe_error(exc)}") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        temporary_path.unlink(missing_ok=True)


def _create_owner_only_regular_file(path: Path) -> int:
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise MigrationPreflightError(f"path is not a regular file: {path}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def _open_owner_only_regular_file(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise MigrationPreflightError(f"could not open SQLite migration lock: {_safe_error(exc)}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise MigrationPreflightError(f"SQLite migration lock is not a regular file: {path}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _acquire_file_lock(descriptor: int, path: Path, *, deadline: float) -> None:
    if fcntl is not None:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise MigrationLockTimeout(f"timed out acquiring SQLite migration file lock: {path}") from exc
                time.sleep(_LOCK_POLL_SECONDS)
    if msvcrt is None:  # pragma: no cover - no supported file lock API
        raise MigrationPreflightError("no supported SQLite migration file lock API")
    if os.fstat(descriptor).st_size == 0:  # pragma: no cover - Windows only
        os.write(descriptor, b"\0")
    while True:  # pragma: no cover - Windows only
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise MigrationLockTimeout(f"timed out acquiring SQLite migration file lock: {path}") from exc
            time.sleep(_LOCK_POLL_SECONDS)


def _release_file_lock(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    if msvcrt is not None:  # pragma: no cover - Windows only
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)


def _require_regular_path(path: Path, *, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise MigrationPreflightError(f"could not inspect {label}: {_safe_error(exc)}") from exc
    if not stat.S_ISREG(mode):
        raise MigrationPreflightError(f"{label} is not a regular file: {path}")


def _require_owner_only_regular_path(path: Path, *, label: str) -> None:
    _require_regular_path(path, label=label)
    permissions = stat.S_IMODE(path.lstat().st_mode)
    if permissions != 0o600:
        raise MigrationPreflightError(f"{label} must have owner-only permissions, found {permissions:o}")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _hit_failpoint(
    failpoint: MigrationFailpoint | None,
    stage: str,
    migration: SchemaMigration | None,
) -> None:
    if failpoint is not None:
        failpoint(stage, migration)


def _rollback_with_note(connection: sqlite3.Connection, exc: BaseException) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except BaseException as rollback_error:
        exc.add_note(
            f"SQLite schema migration rollback failed: {type(rollback_error).__name__}: {_safe_error(rollback_error)}"
        )


def _safe_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    return message[:500] or type(exc).__name__


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "FINGERPRINT_ALGORITHM",
    "MAX_SCHEMA_VERSION",
    "MAX_SUPPORTED_SCHEMA_VERSION",
    "MIGRATION_HISTORY_TABLE",
    "FutureSchemaVersionError",
    "Migration",
    "MigrationConnection",
    "MigrationHistoryError",
    "MigrationLockTimeout",
    "MigrationPreflightError",
    "MigrationRegistry",
    "MigrationRegistryError",
    "MigrationResult",
    "SQLiteSchemaMigrator",
    "SchemaMigration",
    "SchemaMigrationError",
    "SchemaMigrationResult",
    "migrate_sqlite_schema",
    "migration_fingerprint",
    "sql_migration",
    "sqlite_schema_migration_lock",
]
