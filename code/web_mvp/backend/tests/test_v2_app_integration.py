import asyncio
from fastapi.testclient import TestClient
import httpx
import json
import pytest
import re
import sqlite3
import struct
import threading
import time

from meeting_copilot_web_mvp.app import (
    _commit_v2_transcript_revisions,
    _live_asr_record_is_finalized,
    create_app,
)
from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp import realtime_transcript_correction
from meeting_copilot_web_mvp.audio_assets import RealtimeWavAssetWriter


def _final_event(segment_id: str = "segment-1", text: str = "需要确认发布负责人。"):
    return {
        "event_type": "final",
        "segment_id": segment_id,
        "text": text,
        "normalized_text": text,
        "start_ms": 100,
        "end_ms": 900,
    }


def _revision_event(
    *,
    segment_id: str,
    original_text: str,
    corrected_text: str,
    revision_id: str | None = None,
    policy_version: str | None = realtime_transcript_correction.POLICY_VERSION,
):
    correction = (
        {"policy_version": policy_version}
        if policy_version is not None
        else {}
    )
    return {
        "id": revision_id or f"transcript_revision:{segment_id}:rtc-v1",
        "event_type": "transcript_revision",
        "payload": {
            "segment_id": f"{segment_id}:rtc-v1",
            "supersedes_segment_id": segment_id,
            "revision_of": segment_id,
            "original_text": original_text,
            "normalized_text": corrected_text,
            "correction": correction,
        },
    }


def test_final_commit_creates_normalized_snapshot_and_two_durable_jobs(tmp_path):
    app = create_app(data_dir=tmp_path)

    first = app.state.commit_v2_final("meeting-1", _final_event())
    duplicate = app.state.commit_v2_final("meeting-1", _final_event())

    assert first["created"] is True
    assert duplicate["created"] is False
    snapshot = app.state.v2_persistence.get_snapshot("meeting-1")
    assert [segment["text"] for segment in snapshot["segments"]] == ["需要确认发布负责人。"]
    assert [job["kind"] for job in app.state.v2_persistence.list_jobs(meeting_id="meeting-1")] == [
        "correction",
        "suggestion",
    ]
    app.state.v2_persistence.close()


def test_correction_revision_commits_against_its_target_segment_and_replays_idempotently(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)
    first_text = "接口先恢度百分之五。"
    corrected_text = "接口先灰度百分之五。"
    app.state.commit_v2_final(
        "meeting-1",
        _final_event(segment_id="segment-1", text=first_text),
    )
    second = app.state.commit_v2_final(
        "meeting-1",
        _final_event(segment_id="segment-2", text="随后观察错误率。"),
    )
    revision = _revision_event(
        segment_id="segment-1",
        original_text=first_text,
        corrected_text=corrected_text,
    )

    committed = _commit_v2_transcript_revisions(
        app.state.v2_persistence,
        meeting_id="meeting-1",
        causation_job_id=second["job_ids"]["correction"],
        revisions=[revision],
        max_input_transcript_seq=second["transcript_seq"],
        now_ms=2_000,
    )
    replayed = _commit_v2_transcript_revisions(
        app.state.v2_persistence,
        meeting_id="meeting-1",
        causation_job_id=second["job_ids"]["correction"],
        revisions=[revision],
        max_input_transcript_seq=second["transcript_seq"],
        now_ms=2_100,
    )

    segments = app.state.v2_persistence.list_transcript_segments(
        "meeting-1",
        limit=10,
    )["segments"]
    assert committed == {
        "revision_count": 1,
        "event_count": 1,
        "segment_ids": ["segment-1"],
    }
    assert replayed == {
        "revision_count": 1,
        "event_count": 0,
        "segment_ids": ["segment-1"],
    }
    assert [(segment["segment_id"], segment["revision"]) for segment in segments] == [
        ("segment-1", 2),
        ("segment-2", 1),
    ]
    assert segments[0]["normalized_text"] == corrected_text
    assert [
        event["type"]
        for event in app.state.v2_persistence.list_events("meeting-1")
        if event["type"] == "transcript.segment.revised"
    ] == ["transcript.segment.revised"]
    app.state.v2_persistence.close()


