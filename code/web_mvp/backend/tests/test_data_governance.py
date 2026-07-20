from __future__ import annotations

import sqlite3

import pytest

from meeting_copilot_web_mvp.data_governance import (
    DataGovernanceService,
    normalize_retention_policy,
)
from meeting_copilot_web_mvp.meeting_preparation import MeetingPreparationStore
from meeting_copilot_web_mvp.v2_persistence import (
    DEFAULT_RETENTION_POLICY,
    MIN_RETENTION_RUN_INTERVAL_MS,
    V2Persistence,
)


DAY_MS = 24 * 60 * 60 * 1_000


@pytest.fixture
def persistence(tmp_path):
    value = V2Persistence(tmp_path / "meeting-copilot.db")
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def governance(persistence, tmp_path):
    return DataGovernanceService(persistence=persistence, data_dir=tmp_path)


def _table_count(persistence: V2Persistence, table: str, meeting_id: str) -> int:
    identity_column = "id" if table == "meetings" else "meeting_id"
    with persistence._lock:
        return int(
            persistence._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {identity_column} = ?",
                (meeting_id,),
            ).fetchone()[0]
        )


def _event_types(persistence: V2Persistence, meeting_id: str) -> set[str]:
    return {event["type"] for event in persistence.list_events(meeting_id)}


