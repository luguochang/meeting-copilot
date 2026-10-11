from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
import subprocess
import threading
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import asr_resource_guard
from meeting_copilot_web_mvp.application_schema import APPLICATION_SCHEMA_VERSION
from meeting_copilot_web_mvp.transcript_recovery import TranscriptRecoveryService
from meeting_copilot_web_mvp.transcript_recovery_api import mount_transcript_recovery_api
from meeting_copilot_web_mvp.transcript_recovery_audio import RecoveryAudioError
from meeting_copilot_web_mvp.transcript_recovery_persistence import RecoveryError, order_transcript_for_reading
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def recording(persistence, root, *, meeting_id="meeting-1", track="microphone", epoch=0, seconds=12):
    relative = f"audio_assets/{meeting_id}/tracks/{track}/epoch-{epoch}/audio.wav"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x01\x00" * (seconds * 16_000))
    with persistence._write_transaction():
        persistence._conn.execute(
            "INSERT INTO recording_sessions(meeting_id,track,epoch,source_type,status,sample_rate_hz,sample_count,duration_ms,"
            "file_size_bytes,output_relative_path,output_sha256,output_file_size_bytes,started_at_ms,sealed_at_ms,updated_at_ms) "
            "VALUES(?,?,?,'browser_microphone','ready',16000,?,?,?,?,?,?,0,100,100)",
            (meeting_id, track, epoch, seconds * 16_000, seconds * 1000, path.stat().st_size, relative,
             hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size))
    return path


@pytest.fixture
def environment(tmp_path):
    persistence = V2Persistence(tmp_path / "meeting_copilot.db")
    persistence.create_meeting(meeting_id="meeting-1", title="synthetic", now_ms=0)
    path = recording(persistence, tmp_path)
    calls = []

    def recognize(crop, *, timeout):
        calls.append((crop, timeout))
        assert crop != path and crop.is_relative_to(tmp_path)
        with wave.open(str(crop), "rb") as wav:
            assert wav.getnframes() == 3 * 16_000
        return {"text": "  这是本地恢复候选。  "}

    service = TranscriptRecoveryService(persistence, tmp_path, recognize=recognize,
                                         available=lambda: True, capacity=lambda *args, **kwargs: None,
                                         clock_ms=lambda: 1000, heartbeat_seconds=0.01)
    yield persistence, service, path, calls
    persistence.close()


def request(service, *, track="microphone", epoch=0, start=0, end=48_000):
    source, segments = service.persistence.recovery_source("meeting-1", track, epoch)
    from meeting_copilot_web_mvp.transcript_recovery_audio import range_plan
    plan = range_plan(source, segments, start_sample=start, end_sample=end)
    return {key: plan[key] for key in ("track", "epoch", "start_sample", "end_sample", "audio_digest", "coverage_digest")}


def candidate(environment):
    persistence, service, _, _ = environment
    job = service.create("meeting-1", request(service))
    assert asyncio.run(service.run_once())
    return persistence.get_recovery_job("meeting-1", job["id"])


def commit(persistence, *, segment_id="original", start=6000, end=7000, text="原始文本"):
    return persistence.commit_final_and_enqueue(meeting_id="meeting-1", final_id="f-" + segment_id,
        segment_id=segment_id, text=text, normalized_text=text, started_at_ms=start, ended_at_ms=end,
        source_track="microphone", evidence_hash="original-evidence", now_ms=800, enqueue_jobs=False)


def test_schema_24_and_get_are_readonly_no_model_or_candidate(environment):
    persistence, service, path, calls = environment
    before = path.read_bytes()
    snapshot = service.describe("meeting-1")
    assert APPLICATION_SCHEMA_VERSION == 26
    assert snapshot["schema_version"] == "transcript_recovery.v1"
    assert snapshot["capability"]["status"] == "ready"
    assert not snapshot["merge_allowed"] and snapshot["jobs"] == []
    assert snapshot["sources"][0]["uncovered_ranges"][0]["end_sample"] == 192_000
    assert "output_relative_path" not in str(snapshot) and "text" not in snapshot["sources"][0]
    assert calls == [] and path.read_bytes() == before
    assert persistence.list_recovery_jobs("meeting-1") == []


def test_create_is_idempotent_overlap_rejected_and_tracks_isolated(environment, tmp_path):
    persistence, service, _, calls = environment
    params = request(service)
    with ThreadPoolExecutor(2) as pool:
        jobs = list(pool.map(lambda _: service.create("meeting-1", params), range(2)))
    assert jobs[0]["id"] == jobs[1]["id"]
    assert len(persistence.list_recovery_jobs("meeting-1")) == 1 and calls == []
    with pytest.raises(RecoveryError, match="recovery_range_overlap"):
        service.create("meeting-1", request(service, start=16000, end=64000))
    recording(persistence, tmp_path, track="system_audio")
    assert service.create("meeting-1", request(service, track="system_audio"))["track"] == "system_audio"
    recording(persistence, tmp_path, epoch=1)
    assert service.create("meeting-1", request(service, epoch=1))["start_ms"] == 12_000