def test_correction_reconciliation_only_accepts_current_policy_revisions(tmp_path):
    app = create_app(data_dir=tmp_path)
    original_text = "接口先恢度百分之五。"
    app.state.commit_v2_final(
        "meeting-1",
        _final_event(segment_id="segment-1", text=original_text),
    )

    reconciliation = _commit_v2_transcript_revisions(
        app.state.v2_persistence,
        meeting_id="meeting-1",
        causation_job_id="correction-job",
        revisions=[
            _revision_event(
                segment_id="segment-1",
                original_text=original_text,
                corrected_text="旧策略修正文本。",
                revision_id="transcript_revision:segment-1:old-policy",
                policy_version="realtime-transcript-correction.v0",
            ),
            _revision_event(
                segment_id="segment-1",
                original_text=original_text,
                corrected_text="无策略修正文本。",
                revision_id="transcript_revision:segment-1:no-policy",
                policy_version=None,
            ),
        ],
        max_input_transcript_seq=1,
        now_ms=2_000,
    )

    segment = app.state.v2_persistence.get_transcript_segment(
        "meeting-1",
        "segment-1",
    )
    assert reconciliation == {
        "revision_count": 0,
        "event_count": 0,
        "segment_ids": [],
    }
    assert segment["normalized_text"] == original_text
    assert segment["revision"] == 1
    app.state.v2_persistence.close()


def test_correction_retry_recovers_current_policy_revision_already_in_legacy_repo(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "team_gateway")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    app = create_app(data_dir=tmp_path)
    original_text = "接口先恢度百分之五。"
    corrected_text = "接口先灰度百分之五。"
    committed = app.state.commit_v2_final(
        "meeting-1",
        _final_event(segment_id="segment-1", text=original_text),
    )
    revision = _revision_event(
        segment_id="segment-1",
        original_text=original_text,
        corrected_text=corrected_text,
    )
    app.state.asr_live_repository.create(
        {
            "session_id": "meeting-1",
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "funasr_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "browser_live_mic",
            "ingest_mode": "live_asr_stream",
            "asr_fallback_used": False,
            "degradation_reasons": [],
            "events": [
                {
                    "id": "transcript_final:segment-1",
                    "event_type": "transcript_final",
                    "at_ms": 1_000,
                    "payload": {
                        "segment_id": "segment-1",
                        "text": original_text,
                        "normalized_text": original_text,
                        "start_ms": 100,
                        "end_ms": 900,
                    },
                },
                revision,
            ],
            "realtime_transcript_correction": {
                "policy_version": realtime_transcript_correction.POLICY_VERSION,
                "status": "completed",
                "processed_segment_ids": ["segment-1"],
                "revised_segment_ids": ["segment-1"],
            },
        }
    )
    persistence = app.state.v2_persistence
    job = persistence.get_job(committed["job_ids"]["correction"])
    real_commit = persistence.commit_transcript_revision

    def fail_v2_projection_once(**_kwargs):
        raise RuntimeError("injected V2 projection failure")

    monkeypatch.setattr(
        persistence,
        "commit_transcript_revision",
        fail_v2_projection_once,
    )
    with pytest.raises(RuntimeError, match="injected V2 projection failure"):
        app.state.v2_correction_job_handler_impl(job)

    monkeypatch.setattr(persistence, "commit_transcript_revision", real_commit)
    recovered = app.state.v2_correction_job_handler_impl(job)

    segment = persistence.get_transcript_segment("meeting-1", "segment-1")
    assert recovered["called"] is False
    assert recovered["gate"]["reason"] == "no_unrevised_final"
    assert recovered["provider_revision_count"] == 0
    assert recovered["v2_reconciliation"] == {
        "revision_count": 1,
        "event_count": 1,
        "segment_ids": ["segment-1"],
        "superseded_job_count": 0,
    }
    assert segment["normalized_text"] == corrected_text
    assert segment["revision"] == 2
    app.state.v2_persistence.close()


def test_correction_job_output_is_bounded_and_omits_cumulative_transcript_state(
    tmp_path,
    monkeypatch,
):
    app = create_app(data_dir=tmp_path)
    committed = app.state.commit_v2_final("meeting-1", _final_event())
    app.state.asr_live_repository.create(
        {
            "session_id": "meeting-1",
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "funasr_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "browser_live_mic",
            "events": [],
        }
    )
    segment_ids = [f"segment-{index}" for index in range(200)]
    monkeypatch.setattr(
        app.state,
        "run_asr_live_session_realtime_corrections_once",
        lambda *_args, **_kwargs: {
            "session_id": "meeting-1",
            "called": False,
            "gate": {
                "eligible": False,
                "reason": "no_unrevised_final",
                "final_events": [
                    {"payload": {"segment_id": segment_id, "text": "不应进入任务结果" * 100}}
                    for segment_id in segment_ids
                ],
                "segment_ids": segment_ids,
                "total_chars": 12_000,
                "elapsed_ms": 1_000,
                "retry_after_ms": 0,
                "oversized_segment_ids": [],
                "policy_version": realtime_transcript_correction.POLICY_VERSION,
            },
            "status": {
                "status": "completed",
                "policy_version": realtime_transcript_correction.POLICY_VERSION,
                "processed_segment_ids": segment_ids,
                "revised_segment_ids": segment_ids[:100],
                "batch_audits": [{"private_transcript": "不应进入任务结果" * 100}],
            },
            "revision_count": 0,
            "transcript_revisions": [],
            "no_revision_segment_ids": [],
        },
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["correction"])

    output = app.state.v2_correction_job_handler_impl(job)

    assert "final_events" not in output["gate"]
    assert "segment_ids" not in output["gate"]
    assert output["gate"]["segment_count"] == 200
    assert "processed_segment_ids" not in output["status"]
    assert "revised_segment_ids" not in output["status"]
    assert "batch_audits" not in output["status"]
    assert output["status"]["processed_segment_count"] == 200
    assert output["status"]["revised_segment_count"] == 100
    serialized = json.dumps(output, ensure_ascii=False)
    assert "不应进入任务结果" not in serialized
    assert len(serialized) < 2_048
    app.state.v2_persistence.close()


