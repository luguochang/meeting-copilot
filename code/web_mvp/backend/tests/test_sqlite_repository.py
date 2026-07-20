from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import threading
import time

import pytest

import meeting_copilot_web_mvp.sqlite_repository as sqlite_repository
from meeting_copilot_web_mvp.application_schema import APPLICATION_SCHEMA_VERSION
from meeting_copilot_web_mvp.sqlite_repository import (
    SqliteAsrLiveSessionRepository,
    SqlitePersistenceCoordinator,
    SqliteSessionRepository,
    SqliteSettingsUsageRepository,
    migrate_json_to_sqlite,
)


def test_sqlite_repositories_share_one_database_file_under_data_dir(tmp_path):
    session_repo = SqliteSessionRepository(tmp_path)
    live_repo = SqliteAsrLiveSessionRepository(tmp_path)

    session_repo.create(
        session_id="meeting_sqlite",
        transcript_report={"segments": []},
        analysis={"suggestion_cards": []},
        state_events=[],
    )
    live_repo.create({"session_id": "live_sqlite", "events": []})

    db_path = tmp_path / "meeting_copilot.db"
    assert db_path.is_file()
    assert not db_path.is_dir()
    assert not (db_path / "meeting_copilot.db").exists()
    assert SqliteSessionRepository(tmp_path).exists("meeting_sqlite")
    assert SqliteAsrLiveSessionRepository(tmp_path).get("live_sqlite")["session_id"] == "live_sqlite"


def test_json_migration_is_idempotent_and_uses_target_database_file(tmp_path):
    live_dir = tmp_path / "live_asr_sessions"
    live_dir.mkdir()
    (live_dir / "legacy_live.json").write_text(
        json.dumps({"session_id": "legacy_live", "events": []}),
        encoding="utf-8",
    )
    db_path = tmp_path / "meeting_copilot.db"

    first_count = migrate_json_to_sqlite(tmp_path, db_path)
    second_count = migrate_json_to_sqlite(tmp_path, db_path)

    assert first_count == 1
    assert second_count == 1
    assert db_path.is_file()
    assert [record["session_id"] for record in SqliteAsrLiveSessionRepository(tmp_path).list()] == ["legacy_live"]


def test_json_migration_hydrates_legacy_live_timestamps_for_history(tmp_path):
    live_dir = tmp_path / "live_asr_sessions"
    live_dir.mkdir()
    legacy_path = live_dir / "legacy_timestamped.json"
    legacy_path.write_text(
        json.dumps({"session_id": "legacy_timestamped", "events": []}),
        encoding="utf-8",
    )
    os.utime(legacy_path, (1_700_000_000, 1_700_000_000))

    assert migrate_json_to_sqlite(tmp_path, tmp_path / "meeting_copilot.db") == 1

    record = SqliteAsrLiveSessionRepository(tmp_path).get("legacy_timestamped")
    expected_ms = 1_700_000_000_000
    assert record["created_at_epoch_ms"] == expected_ms
    assert record["last_activity_at_epoch_ms"] == expected_ms


