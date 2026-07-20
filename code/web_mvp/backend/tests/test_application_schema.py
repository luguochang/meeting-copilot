from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import stat
from pathlib import Path

import pytest

import meeting_copilot_web_mvp.application_schema as application_schema
from meeting_copilot_web_mvp.application_schema import (
    APPLICATION_SCHEMA_VERSION,
    bootstrap_application_schema,
)


def _version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _objects(path: Path, object_type: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
                (object_type,),
            )
        }


def test_fresh_database_bootstraps_legacy_and_v2_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "meeting_copilot.db"

    result = bootstrap_application_schema(database_path)

    assert result.source_version == 0
    assert result.final_version == APPLICATION_SCHEMA_VERSION
    assert result.applied_versions == (1, 2, 3)
    assert result.backup_path is None
    assert _version(database_path) == APPLICATION_SCHEMA_VERSION
    assert {
        "asr_live_sessions",
        "sessions",
        "pending_audio_cleanup",
        "deleted_sessions",
        "app_settings",
        "llm_usage_ledger",
        "meetings",
        "meeting_events",
        "transcript_segments",
        "semantic_paragraphs",
        "jobs",
        "suggestions",
        "minutes",
        "review_documents",
        "recording_sessions",
        "deletion_jobs",
        "data_governance_settings",
    }.issubset(_objects(database_path, "table"))
    assert {
        "idx_llm_usage_timestamp",
        "idx_llm_usage_session_timestamp",
        "idx_meeting_events_unpublished",
        "idx_transcript_segments_meeting_order",
        "idx_recording_import_jobs_claim",
        "idx_recording_import_jobs_expired_lease",
        "idx_deletion_jobs_idempotency",
    }.issubset(_objects(database_path, "index"))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        migration_history = connection.execute(
            "SELECT version, name FROM meeting_copilot_schema_migrations ORDER BY version"
        ).fetchall()
    assert migration_history == [
        (1, "create_legacy_repository_schema"),
        (2, "create_v2_application_schema"),
        (3, "add_native_pcm_source_ranges"),
    ]


def test_existing_legacy_v1_database_is_backed_up_and_upgraded_without_data_loss(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO sessions (session_id, record_json) VALUES (?, ?)",
            ("preserve-me", '{"session_id":"preserve-me"}'),
        )
        connection.execute("PRAGMA user_version = 1")

    result = bootstrap_application_schema(database_path)

    assert result.source_version == 1
    assert result.final_version == APPLICATION_SCHEMA_VERSION
    assert result.applied_versions == (2, 3)
    assert result.backup_path is not None
    backup_stat = result.backup_path.stat()
    assert stat.S_ISREG(backup_stat.st_mode)
    assert stat.S_IMODE(backup_stat.st_mode) == 0o600
    with sqlite3.connect(result.backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("SELECT record_json FROM sessions WHERE session_id = 'preserve-me'").fetchone() == (
            '{"session_id":"preserve-me"}',
        )
        assert backup.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == APPLICATION_SCHEMA_VERSION
        assert connection.execute("SELECT record_json FROM sessions WHERE session_id = 'preserve-me'").fetchone() == (
            '{"session_id":"preserve-me"}',
        )
        assert connection.execute("SELECT 1 FROM meeting_events").fetchone() is None
        assert connection.execute(
            "SELECT version, name FROM meeting_copilot_schema_migrations ORDER BY version"
        ).fetchall() == [
            (2, "create_v2_application_schema"),
            (3, "add_native_pcm_source_ranges"),
        ]


def test_failed_v2_migration_rolls_back_v1_database_and_retains_verified_backup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
        connection.execute("INSERT INTO sessions VALUES ('rollback-me', '{}')")
        connection.execute("PRAGMA user_version = 1")

    class InjectedFailure(RuntimeError):
        pass

    def fail_after_v2(stage: str, migration: object | None) -> None:
        if stage == "after_migration" and getattr(migration, "version", None) == 2:
            raise InjectedFailure("stop application schema migration")

    with pytest.raises(InjectedFailure, match="stop application schema migration") as captured:
        bootstrap_application_schema(database_path, failpoint=fail_after_v2)

    assert _version(database_path) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT record_json FROM sessions WHERE session_id = 'rollback-me'").fetchone() == (
            "{}",
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meetings'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meeting_copilot_schema_migrations'"
        ).fetchone() is None
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    notes = "\n".join(getattr(captured.value, "__notes__", ()))
    assert "complete pre-migration backup:" in notes
    backup_path = Path(notes.rsplit("complete pre-migration backup: ", 1)[1])
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_old_v2_partial_schema_at_version_zero_is_completed_and_preserved(tmp_path: Path) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE meetings ("
            "id TEXT PRIMARY KEY, state TEXT NOT NULL, title TEXT, started_at_ms INTEGER, "
            "ended_at_ms INTEGER, latest_seq INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1, "
            "created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO meetings (id, state, title, created_at_ms, updated_at_ms) "
            "VALUES ('old-v2', 'ended', 'Keep title', 1, 1)"
        )
        connection.execute(
            "CREATE TABLE meeting_entities ("
            "meeting_id TEXT NOT NULL, entity_id TEXT NOT NULL, "
            "kind TEXT NOT NULL CHECK (kind IN ('current_topic', 'open_question')), "
            "status TEXT NOT NULL, text TEXT NOT NULL, confidence REAL, "
            "evidence_json TEXT, evidence_segment_ids_json TEXT NOT NULL, owner TEXT, "
            "updated_at_ms INTEGER, PRIMARY KEY (meeting_id, entity_id))"
        )
        connection.execute(
            "INSERT INTO meeting_entities VALUES ("
            "'old-v2', 'entity-1', 'current_topic', 'open', 'topic', 0.75, "
            "'{\"source\":\"legacy-partial-v2\"}', '[]', 'Chase', 1)"
        )

    result = bootstrap_application_schema(database_path)

    assert result.source_version == 0
    assert result.applied_versions == (1, 2, 3)
    with sqlite3.connect(database_path) as connection:
        meeting_columns = {row[1] for row in connection.execute("PRAGMA table_info(meetings)").fetchall()}
        entity_columns = {row[1] for row in connection.execute("PRAGMA table_info(meeting_entities)").fetchall()}
        assert "title_source" in meeting_columns
        assert {"confidence", "evidence_json", "owner", "deadline", "mitigation"}.issubset(entity_columns)
        assert connection.execute("SELECT title FROM meetings WHERE id = 'old-v2'").fetchone() == ("Keep title",)
        assert connection.execute("SELECT text FROM meeting_entities WHERE entity_id = 'entity-1'").fetchone() == (
            "topic",
        )
        assert connection.execute(
            "SELECT confidence, evidence_json, owner FROM meeting_entities WHERE entity_id = 'entity-1'"
        ).fetchone() == (0.75, '{"source":"legacy-partial-v2"}', "Chase")