def test_semantic_quality_blocked_suggestion_waits_for_same_segment_correction(
    tmp_path,
):
    async def scenario():
        app = create_app(data_dir=tmp_path)
        raw_text = (
            "接口先灰度百分之五，P99 延迟超过九百毫秒就回滚。"
            " aror olderker laack reqzzxqv noizzz qqqq redster toymant"
        )
        committed = app.state.commit_v2_final(
            "meeting-1",
            _final_event(text=raw_text),
        )
        app.state.asr_live_repository.create(
            {
                "session_id": "meeting-1",
                "source": "live_asr_stream",
                "trace_kind": "live_event",
                "provider": "funasr_realtime",
                "provider_mode": "real",
                "is_mock": False,
                "input_source": "browser_live_mic",
                "degradation_reasons": ["asr_semantic_quality_blocked"],
                "asr_semantic_quality": {
                    "status": "blocked",
                    "blocker": "asr_semantic_quality_blocked",
                },
                "events": [
                    {
                        "id": "transcript_final:segment-1",
                        "event_type": "transcript_final",
                        "payload": {
                            "segment_id": "segment-1",
                            "text": raw_text,
                            "normalized_text": raw_text,
                        },
                    }
                ],
            }
        )
        now_ms = int(time.time() * 1_000)
        claimed = app.state.v2_persistence.claim_next_job(
            worker_id="suggestion-worker",
            lane="suggestion",
            now_ms=now_ms,
            lease_ms=60_000,
        )
        assert claimed is not None
        assert claimed["id"] == committed["job_ids"]["suggestion"]

        task = asyncio.create_task(app.state.v2_suggestion_job_handler_impl(claimed))
        await asyncio.sleep(0.05)
        assert not task.done()

        revised = app.state.v2_persistence.commit_transcript_revision(
            meeting_id="meeting-1",
            segment_id="segment-1",
            expected_evidence_hash=claimed["evidence_hash"],
            corrected_text="接口先灰度百分之五，P99 延迟超过九百毫秒就回滚。",
            revision_id="revision-1",
            now_ms=now_ms + 100,
            evidence_remap_reason="validated_meaning_preserved_correction",
        )
        assert revised is not None
        output = await asyncio.wait_for(task, timeout=1)

        assert output == {
            "generated_card_count": 0,
            "reason": "evidence_superseded_before_generation",
        }
        assert app.state.v2_persistence.get_snapshot("meeting-1")["suggestions"] == []
        assert app.state.v2_persistence.get_job(claimed["id"])["status"] == "cancelled"
        app.state.v2_persistence.close()

    asyncio.run(scenario())


def test_correction_batch_deferral_uses_typed_retry_instead_of_invalid_trace_stage(
    tmp_path,
    monkeypatch,
):
    app = create_app(data_dir=tmp_path)
    committed = app.state.commit_v2_final("meeting-1", _final_event())
    app.state.asr_live_repository.create(
        {
            "session_id": "meeting-1",
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "funasr_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "browser_live_mic",
            "events": [],
        }
    )
    monkeypatch.setattr(
        app.state,
        "run_asr_live_session_realtime_corrections_once",
        lambda *_args, **_kwargs: {
            "called": False,
            "gate": {
                "eligible": False,
                "reason": "batch_gate_closed",
                "segment_ids": ["segment-1"],
                "elapsed_ms": 1_000,
            },
            "transcript_revisions": [],
            "no_revision_segment_ids": [],
        },
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["correction"])

    with pytest.raises(app_module.CorrectionBatchDeferred) as deferred:
        app.state.v2_correction_job_handler_impl(job)

    assert deferred.value.retry_after_ms == (
        realtime_transcript_correction.MIN_INTERVAL_MS - 1_000
    )
    app.state.v2_persistence.close()