def _seed_complete_meeting(
    persistence: V2Persistence,
    data_dir,
    *,
    meeting_id: str = "meeting-1",
    now_ms: int = 10_000,
) -> MeetingPreparationStore:
    persistence.create_meeting(
        meeting_id=meeting_id,
        title="数据治理测试会议",
        now_ms=now_ms,
    )
    committed = persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id=f"{meeting_id}-final-1",
        segment_id=f"{meeting_id}-segment-1",
        text="确认发布计划和负责人。",
        normalized_text="确认发布计划和负责人。",
        started_at_ms=now_ms,
        ended_at_ms=now_ms + 1_000,
        evidence_hash=f"{meeting_id}-hash-1",
        speaker_id="cluster-a",
        speaker_confidence=0.9,
        now_ms=now_ms + 1_000,
    )
    persistence.begin_recording(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=16_000,
        lease_owner="capture-owner",
        lease_ms=60_000,
        now_ms=now_ms + 2_000,
    )
    persistence.record_audio_chunk(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        chunk_seq=0,
        relative_path=f"audio_assets/{meeting_id}/chunks/chunk-00000000.pcm",
        sha256="a" * 64,
        sample_rate_hz=16_000,
        sample_count=16_000,
        duration_ms=1_000,
        file_size_bytes=32_000,
        lease_owner="capture-owner",
        lease_ms=60_000,
        now_ms=now_ms + 3_000,
    )
    persistence.create_import_job(
        meeting_id=meeting_id,
        source_relative_path=f"audio_assets/{meeting_id}/source.m4a",
        original_filename="source.m4a",
        file_size_bytes=4_096,
        now_ms=now_ms + 4_000,
    )

    jobs = persistence.list_jobs(meeting_id=meeting_id)
    suggestion_job = next(job for job in jobs if job["kind"] == "suggestion")
    with persistence._write_transaction():
        persistence._conn.execute(
            "INSERT INTO suggestions ("
            "suggestion_id, meeting_id, job_id, generation_id, evidence_segment_id, "
            "evidence_transcript_seq, evidence_hash, state_revision, status, draft_text, "
            "draft_seq, text, final_draft_seq, created_at_ms, updated_at_ms, committed_at_ms"
            ") VALUES (?, ?, ?, ?, ?, 1, ?, 1, 'committed', ?, 1, ?, 1, ?, ?, ?)",
            (
                f"{meeting_id}-suggestion-1",
                meeting_id,
                suggestion_job["id"],
                f"{meeting_id}-generation-1",
                committed["segment_id"],
                f"{meeting_id}-hash-1",
                "建议确认负责人。",
                "建议确认负责人。",
                now_ms + 5_000,
                now_ms + 5_000,
                now_ms + 5_000,
            ),
        )
        persistence._conn.execute(
            "INSERT INTO meeting_entities ("
            "meeting_id, entity_id, kind, status, text, confidence, evidence_json, "
            "evidence_segment_ids_json, updated_at_ms, version, first_seen_seq, last_updated_seq"
            ") VALUES (?, ?, 'decision_candidate', 'candidate', ?, 0.9, ?, ?, ?, 1, 1, 1) "
            "ON CONFLICT(meeting_id, entity_id) DO NOTHING",
            (
                meeting_id,
                f"{meeting_id}-decision-1",
                "采用灰度发布。",
                "{}",
                f'["{committed["segment_id"]}"]',
                now_ms + 5_000,
            ),
        )
        persistence._conn.execute(
            "INSERT INTO minutes (meeting_id, version, status, markdown, created_at_ms, updated_at_ms) "
            "VALUES (?, 1, 'ready', ?, ?, ?)",
            (meeting_id, "# 会议纪要", now_ms + 5_000, now_ms + 5_000),
        )
        persistence._conn.execute(
            "INSERT INTO approach_artifacts ("
            "meeting_id, cards_json, degraded, created_at_ms, updated_at_ms"
            ") VALUES (?, '[]', 0, ?, ?)",
            (meeting_id, now_ms + 5_000, now_ms + 5_000),
        )
        persistence._conn.execute(
            "INSERT INTO search_documents (meeting_id, transcript_text, transcript_hash, updated_at_ms) "
            "VALUES (?, ?, ?, ?)",
            (meeting_id, "确认发布计划和负责人。", "b" * 64, now_ms + 5_000),
        )
        document_id = f"{meeting_id}-minutes-document"
        persistence._conn.execute(
            "INSERT INTO review_documents ("
            "document_id, meeting_id, document_kind, source_transcript_revision, revision, "
            "ai_version, user_version, ai_content_json, user_content_json, user_modified, "
            "dirty_state, created_at_ms, updated_at_ms"
            ") VALUES (?, ?, 'minutes', 1, 1, 1, 0, '{}', NULL, 0, 'saved', ?, ?)",
            (document_id, meeting_id, now_ms + 5_000, now_ms + 5_000),
        )
        persistence._conn.execute(
            "INSERT INTO review_document_revisions ("
            "document_id, revision, version_kind, version, author, "
            "source_transcript_revision, content_json, created_at_ms"
            ") VALUES (?, 1, 'ai_generated', 1, 'test', 1, '{}', ?)",
            (document_id, now_ms + 5_000),
        )
        persistence._append_event_locked(
            meeting_id=meeting_id,
            event_type="meeting.minutes.ready",
            aggregate_type="minutes",
            aggregate_id=meeting_id,
            occurred_at_ms=now_ms + 5_000,
            idempotency_key="test.minutes.ready",
            payload={"status": "ready"},
        )

    session_dir = data_dir / "audio_assets" / meeting_id
    (session_dir / "chunks").mkdir(parents=True)
    (session_dir / "audio.wav").write_bytes(b"recording")
    (session_dir / "chunks" / "chunk-00000000.pcm").write_bytes(b"chunk")
    import_dir = data_dir / "imports" / meeting_id
    import_dir.mkdir(parents=True)
    (import_dir / "source.m4a").write_bytes(b"source")
    preparation_store = MeetingPreparationStore(data_dir / "meeting_preparation")
    preparation_store.save(
        meeting_id,
        hotwords=["P99"],
        notice_acknowledged=True,
        updated_at_ms=now_ms + 5_000,
    )
    persistence.end_meeting(meeting_id=meeting_id, now_ms=now_ms + 6_000)
    return preparation_store