def test_v2_migration_fingerprint_covers_every_reviewed_declaration_section() -> None:
    material = application_schema._V2_FINGERPRINT_MATERIAL
    expected = application_schema._fingerprint_v2_material(material)

    assert application_schema.APPLICATION_SCHEMA_MIGRATIONS[1].fingerprint == expected
    assert set(material) == {
        "additive_columns",
        "backfill_statements",
        "base_schema_statements",
        "identity",
        "index_statements",
        "meeting_entities_rebuild",
        "meeting_title_backfill",
        "seed_statements",
    }

    mutations = {
        "identity": lambda value: value.__setitem__("identity", "changed-identity"),
        "base_schema_statements": lambda value: value["base_schema_statements"].append(
            "CREATE TABLE fingerprint_probe (id INTEGER PRIMARY KEY)"
        ),
        "additive_columns": lambda value: value["additive_columns"][0][1].append(
            ["fingerprint_probe", "TEXT"]
        ),
        "meeting_entities_rebuild": lambda value: value["meeting_entities_rebuild"][
            "required_kinds"
        ].append("fingerprint_probe"),
        "meeting_title_backfill": lambda value: value["meeting_title_backfill"].__setitem__(
            "format", "%Y fingerprint probe"
        ),
        "backfill_statements": lambda value: value["backfill_statements"].append(
            "UPDATE fingerprint_probe SET id = id"
        ),
        "index_statements": lambda value: value["index_statements"].append(
            "CREATE INDEX fingerprint_probe_idx ON fingerprint_probe(id)"
        ),
        "seed_statements": lambda value: value["seed_statements"].append(
            ["INSERT INTO fingerprint_probe (id) VALUES (?)", [1]]
        ),
    }
    for section, mutate in mutations.items():
        changed = json.loads(json.dumps(material))
        mutate(changed)
        assert application_schema._fingerprint_v2_material(changed) != expected, section