def test_v2_snapshot_and_event_endpoints_use_normalized_tables(tmp_path):
    app = create_app(data_dir=tmp_path)
    app.state.commit_v2_final("meeting-1", _final_event())

    with TestClient(app) as client:
        snapshot = client.get("/v2/meetings/meeting-1/snapshot")
        events = client.get("/v2/meetings/meeting-1/events", params={"after_seq": 0})
        stream = client.get(
            "/v2/meetings/meeting-1/events",
            params={"after_seq": 0, "once": True},
            headers={"accept": "text/event-stream"},
        )

    assert snapshot.status_code == 200
    assert snapshot.json()["last_seq"] == events.json()["last_seq"]
    assert snapshot.json()["segments"][0]["segment_id"] == "segment-1"
    assert [job["kind"] for job in snapshot.json()["jobs"]] == [
        "correction",
        "suggestion",
    ]
    assert all("output" not in job and "evidence_hash" not in job for job in snapshot.json()["jobs"])
    assert snapshot.json()["current_topic"]["evidence_segment_ids"] == ["segment-1"]
    assert snapshot.json()["open_questions"][0]["status"] == "open"
    assert events.status_code == 200
    assert [event["type"] for event in events.json()["events"]][:3] == [
        "transcript.segment.finalized",
        "meeting.topic.updated",
        "meeting.open_question.updated",
    ]

    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" in stream.text
    assert "event: transcript.segment.finalized\n" in stream.text
    assert '"segment_id":"segment-1"' in stream.text


def test_v2_event_endpoint_returns_a_bounded_page_contract(tmp_path):
    app = create_app(data_dir=tmp_path)
    for index in range(2):
        app.state.commit_v2_final(
            "meeting-1",
            _final_event(
                segment_id=f"page-segment-{index}",
                text=f"第{index + 1}段会议内容。",
            ),
        )

    with TestClient(app) as client:
        first = client.get("/v2/meetings/meeting-1/events", params={"limit": 2})
        invalid = client.get("/v2/meetings/meeting-1/events", params={"limit": 1_001})

    assert first.status_code == 200
    body = first.json()
    assert len(body["events"]) == 2
    assert body["has_more"] is True
    assert body["next_after_seq"] == body["events"][-1]["seq"]
    assert invalid.status_code == 422


def test_end_waits_for_live_asr_finalization_before_ending_and_scheduling_review(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)
    persistence = app.state.v2_persistence
    persistence.create_meeting(meeting_id="meeting-1", title=None, now_ms=1_000)
    app.state.asr_live_repository.create(
        {
            "session_id": "meeting-1",
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "funasr_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "browser_live_mic",
            "events": [],
        }
    )

    def finish_asr_stream():
        time.sleep(0.05)
        app.state.commit_v2_final("meeting-1", _final_event())

        def add_end_of_stream(record):
            return {
                **record,
                "events": [
                    *list(record.get("events") or []),
                    {
                        "id": "end-of-stream",
                        "event_type": "end_of_stream",
                        "at_ms": 900,
                        "payload": {},
                    },
                ],
            }

        app.state.asr_live_repository.update("meeting-1", add_end_of_stream)

    finalizer = threading.Thread(target=finish_asr_stream)
    finalizer.start()
    started_at = time.monotonic()
    end_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/v2/meetings/{meeting_id}/end"
    )
    response = end_route.endpoint(
        "meeting-1",
        {"action": "end_and_review"},
    )
    job_kinds = {
        job["kind"]
        for job in persistence.list_jobs(meeting_id="meeting-1")
    }
    finalizer.join(timeout=1)

    assert time.monotonic() - started_at >= 0.04
    snapshot = response["snapshot"]
    assert snapshot["runtime"]["phase"] == "ended"
    assert [segment["segment_id"] for segment in snapshot["segments"]] == [
        "segment-1"
    ]
    assert job_kinds >= {
        "correction",
        "suggestion",
        "minutes",
        "approach",
        "index",
    }


def test_end_accepts_normalized_evaluation_summary_as_live_asr_finalization():
    assert _live_asr_record_is_finalized(
        {
            "events": [
                {
                    "event_type": "evaluation_summary",
                    "payload": {"end_of_stream_event_count": 1},
                },
            ],
        }
    ) is True
    assert _live_asr_record_is_finalized(
        {
            "events": [
                {
                    "event_type": "evaluation_summary",
                    "payload": {"end_of_stream_event_count": 0},
                },
            ],
        }
    ) is False


def test_v2_snapshot_exposes_semantic_quality_pause_without_transcript_data(tmp_path):
    app = create_app(data_dir=tmp_path)
    persistence = app.state.v2_persistence
    persistence.create_meeting(meeting_id="meeting-quality", title=None, now_ms=1_000)
    app.state.asr_live_repository.create(
        {
            "session_id": "meeting-quality",
            "source": "live_asr_stream",
            "provider": "funasr_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "degradation_reasons": ["asr_semantic_quality_blocked", "private_text_should_not_escape"],
            "formal_derivation_status": "suppressed_by_asr_semantic_quality",
            "events": [],
        }
    )

    response = TestClient(app).get("/v2/meetings/meeting-quality/snapshot")

    assert response.status_code == 200
    diagnostics = response.json()["diagnostics"]
    assert diagnostics["formal_derivation_status"] == "suppressed_by_asr_semantic_quality"
    assert diagnostics["degradation_reasons"] == ["asr_semantic_quality_blocked"]
    assert "private_text_should_not_escape" not in str(response.json())