@pytest.mark.parametrize("changed", ["digest", "file", "coverage"])
def test_create_rechecks_source_and_preserves_original(environment, changed):
    persistence, service, path, calls = environment
    params = request(service)
    if changed == "digest":
        params["audio_digest"] = "0" * 64
    elif changed == "file":
        with path.open("r+b") as output:
            output.seek(50)
            output.write(b"changed")
    else:
        commit(persistence, start=1000, end=2000)
    with pytest.raises((RecoveryError, RecoveryAudioError)):
        service.create("meeting-1", params)
    assert persistence.list_recovery_jobs("meeting-1") == [] and calls == []


def test_candidate_is_preview_only_180_seconds_temp_cleaned(environment):
    persistence, service, path, calls = environment
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    job = candidate(environment)
    assert job["status"] == "candidate_ready" and job["attempts"] == 1
    assert job["candidate_version"] == 1 and job["candidate_text"] == "这是本地恢复候选。"
    assert not job["execution_active"] and not job["merged_segment_ids"]
    assert calls[0][1] == 180 and not calls[0][0].exists()
    assert list((path.parents[3] / "transcript-recovery-temp").glob("range-*")) == []
    assert persistence.get_snapshot("meeting-1")["segments"] == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert persistence._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("during", ["before", "during"])
def test_execution_source_change_is_stale_without_late_candidate(environment, during):
    persistence, service, _, calls = environment
    job = service.create("meeting-1", request(service))
    if during == "before":
        commit(persistence, start=1000, end=2000)
    else:
        original = service.recognize
        def recognize(*args, **kwargs):
            commit(persistence, start=1000, end=2000)
            return original(*args, **kwargs)
        service.recognize = recognize
    assert asyncio.run(service.run_once())
    result = persistence.get_recovery_job("meeting-1", job["id"])
    assert result["status"] == "stale" and result["original_candidate_text"] is None
    assert len(calls) == (0 if during == "before" else 1)


def test_active_capture_waits_without_recognizing_or_spending_attempts(environment):
    persistence, service, _, calls = environment
    job = service.create("meeting-1", request(service))
    persistence.begin_recording(meeting_id="meeting-1", track="system_audio", epoch=1,
                                sample_rate_hz=16000, source_type="browser_system_audio", lease_owner="capture", now_ms=500, lease_ms=5000)
    assert not asyncio.run(service.run_once())
    result = persistence.get_recovery_job("meeting-1", job["id"])
    assert result["status"] == "waiting_resources" and result["error_code"] == "recovery_capture_active"
    assert result["attempts"] == 0 and calls == []
    assert service.describe("meeting-1")["capability"]["reason"] == "recovery_capture_active"


def test_capacity_waiting_and_component_failure_are_truthful(environment):
    persistence, service, _, calls = environment
    job = service.create("meeting-1", request(service))
    def insufficient(*args, **kwargs):
        raise asr_resource_guard.AsrResourceError("asr_capacity_unavailable")
    service.capacity = insufficient
    assert asyncio.run(service.run_once())
    result = persistence.get_recovery_job("meeting-1", job["id"])
    assert result["status"] == "waiting_resources" and result["attempts"] == 0
    service.available = lambda: False
    assert asyncio.run(service.run_once())
    result = persistence.get_recovery_job("meeting-1", job["id"])
    assert result["status"] == "failed" and result["error_code"] == "recovery_components_missing"
    assert result["attempts"] == 0 and calls == []


@pytest.mark.parametrize("result,code", [(None, "recovery_candidate_invalid"), ({"text": 123}, "recovery_candidate_invalid"),
                                         ({"text": " "}, "recovery_no_speech"), ({"text": "a" * 20001}, "recovery_candidate_invalid")])
def test_invalid_asr_results_fail_not_ready(environment, result, code):
    persistence, service, _, _ = environment
    job = service.create("meeting-1", request(service))
    service.recognize = lambda *args, **kwargs: result
    assert asyncio.run(service.run_once())
    result = persistence.get_recovery_job("meeting-1", job["id"])
    assert result["status"] == "failed" and result["error_code"] == code and result["attempts"] == 1
    assert not asyncio.run(service.run_once())  # no automatic recognition retry


def test_timeout_retries_are_explicit_and_max_three(environment):
    persistence, service, _, _ = environment
    job = service.create("meeting-1", request(service))
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic-private-command", kwargs["timeout"])
    service.recognize = timeout
    for attempt in range(1, 4):
        assert asyncio.run(service.run_once())
        job = persistence.get_recovery_job("meeting-1", job["id"])
        assert job["status"] == "failed" and job["error_code"] == "recovery_asr_timeout" and job["attempts"] == attempt
        assert not asyncio.run(service.run_once())
        if attempt < 3:
            job = service.retry("meeting-1", job["id"], job["version"])
    with pytest.raises(RecoveryError, match="recovery_attempts_exhausted"):
        service.retry("meeting-1", job["id"], job["version"])


