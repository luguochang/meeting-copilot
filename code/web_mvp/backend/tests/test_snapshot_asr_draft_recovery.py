"""Read-only HTTP recovery of owned, non-authoritative ASR previews."""

from __future__ import annotations

from contextlib import contextmanager
import json

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.asr_live_events import build_asr_live_events
from meeting_copilot_web_mvp.degradation_controller import DegradationController


MID = "public-snapshot-draft"


@contextmanager
def snapshot_app(tmp_path, monkeypatch, *, mode="llm_first"):
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda cls: None))
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", "")
    app = app_module.create_app(data_dir=tmp_path, semantic_projection_mode=mode)
    app.state.v2_persistence.create_meeting(meeting_id=MID, title="公开草稿测试", now_ms=1_000)
    # No lifespan: GETs cannot start timers, model workers or device capture.
    client = TestClient(app)
    try:
        yield app, client
    finally:
        client.close()
        app.state.v2_persistence.close()
        app.state.asr_live_repository.close()
        app.state.session_repository.close()


def partial(*, segment="microphone:public-draft", text="公开测试：讨论恢复草稿。", start=1_000,
            end=3_000, track="microphone", epoch=2, status="failed", reason="asr_resource_budget_insufficient"):
    return {"event_type": "partial", "segment_id": segment, "text": text,
            "start_ms": start, "end_ms": end, "received_at_ms": end,
            "source_track": track, "capture_epoch": epoch, "authoritative": False,
            "final_source": "online_terminal_partial", "refinement_status": status,
            "refinement_reason": reason}


def live_record(app, raw_events=(), *, meeting_id=MID, reasons=(), **extra):
    events = build_asr_live_events(session_id=meeting_id, provider="public_test_asr",
                                  streaming_events=list(raw_events), is_mock=False)
    record = {"session_id": meeting_id, "provider": "public_test_asr", "is_mock": False,
              "provider_mode": "real", "input_source": "browser_live_mic",
              "audio_source": "browser_live_mic", "events": events,
              "degradation_reasons": list(reasons), **extra}
    app.state.asr_live_repository.create(record)
    return record


def snapshot(app, client, *, segment_limit=500):
    before = app.state.v2_persistence.list_jobs(meeting_id=MID)
    segments_before = app.state.v2_persistence.list_transcript_segments(MID, limit=1_000)
    response = client.get(f"/v2/meetings/{MID}/snapshot", params={"segment_limit": segment_limit})
    assert response.status_code == 200, response.text
    assert app.state.v2_persistence.list_jobs(meeting_id=MID) == before
    assert app.state.v2_persistence.list_transcript_segments(MID, limit=1_000) == segments_before
    return response.json()


def capability(body):
    return body["runtime"]["ai"]["capabilities"]["transcript"]


def final(app, *, segment="microphone:public-draft", end=3_000, text="正式公开测试文字。"):
    result = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id=MID, final_id=f"final:{segment}", segment_id=segment,
        text=text, normalized_text=text, started_at_ms=1_000, ended_at_ms=end,
        source_track="microphone", evidence_hash=f"hash:{segment}", now_ms=4_000,
    )
    # A completed correction is a real persisted projection precondition.
    app.state.v2_persistence.mark_correction_segments_no_change(
        meeting_id=MID, segment_ids=[segment], now_ms=4_100,
        max_input_transcript_seq=app.state.v2_persistence.get_transcript_segment(MID, segment)["transcript_seq"],
    )
    return result


@pytest.mark.parametrize("has_record,mode", [(False, "llm_first"), (True, "llm_first"), (False, "legacy")])
def test_empty_transcript_waits_and_never_reports_stable(tmp_path, monkeypatch, has_record, mode):
    with snapshot_app(tmp_path, monkeypatch, mode=mode) as (app, client):
        if has_record:
            live_record(app)
        body = snapshot(app, client)
        assert body["active_partial"] is None
        assert capability(body)["state"] == "idle"
        assert capability(body)["label"] == "等待正式文字"
        assert not app.state.v2_persistence.list_jobs(meeting_id=MID)


def test_failed_terminal_preview_recovers_owned_provenance_without_final_or_jobs(tmp_path, monkeypatch):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        live_record(app, [partial()], reasons=["offline_refinement_unavailable"])
        body = snapshot(app, client)
        draft = body["active_partial"]
        assert draft is not None
        assert draft["meeting_id"] == MID
        assert draft["segment_id"] == "microphone:public-draft"
        assert draft["source_event_id"] == "transcript_partial:microphone:public-draft"
        assert draft["source_track"] == "microphone"
        assert draft["capture_epoch"] == 2
        assert draft["started_at_ms"] == 1_000
        assert draft["ended_at_ms"] == 3_000
        assert draft["authoritative"] is False and draft["is_final"] is False
        assert draft["source"] == "own_asr_live_record"
        assert capability(body)["state"] == "paused"
        assert capability(body)["label"] == "识别精修受阻，草稿已保留"
        assert capability(body)["error_class"] == "asr_resource_budget_insufficient"
        assert "正式" in capability(body)["detail"]
        assert body["segments"] == [] and body["jobs"] == []
        assert snapshot(app, client)["active_partial"] == draft


