from __future__ import annotations

import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from meeting_copilot_web_mvp.sqlite_schema import (
    CURRENT_SCHEMA_VERSION,
    MAX_SUPPORTED_SCHEMA_VERSION,
    MIGRATION_HISTORY_TABLE,
    FutureSchemaVersionError,
    MigrationHistoryError,
    MigrationRegistryError,
    SchemaMigration,
    migrate_sqlite_schema,
    sql_migration,
    sqlite_schema_migration_lock,
)


def _create_database(path: Path, *, version: int = 0) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_items (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_items (value) VALUES ('preserved')")
        connection.execute(f"PRAGMA user_version = {version}")


def _schema_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _test_migrations() -> tuple[SchemaMigration, ...]:
    return (
        sql_migration(
            1,
            "create_notes",
            (
                "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)",
                "INSERT INTO notes (body) VALUES ('v1')",
            ),
        ),
        sql_migration(
            2,
            "add_note_status",
            (
                "ALTER TABLE notes ADD COLUMN status TEXT NOT NULL DEFAULT 'open'",
                "CREATE INDEX idx_notes_status ON notes(status)",
            ),
        ),
    )


def test_schema_version_constants_expose_the_supported_production_envelope() -> None:
    assert CURRENT_SCHEMA_VERSION == 1
    assert MAX_SUPPORTED_SCHEMA_VERSION == CURRENT_SCHEMA_VERSION


def test_future_schema_fails_closed_before_database_or_backup_writes(tmp_path: Path) -> None:
    database_path = tmp_path / "future.sqlite3"
    future_version = MAX_SUPPORTED_SCHEMA_VERSION + 1
    _create_database(database_path, version=future_version)
    bytes_before = database_path.read_bytes()
    backup_dir = tmp_path / "backups"
    lock_path = tmp_path / "migration.lock"

    with pytest.raises(FutureSchemaVersionError, match=str(future_version)):
        migrate_sqlite_schema(
            database_path,
            migrations=(),
            backup_dir=backup_dir,
            lock_path=lock_path,
        )

    assert database_path.read_bytes() == bytes_before
    assert not backup_dir.exists()
    assert not lock_path.exists()
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?",
                (MIGRATION_HISTORY_TABLE,),
            ).fetchone()
            is None
        )


def test_incomplete_registry_fails_before_lock_or_backup_side_effects(tmp_path: Path) -> None:
    database_path = tmp_path / "meeting.sqlite3"
    backup_dir = tmp_path / "backups"
    lock_path = tmp_path / "migration.lock"
    _create_database(database_path)
    bytes_before = database_path.read_bytes()

    with pytest.raises(MigrationRegistryError, match="missing sequential version"):
        migrate_sqlite_schema(
            database_path,
            migrations=(_test_migrations()[1],),
            current_version=2,
            max_supported_version=2,
            backup_dir=backup_dir,
            lock_path=lock_path,
        )

    assert database_path.read_bytes() == bytes_before
    assert not backup_dir.exists()
    assert not lock_path.exists()


def test_sequential_migrations_create_verified_backup_history_and_are_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting.sqlite3"
    backup_dir = tmp_path / "backups"
    _create_database(database_path)
    migrations = _test_migrations()

    result = migrate_sqlite_schema(
        database_path,
        migrations=migrations,
        current_version=2,
        max_supported_version=2,
        backup_dir=backup_dir,
    )

    assert result.source_version == 0
    assert result.final_version == 2
    assert result.applied_versions == (1, 2)
    assert result.backup_path is not None
    backup_path = result.backup_path
    backup_stat = backup_path.lstat()
    assert stat.S_ISREG(backup_stat.st_mode)
    assert stat.S_IMODE(backup_stat.st_mode) == 0o600
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert int(backup.execute("PRAGMA user_version").fetchone()[0]) == 0
        assert backup.execute("SELECT value FROM legacy_items").fetchone() == ("preserved",)
        assert backup.execute("SELECT 1 FROM sqlite_master WHERE name = 'notes'").fetchone() is None

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 2
        assert connection.execute("SELECT body, status FROM notes").fetchall() == [("v1", "open")]
        history = connection.execute(
            f"SELECT version, name, fingerprint FROM {MIGRATION_HISTORY_TABLE} ORDER BY version"
        ).fetchall()
    assert history == [(migration.version, migration.name, migration.fingerprint) for migration in migrations]

    second = migrate_sqlite_schema(
        database_path,
        migrations=migrations,
        current_version=2,
        max_supported_version=2,
        backup_dir=backup_dir,
    )

    assert second.source_version == 2
    assert second.final_version == 2
    assert second.applied_versions == ()
    assert second.backup_path is None
    assert list(backup_dir.glob("*.sqlite3")) == [backup_path]