def test_recording_deletion_removes_only_recording_assets_and_metadata(
    persistence,
    governance,
    tmp_path,
):
    _seed_complete_meeting(persistence, tmp_path)
    job = governance.request_deletion(
        meeting_id="meeting-1",
        deletion_scope="recording",
        now_ms=20_000,
        idempotency_key="request-recording-1",
    )

    completed = governance.execute_deletion_job(job_id=job["id"], now_ms=21_000)
    repeated = governance.execute_deletion_job(job_id=job["id"], now_ms=22_000)

    assert completed == repeated
    assert completed["status"] == "completed"
    assert completed["attempts"] == 1
    assert _table_count(persistence, "audio_chunks", "meeting-1") == 0
    assert _table_count(persistence, "recording_sessions", "meeting-1") == 0
    assert _table_count(persistence, "recording_import_jobs", "meeting-1") == 0
    assert _table_count(persistence, "transcript_segments", "meeting-1") == 1
    assert _table_count(persistence, "meeting_speakers", "meeting-1") == 1
    assert _table_count(persistence, "suggestions", "meeting-1") == 1
    assert _table_count(persistence, "minutes", "meeting-1") == 1
    assert not (tmp_path / "audio_assets" / "meeting-1").exists()
    assert not (tmp_path / "imports" / "meeting-1").exists()
    assert not (tmp_path / "meeting_preparation" / "meeting-1.json").exists()
    assert not any(value.startswith("recording.") for value in _event_types(persistence, "meeting-1"))
    assert "transcript.segment.finalized" in _event_types(persistence, "meeting-1")


def test_derived_deletion_preserves_recording_and_canonical_transcript(
    persistence,
    governance,
    tmp_path,
):
    _seed_complete_meeting(persistence, tmp_path)
    job = governance.request_deletion(
        meeting_id="meeting-1",
        deletion_scope="derived",
        now_ms=20_000,
        idempotency_key="request-derived-1",
    )

    governance.execute_deletion_job(job_id=job["id"], now_ms=21_000)

    assert _table_count(persistence, "transcript_segments", "meeting-1") == 1
    assert _table_count(persistence, "meeting_speakers", "meeting-1") == 1
    assert _table_count(persistence, "semantic_paragraphs", "meeting-1") == 1
    assert _table_count(persistence, "audio_chunks", "meeting-1") == 1
    assert _table_count(persistence, "recording_sessions", "meeting-1") == 1
    for table in (
        "suggestions",
        "jobs",
        "meeting_entities",
        "minutes",
        "approach_artifacts",
        "search_documents",
        "review_documents",
    ):
        assert _table_count(persistence, table, "meeting-1") == 0
    assert (tmp_path / "audio_assets" / "meeting-1" / "audio.wav").exists()
    assert (tmp_path / "imports" / "meeting-1" / "source.m4a").exists()
    assert (tmp_path / "meeting_preparation" / "meeting-1.json").exists()
    assert "meeting.minutes.ready" not in _event_types(persistence, "meeting-1")
    assert "transcript.segment.finalized" in _event_types(persistence, "meeting-1")
    assert any(value.startswith("recording.") for value in _event_types(persistence, "meeting-1"))


def test_transcript_deletion_cascades_all_text_dependent_derivatives_only(
    persistence,
    governance,
    tmp_path,
):
    _seed_complete_meeting(persistence, tmp_path)
    job = governance.request_deletion(
        meeting_id="meeting-1",
        deletion_scope="transcript",
        now_ms=20_000,
        idempotency_key="request-transcript-1",
    )

    governance.execute_deletion_job(job_id=job["id"], now_ms=21_000)

    for table in (
        "transcript_segments",
        "meeting_speakers",
        "asr_checkpoints",
        "semantic_paragraphs",
        "suggestions",
        "jobs",
        "meeting_entities",
        "minutes",
        "approach_artifacts",
        "search_documents",
        "review_documents",
    ):
        assert _table_count(persistence, table, "meeting-1") == 0
    assert _table_count(persistence, "audio_chunks", "meeting-1") == 1
    assert _table_count(persistence, "recording_sessions", "meeting-1") == 1
    assert (tmp_path / "audio_assets" / "meeting-1" / "audio.wav").exists()
    assert (tmp_path / "imports" / "meeting-1" / "source.m4a").exists()
    assert (tmp_path / "meeting_preparation" / "meeting-1.json").exists()
    assert not any(value.startswith("transcript.") for value in _event_types(persistence, "meeting-1"))
    assert any(value.startswith("recording.") for value in _event_types(persistence, "meeting-1"))


