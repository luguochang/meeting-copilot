"""Native L2 corrections of ACK-gated online finals; no real model/ASR calls."""

import time

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import app as app_module, asr_correct, asr_refiner
from meeting_copilot_web_mvp.app import (
    CorrectionProviderTerminalDegraded,
    CorrectionResultRejected,
    create_app,
)


RAW = "接口先恢度 5%，如果错误率超过 0.1% 就回滚，张三今天补回滚脚本并确认监控阈值。"
CORRECTED = RAW.replace("恢度", "灰度")
WORKER = "resource-l2-test"


def _native_correction(monkeypatch, tmp_path, *, reason, reasons, event_changes=None, segment_count=1, anchor_seq=None):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-test")
    monkeypatch.delenv("LLM_GATEWAY_CORRECTION_MODEL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    app = create_app(data_dir=tmp_path, prewarm_funasr=False, semantic_projection_mode="llm_first")
    event = {
        "event_type": "final", "segment_id": "s1", "text": RAW, "normalized_text": RAW,
        "start_ms": 300, "end_ms": 15_100, "received_at_ms": 15_100,
        "is_final": True, "authoritative": True, "final_source": "local_realtime_online_final",
        "refinement_status": "unavailable" if reason == asr_refiner.WARMING_REFINEMENT_REASON else "failed",
        "refinement_reason": reason,
    }
    event.update(event_changes or {})
    # Keep a committed final to prove that eligibility does not come merely
    # from an existing durable transcript row when the ASR source is unsafe.
    events = []
    for index in range(1, segment_count + 1):
        current = dict(event)
        if segment_count > 1:
            current.update(segment_id=f"s{index}", text=f"第{index}个" + RAW, normalized_text=f"第{index}个" + RAW,
                           start_ms=index * 15_000, end_ms=(index + 1) * 15_000, received_at_ms=(index + 1) * 15_000)
        events.append(current)
        app.state.commit_v2_final("meeting-1", {**current, "event_type": "final"})
    app.state.asr_live_repository.create({
        "session_id": "meeting-1", "source": "live_asr_stream", "trace_kind": "live_event",
        "provider": "funasr_realtime", "provider_mode": "real", "is_mock": False,
        "input_source": "browser_live_mic", "degradation_reasons": reasons,
        "events": app_module.build_asr_live_events(
            session_id="meeting-1", provider="funasr_realtime", streaming_events=events, is_mock=False,
        ),
    })
    persistence = app.state.v2_persistence
    now_ms = time.time_ns() // 1_000_000
    persistence.end_meeting(meeting_id="meeting-1", now_ms=now_ms)
    if anchor_seq is not None:
        # A native queue claim of an older anchor must not publish later facts.
        with persistence._write_transaction():
            persistence._conn.execute("UPDATE jobs SET priority = 0 WHERE kind = 'correction'")
            persistence._conn.execute("UPDATE jobs SET priority = 100 WHERE kind = 'correction' AND input_transcript_seq = ?", (anchor_seq,))
    job = persistence.claim_next_job(worker_id=WORKER, lane="correction", now_ms=now_ms + 1, lease_ms=30_000)
    assert job is not None and job["evidence_segment_id"] == f"s{anchor_seq or 1}"
    assert TestClient(app).get("/settings").json()["asr"]["l2_correction_enabled"] is True
    return app, persistence, job


@pytest.mark.parametrize("reason,reasons", [
    (asr_refiner.WARMING_REFINEMENT_REASON, [asr_refiner.WARMING_ONLINE_FINAL_REASON]),
    ("asr_capacity_unavailable", [asr_refiner.RESOURCE_GUARD_ONLINE_FINAL_REASON, "offline_refinement_unavailable"]),
    ("asr_resource_guard_triggered", [asr_refiner.RESOURCE_GUARD_ONLINE_FINAL_REASON]),
    ("asr_cold_start_busy", [asr_refiner.RESOURCE_GUARD_ONLINE_FINAL_REASON]),
    ("asr_capacity_unavailable", [asr_refiner.WARMING_ONLINE_FINAL_REASON,
                                  asr_refiner.RESOURCE_GUARD_ONLINE_FINAL_REASON, "offline_refinement_unavailable"]),
])
def test_native_durable_l2_corrects_closed_online_source_and_keeps_raw(monkeypatch, tmp_path, reason, reasons):
    app, persistence, job = _native_correction(monkeypatch, tmp_path, reason=reason, reasons=reasons)
    calls = []

    def correct(raw, _config, **_kwargs):
        calls.append(raw)
        return raw.replace("恢度", "灰度"), {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}, False

    monkeypatch.setattr(asr_correct, "correct_transcript", correct)
    try:
        output = app.state.v2_correction_job_handler_impl(job)
        assert len(calls) == 1
        assert persistence.complete_job(job_id=job["id"], worker_id=WORKER,
                                        now_ms=time.time_ns() // 1_000_000, output=output)
        client = TestClient(app)
        snapshot = client.get("/v2/meetings/meeting-1/snapshot").json()
        segment = snapshot["segments"][0]
        assert segment["text"] == RAW
        assert segment["normalized_text"] == CORRECTED
        assert segment["correction_status"] == "changed"
        assert segment["revision"] == 2
        assert segment["correction_before_text"] == RAW
        assert segment["correction_after_text"] == CORRECTED
        assert "灰度" in " ".join(p["text"] for p in snapshot["semantic_paragraphs"])
        paged = client.get("/v2/meetings/meeting-1/transcript").json()["segments"][0]
        assert paged == segment
        source = app.state.asr_live_repository.get("meeting-1")
        originals = [e for e in source["events"] if e["event_type"] == "transcript_final"]
        assert originals[0]["payload"]["text"] == RAW
        assert source["realtime_transcript_correction"]["status"] == "completed"
        assert any(e["event_type"] == "transcript_revision" for e in source["events"])
    finally:
        for repository in app.state.sqlite_repositories:
            repository.close()


@pytest.mark.parametrize("changes,reason", [
    ({"event_type": "partial", "is_final": False, "authoritative": False}, asr_refiner.WARMING_REFINEMENT_REASON),
    ({"authoritative": False}, asr_refiner.WARMING_REFINEMENT_REASON),
    ({"event_type": "partial", "is_final": False}, "asr_capacity_unavailable"),
    ({"final_source": "recognition_draft"}, "asr_capacity_unavailable"),
    ({"end_ms": 300}, asr_refiner.WARMING_REFINEMENT_REASON),
    ({}, "asr_transport_error"),
    ({}, "asr_components_missing"),
])
def test_native_l2_does_not_bypass_bad_source(monkeypatch, tmp_path, changes, reason):
    app, persistence, job = _native_correction(
        monkeypatch, tmp_path, reason=reason,
        reasons=[asr_refiner.WARMING_ONLINE_FINAL_REASON, asr_refiner.RESOURCE_GUARD_ONLINE_FINAL_REASON],
        event_changes=changes,
    )
    calls = []
    monkeypatch.setattr(asr_correct, "correct_transcript", lambda *_a, **_kw: calls.append(True))
    try:
        with pytest.raises(CorrectionProviderTerminalDegraded) as caught:
            app.state.v2_correction_job_handler_impl(job)
        assert caught.value.durable_error_class == "degraded_asr_session"
        assert calls == []
        assert persistence.list_transcript_segments("meeting-1")["segments"][0]["normalized_text"] == RAW
    finally:
        for repository in app.state.sqlite_repositories:
            repository.close()


def test_resource_final_still_rejects_changed_numeric_fact(monkeypatch, tmp_path):
    app, persistence, job = _native_correction(
        monkeypatch, tmp_path, reason="asr_capacity_unavailable",
        reasons=[asr_refiner.WARMING_ONLINE_FINAL_REASON, asr_refiner.RESOURCE_GUARD_ONLINE_FINAL_REASON],
    )
    monkeypatch.setattr(asr_correct, "correct_transcript", lambda raw, *_a, **_kw: (
        raw.replace("恢度", "灰度").replace("5%", "50%"), {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}, False,
    ))
    try:
        with pytest.raises(CorrectionResultRejected) as caught:
            app.state.v2_correction_job_handler_impl(job)
        assert caught.value.durable_error_class == "correction_meeting_fact_changed"
        assert persistence.get_snapshot("meeting-1")["segments"][0]["normalized_text"] == RAW
        source = app.state.asr_live_repository.get("meeting-1")
        assert not any(e["event_type"] == "transcript_revision" for e in source["events"])
    finally:
        for repository in app.state.sqlite_repositories:
            repository.close()


def test_native_later_anchor_replays_paid_nine_segment_batch_without_widening_first_fence(monkeypatch, tmp_path):
    app, persistence, first_job = _native_correction(
        monkeypatch, tmp_path, reason="asr_capacity_unavailable",
        reasons=[asr_refiner.WARMING_ONLINE_FINAL_REASON, asr_refiner.RESOURCE_GUARD_ONLINE_FINAL_REASON],
        segment_count=9, anchor_seq=4,
    )
    calls = []

    def correct(raw, _config, **_kwargs):
        calls.append(raw)
        return raw.replace("恢度", "灰度"), {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}, False

    monkeypatch.setattr(asr_correct, "correct_transcript", correct)
    try:
        first = app.state.v2_correction_job_handler_impl(first_job)
        assert first["status"]["processed_segment_count"] == 9
        assert first["v2_reconciliation"]["segment_ids"] == ["s1", "s2", "s3", "s4"]
        assert persistence.complete_job(job_id=first_job["id"], worker_id=WORKER,
                                        now_ms=time.time_ns() // 1_000_000, output=first)
        after_first = persistence.list_transcript_segments("meeting-1")["segments"]
        assert [s["correction_status"] for s in after_first[:4]] == ["changed"] * 4
        assert [s["correction_status"] for s in after_first[4:]] == ["pending"] * 5
        assert all("恢度" in s["normalized_text"] for s in after_first[4:])
        assert all(job["status"] == "pending" for job in persistence.list_jobs(meeting_id="meeting-1", lane="correction")
                   if int(job["input_transcript_seq"]) > 4)
        with persistence._write_transaction():
            persistence._conn.execute("UPDATE jobs SET priority = 100 WHERE kind = 'correction' AND input_transcript_seq = 9")
        next_job = persistence.claim_next_job(worker_id=WORKER, lane="correction",
                                              now_ms=time.time_ns() // 1_000_000 + 1, lease_ms=30_000)
        assert next_job is not None and next_job["input_transcript_seq"] == 9
        replay = app.state.v2_correction_job_handler_impl(next_job)
        assert replay["called"] is False
        assert replay["gate"]["reason"] == "no_unrevised_final"
        assert replay["v2_reconciliation"]["revision_count"] == 9
        assert replay["v2_reconciliation"]["event_count"] == 5
        assert persistence.complete_job(job_id=next_job["id"], worker_id=WORKER,
                                        now_ms=time.time_ns() // 1_000_000, output=replay)
        snapshot = TestClient(app).get("/v2/meetings/meeting-1/snapshot").json()
        assert len(calls) == 1
        assert len(snapshot["segments"]) == 9
        assert all(s["correction_status"] == "changed" and s["revision"] == 2 for s in snapshot["segments"])
        assert all("恢度" in s["text"] and "灰度" in s["normalized_text"] for s in snapshot["segments"])
    finally:
        for repository in app.state.sqlite_repositories:
            repository.close()