def test_app_lifecycle_runs_durable_jobs_without_browser_trigger(tmp_path):
    app = create_app(data_dir=tmp_path)
    seen: list[tuple[str, str]] = []

    def correction_handler(job):
        seen.append(("correction", job["id"]))
        return {"called": True, "transcript_revisions": []}

    def suggestion_handler(job):
        seen.append(("suggestion", job["id"]))
        return {"generated_card_count": 0, "runs": []}

    app.state.v2_correction_job_handler_impl = correction_handler
    app.state.v2_suggestion_job_handler_impl = suggestion_handler
    committed = app.state.commit_v2_final("meeting-1", _final_event())

    with TestClient(app):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            jobs = app.state.v2_persistence.list_jobs(meeting_id="meeting-1")
            if all(job["status"] == "succeeded" for job in jobs):
                break
            time.sleep(0.01)
        else:
            raise AssertionError(f"durable jobs did not complete: {jobs}")

        assert {lane for lane, _job_id in seen} == {"correction", "suggestion"}
        assert {job["status"] for job in jobs} == {"succeeded"}
        for job_id in committed["job_ids"].values():
            trace = app.state.pipeline_traces.export(job_id)
            assert "final_committed" in trace["stages"]
            assert "job_queued" in trace["stages"]
            assert "job_claimed" in trace["stages"]


def test_post_meeting_jobs_fail_closed_when_correction_is_terminally_failed(tmp_path):
    app = create_app(data_dir=tmp_path)
    committed = app.state.commit_v2_final("meeting-1", _final_event())
    persistence = app.state.v2_persistence
    now_ms = time.time_ns() // 1_000_000
    correction = persistence.claim_next_job(
        worker_id="test-correction",
        lane="correction",
        now_ms=now_ms,
        lease_ms=30_000,
    )
    assert correction is not None
    persistence.fail_job(
        job_id=correction["id"],
        worker_id="test-correction",
        now_ms=now_ms + 1,
        error_class="CorrectionProjectionFailed",
    )

    with pytest.raises(RuntimeError, match="blocked by transcript correction"):
        app.state.v2_post_job_handler_impls["minutes"](
            {
                "id": "minutes-job",
                "meeting_id": "meeting-1",
                "evidence_segment_id": committed["segment_id"],
            }
        )

    persistence.close()


def test_post_meeting_jobs_fail_closed_when_correction_is_cancelled(tmp_path):
    app = create_app(data_dir=tmp_path)
    committed = app.state.commit_v2_final("meeting-1", _final_event())
    persistence = app.state.v2_persistence
    cancelled = persistence.supersede_correction_jobs(
        meeting_id="meeting-1",
        segment_ids=[committed["segment_id"]],
        except_job_id="replacement-correction",
        now_ms=time.time_ns() // 1_000_000,
    )
    assert cancelled == 1

    with pytest.raises(RuntimeError, match="no successful replacement"):
        app.state.v2_post_job_handler_impls["minutes"](
            {
                "id": "minutes-job",
                "meeting_id": "meeting-1",
                "evidence_segment_id": committed["segment_id"],
            }
        )

    persistence.close()


def test_post_meeting_wait_accepts_superseded_correction_with_successful_replacement(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)
    first = app.state.commit_v2_final(
        "meeting-1",
        _final_event(segment_id="segment-1"),
    )
    second = app.state.commit_v2_final(
        "meeting-1",
        _final_event(segment_id="segment-2", text="继续确认回滚窗口。"),
    )
    persistence = app.state.v2_persistence
    now_ms = time.time_ns() // 1_000_000
    assert persistence.supersede_correction_jobs(
        meeting_id="meeting-1",
        segment_ids=[first["segment_id"]],
        except_job_id=second["job_ids"]["correction"],
        now_ms=now_ms,
    ) == 1
    replacement = persistence.claim_next_job(
        worker_id="replacement-correction",
        lane="correction",
        now_ms=now_ms + 1,
        lease_ms=30_000,
    )
    assert replacement is not None
    assert replacement["id"] == second["job_ids"]["correction"]
    persistence.complete_job(
        job_id=replacement["id"],
        worker_id="replacement-correction",
        now_ms=now_ms + 2,
        output={"no_revision_needed": True},
    )

    app.state.wait_for_v2_correction_jobs("meeting-1", timeout_seconds=0.1)

    persistence.close()