def test_cancel_running_keeps_owned_resource_and_blocks_late_output_and_other_worker(environment):
    persistence, service, _, _ = environment
    entered, release = threading.Event(), threading.Event()
    def recognize(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {"text": "迟到结果"}
    service.recognize = recognize
    job = service.create("meeting-1", request(service))
    async def exercise():
        task = asyncio.create_task(service.run_once())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            current = persistence.get_recovery_job("meeting-1", job["id"])
            cancelled = service.cancel("meeting-1", job["id"], current["version"])
            assert cancelled["execution_active"] and cancelled["status"] == "cancelled"
            assert persistence.claim_recovery_job(owner="another", now_ms=1001) is None
            assert persistence.heartbeat_recovery_job(job["id"], owner=service.owner, now_ms=2000)
        finally:
            release.set()
            await task
    asyncio.run(exercise())
    result = persistence.get_recovery_job("meeting-1", job["id"])
    assert result["status"] == "cancelled" and result["original_candidate_text"] is None and not result["execution_active"]
    assert persistence.get_snapshot("meeting-1")["segments"] == []


def test_restart_lease_expiry_preserves_attempts_requires_explicit_retry(environment):
    persistence, service, _, _ = environment
    job = service.create("meeting-1", request(service))
    claimed = persistence.claim_recovery_job(owner="dead-worker", now_ms=1000, lease_ms=2000)
    persistence.begin_recovery_attempt(job["id"], owner="dead-worker", version=claimed["version"], now_ms=1001, verify=lambda _: None)
    assert persistence.recover_recovery_leases(now_ms=2999) == 0
    assert persistence.claim_recovery_job(owner="new", now_ms=2999) is None
    assert persistence.recover_recovery_leases(now_ms=3000) == 1
    current = persistence.get_recovery_job("meeting-1", job["id"])
    assert current["status"] == "failed" and current["attempts"] == 1 and current["error_code"] == "recovery_worker_interrupted"
    assert persistence.claim_recovery_job(owner="new", now_ms=3001) is None
    assert not persistence.finish_recovery_job(job["id"], owner="dead-worker", version=claimed["version"], now_ms=3002,
                                               status="candidate_ready", text="old")
    assert service.retry("meeting-1", job["id"], current["version"])["status"] == "queued"


def test_merge_atomic_separate_history_preserves_original_seq_and_no_live_jobs(environment):
    persistence, service, _, _ = environment
    commit(persistence)
    original = persistence.get_snapshot("meeting-1")["segments"][0]
    job = candidate(environment)
    with pytest.raises(RecoveryError, match="recovery_merge_capture_active"):
        service.merge("meeting-1", job["id"], job["candidate_version"], None)
    persistence.end_meeting(meeting_id="meeting-1", now_ms=1500)
    old_paragraph = persistence.list_semantic_paragraphs("meeting-1")["paragraphs"][0]
    entities = [dict(row) for row in persistence._conn.execute("SELECT * FROM meeting_entities").fetchall()]
    jobs_before = [dict(row) for row in persistence._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]
    result = service.merge("meeting-1", job["id"], job["candidate_version"], "人工修正候选")
    assert result["status"] == "merged" and result["original_candidate_text"] == "这是本地恢复候选。"
    assert result["candidate_text"] == "人工修正候选"
    again = service.merge("meeting-1", job["id"], job["candidate_version"], "人工修正候选")
    assert again == result and service.create("meeting-1", request_before_merge(job))["id"] == job["id"]
    page = persistence.list_transcript_segments("meeting-1")
    assert [row["transcript_seq"] for row in page["segments"]] == [1, 2]
    assert page["segments"][0] == original
    recovered = page["segments"][1]
    assert recovered["started_at_ms"] == 0 and recovered["ended_at_ms"] == 3000
    assert recovered["speaker_id"] is None and recovered["recovery_provenance"]["correction"] == "not_requested"
    assert [row["transcript_seq"] for row in order_transcript_for_reading(page["segments"])] == [2, 1]
    assert [row["transcript_seq"] for row in persistence.get_snapshot("meeting-1")["segments"]] == [2, 1]
    paragraphs = persistence.list_semantic_paragraphs("meeting-1")["paragraphs"]
    assert len(paragraphs) == 2 and paragraphs[0]["status"] == "stable"
    assert paragraphs[1] == old_paragraph
    assert persistence.get_meeting("meeting-1")["state"] == "ended"
    assert [dict(row) for row in persistence._conn.execute("SELECT * FROM meeting_entities").fetchall()] == entities
    assert [dict(row) for row in persistence._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()] == jobs_before
    coverage = persistence.list_intelligence_input_coverage("meeting-1")
    assert coverage[-1]["status"] == "excluded" and coverage[-1]["failure_reason"] == "historical_recovery_manual"


def request_before_merge(job):
    return {key: job[key] for key in ("track", "epoch", "start_sample", "end_sample", "audio_digest", "coverage_digest")}


def test_merge_rolls_back_everything_if_semantic_insert_fails(environment):
    persistence, service, _, _ = environment
    job = candidate(environment)
    persistence.end_meeting(meeting_id="meeting-1", now_ms=1500)
    event_count = persistence._conn.execute("SELECT COUNT(*) FROM meeting_events").fetchone()[0]
    persistence._conn.execute("CREATE TRIGGER reject_recovery BEFORE INSERT ON semantic_paragraphs BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.merge("meeting-1", job["id"], job["candidate_version"], None)
    assert persistence.get_recovery_job("meeting-1", job["id"])["status"] == "candidate_ready"
    assert persistence.get_snapshot("meeting-1")["segments"] == []
    assert persistence._conn.execute("SELECT COUNT(*) FROM asr_checkpoints").fetchone()[0] == 0
    assert persistence._conn.execute("SELECT COUNT(*) FROM meeting_events").fetchone()[0] == event_count


@pytest.mark.parametrize("change", ["coverage", "file", "version"])
def test_merge_stale_source_or_candidate_version_rejected(environment, change):
    persistence, service, path, _ = environment
    job = candidate(environment)
    persistence.end_meeting(meeting_id="meeting-1", now_ms=1500)
    if change == "coverage":
        commit(persistence, start=1000, end=2000)
    elif change == "file":
        with path.open("r+b") as output:
            output.seek(70)
            output.write(b"changed")
    with pytest.raises((RecoveryAudioError, RecoveryError)):
        service.merge("meeting-1", job["id"], job["candidate_version"] + int(change == "version"), None)
    result = persistence.get_recovery_job("meeting-1", job["id"])
    assert result["status"] == ("candidate_ready" if change == "version" else "stale")
    assert not result["merged_segment_ids"]


def test_meeting_owned_path_and_sealed_readiness_enforced(environment):
    persistence, service, _, _ = environment
    with persistence._write_transaction():
        persistence._conn.execute("UPDATE recording_sessions SET output_relative_path='audio_assets/other/audio.wav'")
    assert service.describe("meeting-1")["sources"] == []
    assert service.describe("meeting-1")["unavailable_sources"][0]["error_code"] == "recovery_audio_path_invalid"


def test_api_shapes_no_paths_and_version_checked_without_starting_model(environment):
    persistence, service, _, calls = environment
    app = FastAPI()
    mount_transcript_recovery_api(app, persistence, service.data_dir)
    # No lifespan: GET must not start worker. API service still uses safe fixture dependencies.
    api = app.state.transcript_recovery
    api.available, api.capacity = service.available, service.capacity
    client = TestClient(app)
    url = "/v2/meetings/meeting-1/transcript-recovery"
    assert client.get(url).json()["capability"]["status"] == "ready"
    params = request(service)
    response = client.post(url, json=params)
    assert response.status_code == 202
    job = response.json()["job"]
    assert client.post(url, json={**params, "path": "C:/secret"}).status_code == 422
    assert client.post(url, json={**params, "epoch": True}).status_code == 422
    assert client.post(url + "/" + job["id"] + "/cancel", json={"expected_version": job["version"] + 1}).json()["error"]["code"] == "recovery_version_conflict"
    assert client.post(url + "/" + job["id"] + "/cancel", json={"expected_version": job["version"]}).json()["job"]["status"] == "cancelled"
    response = client.get("/v2/meetings/missing/transcript-recovery")
    assert response.status_code == 404 and response.json()["error"]["code"] == "recovery_meeting_not_found"
    assert str(service.data_dir) not in str(response.json()) and calls == []


def test_create_hashing_does_not_hold_database_while_another_meeting_is_capturing(environment):
    persistence, service, _, _ = environment
    persistence.create_meeting(meeting_id="live-other", title="synthetic", now_ms=0)
    persistence.begin_recording(meeting_id="live-other", track="microphone", epoch=1,
        sample_rate_hz=16000, source_type="browser_microphone", lease_owner="live", now_ms=500, lease_ms=5000)
    hashing, resume = threading.Event(), threading.Event()
    def verify(source):
        hashing.set()
        assert resume.wait(3)
        service._verify(params)(source)
    params = request(service)
    with ThreadPoolExecutor(2) as pool:
        creation = pool.submit(persistence.create_recovery_job, "meeting-1", params, now_ms=1000, verify=verify)
        try:
            assert hashing.wait(2)
            realtime = pool.submit(persistence.commit_final_and_enqueue, meeting_id="live-other", final_id="real-final",
                segment_id="real-segment", text="synthetic live final", normalized_text="synthetic live final",
                started_at_ms=0, ended_at_ms=500, source_track="microphone", evidence_hash="synthetic",
                now_ms=1001, enqueue_jobs=False)
            assert realtime.result(timeout=1)["transcript_seq"] == 1
        finally:
            resume.set()
        assert creation.result(timeout=2)["status"] == "queued"


def test_source_changed_during_transaction_free_hash_cannot_publish_job(environment):
    persistence, service, _, _ = environment
    params = request(service)
    def verify(source):
        service._verify(params)(source)
        commit(persistence, start=1000, end=2000)
    with pytest.raises(RecoveryError, match="recovery_source_changed"):
        persistence.create_recovery_job("meeting-1", params, now_ms=1000, verify=verify)
    assert persistence.list_recovery_jobs("meeting-1") == []


def test_begin_attempt_hash_is_unlocked_and_cancel_race_does_not_spend_or_call_asr(environment):
    persistence, service, _, calls = environment
    job = service.create("meeting-1", request(service))
    claimed = persistence.claim_recovery_job(owner="worker", now_ms=1000)
    def verify(source):
        service._verify(job)(source)
        service.cancel("meeting-1", job["id"], claimed["version"])
    with pytest.raises(RecoveryError, match="recovery_execution_superseded"):
        persistence.begin_recovery_attempt(job["id"], owner="worker", version=claimed["version"], now_ms=1001, verify=verify)
    persistence.finish_recovery_job(job["id"], owner="worker", version=claimed["version"], now_ms=1002, status="failed")
    cancelled = persistence.get_recovery_job("meeting-1", job["id"])
    assert cancelled["status"] == "cancelled" and cancelled["attempts"] == 0 and calls == []


def test_merge_preserves_saved_human_final_marks_source_stale_and_history_exports(environment):
    persistence, service, _, _ = environment
    commit(persistence)
    final = persistence.save_user_final_document(meeting_id="meeting-1", document_kind="transcript",
        expected_revision=0, content={"text": "用户已经确认的人工稿"}, now_ms=900)
    job = candidate(environment)
    persistence.end_meeting(meeting_id="meeting-1", now_ms=1500)
    before_hash = persistence.capture_review_source("meeting-1")["hash"]
    service.merge("meeting-1", job["id"], job["candidate_version"], None)
    after = persistence.get_review_document("meeting-1", "transcript")
    assert after["user_final"] == final["user_final"]
    assert after["dirty_state"] == "stale"
    assert persistence.capture_review_source("meeting-1")["hash"] != before_hash
    from meeting_copilot_web_mvp.app import _v2_complete_transcript
    assert [row["transcript_seq"] for row in _v2_complete_transcript(persistence, "meeting-1")] == [2, 1]
    cursor = persistence.list_transcript_segments("meeting-1", after_transcript_seq=1, limit=1)
    assert len(cursor["segments"]) == 1 and cursor["segments"][0]["transcript_seq"] == 2


def test_deleting_meeting_during_running_asr_removes_job_and_blocks_late_result(environment):
    persistence, service, _, _ = environment
    from meeting_copilot_web_mvp.data_governance import DataGovernanceService
    governance = DataGovernanceService(persistence=persistence, data_dir=service.data_dir)
    entered, release = threading.Event(), threading.Event()
    def recognize(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {"text": "late synthetic result"}
    service.recognize = recognize
    service.create("meeting-1", request(service))
    async def exercise():
        task = asyncio.create_task(service.run_once())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            deletion = governance.request_deletion(meeting_id="meeting-1", deletion_scope="all", now_ms=2000)
            governance.execute_deletion_job(job_id=deletion["id"], now_ms=2001)
        finally:
            release.set()
            await task
    asyncio.run(exercise())
    assert persistence._conn.execute("SELECT COUNT(*) FROM transcript_recovery_jobs").fetchone()[0] == 0
    assert persistence._conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 0
    assert not (service.data_dir / "audio_assets" / "meeting-1").exists()


@pytest.mark.parametrize("extra,valid", [("", True), ("a", False)])
def test_candidate_utf8_64kib_boundary_independent_of_character_bound(environment, extra, valid):
    persistence, service, _, _ = environment
    job = service.create("meeting-1", request(service))
    service.recognize = lambda *args, **kwargs: {"text": "😀" * 16384 + extra}
    assert asyncio.run(service.run_once())
    current = persistence.get_recovery_job("meeting-1", job["id"])
    assert current["status"] == ("candidate_ready" if valid else "failed")
    assert current["error_code"] == (None if valid else "recovery_candidate_invalid")


def test_service_lifespan_stop_waits_for_owned_model_return(environment):
    persistence, service, _, _ = environment
    entered, release = threading.Event(), threading.Event()
    def recognize(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {"text": "safe shutdown candidate"}
    service.recognize = recognize
    job = service.create("meeting-1", request(service))
    async def exercise():
        await service.start()
        stopping = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            stopping = asyncio.create_task(service.stop())
            await asyncio.sleep(0.03)
            assert not stopping.done()
            assert persistence.get_recovery_job("meeting-1", job["id"])["execution_active"]
        finally:
            release.set()
            if stopping is not None:
                await stopping
            else:
                await service.stop()
    asyncio.run(exercise())
    assert persistence.get_recovery_job("meeting-1", job["id"])["status"] == "candidate_ready"


def test_schema_23_migrates_additively_without_renumbering_existing_segments(tmp_path):
    from meeting_copilot_web_mvp import application_schema
    from meeting_copilot_web_mvp.sqlite_schema import migrate_sqlite_schema
    database = tmp_path / "meeting_copilot.db"
    migrate_sqlite_schema(database, application_schema.APPLICATION_SCHEMA_MIGRATIONS[:23], current_version=23, max_supported_version=23)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO meetings(id,state,started_at_ms,latest_seq,revision,created_at_ms,updated_at_ms) VALUES('old','ended',0,0,1,0,0)")
        connection.execute("INSERT INTO transcript_segments(meeting_id,segment_id,final_id,transcript_seq,text,normalized_text,evidence_hash,created_at_ms,updated_at_ms) "
                           "VALUES('old','old-segment','old-final',17,'original','original','hash',0,0)")
    upgraded = V2Persistence(database)
    try:
        segment = upgraded.list_transcript_segments("old")["segments"][0]
        assert segment["transcript_seq"] == 17 and segment["text"] == "original" and segment.get("recovery_job_id") is None
        assert upgraded.list_recovery_jobs("old") == []
        assert upgraded._conn.execute("PRAGMA user_version").fetchone()[0] == 26
        assert {"request_context_json"}.issubset({
            row[1] for row in upgraded._conn.execute("PRAGMA table_info(jobs)")
        })
        assert upgraded._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='material_sets'"
        ).fetchone()[0] == "material_sets"
    finally:
        upgraded.close()



def test_concurrent_start_during_stop_does_not_lose_owned_worker(environment):
    persistence, service, _, _ = environment
    entered, release = threading.Event(), threading.Event()
    count = 0
    def recognize(*args, **kwargs):
        nonlocal count
        count += 1
        entered.set()
        assert release.wait(3)
        return {"text": "safe concurrent lifecycle"}
    service.recognize = recognize
    service.create("meeting-1", request(service))
    async def exercise():
        await service.start()
        original = service._task
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            stopping = asyncio.create_task(service.stop())
            await asyncio.sleep(0.01)
            await service.start()
            assert service._task is original and service._stop.is_set()
            release.set()
            await asyncio.wait_for(stopping, 2)
            assert original.done() and service._task is None
            await service.start()
            assert service._task is not original
            await service.stop()
        finally:
            release.set()
            if service._task is not None:
                await service.stop()
    asyncio.run(exercise())
    assert count == 1


def test_expired_old_finish_same_owner_cannot_clear_new_claim(environment):
    persistence, service, _, _ = environment
    job = service.create("meeting-1", request(service))
    old = persistence.claim_recovery_job(owner="same-worker", now_ms=1000, lease_ms=10)
    assert persistence.recover_recovery_leases(now_ms=1010) == 1
    failed = persistence.get_recovery_job("meeting-1", job["id"])
    service.retry("meeting-1", job["id"], failed["version"])
    new = persistence.claim_recovery_job(owner="same-worker", now_ms=1011)
    assert not persistence.finish_recovery_job(job["id"], owner="same-worker", version=old["version"], now_ms=1012,
                                               status="candidate_ready", text="late old claim")
    assert not persistence.heartbeat_recovery_job(job["id"], owner="same-worker", version=old["version"], now_ms=1013)
    current = persistence.get_recovery_job("meeting-1", job["id"])
    assert current["execution_active"] and current["status"] == "running" and current["version"] == new["version"]
    assert persistence.heartbeat_recovery_job(job["id"], owner="same-worker", version=new["version"], now_ms=1013)
    assert persistence.finish_recovery_job(job["id"], owner="same-worker", version=new["version"], now_ms=1014,
                                            status="candidate_ready", text="valid current claim", verify=service._verify(job))
    assert persistence.get_recovery_job("meeting-1", job["id"])["candidate_text"] == "valid current claim"


@pytest.mark.parametrize("before_spawn", [False, True])
def test_running_probe_failure_is_charged_and_not_automatic_retry(environment, before_spawn):
    persistence, service, _, calls = environment
    job = service.create("meeting-1", request(service))
    count = 0
    def recognize(*args, **kwargs):
        nonlocal count
        count += 1
        error = asr_resource_guard.AsrResourceError("asr_resource_probe_failed")
        if before_spawn:
            error.execution_phase = "before_spawn"
        raise error
    service.recognize = recognize
    assert asyncio.run(service.run_once())
    current = persistence.get_recovery_job("meeting-1", job["id"])
    assert current["status"] == ("waiting_resources" if before_spawn else "failed")
    assert current["attempts"] == (0 if before_spawn else 1)
    if not before_spawn:
        assert not asyncio.run(service.run_once()) and count == 1
    assert calls == []


def test_owned_cleanup_keeps_failed_execution_active_until_its_lease_released(environment):
    persistence, service, _, _ = environment
    entered, released = threading.Event(), threading.Event()
    class OwnedLease:
        @property
        def is_released(self):
            return released.is_set()
    def recognize(*args, **kwargs):
        error = asr_resource_guard.AsrResourceError("asr_resource_guard_triggered")
        error.execution_phase = "inference_or_cleanup"
        error.owned_cleanup_lease = OwnedLease()
        entered.set()
        raise error
    service.recognize = recognize
    job = service.create("meeting-1", request(service))
    async def exercise():
        task = asyncio.create_task(service.run_once())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            deadline = asyncio.get_running_loop().time() + 2
            while asyncio.get_running_loop().time() < deadline:
                current = persistence.get_recovery_job("meeting-1", job["id"])
                if current["status"] == "failed":
                    break
                await asyncio.sleep(0.01)
            assert current["status"] == "failed" and current["execution_active"] and current["attempts"] == 1
            assert not task.done()
            assert list((service.data_dir / "audio_assets/meeting-1/transcript-recovery-temp").glob("range-*/range.wav"))
            with pytest.raises(RecoveryError, match="recovery_retry_not_allowed"):
                service.retry("meeting-1", job["id"], current["version"])
            cancelled = service.cancel("meeting-1", job["id"], current["version"])
            assert cancelled["execution_active"]
            assert persistence.claim_recovery_job(owner="another", now_ms=1001) is None
        finally:
            released.set()
            await asyncio.wait_for(task, 2)
    asyncio.run(exercise())
    current = persistence.get_recovery_job("meeting-1", job["id"])
    assert current["status"] == "cancelled" and not current["execution_active"] and current["attempts"] == 1
    assert list((service.data_dir / "audio_assets/meeting-1/transcript-recovery-temp").glob("range-*")) == []


@pytest.mark.parametrize("with_candidate", [False, True])
def test_explicit_retry_of_cancelled_preserves_candidate_and_budget(environment, with_candidate):
    persistence, service, _, calls = environment
    job = candidate(environment) if with_candidate else service.create("meeting-1", request(service))
    job = service.cancel("meeting-1", job["id"], job["version"])
    count = len(calls)
    restored = service.retry("meeting-1", job["id"], job["version"])
    assert restored["status"] == ("candidate_ready" if with_candidate else "queued")
    assert restored["original_candidate_text"] == job["original_candidate_text"]
    assert restored["candidate_version"] == job["candidate_version"] and restored["attempts"] == job["attempts"]
    if with_candidate:
        assert not asyncio.run(service.run_once()) and len(calls) == count
    else:
        assert asyncio.run(service.run_once()) and len(calls) == count + 1


def test_cancelled_candidate_retry_source_changed_is_stale(environment):
    persistence, service, _, _ = environment
    job = candidate(environment)
    job = service.cancel("meeting-1", job["id"], job["version"])
    commit(persistence, start=1000, end=2000)
    with pytest.raises(RecoveryError, match="recovery_source_changed"):
        service.retry("meeting-1", job["id"], job["version"])
    assert persistence.get_recovery_job("meeting-1", job["id"])["status"] == "stale"


def test_api_validation_and_storage_failure_never_echo_sensitive_input(environment):
    persistence, service, _, _ = environment
    app = FastAPI()
    mount_transcript_recovery_api(app, persistence, service.data_dir)
    api = app.state.transcript_recovery
    client = TestClient(app)
    url = "/v2/meetings/meeting-1/transcript-recovery"
    response = client.post(url, json={**request(service), "path": "C:/synthetic-secret"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "recovery_request_invalid"
    assert "synthetic-secret" not in response.text
    def broken(*args):
        raise sqlite3.OperationalError("C:/synthetic-secret plus raw candidate")
    api.create = broken
    response = client.post(url, json=request(service))
    assert response.status_code == 503 and response.json()["error"]["code"] == "recovery_storage_unavailable"
    assert "synthetic-secret" not in response.text and "raw candidate" not in response.text


def test_full_app_api_preview_merge_reload_is_wired_without_provider_or_real_asr(tmp_path):
    from meeting_copilot_web_mvp.app import create_app
    app = create_app(data_dir=tmp_path)
    persistence = app.state.v2_persistence
    try:
        persistence.create_meeting(meeting_id="meeting-1", title="synthetic", now_ms=0)
        recording(persistence, tmp_path)
        commit(persistence)
        service = app.state.transcript_recovery
        service.available, service.capacity = lambda: True, lambda *args, **kwargs: None
        service.recognize = lambda *args, **kwargs: {"text": "synthetic recovered history"}
        client = TestClient(app)  # manually run only this fake worker, no other runtime/model startup
        url = "/v2/meetings/meeting-1/transcript-recovery"
        assert client.get(url).json()["jobs"] == []
        params = request(service)
        job = client.post(url, json=params).json()["job"]
        assert asyncio.run(service.run_once())
        job = client.get(url).json()["jobs"][0]
        assert job["status"] == "candidate_ready"
        persistence.end_meeting(meeting_id="meeting-1", now_ms=1500)
        jobs_before = [dict(row) for row in persistence._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]
        merged = client.post(url + "/" + job["id"] + "/merge", json={"candidate_version": job["candidate_version"],
                                                                                    "text": "synthetic reviewed history"})
        assert merged.status_code == 200 and merged.json()["job"]["status"] == "merged"
        segments = client.get("/v2/meetings/meeting-1/transcript").json()["segments"]
        assert [segment["transcript_seq"] for segment in segments] == [1, 2]
        assert segments[1]["recovery_provenance"]["user_edited"]
        snapshot = client.get("/v2/meetings/meeting-1/snapshot").json()
        assert [segment["transcript_seq"] for segment in snapshot["segments"]] == [2, 1]
        assert snapshot["runtime"]["phase"] == "ended"
        assert [dict(row) for row in persistence._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()] == jobs_before
    finally:
        for repository in app.state.sqlite_repositories:
            repository.close()


def test_hidden_cross_track_duplicate_is_existing_coverage_not_missing_audio(environment, tmp_path):
    persistence, service, _, calls = environment
    recording(persistence, tmp_path, track="system_audio")
    for track in ("microphone", "system_audio"):
        persistence.commit_final_and_enqueue(meeting_id="meeting-1", final_id="f-" + track,
            segment_id="s-" + track, text="同一条实际已经保存的文字", normalized_text="同一条实际已经保存的文字",
            started_at_ms=0, ended_at_ms=3000, source_track=track, evidence_hash="hash-" + track,
            now_ms=800, enqueue_jobs=False)
    visible = persistence.list_transcript_segments("meeting-1")["segments"]
    assert len(visible) == 1 and visible[0]["source_track"] == "microphone"
    row = persistence._conn.execute("SELECT * FROM transcript_segments WHERE source_track='system_audio'").fetchone()
    assert row["duplicate_of_segment_id"] == "s-microphone"
    source, segments = persistence.recovery_source("meeting-1", "system_audio", 0)
    assert source["uncovered_ranges"][0]["start_sample"] == 48_000
    assert len(segments) == 2
    with pytest.raises(RecoveryError, match="recovery_range_covered"):
        service.create("meeting-1", request(service, track="system_audio"))
    assert persistence.list_recovery_jobs("meeting-1") == [] and calls == []


def test_native_raw_recovery_merge_projects_not_requested_without_inventing_failure(tmp_path):
    from meeting_copilot_web_mvp.app import create_app
    app = create_app(data_dir=tmp_path, prewarm_funasr=False)
    persistence = app.state.v2_persistence
    try:
        persistence.create_meeting(meeting_id="meeting-1", title="synthetic", now_ms=0)
        path = recording(persistence, tmp_path)
        source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        service = app.state.transcript_recovery
        service.available, service.capacity = lambda: True, lambda *args, **kwargs: None
        raw = "这是本地录音恢复的原始识别文字，尚未请求大模型校对。"
        service.recognize = lambda *args, **kwargs: {"text": raw}
        client = TestClient(app)  # no lifespan, provider, ASR model or other worker
        url = "/v2/meetings/meeting-1/transcript-recovery"
        assert client.post(url, json=request(service)).status_code == 202
        assert asyncio.run(service.run_once())
        job = client.get(url).json()["jobs"][0]
        assert job["status"] == "candidate_ready"
        persistence.end_meeting(meeting_id="meeting-1", now_ms=1500)
        jobs_before = [dict(row) for row in persistence._conn.execute("SELECT * FROM jobs ORDER BY id")]
        merged = client.post(url + "/" + job["id"] + "/merge", json={
            "candidate_version": job["candidate_version"], "text": None,
        })
        assert merged.status_code == 200 and merged.json()["job"]["status"] == "merged"
        snapshot = client.get("/v2/meetings/meeting-1/snapshot").json()
        segment = snapshot["segments"][0]
        assert segment["text"] == segment["normalized_text"] == raw
        assert segment["correction_status"] == "not_requested"
        assert segment["correction_error_class"] is None
        assert segment["correction_status_provenance"] == "historical_recovery_not_requested"
        assert segment["recovery_provenance"]["correction"] == "not_requested"
        assert segment["recovery_provenance"]["user_edited"] is False
        assert client.get("/v2/meetings/meeting-1/transcript").json()["segments"][0] == segment
        assert raw in " ".join(p["text"] for p in snapshot["semantic_paragraphs"])
        assert persistence._conn.execute("SELECT correction_status FROM transcript_segments").fetchone()[0] == "pending"
        assert [dict(row) for row in persistence._conn.execute("SELECT * FROM jobs ORDER BY id")] == jobs_before
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source_digest
    finally:
        for repository in app.state.sqlite_repositories:
            repository.close()