def test_history_fingerprint_drift_fails_before_creating_another_backup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting.sqlite3"
    backup_dir = tmp_path / "backups"
    _create_database(database_path)
    migrations = _test_migrations()
    migrate_sqlite_schema(
        database_path,
        migrations=migrations,
        current_version=2,
        max_supported_version=2,
        backup_dir=backup_dir,
    )
    backups_before = set(backup_dir.iterdir())
    changed_v1 = sql_migration(
        1,
        "create_notes",
        ("CREATE TABLE notes (id INTEGER PRIMARY KEY, changed TEXT)",),
    )

    with pytest.raises(MigrationHistoryError, match="fingerprint"):
        migrate_sqlite_schema(
            database_path,
            migrations=(changed_v1, migrations[1]),
            current_version=2,
            max_supported_version=2,
            backup_dir=backup_dir,
        )

    assert set(backup_dir.iterdir()) == backups_before
    assert _schema_version(database_path) == 2


def test_failpoint_rolls_back_all_steps_and_keeps_complete_backup(tmp_path: Path) -> None:
    database_path = tmp_path / "meeting.sqlite3"
    backup_dir = tmp_path / "backups"
    _create_database(database_path)

    class InjectedFailure(RuntimeError):
        pass

    def fail_after_first_step(stage: str, migration: SchemaMigration | None) -> None:
        if stage == "after_migration" and migration is not None and migration.version == 1:
            raise InjectedFailure("stop after v1")

    with pytest.raises(InjectedFailure, match="stop after v1"):
        migrate_sqlite_schema(
            database_path,
            migrations=_test_migrations(),
            current_version=2,
            max_supported_version=2,
            backup_dir=backup_dir,
            failpoint=fail_after_first_step,
        )

    assert _schema_version(database_path) == 0
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("SELECT value FROM legacy_items").fetchall() == [("preserved",)]
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name IN ('notes', ?) LIMIT 1",
                (MIGRATION_HISTORY_TABLE,),
            ).fetchone()
            is None
        )

    backups = list(backup_dir.glob("*.sqlite3"))
    assert len(backups) == 1
    backup_stat = backups[0].lstat()
    assert stat.S_ISREG(backup_stat.st_mode)
    assert stat.S_IMODE(backup_stat.st_mode) == 0o600
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert int(backup.execute("PRAGMA user_version").fetchone()[0]) == 0
        assert backup.execute("SELECT value FROM legacy_items").fetchall() == [("preserved",)]


def test_public_migration_lock_is_reentrant_and_owner_only(tmp_path: Path) -> None:
    database_path = tmp_path / "meeting.sqlite3"
    lock_path = tmp_path / "explicit.schema.lock"

    with sqlite_schema_migration_lock(
        database_path,
        lock_path=lock_path,
        timeout_seconds=1.0,
    ) as acquired_path:
        assert acquired_path == lock_path
        with sqlite_schema_migration_lock(
            database_path,
            lock_path=lock_path,
            timeout_seconds=1.0,
        ):
            lock_stat = lock_path.lstat()
            assert stat.S_ISREG(lock_stat.st_mode)
            assert stat.S_IMODE(lock_stat.st_mode) == 0o600
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from meeting_copilot_web_mvp.sqlite_schema import "
                    "MigrationLockTimeout, sqlite_schema_migration_lock; "
                    "from pathlib import Path; "
                    "database = Path(" + repr(str(database_path)) + "); "
                    "lock = Path(" + repr(str(lock_path)) + "); "
                    "\ntry:\n"
                    "    with sqlite_schema_migration_lock(database, lock_path=lock, "
                    "timeout_seconds=0.05):\n"
                    "        raise SystemExit(2)\n"
                    "except MigrationLockTimeout:\n"
                    "    raise SystemExit(0)\n"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert child.returncode == 0, child.stderr
