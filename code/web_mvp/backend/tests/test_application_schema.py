from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
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


def test_memory_jobs_migration_preserves_v21_manual_memory_and_exclusion(tmp_path: Path) -> None:
    from meeting_copilot_web_mvp.sqlite_schema import migrate_sqlite_schema
    database = tmp_path / "memory-v21.db"
    migrate_sqlite_schema(database, application_schema.APPLICATION_SCHEMA_MIGRATIONS[:21], current_version=21, max_supported_version=21)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO user_memories(id,scope,scope_id,kind,memory_key,value,status,origin,fingerprint,created_at_ms,updated_at_ms) "
                           "VALUES('manual','global','','preference','response_length','brief','active','user','original-hash',1,1)")
        connection.execute("INSERT INTO memory_exclusions VALUES('deleted-original-hash',1)")
        fingerprints = connection.execute("SELECT version,fingerprint FROM meeting_copilot_schema_migrations ORDER BY version").fetchall()
    result = migrate_sqlite_schema(database, application_schema.APPLICATION_SCHEMA_MIGRATIONS[:22], current_version=22, max_supported_version=22)
    assert result.applied_versions == (22,)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value,status,revision,reconciliation_job_id FROM user_memories WHERE id='manual'").fetchone() == ("brief", "active", 1, None)
        assert connection.execute("SELECT fingerprint FROM memory_exclusions").fetchone()[0] == "deleted-original-hash"
        assert connection.execute("SELECT version,fingerprint FROM meeting_copilot_schema_migrations WHERE version<=21 ORDER BY version").fetchall() == fingerprints
        assert connection.execute("SELECT COUNT(*) FROM memory_reconcile_jobs").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


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
    assert result.applied_versions == tuple(range(1, APPLICATION_SCHEMA_VERSION + 1))
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
            "ask_threads",
            "ask_messages",
            "meeting_notes",
            "meeting_note_evidence",
            "intelligence_input_coverage",
            "agent_work_items",
            "agent_work_item_evidence",
    }.issubset(_objects(database_path, "table"))
    assert {
        "idx_llm_usage_timestamp",
        "idx_llm_usage_session_timestamp",
        "idx_meeting_events_unpublished",
        "idx_transcript_segments_meeting_order",
        "idx_recording_import_jobs_claim",
        "idx_recording_import_jobs_expired_lease",
        "idx_deletion_jobs_idempotency",
            "idx_intelligence_input_coverage_status",
            "idx_agent_work_items_state",
            "idx_agent_work_items_decision",
            "idx_agent_work_item_evidence_meeting",
    }.issubset(_objects(database_path, "index"))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert {"request_context_json"}.issubset(
            {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        )
        assert {"context_json", "input_context_json"}.issubset(
            {row[1] for row in connection.execute("PRAGMA table_info(agent_work_items)")}
        )
        assert {"set_id", "active_version_id", "enabled"}.issubset(
            {row[1] for row in connection.execute("PRAGMA table_info(meeting_attachments)")}
        )
        assert {
            "material_sets", "meeting_attachments", "meeting_attachment_versions",
            "meeting_attachment_spans", "meeting_attachment_jobs", "meeting_attachment_search",
        }.issubset({row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )})
        migration_history = connection.execute(
            "SELECT version, name FROM meeting_copilot_schema_migrations ORDER BY version"
        ).fetchall()
    assert migration_history == [
        (1, "create_legacy_repository_schema"),
        (2, "create_v2_application_schema"),
        (3, "add_native_pcm_source_ranges"),
            (4, "add_meeting_ask_ai_workspace"),
            (5, "add_meeting_notes"),
            (6, "version_semantic_reading_blocks"),
            (7, "add_realtime_intelligence_timing_chain"),
            (8, "add_realtime_provider_circuit_state"),
        (9, "add_intelligence_trigger_contract"),
            (10, "add_intelligence_input_coverage"),
            (11, "add_durable_pi_work_items"),
            (12, "add_realtime_answer_copilot_fields"),
        (13, "bind_answer_context_evidence_versions"),
        (14, "add_local_project_knowledge_library"),
        (15, "add_durable_knowledge_jobs"),
        (16, "add_confirmed_project_outcomes"),
        (17, "bind_live_document_evidence"),
        (18, "allow_user_entities_before_audio"),
        (19, "add_controlled_user_memory"),
        (20, "link_versioned_notes_and_human_finals"),
        (21, "add_explicit_project_reconciliation_jobs"),
        (22, "add_source_bound_memory_reconciliation"),
        (23, "add_explicit_project_preparation"),
        (24, "add_explicit_historical_transcript_recovery"),
        (25, "add_realtime_participation_context"),
        (26, "add_meeting_material_sets"),
    ]


