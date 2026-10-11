from __future__ import annotations

import pytest

from meeting_copilot_web_mvp.pipeline_trace import (
    PipelineTraceCollector,
    observe_provider_timing,
    ui_render_requirement,
)
from meeting_copilot_web_mvp.realtime_slo import RealtimeSLOStore, build_realtime_slo_report


def _pi_timing(collector, *, first=1_500, completed=3_000):
    return observe_provider_timing(
        collector,
        trace_id="deep-1",
        meeting_id="meeting-1",
        timings={
            "clock": "unix_epoch_ms",
            "started_at_ms": 1_100,
            "first_token_at_ms": first,
            "completed_at_ms": completed,
        },
        wall_time_ns=4_000_000_000,
        monotonic_ns=4_000_000_000,
    )


def _deep_trace(*, outcome="success", visible=True):
    collector = PipelineTraceCollector(clock_ns=lambda: 4_000_000_000)
    trace = collector.create(trace_id="deep-1", meeting_id="meeting-1")
    trace.mark("job_queued", monotonic_ns=1_000_000_000, attributes={"lane": "pi_deep"})
    trace.mark("job_claimed", monotonic_ns=1_050_000_000)
    _pi_timing(collector)
    trace.mark("event_emitted", monotonic_ns=3_200_000_000)
    if visible:
        trace.mark("ui_rendered", monotonic_ns=3_500_000_000)
    trace.record_terminal(outcome, result_outcome=outcome, monotonic_ns=3_600_000_000)
    if not visible:
        trace.record_provenance_stage(
            "ui_render_ack", status="not_required", reason="ui_target_no_coach_intervention",
            monotonic_ns=3_600_000_000,
        )
    return collector


def test_deep_measures_trigger_to_decision_without_fabricated_speech_or_provider_time(tmp_path):
    collector = _deep_trace()
    store = RealtimeSLOStore(state_path=tmp_path / "slo.json")
    store.observe(collector.slo_snapshots()[0])
    restored = RealtimeSLOStore(state_path=tmp_path / "slo.json")
    report = restored.report(meeting_id="meeting-1")
    lane = report["lanes"]["pi_deep"]
    metrics = lane["metrics"]
    assert metrics["trigger_to_first_token_ms"]["p50_ms"] == 500
    assert metrics["trigger_to_decision_ms"]["p50_ms"] == 2_000
    assert metrics["trigger_to_event_emitted_ms"]["p50_ms"] == 2_200
    assert metrics["trigger_to_ui_ms"]["p50_ms"] == 2_500
    assert metrics["evaluation_ttft_ms"]["p50_ms"] == 400
    assert metrics["evaluation_total_ms"]["p50_ms"] == 1_900
    assert metrics["final_to_first_token_ms"]["measurement_status"] == "not_applicable"
    assert metrics["provider_ttft_ms"]["measurement_status"] == "unknown"
    assert metrics["request_ttft_ms"]["measurement_status"] == "unknown"
    assert lane["metric_definitions"]["trigger_to_decision_ms"]["end"] == "evaluation_completed"
    assert lane["slo_verdict"]["availability_status"] == "insufficient_data"


def test_failed_evaluation_preserves_observed_token_but_has_no_successful_decision_sample():
    collector = _deep_trace(outcome="timeout", visible=False)
    lane = build_realtime_slo_report(collector.slo_snapshots(), meeting_id="meeting-1")["lanes"]["pi_deep"]
    assert lane["metrics"]["evaluation_total_ms"]["p50_ms"] == 1_900
    assert lane["metrics"]["trigger_to_first_token_ms"]["p50_ms"] == 500
    assert lane["metrics"]["trigger_to_decision_ms"]["count"] == 0
    assert lane["metrics"]["trigger_to_decision_ms"]["measurement_status"] == "unknown"
    assert lane["metrics"]["event_to_ui_ms"]["measurement_status"] == "not_applicable"
    assert lane["slo_verdict"]["availability_status"] == "fail"


def test_missing_then_observed_contract_upgrades_and_missing_reobservation_preserves_it():
    collector = PipelineTraceCollector()
    observe_provider_timing(collector, trace_id="deep-1", meeting_id="meeting-1", timings=None)
    _pi_timing(collector)
    before = collector.export("deep-1")["execution"]["timing_contract"]
    observe_provider_timing(collector, trace_id="deep-1", meeting_id="meeting-1", timings=None)
    after = collector.export("deep-1")["execution"]["timing_contract"]
    assert before["status"] == "partial"
    assert after == before
    assert after["evaluation"]["first_token"] == 1_500_000_000


