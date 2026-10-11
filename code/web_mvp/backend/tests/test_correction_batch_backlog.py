"""Real backlog reproductions using local persistence and injected providers only."""

import asyncio
import time

import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.v2_persistence import transcript_evidence_hash


def _fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-test")
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    events = []
    for number in range(1, 5):
        text = f"第{number}段讨论需要保留未经确认的原话。"
        event = {"event_type": "final", "segment_id": f"s{number}",
                 "text": text, "normalized_text": text,
                 "start_ms": number * 1_000, "end_ms": number * 1_000 + 900}
        app.state.commit_v2_final("meeting-1", event)
        events.append(event)
    app.state.commit_v2_final("meeting-2", events[1])
    app.state.asr_live_repository.create({
        "session_id": "meeting-1", "source": "live_asr_stream", "trace_kind": "live_event",
        "provider": "funasr_realtime", "provider_mode": "real", "is_mock": False,
        "input_source": "browser_live_mic", "asr_fallback_used": False,
        "degradation_reasons": [],
        "events": [{**event, "event_type": "transcript_final"} for event in events],
    })
    persistence = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    job = persistence.claim_next_job(worker_id="backlog-test", lane="correction",
                                     now_ms=now, lease_ms=60_000)
    assert job["evidence_segment_id"] == "s1"
    hashes = {event["segment_id"]: transcript_evidence_hash(event["segment_id"], event["text"])
              for event in events}
    return app, persistence, job, hashes, now


def _reconcile(persistence, job, hashes, now):
    return persistence.reconcile_terminal_correction_batch(
        meeting_id="meeting-1", batch_id="rtc_batch_test", error_class="provider_timeout",
        expected_evidence_hashes=hashes, completed_at_ms=now + 1,
        causation_job_id=job["id"], expected_lease_owner=job["lease_owner"], now_ms=now + 2,
    )


def _segment(persistence, meeting_id, segment_id):
    return next(row for row in persistence.list_transcript_segments(meeting_id)["segments"]
                if row["segment_id"] == segment_id)


def test_future_batch_failure_settles_only_exact_targets_and_retains_current_lease(monkeypatch, tmp_path):
    app, persistence, job, hashes, now = _fixture(monkeypatch, tmp_path)
    try:
        result = _reconcile(persistence, job, {"s2": hashes["s2"]}, now)
        target = _segment(persistence, "meeting-1", "s2")
        assert result["segment_ids"] == ["s2"]
        assert target["correction_status"] == "failed_preserved_original"
        assert target["correction_error_class"] == "provider_timeout"
        assert target["revision"] == 1 and target["text"] == target["normalized_text"]
        assert _segment(persistence, "meeting-1", "s1")["correction_status"] == "processing"
        assert _segment(persistence, "meeting-1", "s3")["correction_status"] == "pending"
        assert _segment(persistence, "meeting-2", "s2")["correction_status"] == "pending"
        current = persistence.get_job(job["id"])
        assert current["status"] == "running" and current["lease_owner"] == "backlog-test"
        settled = next(row for row in persistence.list_jobs(meeting_id="meeting-1", lane="correction")
                       if row["evidence_segment_id"] == "s2")
        assert settled["status"] == "failed" and settled["attempts"] == 0
        assert settled["output"]["called"] is False
        assert settled["output"]["provider_attempt_count"] == 0
        assert settled["output"]["failure_provenance"] == "shared_correction_batch"
        assert settled["output"]["batch_id"] == "rtc_batch_test"
        # Idempotent reconciliation does not manufacture another result event.
        assert _reconcile(persistence, job, {"s2": hashes["s2"]}, now)["settled_job_count"] == 0
    finally:
        persistence.close()


@pytest.mark.parametrize("guard", ["hash", "revision", "no_change", "cancelled", "expired", "new_repair", "current_new_repair"])
def test_terminal_batch_projection_fails_closed_at_each_concurrency_guard(monkeypatch, tmp_path, guard):
    app, persistence, job, hashes, now = _fixture(monkeypatch, tmp_path)
    try:
        with persistence._write_transaction():
            if guard == "hash":
                persistence._conn.execute("UPDATE transcript_segments SET evidence_hash='new-hash' WHERE meeting_id='meeting-1' AND segment_id='s2'")
            elif guard == "revision":
                persistence._conn.execute("UPDATE transcript_segments SET revision=2, correction_status='changed' WHERE meeting_id='meeting-1' AND segment_id='s2'")
            elif guard == "no_change":
                persistence._conn.execute("UPDATE transcript_segments SET correction_status='no_change' WHERE meeting_id='meeting-1' AND segment_id='s2'")
            elif guard == "cancelled":
                persistence._conn.execute("UPDATE jobs SET status='cancelled', lease_owner=NULL WHERE id=?", (job["id"],))
            elif guard == "expired":
                persistence._conn.execute("UPDATE jobs SET lease_until_ms=? WHERE id=?", (now, job["id"]))
            elif guard == "new_repair":
                persistence._conn.execute("UPDATE jobs SET created_at_ms=? WHERE meeting_id='meeting-1' AND kind='correction' AND evidence_segment_id='s2'", (now + 10,))
            else:
                persistence._conn.execute("UPDATE jobs SET created_at_ms=? WHERE id=?", (now + 10, job["id"]))
        before = _segment(persistence, "meeting-1", "s2")
        result = _reconcile(persistence, job, {"s2": hashes["s2"]}, now)
        assert result["segment_ids"] == [] and result["settled_job_count"] == 0
        assert _segment(persistence, "meeting-1", "s2") == before
    finally:
        persistence.close()


