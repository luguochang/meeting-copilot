from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from meeting_copilot_web_mvp.pipeline_trace import (
    PIPELINE_STAGES,
    PipelineTraceCollector,
    classify_pipeline_failure,
    normalize_provider_timing_stages,
)


EXPECTED_STAGES = (
    "audio_active",
    "final_committed",
    "job_queued",
    "job_claimed",
    "provider_connected",
    "first_token",
    "provider_completed",
    "validated",
    "event_emitted",
    "ui_rendered",
)


def test_stage_contract_and_latency_breakdown_use_monotonic_nanoseconds():
    collector = PipelineTraceCollector()
    trace = collector.create(
        trace_id="trace-1",
        meeting_id="meeting-1",
        job_id="job-1",
        generation_id="generation-1",
    )

    for index, stage in enumerate(EXPECTED_STAGES):
        trace.mark(stage, monotonic_ns=1_000_000_000 + index * 100_000_000)

    breakdown = trace.latency_breakdown()

    assert PIPELINE_STAGES == EXPECTED_STAGES
    assert breakdown["complete"] is True
    assert breakdown["missing_stages"] == []
    assert breakdown["total_ms"] == 900.0
    assert breakdown["stage_delta_ms"]["audio_active"] == 0.0
    assert breakdown["stage_delta_ms"]["first_token"] == 100.0
    assert breakdown["from_start_ms"]["first_token"] == 500.0
    assert breakdown["transitions_ms"]["provider_connected->first_token"] == 100.0


def test_duplicate_stage_is_idempotent_and_preserves_first_observation():
    trace = PipelineTraceCollector().create(trace_id="trace-1", meeting_id="meeting-1")

    first = trace.mark(
        "audio_active",
        monotonic_ns=100,
        attributes={"source": "microphone"},
    )
    duplicate = trace.mark(
        "audio_active",
        monotonic_ns=999,
        attributes={"source": "replayed-event"},
    )

    assert duplicate is first
    assert duplicate.monotonic_ns == 100
    assert duplicate.attributes == {"source": "microphone"}
    assert len(trace.stage_marks) == 1


def test_trace_associations_can_be_bound_once_and_queried_from_collector():
    collector = PipelineTraceCollector()
    trace = collector.create(trace_id="trace-1", meeting_id="meeting-1")

    trace.bind(job_id="job-1", generation_id="generation-1")

    assert trace.meeting_id == "meeting-1"
    assert trace.job_id == "job-1"
    assert trace.generation_id == "generation-1"
    assert collector.find(meeting_id="meeting-1") == [trace]
    assert collector.find(job_id="job-1") == [trace]
    assert collector.find(generation_id="generation-1") == [trace]
    assert collector.find(meeting_id="meeting-1", job_id="job-1") == [trace]

    trace.bind(job_id="job-1", generation_id="generation-1")
    with pytest.raises(ValueError, match="job_id is already bound"):
        trace.bind(job_id="job-2")


def test_conflicting_multi_field_bind_is_atomic():
    trace = PipelineTraceCollector().create(
        trace_id="trace-1",
        meeting_id="meeting-1",
        generation_id="generation-1",
    )

    with pytest.raises(ValueError, match="generation_id is already bound"):
        trace.bind(job_id="job-1", generation_id="generation-2")

    assert trace.job_id is None
    assert trace.generation_id == "generation-1"


def test_collector_record_creates_or_updates_one_trace_and_exports_snapshot():
    ticks = iter((10, 20))
    collector = PipelineTraceCollector(clock_ns=lambda: next(ticks))

    first = collector.record(
        "trace-1",
        "audio_active",
        meeting_id="meeting-1",
    )
    second = collector.record(
        "trace-1",
        "final_committed",
        meeting_id="meeting-1",
        job_id="job-1",
        generation_id="generation-1",
    )
    exported = collector.export("trace-1")

    assert first.monotonic_ns == 10
    assert second.monotonic_ns == 20
    assert exported["trace_id"] == "trace-1"
    assert exported["meeting_id"] == "meeting-1"
    assert exported["job_id"] == "job-1"
    assert exported["generation_id"] == "generation-1"
    assert exported["stages"]["audio_active"]["monotonic_ns"] == 10
    assert exported["latency"]["total_ms"] == 0.00001


