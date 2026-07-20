from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from meeting_copilot_web_mvp.pipeline_trace import PipelineTraceCollector
from meeting_copilot_web_mvp.realtime_slo import (
    DEFAULT_REALTIME_SLO_THRESHOLDS_MS,
    RealtimeSLOStore,
    build_realtime_slo_report,
)


def _snapshot(
    trace_id: str,
    *,
    meeting_id: str = "meeting-1",
    lane: str = "correction",
    base_ms: int = 0,
    first_token_ms: int = 500,
    event_emitted_ms: int = 1_000,
    ui_rendered_ms: int = 1_100,
) -> dict:
    collector = PipelineTraceCollector()
    stage_ms = {
        "audio_active": base_ms,
        "final_committed": base_ms + 100,
        "job_queued": base_ms + 200,
        "job_claimed": base_ms + 300,
        "provider_connected": base_ms + 400,
        "first_token": base_ms + first_token_ms,
        "provider_completed": base_ms + event_emitted_ms - 100,
        "validated": base_ms + event_emitted_ms - 50,
        "event_emitted": base_ms + event_emitted_ms,
        "ui_rendered": base_ms + ui_rendered_ms,
    }
    for stage, timestamp_ms in stage_ms.items():
        attributes = {"lane": lane} if stage == "job_queued" else None
        collector.record(
            trace_id,
            stage,
            meeting_id=meeting_id,
            monotonic_ns=timestamp_ms * 1_000_000,
            attributes=attributes,
        )
    return collector.slo_snapshots()[0]


def test_report_groups_by_meeting_and_lane_with_all_realtime_latency_metrics():
    first = _snapshot(
        "trace-1",
        first_token_ms=400,
        event_emitted_ms=800,
        ui_rendered_ms=850,
    )
    second = _snapshot(
        "trace-2",
        base_ms=2_000,
        first_token_ms=800,
        event_emitted_ms=1_200,
        ui_rendered_ms=1_350,
    )
    other_lane = _snapshot("trace-3", lane="suggestion")
    other_meeting = _snapshot("trace-4", meeting_id="meeting-2")

    report = build_realtime_slo_report(
        [first, second, other_lane, other_meeting],
        meeting_id="meeting-1",
    )
    correction = report["lanes"]["correction"]

    assert report["schema_version"] == "meeting_copilot.realtime_ai_slo.v1"
    assert report["meeting_id"] == "meeting-1"
    assert report["trace_count"] == 3
    assert correction["count"] == 2
    assert correction["metrics"]["final_to_first_token_ms"] == {
        "count": 2,
        "p50_ms": 500.0,
        "p95_ms": 680.0,
        "max_ms": 700.0,
    }
    assert correction["metrics"]["final_to_event_emitted_ms"] == {
        "count": 2,
        "p50_ms": 900.0,
        "p95_ms": 1080.0,
        "max_ms": 1100.0,
    }
    assert correction["metrics"]["queue_wait_ms"]["p50_ms"] == 100.0
    assert correction["metrics"]["provider_ttft_ms"]["p50_ms"] == 200.0
    assert correction["metrics"]["provider_total_ms"]["p50_ms"] == 500.0
    assert correction["metrics"]["event_to_ui_ms"]["p50_ms"] == 100.0
    assert correction["slo_verdict"]["status"] == "pass"


def test_missing_cancelled_and_retry_counts_are_explicit_and_no_sample_is_not_zero():
    collector = PipelineTraceCollector()
    collector.record(
        "partial",
        "job_queued",
        meeting_id="meeting-1",
        monotonic_ns=10,
        attributes={
            "lane": "correction",
            "transcript": "must never enter diagnostics",
            "api_key": "sk-must-not-leak",
        },
    )
    collector.record_retry("partial", count=2)
    collector.record_cancelled("partial")

    report = build_realtime_slo_report(collector.slo_snapshots(), meeting_id="meeting-1")
    lane = report["lanes"]["correction"]
    serialized = json.dumps(report)

    assert lane["count"] == 1
    assert lane["missing_trace_count"] == 1
    assert lane["missing_stage_counts"]["first_token"] == 1
    assert lane["cancelled_count"] == 1
    assert lane["retry_count"] == 2
    assert lane["metrics"]["provider_ttft_ms"] == {
        "count": 0,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }
    assert lane["slo_verdict"]["status"] == "no_data"
    assert lane["slo_verdict"]["metrics"]["provider_ttft_ms"]["status"] == "no_data"
    assert "must never enter diagnostics" not in serialized
    assert "sk-must-not-leak" not in serialized


def test_default_lane_thresholds_produce_an_explicit_failure_verdict():
    slow = _snapshot(
        "slow-correction",
        first_token_ms=5_500,
        event_emitted_ms=7_000,
        ui_rendered_ms=7_100,
    )

    report = build_realtime_slo_report([slow], meeting_id="meeting-1")
    verdict = report["lanes"]["correction"]["slo_verdict"]

    assert DEFAULT_REALTIME_SLO_THRESHOLDS_MS["correction"]["final_to_event_emitted_ms"] == 6_000
    assert verdict["status"] == "fail"
    assert verdict["metrics"]["final_to_event_emitted_ms"] == {
        "status": "fail",
        "threshold_ms": 6_000.0,
        "observed_p95_ms": 6_900.0,
    }


