from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def persistence(tmp_path):
    instance = V2Persistence(tmp_path / "meeting-copilot.db")
    yield instance
    instance.close()


def _create_import_job(
    persistence: V2Persistence,
    *,
    meeting_id: str = "import-meeting",
    now_ms: int = 1_000,
    max_attempts: int = 3,
):
    persistence.create_meeting(
        meeting_id=meeting_id,
        title="Imported meeting",
        now_ms=now_ms,
    )
    return persistence.create_import_job(
        meeting_id=meeting_id,
        source_relative_path=f"imports/{meeting_id}/source.m4a",
        original_filename="source.m4a",
        file_size_bytes=4_096,
        max_attempts=max_attempts,
        now_ms=now_ms,
    )


def test_create_get_list_and_projections_are_idempotent(persistence):
    created = _create_import_job(persistence)
    repeated = persistence.create_import_job(
        meeting_id="import-meeting",
        source_relative_path="imports/import-meeting/source.m4a",
        original_filename="source.m4a",
        file_size_bytes=4_096,
        max_attempts=3,
        now_ms=9_000,
    )

    assert repeated == created
    assert created == {
        "id": created["id"],
        "meeting_id": "import-meeting",
        "status": "pending",
        "stage": "reading",
        "progress": 0,
        "source_relative_path": "imports/import-meeting/source.m4a",
        "original_filename": "source.m4a",
        "file_size_bytes": 4_096,
        "attempts": 0,
        "max_attempts": 3,
        "next_attempt_at_ms": 1_000,
        "lease_owner": None,
        "lease_until_ms": None,
        "error_class": None,
        "error_message": None,
        "created_at_ms": 1_000,
        "updated_at_ms": 1_000,
        "completed_at_ms": None,
    }
    assert persistence.get_import_job(created["id"]) == created
    assert persistence.list_import_jobs(statuses=("pending",)) == [created]
    assert persistence.list_import_jobs(statuses=("running",)) == []
    assert persistence.get_snapshot("import-meeting")["import_job"] == created
    history = persistence.list_meetings()
    assert history[0]["import_job"] == created
    assert history[0]["id"] == "import-meeting"
    assert [item["id"] for item in persistence.list_meetings_page(status="processing")["meetings"]] == [
        "import-meeting"
    ]

    with pytest.raises(ValueError, match="conflicting fields"):
        persistence.create_import_job(
            meeting_id="import-meeting",
            source_relative_path="imports/import-meeting/different.m4a",
            original_filename="source.m4a",
            file_size_bytes=4_096,
            max_attempts=3,
            now_ms=10_000,
        )
    with pytest.raises(ValueError, match="unsupported import job statuses"):
        persistence.list_import_jobs(statuses=("unknown",))


def test_claim_stage_lease_fence_and_completion_are_durable(persistence):
    created = _create_import_job(persistence)
    claimed = persistence.claim_import_job(worker_id="worker-a", now_ms=1_100, lease_ms=100)

    assert claimed["id"] == created["id"]
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["lease_owner"] == "worker-a"
    assert claimed["lease_until_ms"] == 1_200
    assert persistence.claim_import_job(worker_id="worker-b", now_ms=1_101, lease_ms=100) is None
    assert (
        persistence.update_import_job_stage(
            job_id=created["id"],
            worker_id="worker-b",
            stage="transcribing",
            progress=40,
            now_ms=1_150,
        )
        is None
    )

    updated = persistence.update_import_job_stage(
        job_id=created["id"],
        worker_id="worker-a",
        stage="transcribing",
        progress=45,
        now_ms=1_150,
        lease_ms=200,
    )
    assert updated["stage"] == "transcribing"
    assert updated["progress"] == 45
    assert updated["lease_until_ms"] == 1_350
    with pytest.raises(ValueError, match="stage cannot move backwards"):
        persistence.update_import_job_stage(
            job_id=created["id"],
            worker_id="worker-a",
            stage="normalizing",
            progress=45,
            now_ms=1_151,
        )
    with pytest.raises(ValueError, match="progress cannot move backwards"):
        persistence.update_import_job_stage(
            job_id=created["id"],
            worker_id="worker-a",
            stage="correcting",
            progress=44,
            now_ms=1_151,
        )

    completed = persistence.complete_import_job(
        job_id=created["id"],
        worker_id="worker-a",
        now_ms=1_349,
    )
    assert completed["status"] == "succeeded"
    assert completed["stage"] == "completed"
    assert completed["progress"] == 100
    assert completed["lease_owner"] is None
    assert completed["completed_at_ms"] == 1_349
    assert (
        persistence.complete_import_job(
            job_id=created["id"],
            worker_id="worker-a",
            now_ms=9_000,
        )
        == completed
    )
    persistence.end_meeting(meeting_id="import-meeting", now_ms=9_001)
    assert [item["id"] for item in persistence.list_meetings_page(status="ready")["meetings"]] == ["import-meeting"]