def test_latest_preview_wins_and_global_or_other_meeting_failure_is_not_used(tmp_path, monkeypatch):
    controller = DegradationController()
    controller.set_level(3, "asr_sidecar_crashed: old-other-meeting")
    monkeypatch.setattr(app_module, "get_degradation_controller", lambda: controller)
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        live_record(app, [partial()], meeting_id="another-public-meeting")
        assert capability(snapshot(app, client))["state"] == "idle"
        live_record(app, [partial(text="旧草稿。"), partial(text="更新的公开草稿。", end=4_000,
                                                        status="", reason="")])
        body = snapshot(app, client)
        assert body["active_partial"]["text"] == "更新的公开草稿。"
        assert capability(body)["state"] == "busy"
        assert capability(body)["label"] == "识别草稿待确认"


def test_formal_coverage_hides_preview_even_outside_requested_page(tmp_path, monkeypatch):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        final(app)
        final(app, segment="microphone:newer", end=7_000)
        live_record(app, [partial()], reasons=["offline_refinement_unavailable"])
        body = snapshot(app, client, segment_limit=1)
        assert body["active_partial"] is None
        assert capability(body)["label"] == "精修已稳定"
        assert capability(body).get("error_class") is None


def test_new_tail_not_fully_covered_by_committed_segment_is_kept(tmp_path, monkeypatch):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        final(app, end=2_000)
        live_record(app, [partial(end=4_000)])
        body = snapshot(app, client)
        assert body["active_partial"]["ended_at_ms"] == 4_000
        assert capability(body)["state"] == "paused"


def test_resolved_refinement_does_not_keep_historical_failure_state(tmp_path, monkeypatch):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        final(app)
        raw = {**partial(), "event_type": "final", "authoritative": True,
               "final_source": "local_offline_refinement", "refinement_status": "refined",
               "refinement_reason": ""}
        live_record(app, [partial(), raw], reasons=["offline_refinement_unavailable"])
        body = snapshot(app, client)
        assert body["active_partial"] is None
        assert capability(body)["state"] == "active"
        assert capability(body)["label"] == "精修已稳定"


def test_ended_meeting_keeps_draft_but_never_claims_active_recognition_or_recording(tmp_path, monkeypatch):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        live_record(app, [partial()])
        app.state.v2_persistence.end_meeting(meeting_id=MID, now_ms=5_000)
        body = snapshot(app, client)
        assert body["active_partial"]["terminal"] is True
        assert capability(body)["state"] == "paused"
        assert capability(body)["label"] == "本次识别已结束，草稿待处理"
        assert body["runtime"]["recording"]["state"] != "active"
        assert body["runtime"]["phase"] == "ended"


@pytest.mark.parametrize("mutation", [
    {"text": ""}, {"text": 123}, {"authoritative": True}, {"is_final": True},
    {"start_ms": -1}, {"end_ms": 1_000}, {"end_ms": True},
    {"source_track": "different-unknown-track"}, {"capture_epoch": True},
])
def test_malformed_or_authoritative_partial_never_becomes_a_draft(tmp_path, monkeypatch, mutation):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        record = live_record(app, [partial()])
        record["events"][0]["payload"].update(mutation)
        app.state.asr_live_repository.replace(record)
        body = snapshot(app, client)
        assert body["active_partial"] is None
        assert capability(body)["label"] == "等待正式文字"


def test_preview_and_failure_details_are_bounded_and_do_not_expose_raw_errors(tmp_path, monkeypatch):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        live_record(app, [partial(text="公开" * 10_000, reason="C:/PRIVATE/path raw-error secret")])
        body = snapshot(app, client)
        assert len(body["active_partial"]["text"]) <= 4_000
        assert body["active_partial"]["text_truncated"] is True
        assert body["active_partial"]["source_text_characters"] == 20_000
        assert capability(body)["state"] == "paused"
        assert "PRIVATE" not in json.dumps(body, ensure_ascii=False)


def test_record_identity_mismatch_is_ignored_without_other_meeting_content(tmp_path, monkeypatch):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        record = live_record(app, [partial()], meeting_id="other-public-identity")
        original_get = app.state.asr_live_repository.get
        monkeypatch.setattr(app.state.asr_live_repository, "get", lambda mid: record if mid == MID else original_get(mid))
        body = snapshot(app, client)
        assert body["active_partial"] is None
        assert capability(body)["label"] == "等待正式文字"


def test_own_failed_tail_backfill_without_final_has_actionable_blocked_state(tmp_path, monkeypatch):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        live_record(app, transcript_backfill={"status": "failed", "error_class": "RuntimeError"})
        body = snapshot(app, client)
        assert body["active_partial"] is None
        assert capability(body)["state"] == "paused"
        assert capability(body)["error_class"] == "asr_backfill_failed"


@pytest.mark.parametrize("reason,state,label", [
    ("offline_worker_warming_up", "busy", "精修模型加载中，草稿已保留"),
    ("offline_worker_prewarm_required", "paused", "精修待预热，草稿已保留"),
    ("offline_request_deadline_expired", "paused", "本轮精修已过期，草稿已保留"),
])
def test_owned_refiner_recovery_states_are_explicit_without_final(tmp_path, monkeypatch, reason, state, label):
    with snapshot_app(tmp_path, monkeypatch) as (app, client):
        live_record(app, [partial(reason=reason)])
        body = snapshot(app, client)
        assert body["active_partial"]["authoritative"] is False
        assert capability(body)["state"] == state
        assert capability(body)["label"] == label
        assert capability(body)["error_class"] == reason
        assert body["segments"] == [] and body["jobs"] == []
