import time

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.app import CorrectionResultRejected, create_app


def _app_with_correction(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-test")
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    raw = "这次可能先安排演练。"
    app.state.commit_v2_final("meeting-1", {
        "event_type": "final", "segment_id": "s1", "text": raw,
        "normalized_text": raw, "start_ms": 0, "end_ms": 1_000,
    })
    app.state.asr_live_repository.create({
        "session_id": "meeting-1", "source": "live_asr_stream", "trace_kind": "live_event",
        "provider": "funasr_realtime", "provider_mode": "real", "is_mock": False,
        "input_source": "browser_live_mic", "events": [],
    })
    persistence = app.state.v2_persistence
    now_ms = time.time_ns() // 1_000_000
    persistence.end_meeting(meeting_id="meeting-1", now_ms=now_ms)
    job = persistence.claim_next_job(worker_id="correction-truth-test", lane="correction", now_ms=now_ms + 1, lease_ms=30_000)
    assert job is not None and job["evidence_segment_id"] == "s1"
    return app, persistence, job, raw, now_ms


@pytest.mark.parametrize("terminal", [False, True])
def test_rejected_correction_never_completes_as_no_change(monkeypatch, tmp_path, terminal):
    app, persistence, job, raw, now_ms = _app_with_correction(monkeypatch, tmp_path)
    status = {
        "status": "partially_completed", "failed_segment_ids": ["s1"],
        "rejected_segment_ids": ["s1"], "terminal_failed_segment_ids": ["s1"] if terminal else [],
        "failure_counts": {"s1": 2 if terminal else 1},
        "rejection_reasons": {"s1": "speech_mode_changed"},
    }
    monkeypatch.setattr(app.state, "run_asr_live_session_realtime_corrections_once", lambda *_a, **_kw: {
        "session_id": "meeting-1", "called": True, "gate": {"segment_ids": ["s1"]},
        "status": status, "revision_count": 0, "transcript_revisions": [], "no_revision_segment_ids": [],
    })
    try:
        with pytest.raises(CorrectionResultRejected) as caught:
            app.state.v2_correction_job_handler_impl(job)
        assert caught.value.retryable is not terminal
        assert caught.value.durable_error_class == "correction_speech_mode_changed"
        before = persistence.list_transcript_segments("meeting-1")["segments"][0]
        assert before["normalized_text"] == raw
        assert before["correction_status"] == "processing"
        if terminal:
            persistence.fail_job(job_id=job["id"], worker_id="correction-truth-test", now_ms=now_ms + 2, error_class=caught.value.durable_error_class)
            after = persistence.list_transcript_segments("meeting-1")["segments"][0]
            assert after["normalized_text"] == raw
            assert after["correction_status"] == "failed_preserved_original"
            assert after["correction_error_class"] == "correction_speech_mode_changed"
            app.state.asr_live_repository.update("meeting-1", lambda record: {**record, "realtime_transcript_correction": {**status, "processed_segment_ids": ["s1"]}})
            response = TestClient(app).post("/v2/meetings/meeting-1/transcript/correction/retry")
            assert response.status_code == 202
            assert response.json()["segment_count"] == 1
            live = app.state.asr_live_repository.get("meeting-1")["realtime_transcript_correction"]
            for field in ("failed_segment_ids", "terminal_failed_segment_ids", "rejected_segment_ids", "processed_segment_ids"):
                assert "s1" not in live[field]
            assert "s1" not in live["rejection_reasons"]
    finally:
        persistence.close()


def test_batch_rejection_of_another_segment_keeps_explicit_unchanged_anchor_successful(monkeypatch, tmp_path):
    app, persistence, job, raw, _ = _app_with_correction(monkeypatch, tmp_path)
    monkeypatch.setattr(app.state, "run_asr_live_session_realtime_corrections_once", lambda *_a, **_kw: {
        "session_id": "meeting-1", "called": True, "gate": {"segment_ids": ["s1", "other"]},
        "status": {"failed_segment_ids": ["other"], "terminal_failed_segment_ids": ["other"]},
        "revision_count": 0, "transcript_revisions": [], "no_revision_segment_ids": ["s1"],
    })
    try:
        output = app.state.v2_correction_job_handler_impl(job)
        assert output["no_revision_segment_count"] == 1
        segment = persistence.list_transcript_segments("meeting-1")["segments"][0]
        assert segment["correction_status"] == "no_change"
        assert segment["normalized_text"] == raw
    finally:
        persistence.close()


def test_later_no_unrevised_job_replays_verified_unchanged_batch_evidence(monkeypatch, tmp_path):
    app, persistence, job, raw, _ = _app_with_correction(monkeypatch, tmp_path)
    app.state.asr_live_repository.update("meeting-1", lambda record: {
        **record,
        "events": [{"event_type": "final", "segment_id": "s1", "text": raw, "normalized_text": raw}],
        "realtime_transcript_correction": {
            "status": "completed", "processed_segment_ids": ["s1", "not-evaluated"],
            "revised_segment_ids": [], "rejected_segment_ids": ["not-evaluated"],
        },
    })
    monkeypatch.setattr(app.state, "run_asr_live_session_realtime_corrections_once", lambda *_a, **_kw: {
        "session_id": "meeting-1", "called": False,
        "gate": {"eligible": False, "reason": "no_unrevised_final"},
        "status": {}, "revision_count": 0, "transcript_revisions": [], "no_revision_segment_ids": [],
    })
    try:
        output = app.state.v2_correction_job_handler_impl(job)
        segment = persistence.list_transcript_segments("meeting-1")["segments"][0]
        assert output["called"] is False
        assert segment["correction_status"] == "no_change"
        assert segment["normalized_text"] == raw
    finally:
        persistence.close()


def test_ended_unchecked_terminal_job_preserves_original_and_allows_retry(monkeypatch, tmp_path):
    app, persistence, job, raw, now_ms = _app_with_correction(monkeypatch, tmp_path)
    try:
        # Cancel the other queued end job so this unchecked no-op is the last.
        with persistence._write_transaction():
            persistence._conn.execute("UPDATE jobs SET status='cancelled' WHERE kind='correction' AND id != ?", (job["id"],))
        persistence.complete_job(job_id=job["id"], worker_id="correction-truth-test", now_ms=now_ms + 2, output={"called": False})
        segment = persistence.list_transcript_segments("meeting-1")["segments"][0]
        assert segment["correction_status"] == "failed_preserved_original"
        assert segment["correction_error_class"] == "correction_not_evaluated"
        assert segment["normalized_text"] == raw
        retry = persistence.enqueue_transcript_correction_retry(meeting_id="meeting-1", now_ms=now_ms + 3)
        assert retry["created"] is True and retry["segment_count"] == 1
    finally:
        persistence.close()


def test_cancelled_correction_without_replacement_never_leaves_processing(monkeypatch, tmp_path):
    app, persistence, job, raw, now_ms = _app_with_correction(monkeypatch, tmp_path)
    try:
        with persistence._write_transaction():
            persistence._conn.execute("UPDATE jobs SET status='cancelled' WHERE kind='correction' AND id != ?", (job["id"],))
        persistence.cancel_job(job_id=job["id"], worker_id="correction-truth-test", now_ms=now_ms + 2, error_class="evidence_superseded")
        segment = persistence.list_transcript_segments("meeting-1")["segments"][0]
        assert segment["correction_status"] == "failed_preserved_original"
        assert segment["correction_error_class"] == "evidence_superseded"
        assert segment["normalized_text"] == raw
    finally:
        persistence.close()


def test_verified_unchanged_replay_cannot_cross_source_hash_change(monkeypatch, tmp_path):
    app, persistence, job, raw, _ = _app_with_correction(monkeypatch, tmp_path)
    app.state.asr_live_repository.update("meeting-1", lambda record: {
        **record, "events": [{"event_type": "final", "segment_id": "s1", "text": "另一份原文。"}],
        "realtime_transcript_correction": {"status": "completed", "processed_segment_ids": ["s1"]},
    })
    monkeypatch.setattr(app.state, "run_asr_live_session_realtime_corrections_once", lambda *_a, **_kw: {
        "session_id": "meeting-1", "called": False, "gate": {"reason": "no_unrevised_final"},
        "status": {}, "transcript_revisions": [], "no_revision_segment_ids": [],
    })
    try:
        app.state.v2_correction_job_handler_impl(job)
        segment = persistence.list_transcript_segments("meeting-1")["segments"][0]
        assert segment["correction_status"] == "processing"
        assert segment["normalized_text"] == raw
    finally:
        persistence.close()


def test_legacy_ended_orphan_projection_never_pretends_work_is_active(monkeypatch, tmp_path):
    app, persistence, _, raw, _ = _app_with_correction(monkeypatch, tmp_path)
    try:
        with persistence._write_transaction():
            persistence._conn.execute("UPDATE jobs SET status='cancelled' WHERE kind='correction'")
        raw_segment = persistence._segment_dict(persistence._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id='meeting-1'").fetchone())
        assert raw_segment["correction_status"] == "processing"
        projected = persistence.get_snapshot("meeting-1")["segments"][0]
        assert projected["correction_status"] == "failed_preserved_original"
        assert projected["correction_error_class"] == "correction_not_evaluated"
        assert projected["correction_status_provenance"] == "terminal_job_projection"
        assert projected["normalized_text"] == raw
        paged = persistence.list_transcript_segments("meeting-1")["segments"][0]
        assert paged == projected
        from fastapi.testclient import TestClient
        client = TestClient(app)
        page = client.get("/v2/meetings/meeting-1/transcript?limit=500").json()["segments"][0]
        snapshot = client.get("/v2/meetings/meeting-1/snapshot").json()["segments"][0]
        assert page["correction_status"] == snapshot["correction_status"] == "failed_preserved_original"
        assert page["correction_error_class"] == snapshot["correction_error_class"] == "correction_not_evaluated"
        assert persistence._conn.execute("SELECT correction_status FROM transcript_segments WHERE meeting_id='meeting-1'").fetchone()[0] == "processing"
    finally:
        persistence.close()


def test_cancelling_old_job_does_not_settle_an_active_replacement(monkeypatch, tmp_path):
    app, persistence, job, _, now_ms = _app_with_correction(monkeypatch, tmp_path)
    try:
        # Seed a second pending durable job covering the exact same input.
        with persistence._write_transaction():
            row = dict(persistence._conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
            row.update(id="replacement-correction", idempotency_key="replacement-correction", status="pending", lease_owner=None, lease_until_ms=None)
            columns = list(row)
            persistence._conn.execute("INSERT INTO jobs (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", [row[key] for key in columns])
        persistence.cancel_job(job_id=job["id"], worker_id="correction-truth-test", now_ms=now_ms + 2, error_class="evidence_superseded")
        segment = persistence.get_snapshot("meeting-1")["segments"][0]
        assert segment["correction_status"] == "processing"
        assert persistence.list_transcript_segments("meeting-1")["segments"][0]["correction_status"] == "processing"
        assert any(item["kind"] == "correction" and item["status"] == "pending" for item in persistence.list_jobs(meeting_id="meeting-1"))
    finally:
        persistence.close()