def test_inflight_attempt_is_insufficient_data_instead_of_a_failure_or_success():
    collector = PipelineTraceCollector()
    trace = collector.create(trace_id="inflight", meeting_id="meeting-1")
    trace.mark("job_queued", monotonic_ns=1_000, attributes={"lane": "answer"})
    trace.record_provider_attempt(1, branch="answer", runtime="direct", monotonic_ns=2_000)
    lane = build_realtime_slo_report(collector.slo_snapshots(), meeting_id="meeting-1")["lanes"]["answer"]
    assert lane["provider_attempts"]["incomplete_count"] == 1
    assert lane["slo_verdict"]["availability_status"] == "insufficient_data"


def test_transport_success_with_missing_observations_is_not_reported_as_verified_success():
    collector = PipelineTraceCollector()
    trace = collector.create(trace_id="missing", meeting_id="meeting-1")
    trace.mark("job_queued", monotonic_ns=1_000, attributes={"lane": "answer"})
    trace.record_provider_attempt(1, branch="answer", runtime="direct", monotonic_ns=2_000)
    trace.record_provider_attempt_outcome(1, "success", branch="answer", runtime="direct", monotonic_ns=3_000)
    observe_provider_timing(collector, trace_id="missing", meeting_id="meeting-1", timings=None)
    trace.record_terminal("success", result_outcome="success", monotonic_ns=4_000)
    lane = build_realtime_slo_report(collector.slo_snapshots(), meeting_id="meeting-1")["lanes"]["answer"]
    assert lane["job_outcomes"]["result_counts"]["success"] == 1
    assert lane["metrics"]["provider_ttft_ms"]["measurement_status"] == "unknown"
    assert lane["slo_verdict"]["availability_status"] == "insufficient_data"


@pytest.mark.parametrize(("lane", "output", "expected"), [
    ("pi_deep", {"coach": {"status": "silent"}}, "not_required"),
    ("intelligence", {"coach": {"status": "intervention"}}, "required"),
    ("intelligence", {"coach": {"status": "intervention", "late_result_discarded": True}}, "not_required"),
    ("correction", {"revision_count": 0}, "not_required"),
    ("correction", {"revision_count": 1}, "required"),
    ("answer", {"generated_answer_count": 0}, "not_required"),
    ("pi_deep", {}, "unknown"),
    ("correction", {"revision_count": False}, "unknown"),
])
def test_ui_requirement_only_marks_explicitly_empty_results_not_required(lane, output, expected):
    assert ui_render_requirement(lane, output)[0] == expected


@pytest.mark.parametrize(("created_at_ms", "has_anchor"), [(99_000, True), (100_001, False), (None, False)])
def test_app_deep_trigger_uses_durable_creation_anchor_and_silent_ui_is_not_required(
    tmp_path, monkeypatch, created_at_ms, has_anchor,
):
    # The test runner must isolate dotenv/global runtime before importing app.
    import meeting_copilot_web_mvp.app as app_module

    app = app_module.create_app(data_dir=tmp_path)
    monkeypatch.setattr(app_module.time, "time_ns", lambda: 100_000_000_000)
    monkeypatch.setattr(app_module.time, "monotonic_ns", lambda: 10_000_000_000)
    monkeypatch.setattr(app.state.v2_persistence, "get_job", lambda _job_id: {
        "trigger_type": "answer_ready", "created_at_ms": created_at_ms,
        "output": {"coach": {"status": "silent"}},
    })
    try:
        app.state.record_v2_job_lifecycle({
            "event": "terminal", "job_id": "deep-1", "meeting_id": "meeting-1", "lane": "pi_deep",
            "terminal_outcome": "success", "result_outcome": "success",
            "lifecycle_at_monotonic_ns": 10_000_000_000,
        })
        snapshot = app.state.pipeline_traces.export("deep-1")
        assert ("job_queued" in snapshot["stages"]) is has_anchor
        if has_anchor:
            assert snapshot["stages"]["job_queued"]["monotonic_ns"] == 9_000_000_000
            assert snapshot["stages"]["job_queued"]["attributes"]["timing_source"] == "durable_job_created_at_ms"
        provenance = snapshot["execution"]["provenance"]
        assert provenance["speech_endpoint"]["status"] == "not_required"
        assert provenance["ui_render_ack"]["status"] == "not_required"
    finally:
        app.state.v2_persistence.close()