def test_all_deletion_preserves_explicit_audit_but_removes_every_meeting_fact(
    persistence,
    governance,
    tmp_path,
):
    _seed_complete_meeting(persistence, tmp_path)
    job = governance.request_deletion(
        meeting_id="meeting-1",
        deletion_scope="all",
        now_ms=20_000,
        idempotency_key="request-all-1",
    )

    completed = governance.execute_deletion_job(job_id=job["id"], now_ms=21_000)

    assert completed["status"] == "completed"
    assert persistence.meeting_exists("meeting-1") is False
    assert _table_count(persistence, "meeting_speakers", "meeting-1") == 0
    assert persistence.is_meeting_tombstoned("meeting-1") is True
    assert persistence.list_events("meeting-1") == []
    assert not (tmp_path / "audio_assets" / "meeting-1").exists()
    assert not (tmp_path / "imports" / "meeting-1").exists()
    assert not (tmp_path / "meeting_preparation" / "meeting-1.json").exists()
    assert [
        event["event_type"]
        for event in persistence.list_data_governance_audit_events(meeting_id="meeting-1")
    ] == [
        "data_deletion.requested",
        "data_deletion.started",
        "data_deletion.completed",
    ]


def test_category_purge_and_completed_audit_are_one_sqlite_transaction(
    persistence,
    governance,
    tmp_path,
):
    _seed_complete_meeting(persistence, tmp_path)
    job = governance.request_deletion(
        meeting_id="meeting-1",
        deletion_scope="derived",
        now_ms=20_000,
        idempotency_key="request-atomic-1",
    )
    persistence.mark_deletion_running(job_id=job["id"], now_ms=20_500)
    with persistence._lock:
        persistence._conn.execute(
            "CREATE TRIGGER fail_derived_delete BEFORE DELETE ON meeting_entities "
            "BEGIN SELECT RAISE(ABORT, 'synthetic governance failure'); END"
        )

    with pytest.raises(sqlite3.DatabaseError, match="synthetic governance failure"):
        persistence.complete_deletion_and_purge(job_id=job["id"], now_ms=21_000)

    assert _table_count(persistence, "suggestions", "meeting-1") == 1
    assert _table_count(persistence, "minutes", "meeting-1") == 1
    assert _table_count(persistence, "meeting_entities", "meeting-1") >= 1
    assert persistence.get_deletion_job(job["id"])["status"] == "running"
    assert "data_deletion.completed" not in {
        event["event_type"]
        for event in persistence.list_data_governance_audit_events(meeting_id="meeting-1")
    }


@pytest.mark.parametrize(
    "managed_path",
    [
        "../audio_assets/meeting-1",
        "audio_assets/meeting-1/../other",
        "audio_assets/other-meeting",
        "meeting_preparation/other-meeting.json",
        "/tmp/meeting-1",
        "audio_assets\\meeting-1",
    ],
)
def test_deletion_job_rejects_paths_outside_owned_meeting_roots(
    persistence,
    managed_path,
):
    persistence.create_meeting(meeting_id="meeting-1", title=None, now_ms=1_000)

    with pytest.raises(ValueError):
        persistence.create_deletion_job(
            meeting_id="meeting-1",
            deletion_scope="recording",
            managed_paths=[managed_path],
            now_ms=2_000,
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (30, "30_days"),
        ("90", "90_days"),
        ("365_days", "365_days"),
        ("manual_only", "manual_only"),
        ("local_until_user_deletes", "local_until_user_deletes"),
    ],
)
def test_retention_policy_normalization(value, expected):
    assert normalize_retention_policy(value) == expected