def test_post_meeting_jobs_fail_closed_when_transcript_has_no_correction_job(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)
    committed = app.state.commit_v2_final("meeting-1", _final_event())
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        connection.execute(
            "DELETE FROM jobs WHERE meeting_id = ? AND kind = 'correction'",
            ("meeting-1",),
        )

    with pytest.raises(RuntimeError, match="correction jobs are missing"):
        app.state.v2_post_job_handler_impls["minutes"](
            {
                "id": "minutes-job",
                "meeting_id": "meeting-1",
                "evidence_segment_id": committed["segment_id"],
            }
        )

    app.state.v2_persistence.close()


def test_later_correction_can_supersede_an_obsolete_failed_job(tmp_path):
    app = create_app(data_dir=tmp_path)
    committed = app.state.commit_v2_final("meeting-1", _final_event())
    persistence = app.state.v2_persistence
    now_ms = time.time_ns() // 1_000_000
    failed = persistence.claim_next_job(
        worker_id="failed-correction",
        lane="correction",
        now_ms=now_ms,
        lease_ms=30_000,
    )
    assert failed is not None
    persistence.fail_job(
        job_id=failed["id"],
        worker_id="failed-correction",
        now_ms=now_ms + 1,
        error_class="ReservationChanged",
    )

    superseded = persistence.supersede_correction_jobs(
        meeting_id="meeting-1",
        segment_ids=[committed["segment_id"]],
        except_job_id="replacement-correction",
        now_ms=now_ms + 2,
    )

    assert superseded == 1
    assert persistence.get_job(failed["id"])["status"] == "cancelled"
    assert persistence.get_job(failed["id"])["error_class"] == "evidence_superseded"
    persistence.close()


def test_v2_end_and_feedback_commands_persist_across_snapshot(tmp_path):
    app = create_app(data_dir=tmp_path)
    committed = app.state.commit_v2_final("meeting-1", _final_event())
    persistence = app.state.v2_persistence
    persistence.upsert_suggestion_draft(
        suggestion_id="suggestion-1",
        meeting_id="meeting-1",
        job_id=committed["job_ids"]["suggestion"],
        generation_id="generation-1",
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash=persistence.get_snapshot("meeting-1")["segments"][0]["evidence_hash"],
        state_revision=1,
        draft_text="建议确认发布负责人。",
        draft_seq=1,
        now_ms=2_000,
    )
    persistence.commit_suggestion(
        suggestion_id="suggestion-1",
        generation_id="generation-1",
        expected_evidence_hash=persistence.get_snapshot("meeting-1")["segments"][0]["evidence_hash"],
        final_draft_seq=1,
        text="建议确认发布负责人。",
        now_ms=2_100,
    )
    app.state.v2_correction_job_handler_impl = lambda job: {"job_id": job["id"]}
    app.state.v2_suggestion_job_handler_impl = lambda job: {"job_id": job["id"]}
    app.state.v2_post_job_handler_impls = {
        "minutes": lambda job: persistence.save_minutes(
            meeting_id=job["meeting_id"],
            job_id=job["id"],
            markdown="# 会议复盘\n\n已生成。",
            structured=None,
            degraded=False,
            now_ms=3_000,
        ),
        "approach": lambda job: persistence.save_approach_cards(
            meeting_id=job["meeting_id"],
            job_id=job["id"],
            cards=[{"suggestion_text": "保留回滚方案"}],
            degraded=False,
            now_ms=3_000,
        ),
        "index": lambda job: persistence.rebuild_search_document(
            meeting_id=job["meeting_id"],
            job_id=job["id"],
            now_ms=3_000,
        ),
    }

    with TestClient(app) as client:
        feedback = client.put(
            "/v2/meetings/meeting-1/suggestions/suggestion-1/feedback",
            json={"feedback": "kept"},
        )
        ended = client.post(
            "/v2/meetings/meeting-1/end",
            json={"action": "end_and_review"},
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            jobs = persistence.list_jobs(meeting_id="meeting-1")
            if all(job["status"] == "succeeded" for job in jobs):
                break
            time.sleep(0.01)
        snapshot = client.get("/v2/meetings/meeting-1/snapshot").json()

    assert feedback.status_code == 200
    assert feedback.json()["suggestion"]["feedback"] == "kept"
    assert ended.status_code == 200
    assert snapshot["runtime"]["phase"] == "ended"
    assert snapshot["suggestions"][0]["feedback"] == "kept"
    assert snapshot["minutes"]["markdown"].startswith("# 会议复盘")
    assert snapshot["approach_cards"][0]["suggestion_text"] == "保留回滚方案"
    assert snapshot["review"]["status"] == "ready"
    assert snapshot["review"]["indexed"] is True
    assert {kind: job["status"] for kind, job in snapshot["review_jobs"].items()} == {
        "minutes": "succeeded",
        "approach": "succeeded",
        "index": "succeeded",
    }
    assert {job["status"] for job in jobs} == {"succeeded"}


def test_backend_serves_built_v2_workbench_and_hashed_assets(tmp_path):
    app = create_app(data_dir=tmp_path)

    with TestClient(app) as client:
        page = client.get("/workbench")
        alias = client.get("/workbench-v2")
        legacy = client.get("/workbench-legacy")
        script_path = re.search(r'<script[^>]+src="([^"]+\.js)"', page.text)
        assert script_path is not None
        script = client.get(script_path.group(1))

    assert page.status_code == 200
    assert '<div id="root"></div>' in page.text
    assert "/workbench-assets/" in page.text
    assert alias.text == page.text
    assert "/static/workbench.js" in legacy.text
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]