def test_partial_trace_reports_only_observed_transitions_and_missing_stages():
    trace = PipelineTraceCollector().create(trace_id="trace-1", meeting_id="meeting-1")
    trace.mark("audio_active", monotonic_ns=1_000_000)
    trace.mark("job_queued", monotonic_ns=4_000_000)

    breakdown = trace.latency_breakdown()

    assert breakdown["complete"] is False
    assert breakdown["total_ms"] == 3.0
    assert breakdown["stage_delta_ms"] == {
        "audio_active": 0.0,
        "job_queued": 3.0,
    }
    assert breakdown["transitions_ms"] == {"audio_active->job_queued": 3.0}
    assert breakdown["missing_stages"] == [
        stage for stage in EXPECTED_STAGES if stage not in {"audio_active", "job_queued"}
    ]


def test_invalid_ids_stages_and_monotonic_regression_are_rejected():
    collector = PipelineTraceCollector()
    with pytest.raises(ValueError, match="meeting_id must not be empty"):
        collector.create(trace_id="trace-1", meeting_id=" ")

    trace = collector.create(trace_id="trace-1", meeting_id="meeting-1")
    with pytest.raises(ValueError, match="unsupported pipeline stage"):
        trace.mark("made_up_stage")
    with pytest.raises(ValueError, match="monotonic_ns must be non-negative"):
        trace.mark("audio_active", monotonic_ns=-1)

    trace.mark("audio_active", monotonic_ns=200)
    with pytest.raises(ValueError, match="precedes the latest recorded stage"):
        trace.mark("final_committed", monotonic_ns=199)


def test_late_observation_is_accepted_when_its_timestamp_preserves_stage_order():
    trace = PipelineTraceCollector().create(trace_id="trace-1", meeting_id="meeting-1")
    trace.mark("provider_connected", monotonic_ns=500)
    trace.mark("final_committed", monotonic_ns=200)
    trace.mark("job_claimed", monotonic_ns=400)

    assert list(trace.stage_marks) == [
        "final_committed",
        "job_claimed",
        "provider_connected",
    ]
    assert trace.latency_breakdown()["total_ms"] == 0.0003


def test_operational_observation_drops_invalid_metric_without_raising(caplog):
    collector = PipelineTraceCollector()
    collector.record(
        "trace-1",
        "job_claimed",
        meeting_id="meeting-1",
        monotonic_ns=100,
    )

    dropped = collector.observe(
        "trace-1",
        "final_committed",
        meeting_id="meeting-1",
        monotonic_ns=200,
    )

    assert dropped is None
    assert "final_committed" not in collector.get("trace-1").stage_marks
    assert "Pipeline trace observation dropped" in caplog.text


def test_stage_marking_is_thread_safe_and_first_write_wins():
    trace = PipelineTraceCollector().create(trace_id="trace-1", meeting_id="meeting-1")

    with ThreadPoolExecutor(max_workers=8) as executor:
        marks = list(
            executor.map(
                lambda timestamp: trace.mark("job_claimed", monotonic_ns=timestamp),
                range(1_000, 1_008),
            )
        )

    assert len({id(mark) for mark in marks}) == 1
    assert len(trace.stage_marks) == 1
    assert trace.stage_marks["job_claimed"].monotonic_ns in range(1_000, 1_008)


