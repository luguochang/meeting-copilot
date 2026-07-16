from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unicodedata

import pytest

from meeting_copilot_web_mvp.v2_migration import (
    MIGRATION_NAME,
    MigrationExecutionError,
    MigrationPreflightError,
    V1ToV2ShadowMigrator,
)
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def _create_legacy_database(
    database_path: Path,
    records: dict[str, str | dict[str, object]],
) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE asr_live_sessions ("
            "session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)"
        )
        for session_id, record in records.items():
            payload = record if isinstance(record, str) else json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
            )
            connection.execute(
                "INSERT INTO asr_live_sessions (session_id, record_json) VALUES (?, ?)",
                (session_id, payload),
            )


def _record(session_id: str, *events: dict[str, object]) -> dict[str, object]:
    return {
        "session_id": session_id,
        "created_at_epoch_ms": 1_000,
        "last_activity_at_epoch_ms": 5_000,
        "events": list(events),
    }


def _final(
    segment_id: str,
    text: str,
    *,
    at_ms: int,
    start_ms: int = 0,
    end_ms: int = 0,
) -> dict[str, object]:
    return {
        "id": f"transcript_final:{segment_id}",
        "event_type": "transcript_final",
        "at_ms": at_ms,
        "payload": {
            "segment_id": segment_id,
            "text": text,
            "normalized_text": text,
            "start_ms": start_ms,
            "end_ms": end_ms,
        },
    }


def _revision(segment_id: str, text: str, *, at_ms: int) -> dict[str, object]:
    return {
        "id": f"transcript_revision:{segment_id}",
        "event_type": "transcript_revision",
        "at_ms": at_ms,
        "payload": {
            "segment_id": f"revision-{segment_id}",
            "supersedes_segment_id": segment_id,
            "corrected_text": text,
            "start_ms": 0,
            "end_ms": at_ms,
        },
    }


def _normalized_text_hash(*texts: str) -> str:
    digest = hashlib.sha256()
    for text in texts:
        normalized = " ".join(unicodedata.normalize("NFC", text).split())
        encoded = normalized.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _table_names(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }


def test_v0_missing_database_and_database_without_legacy_table_are_safe_noops(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing.db"

    missing = V1ToV2ShadowMigrator(missing_path).run()

    assert missing["status"] == "no_source_database"
    assert missing["backup_path"] is None
    assert not missing_path.exists()

    database_path = tmp_path / "unrelated.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE retained (value TEXT NOT NULL)")
        connection.execute("INSERT INTO retained VALUES ('preserved')")

    no_table = V1ToV2ShadowMigrator(database_path).run()

    assert no_table["status"] == "no_source_table"
    assert Path(no_table["backup_path"]).is_file()
    assert "retained" in _table_names(database_path)
    assert "meetings" not in _table_names(database_path)
    assert "v2_migration_markers" not in _table_names(database_path)