def test_v2_delete_removes_normalized_facts_audio_and_keeps_audit(tmp_path):
    app = create_app(data_dir=tmp_path)
    app.state.v2_correction_job_handler_impl = lambda job: {"job_id": job["id"]}
    app.state.v2_suggestion_job_handler_impl = lambda job: {"job_id": job["id"]}
    app.state.commit_v2_final("meeting-1", _final_event())
    audio_dir = tmp_path / "audio_assets" / "meeting-1"
    audio_dir.mkdir(parents=True)
    (audio_dir / "audio.wav").write_bytes(b"RIFF-test")

    with TestClient(app) as client:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            jobs = app.state.v2_persistence.list_jobs(meeting_id="meeting-1")
            if all(job["status"] == "succeeded" for job in jobs):
                break
            time.sleep(0.01)
        deleted = client.delete("/v2/meetings/meeting-1")
        repeated = client.delete("/v2/meetings/meeting-1")
        meeting_exists = app.state.v2_persistence.meeting_exists("meeting-1")
        remaining_jobs = app.state.v2_persistence.list_jobs(meeting_id="meeting-1")
        deletion_jobs = app.state.v2_persistence.list_deletion_jobs()

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    deletion_job = deleted.json()["deletion_job"]
    assert deletion_job["status"] == "completed"
    assert deletion_job["attempts"] == 1
    assert repeated.status_code == 404
    assert not audio_dir.exists()
    assert meeting_exists is False
    assert remaining_jobs == []
    assert deletion_jobs == [deletion_job]


def test_v2_delete_cancels_active_capture_before_purging_files(tmp_path):
    app = create_app(data_dir=tmp_path)
    app.state.v2_persistence.create_meeting(
        meeting_id="active-delete",
        title=None,
        now_ms=1_000,
    )
    audio_dir = tmp_path / "audio_assets" / "active-delete"
    audio_dir.mkdir(parents=True)
    (audio_dir / "chunk.inprogress").write_bytes(b"active")
    capture_cancelled = asyncio.Event()

    async def run() -> None:
        async def active_capture() -> None:
            try:
                await asyncio.Future()
            finally:
                capture_cancelled.set()

        capture_task = asyncio.create_task(active_capture())
        app.state.active_v2_capture_tasks.setdefault("active-delete", set()).add(capture_task)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.delete("/v2/meetings/active-delete")

        assert response.status_code == 200
        assert capture_cancelled.is_set()
        assert capture_task.cancelled()

    asyncio.run(run())

    assert app.state.v2_persistence.is_meeting_tombstoned("active-delete") is True
    assert app.state.v2_persistence.meeting_exists("active-delete") is False
    assert not audio_dir.exists()
    app.state.v2_persistence.close()


@pytest.mark.parametrize(
    ("route", "expected_status"),
    (
        ("/live/asr/sessions/legacy-active-delete", 200),
        ("/sessions/legacy-active-delete", 204),
    ),
)
def test_legacy_delete_routes_share_capture_fence_and_prevent_recreation(
    tmp_path,
    route,
    expected_status,
):
    app = create_app(data_dir=tmp_path)
    record = {
        "session_id": "legacy-active-delete",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "events": [],
        "audio": {},
    }
    app.state.asr_live_repository.create(record)
    audio_dir = tmp_path / "audio_assets" / "legacy-active-delete"
    audio_dir.mkdir(parents=True)
    (audio_dir / "chunk.inprogress").write_bytes(b"active")
    capture_cancelled = asyncio.Event()

    async def run() -> None:
        async def active_capture() -> None:
            try:
                await asyncio.Future()
            finally:
                capture_cancelled.set()

        capture_task = asyncio.create_task(active_capture())
        app.state.active_v2_capture_tasks.setdefault(
            "legacy-active-delete",
            set(),
        ).add(capture_task)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.delete(route)

        assert response.status_code == expected_status
        assert capture_cancelled.is_set()
        assert capture_task.cancelled()

    asyncio.run(run())

    assert app.state.v2_persistence.is_meeting_tombstoned("legacy-active-delete") is True
    with pytest.raises(RuntimeError, match="deleted"):
        app.state.asr_live_repository.create(record)
    assert not audio_dir.exists()
    app.state.v2_persistence.close()