def test_safe_schema_migration_report_excludes_database_and_backup_paths(tmp_path: Path) -> None:
    database_path = tmp_path / "private" / "meeting_copilot.db"
    database_path.parent.mkdir()
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
        connection.execute("PRAGMA user_version = 1")

    result = bootstrap_application_schema(database_path)
    report = application_schema.safe_schema_migration_report(result)
    serialized = json.dumps(report, sort_keys=True)

    assert report == {
        "schema_version": "application-schema-migration-report.v1",
        "status": "ready",
        "storage": "sqlite",
        "source_version": 1,
        "final_version": APPLICATION_SCHEMA_VERSION,
        "applied_versions": [2, 3],
        "migrated": True,
        "backup_created": True,
    }
    assert str(database_path) not in serialized
    assert str(result.backup_path) not in serialized
    assert "migration_backups" not in serialized


def test_known_prerelease_v2_fingerprint_is_promoted_but_unknown_history_fails_closed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    bootstrap_application_schema(database_path)
    current_fingerprint = application_schema.APPLICATION_SCHEMA_MIGRATIONS[1].fingerprint
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE meeting_copilot_schema_migrations SET fingerprint = ? WHERE version = 2",
            (application_schema._LEGACY_V2_INCOMPLETE_FINGERPRINT,),
        )

    result = bootstrap_application_schema(database_path)

    assert result.applied_versions == ()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT fingerprint FROM meeting_copilot_schema_migrations WHERE version = 2"
        ).fetchone() == (current_fingerprint,)
        connection.execute(
            "UPDATE meeting_copilot_schema_migrations SET fingerprint = ? WHERE version = 2",
            ("sha256:" + "0" * 64,),
        )

    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        bootstrap_application_schema(database_path)


@pytest.mark.parametrize(
    ("damage_sql", "damage_check_sql"),
    [
        (
            "ALTER TABLE suggestions DROP COLUMN feedback_at_ms",
            "SELECT 1 FROM pragma_table_info('suggestions') WHERE name = 'feedback_at_ms'",
        ),
        (
            "DROP INDEX idx_deletion_jobs_idempotency",
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_deletion_jobs_idempotency'",
        ),
    ],
    ids=("missing-additive-column", "missing-procedural-index"),
)
def test_prerelease_fingerprint_promotion_rejects_incomplete_v2_shape_without_repair(
    tmp_path: Path,
    damage_sql: str,
    damage_check_sql: str,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    bootstrap_application_schema(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(damage_sql)
        connection.execute(
            "UPDATE meeting_copilot_schema_migrations SET fingerprint = ? WHERE version = 2",
            (application_schema._LEGACY_V2_INCOMPLETE_FINGERPRINT,),
        )

    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        bootstrap_application_schema(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT fingerprint FROM meeting_copilot_schema_migrations WHERE version = 2"
        ).fetchone() == (application_schema._LEGACY_V2_INCOMPLETE_FINGERPRINT,)
        assert connection.execute(damage_check_sql).fetchone() is None


def test_concurrent_bootstrap_is_serialized_and_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "meeting_copilot.db"

    def bootstrap() -> tuple[int, tuple[int, ...]]:
        result = bootstrap_application_schema(database_path)
        return result.final_version, result.applied_versions

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: bootstrap(), range(6)))

    assert results.count((APPLICATION_SCHEMA_VERSION, (1, 2, 3))) == 1
    assert results.count((APPLICATION_SCHEMA_VERSION, ())) == 5
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_v3_migration_preserves_v2_audio_chunks_and_adds_nullable_source_ranges(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    application_schema.migrate_sqlite_schema(
        database_path,
        application_schema.APPLICATION_SCHEMA_MIGRATIONS[:2],
        current_version=2,
        max_supported_version=2,
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO audio_chunks ("
            "meeting_id, track, epoch, chunk_seq, relative_path, sha256, sample_rate_hz, "
            "sample_count, duration_ms, file_size_bytes, status, captured_at_ms, created_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-browser-audio",
                "microphone",
                0,
                0,
                "audio_assets/legacy/chunk-00000000.pcm",
                "a" * 64,
                16_000,
                16_000,
                1_000,
                64_000,
                "committed",
                1_000,
                1_000,
            ),
        )

    result = bootstrap_application_schema(database_path)

    assert result.source_version == 2
    assert result.applied_versions == (3,)
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(audio_chunks)")}
        assert {
            "source_sequence_start",
            "source_sequence_end",
            "source_timestamp_start_ms",
            "source_timestamp_end_ms",
        } <= columns
        assert connection.execute(
            "SELECT source_sequence_start, source_sequence_end, "
            "source_timestamp_start_ms, source_timestamp_end_ms "
            "FROM audio_chunks WHERE meeting_id = ?",
            ("legacy-browser-audio",),
        ).fetchone() == (None, None, None, None)
