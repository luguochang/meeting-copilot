from __future__ import annotations

import hashlib

import pytest

from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def persistence(tmp_path):
    instance = V2Persistence(tmp_path / "meeting-copilot.db")
    yield instance
    instance.close()


def _create_import(persistence: V2Persistence, now_ms: int = 1_000):
    persistence.create_meeting(meeting_id="import-duration", title="合成录音导入", now_ms=now_ms)
    return persistence.create_import_job(
        meeting_id="import-duration",
        source_relative_path="audio_assets/import-duration/source.wav",
        original_filename="synthetic.wav",
        file_size_bytes=1_024_044,
        now_ms=now_ms,
    )


def _register_audio(persistence: V2Persistence, *, started_at_ms: int, now_ms: int):
    persistence.register_imported_recording(
        meeting_id="import-duration",
        source_type="imported_file",
        relative_path="audio_assets/import-duration/audio.wav",
        sha256="a" * 64,
        sample_rate_hz=16_000,
        sample_count=512_000,
        duration_ms=32_000,
        file_size_bytes=1_024_078,
        started_at_ms=started_at_ms,
        now_ms=now_ms,
    )


def test_import_wait_failure_and_retry_do_not_claim_recording_duration(persistence):
    job = _create_import(persistence)
    assert persistence.get_snapshot("import-duration")["runtime"]["elapsed_ms"] is None
    persistence.claim_import_job(worker_id="first", now_ms=1_000, lease_ms=10_000)
    persistence.fail_import_job(
        job_id=job["id"], worker_id="first", error_class="file_import_processing_failed", now_ms=1_452
    )
    assert persistence.get_snapshot("import-duration")["runtime"]["elapsed_ms"] is None
    persistence.retry_import_job(job_id=job["id"], now_ms=2_680_000)
    assert persistence.get_snapshot("import-duration")["runtime"]["elapsed_ms"] is None
    assert persistence.get_meeting("import-duration")["created_at_ms"] == 1_000


def test_six_segments_32_second_retry_keeps_audit_clock_but_displays_audio_duration(persistence):
    # Match the observed 45:13 wall-clock gap without using recorded text.
    created_at = 1_791_393_911_869
    completed_at = 1_791_396_625_009
    job = _create_import(persistence, created_at)
    persistence.claim_import_job(worker_id="first", now_ms=created_at, lease_ms=10_000)
    persistence.fail_import_job(
        job_id=job["id"], worker_id="first", error_class="file_import_processing_failed", now_ms=created_at + 452
    )
    retry_at = completed_at - 31_632
    persistence.retry_import_job(job_id=job["id"], now_ms=retry_at)
    persistence.claim_import_job(worker_id="retry", now_ms=retry_at, lease_ms=120_000)
    _register_audio(persistence, started_at_ms=created_at, now_ms=completed_at - 10)
    texts = ("合成介绍内容。", "检查付款流程。", "关注退款问题。", "说明设备限制。", "确认演示步骤。", "提出下一议程。")
    for index, text in enumerate(texts):
        persistence.commit_final_and_enqueue(
            meeting_id="import-duration",
            final_id=f"final-{index}",
            segment_id=f"segment-{index}",
            text=text,
            normalized_text=text,
            started_at_ms=index * 5_000,
            ended_at_ms=32_000 if index == 5 else (index + 1) * 5_000,
            evidence_hash=hashlib.sha256(text.encode()).hexdigest(),
            now_ms=completed_at - 9 + index,
            enqueue_jobs=False,
            source_track="uploaded_file",
        )
    persistence.end_meeting(meeting_id="import-duration", now_ms=completed_at)
    persistence.complete_import_job(job_id=job["id"], worker_id="retry", now_ms=completed_at + 21)
    snapshot = persistence.get_snapshot("import-duration")
    assert len(snapshot["segments"]) == 6
    assert snapshot["audio"]["duration_ms"] == snapshot["runtime"]["elapsed_ms"] == 32_000
    meeting = persistence.get_meeting("import-duration")
    assert meeting["created_at_ms"] == meeting["started_at_ms"] == created_at
    assert meeting["ended_at_ms"] == completed_at
    assert meeting["ended_at_ms"] - meeting["started_at_ms"] == 2_713_140
    assert snapshot["import_job"]["created_at_ms"] == created_at


def test_registered_import_without_import_job_still_uses_audio_duration(persistence):
    persistence.create_meeting(meeting_id="import-duration", title="历史导入", now_ms=1_000)
    _register_audio(persistence, started_at_ms=1_000, now_ms=500_000)
    persistence.end_meeting(meeting_id="import-duration", now_ms=600_000)
    assert persistence.get_snapshot("import-duration")["runtime"]["elapsed_ms"] == 32_000


def test_regular_meeting_keeps_existing_wall_clock_duration(persistence):
    persistence.create_meeting(meeting_id="live-duration", title="合成实时会", now_ms=1_000)
    persistence.end_meeting(meeting_id="live-duration", now_ms=60_000)
    assert persistence.get_snapshot("live-duration")["runtime"]["elapsed_ms"] == 59_000