def test_collector_retention_is_bounded_and_eviction_preserves_raw_export_compatibility():
    evicted = []
    collector = PipelineTraceCollector(max_traces=2, on_evict=evicted.append)

    collector.record(
        "trace-1",
        "job_queued",
        meeting_id="meeting-1",
        attributes={"lane": "correction", "segment_id": "segment-1"},
        monotonic_ns=1,
    )
    collector.record(
        "trace-2",
        "job_queued",
        meeting_id="meeting-1",
        attributes={"lane": "suggestion"},
        monotonic_ns=2,
    )
    expected_export = collector.export("trace-2")
    collector.record(
        "trace-3",
        "job_queued",
        meeting_id="meeting-1",
        attributes={"lane": "intelligence"},
        monotonic_ns=3,
    )

    assert len(collector) == 2
    with pytest.raises(KeyError, match="unknown pipeline trace"):
        collector.get("trace-1")
    assert collector.export("trace-2") == expected_export
    assert len(evicted) == 1
    assert evicted[0]["trace_id"] == "trace-1"
    assert evicted[0]["lane"] == "correction"
    assert "attributes" not in evicted[0]


def test_bounded_collector_remains_thread_safe_under_concurrent_trace_creation():
    evicted = []
    collector = PipelineTraceCollector(max_traces=8, on_evict=evicted.append)

    def record(index: int) -> None:
        collector.record(
            f"trace-{index:03d}",
            "job_queued",
            meeting_id="meeting-1",
            attributes={"lane": "suggestion"},
            monotonic_ns=index,
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(record, range(100)))

    assert len(collector) == 8
    assert len(evicted) == 92
    retained = collector.slo_snapshots()
    assert len(retained) == 8
    assert {snapshot["meeting_id"] for snapshot in retained} == {"meeting-1"}


def test_retry_and_cancellation_are_exposed_in_raw_and_slo_exports():
    collector = PipelineTraceCollector()
    collector.record(
        "trace-1",
        "job_queued",
        meeting_id="meeting-1",
        attributes={"lane": "correction"},
        monotonic_ns=1,
    )
    collector.record_retry("trace-1", count=2)
    collector.record_cancelled("trace-1")

    raw_export = collector.export("trace-1")
    assert raw_export["retry_count"] == 2
    assert raw_export["cancelled"] is True
    assert raw_export["execution"]["terminal"] is None
    snapshot = collector.slo_snapshots()[0]
    assert snapshot["retry_count"] == 2
    assert snapshot["cancelled"] is True
    assert snapshot["lane"] == "correction"


def test_collector_rejects_invalid_retention_and_lifecycle_counts():
    with pytest.raises(ValueError, match="max_traces must be positive"):
        PipelineTraceCollector(max_traces=0)

    collector = PipelineTraceCollector()
    collector.create(trace_id="trace-1", meeting_id="meeting-1")
    with pytest.raises(ValueError, match="retry count must be positive"):
        collector.record_retry("trace-1", count=0)


def test_rejected_stage_mark_does_not_partially_bind_lane():
    trace = PipelineTraceCollector().create(trace_id="trace-1", meeting_id="meeting-1")
    trace.mark("provider_connected", monotonic_ns=500)

    with pytest.raises(ValueError, match="follows the earliest later stage"):
        trace.mark(
            "final_committed",
            monotonic_ns=600,
            attributes={"lane": "correction"},
        )

    assert trace.slo_snapshot()["lane"] == "unknown"


def test_eviction_is_published_even_when_new_trace_mark_is_rejected():
    evicted = []
    collector = PipelineTraceCollector(max_traces=1, on_evict=evicted.append)
    collector.record(
        "trace-1",
        "job_queued",
        meeting_id="meeting-1",
        monotonic_ns=1,
        attributes={"lane": "correction"},
    )

    with pytest.raises(ValueError, match="unsupported pipeline stage"):
        collector.record("trace-2", "invalid", meeting_id="meeting-1")

    assert [snapshot["trace_id"] for snapshot in evicted] == ["trace-1"]