def test_pre_audio_entity_migration_preserves_existing_facts_and_rejects_negative_sequence(tmp_path: Path) -> None:
    from meeting_copilot_web_mvp.sqlite_schema import migrate_sqlite_schema

    database = tmp_path / "before-audio.db"
    migrate_sqlite_schema(database, application_schema.APPLICATION_SCHEMA_MIGRATIONS[:17], current_version=17, max_supported_version=17)
    original = ("meeting", "fact", "decision_candidate", "confirmed", "原有决定", 0.91,
                '{"segment_id":"segment"}', '["segment"]', "张先生", "下周", "措施", 1234, 4, 3, 8)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO meeting_entities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", original)
    result = bootstrap_application_schema(database)
    assert result.backup_path is not None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM meeting_entities WHERE entity_id='fact'").fetchone() == original
        connection.execute("INSERT INTO meeting_entities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("meeting", "before", "decision_candidate", "candidate", "资料事实", 1,
                            "{}", "[]", None, None, None, 2345, 1, 0, 0))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE meeting_entities SET first_seen_seq=-1 WHERE entity_id='before'")
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    with sqlite3.connect(result.backup_path) as backup:
        assert backup.execute("SELECT * FROM meeting_entities WHERE entity_id='fact'").fetchone() == original


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
    assert result.applied_versions == tuple(range(2, APPLICATION_SCHEMA_VERSION + 1))
    assert result.backup_path is not None
    backup_stat = result.backup_path.stat()
    assert stat.S_ISREG(backup_stat.st_mode)
    if os.name != "nt":
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
        assert {"request_context_json"}.issubset(
            {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        )
        assert {"context_json", "input_context_json"}.issubset(
            {row[1] for row in connection.execute("PRAGMA table_info(agent_work_items)")}
        )
        assert {"set_id", "active_version_id", "enabled"}.issubset(
            {row[1] for row in connection.execute("PRAGMA table_info(meeting_attachments)")}
        )
        assert {
            "material_sets", "meeting_attachments", "meeting_attachment_versions",
            "meeting_attachment_spans", "meeting_attachment_jobs", "meeting_attachment_search",
        }.issubset({row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )})
        assert connection.execute(
            "SELECT version, name FROM meeting_copilot_schema_migrations ORDER BY version"
        ).fetchall() == [
            (2, "create_v2_application_schema"),
                (3, "add_native_pcm_source_ranges"),
                    (4, "add_meeting_ask_ai_workspace"),
                    (5, "add_meeting_notes"),
                    (6, "version_semantic_reading_blocks"),
                    (7, "add_realtime_intelligence_timing_chain"),
                    (8, "add_realtime_provider_circuit_state"),
                    (9, "add_intelligence_trigger_contract"),
                            (10, "add_intelligence_input_coverage"),
                            (11, "add_durable_pi_work_items"),
                            (12, "add_realtime_answer_copilot_fields"),
            (13, "bind_answer_context_evidence_versions"),
            (14, "add_local_project_knowledge_library"),
            (15, "add_durable_knowledge_jobs"),
            (16, "add_confirmed_project_outcomes"),
            (17, "bind_live_document_evidence"),
            (18, "allow_user_entities_before_audio"),
            (19, "add_controlled_user_memory"),
            (20, "link_versioned_notes_and_human_finals"),
            (21, "add_explicit_project_reconciliation_jobs"),
            (22, "add_source_bound_memory_reconciliation"),
            (23, "add_explicit_project_preparation"),
            (24, "add_explicit_historical_transcript_recovery"),
            (25, "add_realtime_participation_context"),
            (26, "add_meeting_material_sets"),
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
    assert result.applied_versions == tuple(range(1, APPLICATION_SCHEMA_VERSION + 1))
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
        "applied_versions": list(range(2, APPLICATION_SCHEMA_VERSION + 1)),
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


def test_prerelease_probe_reopens_after_transient_windows_post_crash_io_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    bootstrap_application_schema(database_path)
    real_connect = sqlite3.connect
    connect_count = 0

    class FailFirstUserVersionRead:
        def __init__(self, connection: sqlite3.Connection, *, fail: bool) -> None:
            self._connection = connection
            self._fail = fail

        def execute(self, statement: str, *args):
            if self._fail and statement == "PRAGMA user_version":
                self._fail = False
                raise sqlite3.OperationalError("disk I/O error")
            return self._connection.execute(statement, *args)

        def close(self) -> None:
            self._connection.close()

    def flaky_connect(*args, **kwargs):
        nonlocal connect_count
        connect_count += 1
        return FailFirstUserVersionRead(real_connect(*args, **kwargs), fail=connect_count == 1)

    monkeypatch.setattr(application_schema.sqlite3, "connect", flaky_connect)

    application_schema._promote_known_prerelease_v2_fingerprint(
        database_path,
        lock_path=None,
        timeout_seconds=1.0,
    )

    assert connect_count == 2


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

    assert results.count((APPLICATION_SCHEMA_VERSION, tuple(range(1, APPLICATION_SCHEMA_VERSION + 1)))) == 1
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
    assert result.applied_versions == tuple(range(3, APPLICATION_SCHEMA_VERSION + 1))
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


def test_v7_migration_preserves_existing_jobs_and_leaves_new_timing_nullable(
    tmp_path: Path,
) -> None:
    """Upgrade a real v6 database without fabricating timing provenance."""

    database_path = tmp_path / "v6-with-job.db"
    application_schema.migrate_sqlite_schema(
        database_path,
        application_schema.APPLICATION_SCHEMA_MIGRATIONS[:6],
        current_version=6,
        max_supported_version=6,
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO meetings ("
            "id, state, title, started_at_ms, latest_seq, revision, created_at_ms, updated_at_ms"
            ") VALUES (?, 'live', ?, ?, ?, ?, ?, ?)",
            ("legacy-timing-meeting", "Keep this meeting", 100, 1, 1, 100, 100),
        )
        connection.execute(
            "INSERT INTO transcript_segments ("
            "meeting_id, segment_id, final_id, transcript_seq, text, normalized_text, "
            "evidence_hash, created_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-timing-meeting",
                "legacy-timing-segment",
                "legacy-timing-final",
                1,
                "旧版本已持久化的文字",
                "旧版本已持久化的文字",
                "legacy-evidence-hash",
                100,
                100,
            ),
        )
        connection.execute(
            "INSERT INTO jobs ("
            "id, meeting_id, kind, status, priority, input_transcript_seq, input_version, "
            "evidence_segment_id, evidence_hash, idempotency_key, attempts, max_attempts, "
            "next_attempt_at_ms, created_at_ms, updated_at_ms"
            ") VALUES (?, ?, 'intelligence', 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-timing-job",
                "legacy-timing-meeting",
                90,
                1,
                1,
                "legacy-timing-segment",
                "legacy-evidence-hash",
                "legacy-timing-idempotency",
                0,
                3,
                500,
                100,
                100,
            ),
        )
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }.isdisjoint(
            {
                "final_committed_at_ms",
                "job_started_at_ms",
                "decision_completed_at_ms",
                "projected_at_ms",
            }
        )

    result = bootstrap_application_schema(database_path)

    assert result.source_version == 6
    assert result.final_version == APPLICATION_SCHEMA_VERSION
    assert result.applied_versions == tuple(range(7, APPLICATION_SCHEMA_VERSION + 1))
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        assert {
            "final_committed_at_ms",
            "job_started_at_ms",
            "decision_completed_at_ms",
            "projected_at_ms",
        } <= columns
        assert connection.execute(
            "SELECT meeting_id, kind, status, priority, input_transcript_seq, input_version, "
            "evidence_segment_id, evidence_hash, idempotency_key, attempts, max_attempts, "
            "next_attempt_at_ms, created_at_ms, updated_at_ms, final_committed_at_ms, "
            "job_started_at_ms, decision_completed_at_ms, projected_at_ms "
            "FROM jobs WHERE id = ?",
            ("legacy-timing-job",),
        ).fetchone() == (
            "legacy-timing-meeting",
            "intelligence",
            "pending",
            90,
            1,
            1,
            "legacy-timing-segment",
            "legacy-evidence-hash",
            "legacy-timing-idempotency",
            0,
            3,
            500,
            100,
            100,
            None,
            None,
            None,
            None,
        )