def test_empty_report_has_no_data_instead_of_zero_latency():
    report = build_realtime_slo_report([], meeting_id="meeting-without-traces")

    assert report == {
        "schema_version": "meeting_copilot.realtime_ai_slo.v1",
        "meeting_id": "meeting-without-traces",
        "trace_count": 0,
        "lanes": {},
        "slo_verdict": {"status": "no_data", "lane_statuses": {}},
    }


def test_store_archives_evicted_traces_and_combines_them_with_bounded_active_traces(tmp_path):
    state_path = tmp_path / "realtime-slo.json"
    store = RealtimeSLOStore(state_path=state_path, max_samples_per_metric=8)
    collector = PipelineTraceCollector(max_traces=1, on_evict=store.observe)

    for trace_id, base_ms in (("trace-1", 0), ("trace-2", 2_000)):
        snapshot = _snapshot(trace_id, base_ms=base_ms)
        for stage, timestamp in snapshot["stages"].items():
            collector.record(
                trace_id,
                stage,
                meeting_id="meeting-1",
                monotonic_ns=timestamp,
                attributes={"lane": "correction"} if stage == "job_queued" else None,
            )

    report = store.report(
        meeting_id="meeting-1",
        active_traces=collector.slo_snapshots(),
    )

    assert len(collector) == 1
    assert report["trace_count"] == 2
    assert report["lanes"]["correction"]["count"] == 2
    assert state_path.is_file()
    assert not list(tmp_path.glob("*.tmp"))


def test_store_checkpoint_is_atomically_persistent_bounded_and_content_free(tmp_path):
    state_path = tmp_path / "realtime-slo.json"
    store = RealtimeSLOStore(
        state_path=state_path,
        max_samples_per_metric=2,
        max_meetings=2,
    )
    traces = [_snapshot(f"trace-{index}", meeting_id=f"meeting-{index}") for index in range(1, 4)]

    store.checkpoint(active_traces=traces)
    persisted = state_path.read_text(encoding="utf-8")
    reloaded = RealtimeSLOStore(
        state_path=state_path,
        max_samples_per_metric=2,
        max_meetings=2,
    )

    assert '"transcript"' not in persisted.lower()
    assert '"audio"' not in persisted.lower()
    assert '"secret"' not in persisted.lower()
    assert reloaded.report(meeting_id="meeting-1")["trace_count"] == 0
    assert reloaded.report(meeting_id="meeting-2")["trace_count"] == 1
    assert reloaded.report(meeting_id="meeting-3")["trace_count"] == 1


def test_recovered_checkpoint_is_replaced_when_same_trace_later_completes(tmp_path):
    state_path = tmp_path / "realtime-slo.json"
    collector = PipelineTraceCollector()
    collector.record(
        "trace-1",
        "job_queued",
        meeting_id="meeting-1",
        monotonic_ns=100,
        attributes={"lane": "correction"},
    )
    RealtimeSLOStore(state_path=state_path).checkpoint(active_traces=collector.slo_snapshots())
    recovered = RealtimeSLOStore(state_path=state_path)

    recovered.observe(_snapshot("trace-1"))
    report = recovered.report(meeting_id="meeting-1")

    assert report["trace_count"] == 1
    assert report["lanes"]["correction"]["missing_trace_count"] == 0


def test_percentiles_use_an_explicit_bounded_recent_sample_window():
    traces = [
        _snapshot(
            f"trace-{index}",
            first_token_ms=first_token_ms,
            event_emitted_ms=first_token_ms + 200,
            ui_rendered_ms=first_token_ms + 300,
        )
        for index, first_token_ms in enumerate((500, 700, 1_100), start=1)
    ]

    report = build_realtime_slo_report(
        traces,
        meeting_id="meeting-1",
        max_samples_per_metric=2,
    )
    lane = report["lanes"]["correction"]

    assert lane["percentile_sample_limit"] == 2
    assert lane["metrics"]["final_to_first_token_ms"] == {
        "count": 3,
        "p50_ms": 800.0,
        "p95_ms": 980.0,
        "max_ms": 1000.0,
    }


def test_store_observation_and_reporting_are_thread_safe():
    store = RealtimeSLOStore()
    snapshots = [_snapshot(f"trace-{index}", base_ms=index * 2_000) for index in range(100)]

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(store.observe, snapshots))

    report = store.report(meeting_id="meeting-1")
    assert report["trace_count"] == 100
    assert report["lanes"]["correction"]["metrics"]["provider_ttft_ms"]["count"] == 100


def test_non_finite_slo_thresholds_are_rejected():
    with pytest.raises(ValueError, match="finite non-negative number"):
        build_realtime_slo_report(
            [_snapshot("trace-1")],
            meeting_id="meeting-1",
            thresholds_ms={"correction": {"provider_ttft_ms": float("nan")}},
        )