def test_content_free_route_attempt_cancel_and_terminal_contract_is_idempotent():
    collector = PipelineTraceCollector(clock_ns=lambda: 5_000)
    collector.record(
        "trace-1",
        "job_queued",
        meeting_id="meeting-1",
        attributes={"lane": "intelligence"},
        monotonic_ns=1_000,
    )

    collector.record_route(
        "trace-1",
        "pi_coach",
        candidate_outcome="eligible",
        circuit_outcome="admitted",
        monotonic_ns=2_000,
    )
    collector.record_provider_attempt(
        "trace-1",
        1,
        branch="coach",
        runtime="pi",
        monotonic_ns=3_000,
    )
    collector.record_provider_attempt_outcome(
        "trace-1",
        1,
        "rate_limit",
        branch="coach",
        runtime="pi",
        http_status=429,
        error_class="StreamingProviderError",
        monotonic_ns=4_000,
    )
    collector.record_provider_attempt_outcome(
        "trace-1",
        1,
        "rate_limit",
        branch="coach",
        runtime="pi",
        http_status=429,
        error_class="StreamingProviderError",
        monotonic_ns=4_500,
    )
    collector.record_cancelled(
        "trace-1",
        result_outcome="rate_limit",
        error_class="StreamingProviderError",
        monotonic_ns=5_000,
    )
    collector.record_abort_ack(
        "trace-1",
        local=True,
        remote_unavailable=True,
    )

    execution = collector.export("trace-1")["execution"]
    assert execution["route"] == {
        "name": "pi_coach",
        "candidate_outcome": "eligible",
        "circuit_outcome": "admitted",
        "decided_at_monotonic_ns": 2_000,
    }
    assert execution["provider_attempts"] == [
        {
            "attempt_index": 1,
            "branch": "coach",
            "runtime": "pi",
            "started_at_monotonic_ns": 3_000,
            "completed_at_monotonic_ns": 4_000,
            "outcome": "rate_limit",
            "http_status": 429,
            "error_class": "StreamingProviderError",
        }
    ]
    assert execution["terminal"]["outcome"] == "cancelled"
    assert execution["terminal"]["result_outcome"] == "rate_limit"
    assert execution["cancellation"]["local_abort_ack"] is True
    assert execution["cancellation"]["remote_ack_unavailable"] is True