def test_json_migration_never_overwrites_newer_sqlite_record_on_later_startup(tmp_path):
    live_dir = tmp_path / "live_asr_sessions"
    live_dir.mkdir()
    legacy_path = live_dir / "legacy_live.json"
    legacy_path.write_text(
        json.dumps(
            {
                "session_id": "legacy_live",
                "events": [],
                "source": "stale_json",
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "meeting_copilot.db"
    migrate_json_to_sqlite(tmp_path, db_path)
    repository = SqliteAsrLiveSessionRepository(tmp_path)
    repository.update(
        "legacy_live",
        lambda record: {**record, "source": "newer_sqlite"},
    )

    migrate_json_to_sqlite(tmp_path, db_path)

    assert SqliteAsrLiveSessionRepository(tmp_path).get("legacy_live")["source"] == "newer_sqlite"


@pytest.mark.parametrize(
    ("legacy_directory", "table_name", "delete_method"),
    [
        ("live_asr_sessions", "asr_live_sessions", "delete_live_session"),
        ("sessions", "sessions", "delete_session_bundle"),
    ],
)
def test_deleted_legacy_json_session_is_not_restored_by_later_migration(
    tmp_path,
    legacy_directory,
    table_name,
    delete_method,
):
    records_dir = tmp_path / legacy_directory
    records_dir.mkdir()
    session_id = f"deleted_legacy_{table_name}"
    legacy_path = records_dir / "original_location.json"
    legacy_path.write_text(
        json.dumps({"session_id": session_id, "events": []}),
        encoding="utf-8",
    )
    db_path = tmp_path / "meeting_copilot.db"

    assert migrate_json_to_sqlite(tmp_path, db_path) == 1
    coordinator = SqlitePersistenceCoordinator(tmp_path)
    getattr(coordinator, delete_method)(session_id)
    legacy_path.rename(records_dir / "moved_legacy_record.json")

    assert migrate_json_to_sqlite(tmp_path, db_path) == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
        tombstone = connection.execute(
            "SELECT session_id, deleted_at_ms FROM deleted_sessions "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        assert tombstone is not None
        assert tombstone[0] == session_id
        assert isinstance(tombstone[1], int)


def test_tombstoned_live_json_skips_malformed_non_identifier_metadata(tmp_path):
    live_dir = tmp_path / "live_asr_sessions"
    live_dir.mkdir()
    session_id = "deleted_live_with_malformed_metadata"
    legacy_path = live_dir / "legacy_live.json"
    legacy_path.write_text(
        json.dumps({"session_id": session_id, "events": []}),
        encoding="utf-8",
    )
    db_path = tmp_path / "meeting_copilot.db"

    assert migrate_json_to_sqlite(tmp_path, db_path) == 1
    SqlitePersistenceCoordinator(tmp_path).delete_live_session(session_id)
    legacy_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "minutes": "malformed_minutes_shape",
                "audio": "malformed_audio_shape",
            }
        ),
        encoding="utf-8",
    )

    assert migrate_json_to_sqlite(tmp_path, db_path) == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0


def test_non_tombstoned_live_json_with_malformed_metadata_fails_closed(tmp_path):
    live_dir = tmp_path / "live_asr_sessions"
    live_dir.mkdir()
    session_id = "live_with_malformed_metadata"
    (live_dir / "legacy_live.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "minutes": "malformed_minutes_shape",
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "meeting_copilot.db"

    with pytest.raises(AttributeError, match="has no attribute 'get'"):
        migrate_json_to_sqlite(tmp_path, db_path)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0


def test_delete_bundle_rolls_back_tombstone_with_session_rows(tmp_path):
    session_id = "delete_tombstone_atomic_rollback"
    SqliteSessionRepository(tmp_path).create(
        session_id=session_id,
        transcript_report={"segments": []},
        analysis={"suggestion_cards": []},
        state_events=[],
    )
    SqliteAsrLiveSessionRepository(tmp_path).create(
        {"session_id": session_id, "events": []}
    )
    db_path = tmp_path / "meeting_copilot.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_live_delete_for_tombstone_test "
            "BEFORE DELETE ON asr_live_sessions "
            "WHEN OLD.session_id = 'delete_tombstone_atomic_rollback' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic live delete failure'); END"
        )

    with pytest.raises(sqlite3.DatabaseError, match="synthetic live delete failure"):
        SqlitePersistenceCoordinator(tmp_path).delete_session_bundle(session_id)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM deleted_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0


def test_schema_version_zero_database_upgrades_to_minimal_tombstone_schema(tmp_path):
    db_path = tmp_path / "meeting_copilot.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO sessions (session_id, record_json) VALUES (?, ?)",
            ("existing_version_zero", '{"session_id":"existing_version_zero"}'),
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0

    assert migrate_json_to_sqlite(tmp_path, db_path) == 0

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == APPLICATION_SCHEMA_VERSION
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = 'existing_version_zero'"
        ).fetchone()[0] == 1
        columns = connection.execute(
            "PRAGMA table_info(deleted_sessions)"
        ).fetchall()
    assert [column[1] for column in columns] == ["session_id", "deleted_at_ms"]