@pytest.mark.parametrize("history", ["valid_legacy", "frozen", "missing_audit", "hash_mismatch", "conflicting_final", "only_one_failure"])
def test_old_terminal_anchor_does_not_trigger_an_unrelated_paid_batch(monkeypatch, tmp_path, history):
    app, persistence, job, hashes, now = _fixture(monkeypatch, tmp_path)
    calls = []
    try:
        def history_record(record):
            audits = [{"batch_id": f"rtc_batch_old{number}", "status": "provider_failed",
                       "segment_ids": ["s1"], "error_code": "provider_timeout",
                       "completed_at_ms": now - 10 + number}
                      for number in range(2)]
            if history == "missing_audit":
                audits = []
            if history == "only_one_failure":
                audits = audits[:1]
            if history in {"frozen", "hash_mismatch"}:
                audits[-1]["target_evidence_hashes"] = {"s1": hashes["s1"] if history == "frozen" else "wrong"}
                audits[-1]["terminal_segment_ids"] = ["s1"]
            events = record["events"]
            if history == "conflicting_final":
                events = [*events, {"event_type": "final", "segment_id": "s1", "text": "冲突原文。"}]
            return {**record, "events": events, "realtime_transcript_correction": {
                "terminal_failed_segment_ids": ["s1"], "processed_segment_ids": ["s1"],
                "failure_counts": {"s1": 2}, "batch_audits": audits,
            }}
        app.state.asr_live_repository.update("meeting-1", history_record)
        def provider_path(*args, **kwargs):
            calls.append(True)
            return {"session_id": "meeting-1", "called": True, "gate": {}, "status": {},
                    "transcript_revisions": [], "no_revision_segment_ids": []}
        app.state.run_asr_live_session_realtime_corrections_once = provider_path
        # Bypass the earlier realtime-priority window, as the observed old backlog does.
        output = app.state.v2_correction_job_handler_impl({**job, "created_at_ms": now - 60_000})
        if history in {"valid_legacy", "frozen"}:
            assert calls == [] and output["called"] is False
            assert output["gate"]["reason"] == "terminal_batch_already_audited"
            assert output["v2_reconciliation"]["failure_provenance"] == "shared_correction_batch"
            assert _segment(persistence, "meeting-1", "s1")["correction_status"] == "failed_preserved_original"
            assert _segment(persistence, "meeting-1", "s2")["correction_status"] == "pending"
        else:
            assert calls == [True]
            assert _segment(persistence, "meeting-1", "s1")["correction_status"] == "processing"
    finally:
        persistence.close()


def test_native_failure_freezes_target_hashes_and_projects_only_second_actual_failure(monkeypatch, tmp_path):
    app, persistence, job, hashes, now = _fixture(monkeypatch, tmp_path)
    calls = []
    try:
        async def failing_provider(*args, **kwargs):
            calls.append(True)
            error = llm_service.LlmProviderTransportError("timeout")
            error.timeout_origin = "read"
            raise error
        monkeypatch.setattr(app_module.asr_correct, "async_correct_transcript", failing_provider)
        old_job = {**job, "created_at_ms": now - 60_000, "attempts": 2}
        with pytest.raises(app_module.RealtimeCorrectionProviderError) as first:
            asyncio.run(app.state.v2_correction_job_handler_async_impl(old_job))
        assert first.value.retryable is True
        assert first.value.failed_batch["expected_evidence_hashes"] == hashes
        assert first.value.failed_batch["terminal_segment_ids"] == []
        assert _segment(persistence, "meeting-1", "s2")["correction_status"] == "pending"
        with pytest.raises(app_module.RealtimeCorrectionProviderError) as second:
            asyncio.run(app.state.v2_correction_job_handler_async_impl(old_job))
        assert second.value.retryable is False
        assert second.value.failed_batch["terminal_segment_ids"] == ["s1", "s2", "s3", "s4"]
        assert len(calls) == 2
        for number in range(1, 5):
            assert _segment(persistence, "meeting-1", f"s{number}")["correction_status"] == "failed_preserved_original"
        assert _segment(persistence, "meeting-2", "s2")["correction_status"] == "pending"
        audit = app.state.asr_live_repository.get("meeting-1")["realtime_transcript_correction"]["batch_audits"][-1]
        assert audit["target_evidence_hashes"] == hashes
        assert audit["provider_timeout_origin"] == "read"
        assert audit["usage_known"] is False
        output = app.state.v2_correction_job_handler_impl(old_job)
        assert output["called"] is False and len(calls) == 2
    finally:
        persistence.close()