def test_remote_abort_ack_and_unavailable_are_mutually_exclusive():
    collector = PipelineTraceCollector()
    collector.record(
        "trace-ack-conflict",
        "job_queued",
        meeting_id="meeting-1",
        attributes={"lane": "intelligence"},
        monotonic_ns=1_000,
    )
    collector.record_cancelled(
        "trace-ack-conflict",
        monotonic_ns=2_000,
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        collector.record_abort_ack(
            "trace-ack-conflict",
            remote=True,
            remote_unavailable=True,
        )

    collector.record_abort_ack("trace-ack-conflict", remote=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        collector.record_abort_ack("trace-ack-conflict", remote_unavailable=True)


def test_provider_attempt_validation_revision_is_one_way_and_preserves_timing():
    collector = PipelineTraceCollector(clock_ns=lambda: 5_000)
    collector.record(
        "trace-validation-revision",
        "job_queued",
        meeting_id="meeting-1",
        attributes={"lane": "intelligence"},
        monotonic_ns=1_000,
    )
    collector.record_provider_attempt(
        "trace-validation-revision",
        1,
        branch="semantic",
        runtime="direct",
        monotonic_ns=2_000,
    )
    collector.record_provider_attempt_outcome(
        "trace-validation-revision",
        1,
        "success",
        branch="semantic",
        runtime="direct",
        monotonic_ns=3_000,
    )

    revised = collector.revise_provider_attempt_validation(
        "trace-validation-revision",
        1,
        branch="semantic",
        runtime="direct",
        error_class="IntelligenceResponseValidationError",
    )
    assert revised["outcome"] == "validation_error"
    assert revised["started_at_monotonic_ns"] == 2_000
    assert revised["completed_at_monotonic_ns"] == 3_000
    with pytest.raises(ValueError, match="only a successful"):
        collector.revise_provider_attempt_validation(
            "trace-validation-revision",
            1,
            branch="semantic",
            runtime="direct",
        )


def test_direct_timing_normalization_keeps_attempt_start_and_provider_stages():
    normalized = normalize_provider_timing_stages(
        {
            "started_at": 10.0,
            "connected_at": 10.1,
            "first_token_at": 10.2,
            "completed_at": 10.4,
        }
    )

    assert normalized.status == "complete"
    assert normalized.source_clock == "monotonic_seconds"
    assert normalized.attempt_started_monotonic_ns == 10_000_000_000
    assert normalized.stages == {
        "provider_connected": 10_100_000_000,
        "first_token": 10_200_000_000,
        "provider_completed": 10_400_000_000,
    }
    assert normalized.invalid_reasons == ()


def test_pi_epoch_timing_is_evaluation_timing_and_never_fabricates_provider_stages():
    normalized = normalize_provider_timing_stages(
        {
            "clock": "unix_epoch_ms",
            "started_at_ms": 1_000,
            "first_token_at_ms": 1_025,
            "completed_at_ms": 1_050,
        },
        wall_time_ns=1_100_000_000,
        monotonic_ns=10_000_000_000,
    )

    assert normalized.status == "partial"
    assert normalized.source_clock == "unix_epoch_ms"
    assert normalized.stages == {"first_token": 9_925_000_000}
    assert normalized.evaluation == {
        "evaluation_started": 9_900_000_000,
        "first_token": 9_925_000_000,
        "evaluation_completed": 9_950_000_000,
    }
    assert normalized.attempt_started_monotonic_ns is None
    assert normalized.invalid_reasons == (
        "pi_provider_connected_unobserved",
        "pi_provider_completed_unobserved",
    )


@pytest.mark.parametrize(
    ("timings", "reason"),
    [
        (
            {
                "clock": "unix_epoch_ms",
                "started_at_ms": 1_000,
                "connected_at": 1.0,
            },
            "mixed_clock_fields",
        ),
        (
            {
                "clock": "unix_epoch_ms",
                "started_at_ms": 1_100,
                "completed_at_ms": 1_000,
            },
            "provider_timing_order_invalid",
        ),
        ({"clock": "wall_clock_seconds", "connected_at": 1.0}, "unsupported_timing_clock"),
    ],
)
def test_invalid_timing_contracts_are_explicit(timings, reason):
    normalized = normalize_provider_timing_stages(timings)

    assert normalized.status == "invalid"
    assert normalized.stages == {}
    assert reason in normalized.invalid_reasons


def test_multiple_direct_attempts_do_not_publish_cross_attempt_total_latency():
    normalized = normalize_provider_timing_stages(
        {
            "started_at": 10.0,
            "connected_at": 10.1,
            "first_token_at": 10.2,
            "completed_at": 12.0,
        },
        provider_attempt_count=2,
    )

    assert "provider_completed" not in normalized.stages
    assert "multiple_provider_attempts_require_attempt_timings" in normalized.invalid_reasons


def test_failure_classifier_covers_acceptance_outcomes_without_error_details():
    class ProviderFailure(RuntimeError):
        def __init__(self, *, category=None, status_code=None):
            super().__init__("private provider detail")
            self.category = category
            self.status_code = status_code

    assert classify_pipeline_failure(TimeoutError()) == "timeout"
    assert classify_pipeline_failure(ProviderFailure(status_code=429)) == "rate_limit"
    assert classify_pipeline_failure(ProviderFailure(status_code=503)) == "provider_5xx"
    assert classify_pipeline_failure(ProviderFailure(category="transport")) == "transport_error"
    assert classify_pipeline_failure(RuntimeError("ignored")) == "failed"