def test_retryable_failure_respects_schedule_attempt_limit_and_idempotency(persistence):
    created = _create_import_job(persistence, max_attempts=2)
    persistence.claim_import_job(worker_id="worker-a", now_ms=1_100, lease_ms=500)
    retry_wait = persistence.fail_import_job(
        job_id=created["id"],
        worker_id="worker-a",
        error_class="decoder_timeout",
        error_message="decoder timed out",
        retryable=True,
        next_attempt_at_ms=2_000,
        now_ms=1_200,
    )

    assert retry_wait["status"] == "retry_wait"
    assert retry_wait["attempts"] == 1
    assert retry_wait["next_attempt_at_ms"] == 2_000
    assert retry_wait["completed_at_ms"] is None
    assert (
        persistence.fail_import_job(
            job_id=created["id"],
            worker_id="worker-a",
            error_class="decoder_timeout",
            error_message="decoder timed out",
            retryable=True,
            next_attempt_at_ms=2_000,
            now_ms=1_201,
        )
        == retry_wait
    )
    assert persistence.claim_import_job(worker_id="worker-b", now_ms=1_999, lease_ms=200) is None

    second = persistence.claim_import_job(worker_id="worker-b", now_ms=2_000, lease_ms=200)
    assert second["attempts"] == 2
    terminal = persistence.fail_import_job(
        job_id=created["id"],
        worker_id="worker-b",
        error_class="decoder_timeout",
        error_message="decoder timed out again",
        retryable=True,
        next_attempt_at_ms=3_000,
        now_ms=2_100,
    )
    assert terminal["status"] == "failed"
    assert terminal["attempts"] == 2
    assert terminal["completed_at_ms"] == 2_100
    assert persistence.claim_import_job(worker_id="worker-c", now_ms=3_000, lease_ms=200) is None
    assert [item["id"] for item in persistence.list_meetings_page(status="failed")["meetings"]] == ["import-meeting"]


def test_user_retry_resets_failed_import_without_changing_source_identity(persistence):
    created = _create_import_job(persistence)
    persistence.claim_import_job(worker_id="worker-a", now_ms=1_100, lease_ms=500)
    failed = persistence.fail_import_job(
        job_id=created["id"],
        worker_id="worker-a",
        error_class="file_asr_component_missing",
        error_message="component missing",
        now_ms=1_200,
    )

    retried = persistence.retry_import_job(job_id=created["id"], now_ms=2_000)

    assert failed["status"] == "failed"
    assert retried["status"] == "pending"
    assert retried["stage"] == "reading"
    assert retried["progress"] == 0
    assert retried["attempts"] == 0
    assert retried["source_relative_path"] == created["source_relative_path"]
    assert retried["original_filename"] == created["original_filename"]
    assert retried["file_size_bytes"] == created["file_size_bytes"]
    with pytest.raises(ValueError, match="only failed"):
        persistence.retry_import_job(job_id=created["id"], now_ms=2_001)