def test_unknown_higher_schema_version_is_rejected(tmp_path):
    db_path = tmp_path / "meeting_copilot.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(f"PRAGMA user_version = {APPLICATION_SCHEMA_VERSION + 1}")

    with pytest.raises(RuntimeError, match=f"unsupported future SQLite schema version {APPLICATION_SCHEMA_VERSION + 1}"):
        migrate_json_to_sqlite(tmp_path, db_path)


def test_json_migration_rolls_back_all_rows_when_later_json_is_malformed(tmp_path):
    live_dir = tmp_path / "live_asr_sessions"
    sessions_dir = tmp_path / "sessions"
    live_dir.mkdir()
    sessions_dir.mkdir()
    (live_dir / "valid_live.json").write_text(
        json.dumps({"session_id": "valid_live", "events": []}),
        encoding="utf-8",
    )
    (sessions_dir / "malformed_session.json").write_text(
        '{"session_id": "malformed_session",',
        encoding="utf-8",
    )
    db_path = tmp_path / "meeting_copilot.db"

    with pytest.raises(ValueError):
        migrate_json_to_sqlite(tmp_path, db_path)

    with sqlite3.connect(db_path) as connection:
        live_count = connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions"
        ).fetchone()[0]
        session_count = connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0]
    assert live_count == 0
    assert session_count == 0


def test_json_migration_error_names_malformed_legacy_file_without_record_content(tmp_path):
    live_dir = tmp_path / "live_asr_sessions"
    sessions_dir = tmp_path / "sessions"
    live_dir.mkdir()
    sessions_dir.mkdir()
    (live_dir / "valid_before_failure.json").write_text(
        json.dumps({"session_id": "valid_before_failure", "events": []}),
        encoding="utf-8",
    )
    sensitive_marker = "DO_NOT_LEAK_RECORD_CONTENT_7842"
    malformed_path = sessions_dir / "offending_legacy.json"
    malformed_path.write_text(
        '{"session_id":"offending_legacy","private_text":"'
        + sensitive_marker,
        encoding="utf-8",
    )
    db_path = tmp_path / "meeting_copilot.db"

    with pytest.raises(ValueError) as captured:
        migrate_json_to_sqlite(tmp_path, db_path)

    message = str(captured.value)
    assert "sessions/offending_legacy.json" in message
    assert "invalid JSON" in message
    assert sensitive_marker not in message
    assert str(tmp_path) not in message
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 0


def test_asr_live_cross_instance_read_modify_write_updates_do_not_lose_changes(tmp_path):
    session_id = "concurrent_live_updates"
    seed_repository = SqliteAsrLiveSessionRepository(tmp_path)
    seed_repository.create(
        {"session_id": session_id, "events": [], "update_markers": []}
    )
    worker_count = 8
    repositories = [
        SqliteAsrLiveSessionRepository(tmp_path) for _ in range(worker_count)
    ]
    start = threading.Barrier(worker_count)

    def append_marker(worker_index):
        start.wait()

        def mutate(record):
            markers = list(record.get("update_markers") or [])
            time.sleep(0.02)
            record["update_markers"] = [*markers, worker_index]
            return record

        repositories[worker_index].update(session_id, mutate)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(append_marker, range(worker_count)))

    assert sorted(seed_repository.get(session_id)["update_markers"]) == list(
        range(worker_count)
    )