def test_default_retention_never_silently_deletes_and_automatic_run_is_daily_gated(
    persistence,
    governance,
):
    now_ms = 400 * DAY_MS
    persistence.create_meeting(
        meeting_id="old-ended",
        title="旧会议",
        now_ms=now_ms - 40 * DAY_MS,
    )
    persistence.end_meeting(
        meeting_id="old-ended",
        now_ms=now_ms - 35 * DAY_MS,
    )
    persistence.create_meeting(
        meeting_id="recent-ended",
        title="近期会议",
        now_ms=now_ms - 10 * DAY_MS,
    )
    persistence.end_meeting(
        meeting_id="recent-ended",
        now_ms=now_ms - 5 * DAY_MS,
    )
    persistence.create_meeting(
        meeting_id="old-live",
        title="仍在进行",
        now_ms=now_ms - 60 * DAY_MS,
    )

    assert governance.get_settings()["retention_policy"] == DEFAULT_RETENTION_POLICY
    disabled = governance.run_retention_if_due(now_ms=now_ms)
    assert disabled == {
        "claimed": False,
        "reason": "automatic_retention_disabled",
        "retention_policy": DEFAULT_RETENTION_POLICY,
    }
    assert persistence.meeting_exists("old-ended") is True

    governance.set_retention_policy(30, now_ms=now_ms)
    completed = governance.run_retention_if_due(now_ms=now_ms + 1)
    too_soon = governance.run_retention_if_due(now_ms=now_ms + 2)

    assert completed["claimed"] is True
    assert completed["run"]["status"] == "completed"
    assert completed["run"]["candidate_count"] == 1
    assert persistence.meeting_exists("old-ended") is False
    assert persistence.meeting_exists("recent-ended") is True
    assert persistence.meeting_exists("old-live") is True
    assert too_soon["claimed"] is False
    assert too_soon["reason"] == "minimum_interval_not_elapsed"
    assert too_soon["next_run_at_ms"] == now_ms + 1 + MIN_RETENTION_RUN_INTERVAL_MS

    governance.set_retention_policy("manual_only", now_ms=now_ms + DAY_MS)
    manual = governance.run_retention_if_due(now_ms=now_ms + DAY_MS + 1)
    assert manual["claimed"] is False
    assert manual["reason"] == "automatic_retention_disabled"


def test_retention_run_retries_a_failed_tombstoned_job_on_the_next_real_interval(
    persistence,
    governance,
    tmp_path,
):
    now_ms = 500 * DAY_MS
    persistence.create_meeting(
        meeting_id="retention-retry",
        title="待自动清理",
        now_ms=now_ms - 50 * DAY_MS,
    )
    persistence.end_meeting(
        meeting_id="retention-retry",
        now_ms=now_ms - 40 * DAY_MS,
    )
    external_dir = tmp_path / "outside-audio"
    external_dir.mkdir()
    (external_dir / "must-remain.txt").write_text("outside", encoding="utf-8")
    audio_root = tmp_path / "audio_assets"
    audio_root.mkdir()
    (audio_root / "retention-retry").symlink_to(external_dir, target_is_directory=True)
    governance.set_retention_policy(30, now_ms=now_ms)

    failed = governance.run_retention_if_due(now_ms=now_ms + 1)

    assert failed["run"]["status"] == "failed"
    assert failed["run"]["error_count"] == 1
    failed_job = persistence.list_deletion_jobs(statuses=("failed",))[0]
    assert failed_job["requested_by"] == "retention"
    assert persistence.is_meeting_tombstoned("retention-retry") is True
    assert persistence.meeting_exists("retention-retry") is True
    (audio_root / "retention-retry").unlink()

    retried = governance.run_retention_if_due(
        now_ms=now_ms + 1 + MIN_RETENTION_RUN_INTERVAL_MS
    )

    assert retried["run"]["status"] == "completed"
    assert retried["run"]["candidate_count"] == 1
    assert retried["deletion_jobs"][0]["attempts"] == 2
    assert persistence.meeting_exists("retention-retry") is False
    assert (external_dir / "must-remain.txt").read_text(encoding="utf-8") == "outside"


def test_legacy_deletion_jobs_are_migrated_with_governance_defaults(tmp_path):
    database_path = tmp_path / "legacy-deletion.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE deletion_jobs ("
        "id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, "
        "status TEXT NOT NULL, paths_json TEXT NOT NULL, "
        "attempts INTEGER NOT NULL DEFAULT 0, error_class TEXT, "
        "created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, "
        "completed_at_ms INTEGER)"
    )
    connection.execute(
        "INSERT INTO deletion_jobs ("
        "id, meeting_id, status, paths_json, attempts, created_at_ms, updated_at_ms"
        ") VALUES ('legacy-job', 'legacy-meeting', 'completed', '[]', 1, 1000, 2000)"
    )
    connection.commit()
    connection.close()

    migrated = V2Persistence(database_path)
    try:
        job = migrated.get_deletion_job("legacy-job")
        assert job["deletion_scope"] == "all"
        assert job["requested_by"] == "user"
        assert job["retention_policy"] is None
        assert job["idempotency_key"] == "legacy:legacy-job"
        assert migrated.get_retention_policy() == DEFAULT_RETENTION_POLICY
    finally:
        migrated.close()