def test_expired_leases_recover_once_and_exhaust_attempts(persistence):
    created = _create_import_job(persistence, max_attempts=2)
    persistence.claim_import_job(worker_id="worker-a", now_ms=1_000, lease_ms=100)
    persistence.update_import_job_stage(
        job_id=created["id"],
        worker_id="worker-a",
        stage="transcribing",
        progress=30,
        now_ms=1_050,
    )

    assert persistence.recover_interrupted_import_jobs(now_ms=1_099) == 0
    assert (
        persistence.fail_import_job(
            job_id=created["id"],
            worker_id="worker-a",
            error_class="late_failure",
            now_ms=1_100,
        )
        is None
    )
    assert persistence.recover_interrupted_import_jobs(now_ms=1_100) == 1
    assert persistence.recover_interrupted_import_jobs(now_ms=1_100) == 0
    recovered = persistence.get_import_job(created["id"])
    assert recovered["status"] == "retry_wait"
    assert recovered["stage"] == "transcribing"
    assert recovered["progress"] == 30
    assert recovered["error_class"] == "lease_expired"
    assert recovered["lease_owner"] is None

    second = persistence.claim_import_job(worker_id="worker-b", now_ms=1_100, lease_ms=100)
    assert second["attempts"] == 2
    assert persistence.recover_interrupted_import_jobs(now_ms=1_200) == 1
    exhausted = persistence.get_import_job(created["id"])
    assert exhausted["status"] == "failed"
    assert exhausted["completed_at_ms"] == 1_200
    assert exhausted["attempts"] == 2


def test_concurrent_connections_claim_an_import_job_once(tmp_path):
    database_path = tmp_path / "concurrent.db"
    first = V2Persistence(database_path)
    second = V2Persistence(database_path)
    try:
        _create_import_job(first)

        def claim(persistence: V2Persistence, worker_id: str):
            return persistence.claim_import_job(
                worker_id=worker_id,
                now_ms=1_100,
                lease_ms=1_000,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda args: claim(*args),
                    ((first, "worker-a"), (second, "worker-b")),
                )
            )
        successful = [item for item in claims if item is not None]
        assert len(successful) == 1
        assert successful[0]["attempts"] == 1
        assert first.get_import_job(successful[0]["id"])["attempts"] == 1
    finally:
        second.close()
        first.close()


def test_meeting_deletion_purges_import_job_and_foreign_key_cascades(persistence):
    created = _create_import_job(persistence)
    deletion = persistence.create_deletion_job(
        meeting_id="import-meeting",
        managed_paths=["imports/import-meeting"],
        now_ms=1_100,
    )
    persistence.mark_deletion_running(job_id=deletion["id"], now_ms=1_200)
    persistence.complete_deletion_and_purge(job_id=deletion["id"], now_ms=1_300)

    assert persistence.list_import_jobs(meeting_id="import-meeting") == []
    with pytest.raises(KeyError, match="recording import job not found"):
        persistence.get_import_job(created["id"])

    cascaded = _create_import_job(persistence, meeting_id="fk-cascade", now_ms=2_000)
    with persistence._write_transaction():
        assert persistence._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        persistence._conn.execute("DELETE FROM meetings WHERE id = ?", ("fk-cascade",))
    with pytest.raises(KeyError, match="recording import job not found"):
        persistence.get_import_job(cascaded["id"])


def test_existing_import_job_table_receives_additive_lease_columns(tmp_path):
    database_path = tmp_path / "legacy-import.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE meetings (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                title TEXT,
                started_at_ms INTEGER,
                ended_at_ms INTEGER,
                latest_seq INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );
            INSERT INTO meetings VALUES (
                'legacy-import', 'live', 'Legacy import', 1000, NULL, 0, 1, 1000, 1000
            );
            CREATE TABLE recording_import_jobs (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                source_relative_path TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                error_class TEXT,
                error_message TEXT,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER,
                FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
            );
            INSERT INTO recording_import_jobs VALUES (
                'legacy-job', 'legacy-import', 'pending', 'reading', 0,
                'imports/legacy/source.m4a', 'source.m4a', 1024, 0, 3,
                NULL, NULL, 1000, 1000, NULL
            );
            """
        )

    persistence = V2Persistence(database_path)
    try:
        migrated = persistence.get_import_job("legacy-job")
        assert migrated["next_attempt_at_ms"] == 0
        assert migrated["lease_owner"] is None
        assert migrated["lease_until_ms"] is None
        assert (
            persistence.claim_import_job(
                worker_id="migration-worker",
                now_ms=1_100,
                lease_ms=100,
            )["id"]
            == "legacy-job"
        )
    finally:
        persistence.close()