def test_app_startup_shadow_migrates_legacy_finals_without_paid_ai_jobs(tmp_path):
    database_path = tmp_path / "meeting_copilot.db"
    legacy_record = {
        "session_id": "legacy-meeting",
        "events": [
            {
                "id": "transcript_final:legacy-segment",
                "event_type": "transcript_final",
                "at_ms": 2_000,
                "payload": {
                    "segment_id": "legacy-segment",
                    "text": "历史会议确认灰度发布。",
                    "normalized_text": "历史会议确认灰度发布。",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                },
            }
        ],
    }
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE asr_live_sessions (session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO asr_live_sessions (session_id, record_json) VALUES (?, ?)",
            ("legacy-meeting", json.dumps(legacy_record, ensure_ascii=False)),
        )

    app = create_app(data_dir=tmp_path)

    assert app.state.v2_migration_report["status"] == "completed"
    snapshot = app.state.v2_persistence.get_snapshot("legacy-meeting")
    assert [segment["normalized_text"] for segment in snapshot["segments"]] == ["历史会议确认灰度发布。"]
    assert snapshot["runtime"]["phase"] == "ended"
    assert app.state.v2_persistence.list_jobs(meeting_id="legacy-meeting") == []
    app.state.v2_persistence.close()


def test_v2_create_history_and_audio_playback_endpoints(tmp_path):
    app = create_app(data_dir=tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/v2/meetings",
            json={
                "meeting_id": "meeting-created",
                "title": "发布评审",
                "expected_duration_seconds": 60,
            },
        )
        app.state.v2_persistence.record_audio_chunk(
            meeting_id="meeting-created",
            track="microphone",
            epoch=0,
            chunk_seq=0,
            relative_path=("audio_assets/meeting-created/chunks/chunk-00000000.pcm"),
            sha256="a" * 64,
            sample_rate_hz=16_000,
            sample_count=16_000,
            duration_ms=1_000,
            file_size_bytes=32_000,
            now_ms=2_000,
        )
        audio_dir = tmp_path / "audio_assets" / "meeting-created"
        audio_dir.mkdir(parents=True)
        (audio_dir / "audio.wav").write_bytes(b"RIFF-test-audio")

        history = client.get("/v2/meetings")
        audio = client.get("/v2/meetings/meeting-created/audio")
        content = client.get("/v2/meetings/meeting-created/audio/content")

    assert created.status_code == 201
    assert created.json()["meeting"]["title"] == "发布评审"
    assert created.json()["storage_preflight"]["allowed"] is True
    assert history.status_code == 200
    assert history.json()["meetings"][0]["id"] == "meeting-created"
    assert history.json()["meetings"][0]["audio_duration_ms"] == 1_000
    assert audio.status_code == 200
    assert audio.json()["assembled"] is True
    assert audio.json()["playback_url"].endswith("/audio/content")
    assert audio.json()["chunk_count"] == 1
    assert content.status_code == 200
    assert content.headers["content-type"] == "audio/wav"
    assert content.content == b"RIFF-test-audio"


def test_app_lifecycle_exports_a_sealed_recording_without_blocking_websocket_path(tmp_path):
    app = create_app(data_dir=tmp_path)
    meeting_id = "recording-background-app"
    lease_owner = "capture-integration"
    app.state.v2_persistence.create_meeting(
        meeting_id=meeting_id,
        title="后台录音导出",
        now_ms=1_000,
    )
    app.state.begin_v2_recording(
        meeting_id,
        {"source_type": "browser_live_mic", "sample_rate_hz": 16_000},
        lease_owner=lease_owner,
    )
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id=meeting_id,
        source_type="browser_live_mic",
        on_chunk_committed=lambda chunk: app.state.record_v2_audio_chunk(
            meeting_id,
            chunk,
            lease_owner=lease_owner,
        ),
    )
    writer.write_float32_pcm(struct.pack("<f", 0.2) * 1_600)
    sealed = writer.seal()
    app.state.seal_v2_recording(
        meeting_id,
        sealed,
        lease_owner=lease_owner,
    )
    audio_path = tmp_path / "audio_assets" / meeting_id / "audio.wav"

    assert not audio_path.exists()
    assert app.state.v2_persistence.list_recording_exports(meeting_id=meeting_id)[0]["status"] == "pending"

    with TestClient(app) as client:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            audio = client.get(f"/v2/meetings/{meeting_id}/audio")
            if audio.json()["assembled"]:
                break
            time.sleep(0.01)
        else:
            raise AssertionError(f"recording export did not finish: {audio.json()}")
        content = client.get(f"/v2/meetings/{meeting_id}/audio/content")

    assert audio.json()["status"] == "saved"
    assert audio.json()["assembled"] is True
    assert audio.json()["exports"][0]["status"] == "succeeded"
    assert content.status_code == 200
    assert content.content.startswith(b"RIFF")