def test_session_cross_instance_card_status_updates_do_not_lose_changes(tmp_path):
    session_id = "concurrent_card_updates"
    seed_repository = SqliteSessionRepository(tmp_path)
    seed_repository.create(
        session_id=session_id,
        transcript_report={"segments": [], "evidence_spans": []},
        analysis={"suggestion_cards": []},
        state_events=[],
    )
    repositories = [SqliteSessionRepository(tmp_path) for _ in range(2)]
    start = threading.Barrier(2)

    def controlled_snapshot(record):
        if not record.card_statuses:
            time.sleep(0.05)
        return {
            "suggestion_cards": [
                {
                    "id": card_id,
                    "status": record.card_statuses.get(card_id, "new"),
                    "show_or_silence_decision": "show",
                }
                for card_id in ("card_a", "card_b")
            ]
        }

    for repository in repositories:
        repository._snapshot_from_record = controlled_snapshot

    def update_card(worker_index):
        start.wait()
        repositories[worker_index].set_card_status(
            session_id,
            f"card_{'a' if worker_index == 0 else 'b'}",
            "kept",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(update_card, range(2)))

    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        stored = json.loads(
            connection.execute(
                "SELECT record_json FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
    assert stored["card_statuses"] == {"card_a": "kept", "card_b": "kept"}


def test_write_transaction_rolls_back_and_recovers_when_commit_fails_once(tmp_path):
    repository = SqliteAsrLiveSessionRepository(tmp_path)
    session_id = "commit_failure_recovery"
    repository.create({"session_id": session_id, "events": [], "revision": 0})
    delegate = repository._conn

    class CommitFailingConnection:
        def __init__(self):
            self.fail_commit = True

        @property
        def in_transaction(self):
            return delegate.in_transaction

        def execute(self, statement, parameters=()):
            if statement == "COMMIT" and self.fail_commit:
                self.fail_commit = False
                raise sqlite3.OperationalError("synthetic commit failure")
            return delegate.execute(statement, parameters)

        def close(self):
            delegate.close()

    repository._conn = CommitFailingConnection()

    with pytest.raises(sqlite3.OperationalError, match="synthetic commit failure"):
        repository.update(
            session_id,
            lambda record: {**record, "revision": 1},
        )

    assert repository._conn.in_transaction is False
    assert repository.get(session_id)["revision"] == 0
    repository.update(
        session_id,
        lambda record: {**record, "revision": 2},
    )
    assert repository.get(session_id)["revision"] == 2


@pytest.mark.parametrize(
    "legacy_directory",
    ["live_asr_sessions", "sessions"],
)
def test_json_migration_rejects_unsafe_session_id_with_safe_filename(
    tmp_path,
    legacy_directory,
):
    records_dir = tmp_path / legacy_directory
    records_dir.mkdir()
    record = {"session_id": "../DO_NOT_LEAK_UNSAFE_ID"}
    if legacy_directory == "live_asr_sessions":
        record["events"] = []
    legacy_path = records_dir / "unsafe_record.json"
    legacy_path.write_text(json.dumps(record), encoding="utf-8")
    db_path = tmp_path / "meeting_copilot.db"

    with pytest.raises(ValueError) as captured:
        migrate_json_to_sqlite(tmp_path, db_path)

    message = str(captured.value)
    assert f"{legacy_directory}/unsafe_record.json" in message
    assert "invalid session_id" in message
    assert "DO_NOT_LEAK_UNSAFE_ID" not in message
    table = "asr_live_sessions" if legacy_directory == "live_asr_sessions" else "sessions"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_json_migration_reads_and_validates_files_before_begin_immediate(
    monkeypatch,
    tmp_path,
):
    live_dir = tmp_path / "live_asr_sessions"
    live_dir.mkdir()
    (live_dir / "pre_read.json").write_text(
        json.dumps({"session_id": "pre_read", "events": []}),
        encoding="utf-8",
    )
    db_path = tmp_path / "meeting_copilot.db"
    original_reader = sqlite_repository._read_legacy_json
    observed_without_write_lock = False

    def observe_write_lock(path, data_dir):
        nonlocal observed_without_write_lock
        with sqlite3.connect(db_path, isolation_level=None, timeout=0.0) as probe:
            probe.execute("BEGIN IMMEDIATE")
            probe.execute("ROLLBACK")
        observed_without_write_lock = True
        return original_reader(path, data_dir)

    monkeypatch.setattr(sqlite_repository, "_read_legacy_json", observe_write_lock)

    migrate_json_to_sqlite(tmp_path, db_path)

    assert observed_without_write_lock is True


def test_all_sqlite_repositories_close_idempotently_and_release_database_file(tmp_path):
    repositories = [
        SqliteAsrLiveSessionRepository(tmp_path),
        SqliteSessionRepository(tmp_path),
        SqliteSettingsUsageRepository(tmp_path, {}),
    ]

    for repository in repositories:
        repository.close()
        repository.close()

    assert all(repository.closed for repository in repositories)
    db_path = tmp_path / "meeting_copilot.db"
    moved_path = tmp_path / "meeting_copilot.closed.db"
    db_path.replace(moved_path)
    moved_path.unlink()
    assert not moved_path.exists()
