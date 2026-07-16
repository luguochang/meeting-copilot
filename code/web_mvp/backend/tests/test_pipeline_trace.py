from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from meeting_copilot_web_mvp.pipeline_trace import (
    PIPELINE_STAGES,
    PipelineTraceCollector,
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