def test_normal_migration_uses_pre_v2_backup_and_reconciles_canonical_finals(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    source_record = _record(
        "meeting-1",
        _final("seg-1", "接口　先灰度  5%。", at_ms=2_000, end_ms=2_000),
        _revision("seg-1", "接口先灰度 5%。", at_ms=2_500),
        _final(
            "seg-2",
            "谁负责回滚？",
            at_ms=4_000,
            start_ms=2_000,
            end_ms=4_000,
        ),
    )
    _create_legacy_database(database_path, {"meeting-1": source_record})
    with sqlite3.connect(database_path) as connection:
        source_payload = connection.execute(
            "SELECT record_json FROM asr_live_sessions WHERE session_id = 'meeting-1'"
        ).fetchone()[0]

    report = V1ToV2ShadowMigrator(database_path).run()

    assert report["status"] == "completed"
    assert report["summary"] == {
        "source_meeting_count": 1,
        "valid_meeting_count": 1,
        "invalid_meeting_count": 0,
        "source_final_count": 2,
        "created_final_count": 2,
        "idempotent_final_count": 0,
        "missing_final_count": 0,
        "conflict_count": 0,
    }
    meeting = report["meetings"][0]
    assert meeting["status"] == "matched"
    assert meeting["source_final_count"] == 2
    assert meeting["matched_v2_final_count"] == 2
    assert meeting["v2_total_final_count"] == 2
    assert meeting["missing_final_ids"] == []
    assert meeting["conflicts"] == []
    expected_hash = _normalized_text_hash("接口先灰度 5%。", "谁负责回滚？")
    assert meeting["source_normalized_text_hash"] == expected_hash
    assert meeting["v2_normalized_text_hash"] == expected_hash

    backup_path = Path(report["backup_path"])
    assert backup_path.is_file()
    backup_tables = _table_names(backup_path)
    assert "asr_live_sessions" in backup_tables
    assert "meetings" not in backup_tables
    assert "v2_migration_markers" not in backup_tables

    persistence = V2Persistence(database_path)
    try:
        snapshot = persistence.get_snapshot("meeting-1")
        assert [segment["normalized_text"] for segment in snapshot["segments"]] == [
            "接口先灰度 5%。",
            "谁负责回滚？",
        ]
        assert persistence.list_jobs(meeting_id="meeting-1") == []
        assert snapshot["suggestions"] == []
    finally:
        persistence.close()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT record_json FROM asr_live_sessions WHERE session_id = 'meeting-1'"
        ).fetchone()[0] == source_payload
        marker = connection.execute(
            "SELECT source_checksum, status, backup_path, attempts "
            "FROM v2_migration_markers WHERE migration_name = ?",
            (MIGRATION_NAME,),
        ).fetchone()
    assert marker == (
        report["source_checksum"],
        "completed",
        report["backup_path"],
        1,
    )


def test_repeated_migration_is_idempotent_and_reuses_source_marker(tmp_path: Path) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    _create_legacy_database(
        database_path,
        {"meeting-1": _record("meeting-1", _final("seg-1", "确认发布窗口。", at_ms=1_000))},
    )

    first = V1ToV2ShadowMigrator(database_path).run()
    second = V1ToV2ShadowMigrator(database_path).run()

    assert first["source_checksum"] == second["source_checksum"]
    assert first["backup_path"] == second["backup_path"]
    assert second["status"] == "completed"
    assert second["summary"]["created_final_count"] == 0
    assert second["summary"]["idempotent_final_count"] == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert connection.execute(
            "SELECT attempts FROM v2_migration_markers WHERE migration_name = ?",
            (MIGRATION_NAME,),
        ).fetchone()[0] == 2


def test_interrupted_migration_keeps_legacy_data_and_resumes_idempotently(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    source_record = _record(
        "meeting-1",
        _final("seg-1", "第一段。", at_ms=1_000),
        _final("seg-2", "第二段。", at_ms=2_000),
    )
    _create_legacy_database(database_path, {"meeting-1": source_record})

    class InterruptingPersistence:
        def __init__(self, path: Path) -> None:
            self._inner = V2Persistence(path)
            self._calls = 0

        def commit_final_and_enqueue(self, **kwargs):
            self._calls += 1
            if self._calls == 2:
                raise RuntimeError("synthetic interruption")
            return self._inner.commit_final_and_enqueue(**kwargs)

        def close(self) -> None:
            self._inner.close()

    with pytest.raises(MigrationExecutionError, match="synthetic interruption") as raised:
        V1ToV2ShadowMigrator(
            database_path,
            persistence_factory=InterruptingPersistence,
        ).run()

    assert raised.value.report["status"] == "failed"
    interrupted = raised.value.report["meetings"][0]
    assert interrupted["status"] == "interrupted"
    assert interrupted["matched_v2_final_count"] == 1
    assert len(interrupted["missing_final_ids"]) == 1
    assert interrupted["conflicts"] == []
    with sqlite3.connect(database_path) as connection:
        assert json.loads(
            connection.execute(
                "SELECT record_json FROM asr_live_sessions WHERE session_id = 'meeting-1'"
            ).fetchone()[0]
        ) == source_record
        assert connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM v2_migration_markers WHERE migration_name = ?",
            (MIGRATION_NAME,),
        ).fetchone()[0] == "failed"

    resumed = V1ToV2ShadowMigrator(database_path).run()

    assert resumed["status"] == "completed"
    assert resumed["summary"]["created_final_count"] == 1
    assert resumed["summary"]["idempotent_final_count"] == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_changed_legacy_content_is_reported_as_conflict_not_a_new_final(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    _create_legacy_database(
        database_path,
        {"meeting-1": _record("meeting-1", _final("seg-1", "原始文字。", at_ms=1_000))},
    )
    V1ToV2ShadowMigrator(database_path).run()

    changed_record = _record(
        "meeting-1",
        _final("seg-1", "后来被改过的文字。", at_ms=1_000),
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE asr_live_sessions SET record_json = ? WHERE session_id = 'meeting-1'",
            (json.dumps(changed_record, ensure_ascii=False, sort_keys=True),),
        )

    report = V1ToV2ShadowMigrator(database_path).run()

    assert report["status"] == "completed_with_issues"
    meeting = report["meetings"][0]
    assert meeting["status"] == "conflict"
    assert meeting["missing_final_ids"] == []
    assert len(meeting["conflicts"]) == 1
    assert meeting["conflicts"][0]["kind"] == "content_conflict"
    assert meeting["source_normalized_text_hash"] != meeting["v2_normalized_text_hash"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 1
        assert connection.execute(
            "SELECT normalized_text FROM transcript_segments"
        ).fetchone()[0] == "原始文字。"


def test_corrupt_json_is_isolated_and_hash_reconciliation_detects_v2_drift(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    _create_legacy_database(
        database_path,
        {
            "broken-meeting": "{not-json",
            "meeting-1": _record(
                "meeting-1",
                _final("seg-1", "数据库迁移谁负责？", at_ms=1_000),
            ),
        },
    )

    first = V1ToV2ShadowMigrator(database_path).run()

    assert first["status"] == "completed_with_issues"
    meetings = {meeting["meeting_id"]: meeting for meeting in first["meetings"]}
    assert meetings["broken-meeting"]["status"] == "invalid_source"
    assert meetings["broken-meeting"]["error_class"] == "invalid_json"
    assert meetings["meeting-1"]["status"] == "matched"
    assert (
        meetings["meeting-1"]["source_normalized_text_hash"]
        == meetings["meeting-1"]["v2_normalized_text_hash"]
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE transcript_segments SET normalized_text = '被篡改的 V2 文字' "
            "WHERE meeting_id = 'meeting-1'"
        )

    second = V1ToV2ShadowMigrator(database_path).run()
    meetings = {meeting["meeting_id"]: meeting for meeting in second["meetings"]}

    assert second["status"] == "completed_with_issues"
    assert meetings["meeting-1"]["status"] == "conflict"
    assert (
        meetings["meeting-1"]["source_normalized_text_hash"]
        != meetings["meeting-1"]["v2_normalized_text_hash"]
    )
    assert any(
        conflict["kind"] == "content_conflict"
        for conflict in meetings["meeting-1"]["conflicts"]
    )


def test_preflight_disk_and_integrity_failures_do_not_modify_source(tmp_path: Path) -> None:
    database_path = tmp_path / "meeting_copilot.db"
    _create_legacy_database(
        database_path,
        {"meeting-1": _record("meeting-1", _final("seg-1", "正文。", at_ms=1_000))},
    )
    before = database_path.read_bytes()

    with pytest.raises(MigrationPreflightError, match="free disk space"):
        V1ToV2ShadowMigrator(
            database_path,
            disk_usage_fn=lambda _path: SimpleNamespace(free=0),
            reserve_bytes=0,
        ).run()

    assert database_path.read_bytes() == before
    assert "meetings" not in _table_names(database_path)

    corrupt_path = tmp_path / "corrupt.db"
    corrupt_path.write_bytes(b"not a sqlite database")
    corrupt_before = corrupt_path.read_bytes()

    with pytest.raises(MigrationPreflightError, match="integrity"):
        V1ToV2ShadowMigrator(corrupt_path).run()

    assert corrupt_path.read_bytes() == corrupt_before
