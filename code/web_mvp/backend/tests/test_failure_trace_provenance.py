from __future__ import annotations

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.pipeline_trace import PipelineTraceCollector
from meeting_copilot_web_mvp.realtime_slo import build_realtime_slo_report


def test_provenance_is_content_free_and_records_explicit_non_provider_path() -> None:
    collector = PipelineTraceCollector(clock_ns=lambda: 1_000)
    trace = collector.create(trace_id="circuit-1", meeting_id="meeting-1")
    trace.mark("job_queued", monotonic_ns=1_000, attributes={"lane": "intelligence"})
    trace.record_route(
        "pi_coach",
        candidate_outcome="eligible",
        circuit_outcome="denied",
        monotonic_ns=2_000,
    )
    trace.record_provenance_stage(
        "reservation",
        status="not_required",
        reason="circuit_denied",
        monotonic_ns=2_000,
    )
    trace.record_terminal(
        "failed",
        result_outcome="circuit_denied",
        error_class="realtime_provider_circuit_open",
        monotonic_ns=3_000,
    )
    for provider_stage in (
        "provider_attempt_start",
        "provider_connected",
        "first_token",
        "provider_completed",
    ):
        trace.record_provenance_stage(
            provider_stage,
            status="not_required",
            reason="circuit_denied",
            monotonic_ns=3_000,
        )
    trace.record_provenance_stage(
        "cancel_requested",
        status="not_required",
        reason="terminal_not_cancelled",
        monotonic_ns=3_000,
    )
    trace.record_provenance_stage(
        "abort_ack",
        status="not_required",
        reason="terminal_not_cancelled",
        monotonic_ns=3_000,
    )
    execution = trace.to_dict()["execution"]
    assert execution["route"]["circuit_outcome"] == "denied"
    assert execution["provider_attempts"] == []
    assert execution["terminal"]["result_outcome"] == "circuit_denied"
    assert execution["provenance"]["provider_attempt_start"]["status"] == "not_required"
    assert execution["required_stage_completeness"]["complete"] is True
    assert execution["required_stage_completeness"]["missing_stages"] == []
    assert "private.example" not in repr(execution)

    report = build_realtime_slo_report(collector.slo_snapshots(), meeting_id="meeting-1")
    lane = report["lanes"]["intelligence"]
    assert lane["job_outcomes"]["count"] == 1
    assert lane["job_outcomes"]["result_counts"]["circuit_denied"] == 1
    assert lane["provider_attempts"]["count"] == 0
    assert lane["slo_verdict"]["availability_status"] == "fail"
    assert lane["required_stage_completeness"]["incomplete_count"] == 0


def test_terminal_trace_seals_unobserved_stages_without_fabricating_timing() -> None:
    collector = PipelineTraceCollector(clock_ns=lambda: 2_000)
    trace = collector.create(trace_id="sealed-1", meeting_id="meeting-sealed")

    trace.record_terminal(
        "failed",
        result_outcome="timeout",
        error_class="provider_timeout",
        monotonic_ns=3_000,
    )

    execution = trace.to_dict()["execution"]
    completeness = execution["required_stage_completeness"]
    assert completeness["complete"] is True
    assert completeness["missing_stages"] == []
    assert completeness["accounted_count"] == completeness["required_count"]
    assert execution["provenance"]["first_token"] == {
        "status": "unavailable",
        "reason": "trace_terminal_before_stage_observed",
        "at_monotonic_ns": 3_000,
        "attributes": {"sealed": True},
    }
    assert execution["timing_contract"] is None


def test_app_lifecycle_fills_failure_without_overwriting_real_attempt_branch(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)
    collector = app.state.pipeline_traces
    collector.record(
        "job-1",
        "job_queued",
        meeting_id="meeting-1",
        attributes={"lane": "intelligence"},
        monotonic_ns=1_000,
    )
    collector.record_route(
        "job-1",
        "pi_coach",
        candidate_outcome="eligible",
        circuit_outcome="admitted",
        monotonic_ns=2_000,
    )
    collector.record_provider_attempt(
        "job-1",
        1,
        branch="coach",
        runtime="pi",
        monotonic_ns=3_000,
    )
    app.state.record_v2_job_lifecycle(
        {
            "event": "attempt_failed",
            "job_id": "job-1",
            "meeting_id": "meeting-1",
            "lane": "intelligence",
            "attempt_index": 1,
            "durable_status": "retry_wait",
            "result_outcome": "rate_limit",
            "error_class": "RateLimited",
            "http_status": 429,
            "branch": "coach",
            "runtime": "pi",
            "lifecycle_at_monotonic_ns": 4_000,
        }
    )
    app.state.record_v2_job_lifecycle(
        {
            "event": "terminal",
            "job_id": "job-1",
            "meeting_id": "meeting-1",
            "lane": "intelligence",
            "attempt_index": 1,
            "durable_status": "failed",
            "terminal_outcome": "failed",
            "result_outcome": "rate_limit",
            "error_class": "RateLimited",
            "http_status": 429,
            "branch": "coach",
            "runtime": "pi",
            "lifecycle_at_monotonic_ns": 5_000,
        }
    )

    execution = collector.export("job-1")["execution"]
    assert execution["provider_attempts"] == [
        {
            "attempt_index": 1,
            "branch": "coach",
            "runtime": "pi",
            "started_at_monotonic_ns": 3_000,
            "completed_at_monotonic_ns": 4_000,
            "outcome": "rate_limit",
            "http_status": 429,
            "error_class": "RateLimited",
        }
    ]
    assert execution["terminal"]["result_outcome"] == "rate_limit"
    assert execution["provenance"]["provider_completed"]["status"] == "failed"
    assert execution["provenance"]["provider_connected"] == {
        "status": "unavailable",
        "reason": "bridge_response_unavailable",
        "at_monotonic_ns": 4_000,
        "attributes": {},
    }
    assert execution["provenance"]["first_token"]["status"] == "unavailable"
    assert execution["provenance"]["agent_tool_loop"]["status"] == "unavailable"
    assert execution["provenance"]["response_validation"]["status"] == "unavailable"
    assert execution["provenance"]["persistence_commit"]["status"] == "not_required"
    assert execution["provenance"]["projection_commit"]["status"] == "not_required"
    assert execution["provenance"]["late_result_guard"]["status"] == "not_required"

    report = build_realtime_slo_report(collector.slo_snapshots(), meeting_id="meeting-1")
    lane = report["lanes"]["intelligence"]
    assert lane["job_outcomes"]["result_counts"]["rate_limit"] == 1
    assert lane["provider_attempts"]["counts"]["rate_limit"] == 1
    assert lane["slo_verdict"]["availability_status"] == "fail"

    app.state.v2_persistence.close()


def test_successful_pi_output_accounts_for_tool_validation_persistence_and_projection(tmp_path) -> None:
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    committed = persistence.commit_final_and_enqueue(
        meeting_id="meeting-complete-trace",
        final_id="complete-final-1",
        segment_id="complete-segment-1",
        text="周五发布前需要确认压测。",
        normalized_text="周五发布前需要确认压测。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="complete-hash-1",
        now_ms=1_000,
    )
    job_id = committed["job_ids"]["intelligence"]
    claimed = persistence.claim_next_job(
        worker_id="trace-worker",
        lane="intelligence",
        now_ms=4_000,
        lease_ms=30_000,
    )
    assert claimed is not None and claimed["id"] == job_id
    output = {
        "applied": {"job_id": job_id, "coach_decision": {"status": "intervention"}},
        "provider_availability": {
            "scope": "pi_coach",
            "admitted": True,
            "provider_attempted": True,
            "provider_attempt_count": 1,
            "terminal_status": "completed",
            "terminal_reason": "provider_completed",
        },
        "coach": {
            "runtime_used": "pi",
            "delivery_status": "on_time",
            "late_result_discarded": False,
            "agent_metrics": {"turns": 2, "tool_calls": 3},
        },
    }
    completed = persistence.complete_job(
        job_id=job_id,
        worker_id="trace-worker",
        now_ms=4_100,
        output=output,
    )
    assert completed is not None
    collector = app.state.pipeline_traces
    collector.record(job_id, "job_queued", meeting_id="meeting-complete-trace", attributes={"lane": "intelligence"}, monotonic_ns=1_000)
    collector.record_route(job_id, "pi_coach", candidate_outcome="eligible", circuit_outcome="admitted", monotonic_ns=2_000)
    collector.record_provider_attempt(job_id, 1, branch="coach", runtime="pi", monotonic_ns=2_500)
    collector.record_provider_attempt_outcome(job_id, 1, "success", branch="coach", runtime="pi", monotonic_ns=3_000)

    app.state.record_v2_job_lifecycle(
        {
            "event": "terminal",
            "job_id": job_id,
            "meeting_id": "meeting-complete-trace",
            "lane": "intelligence",
            "durable_status": "succeeded",
            "terminal_outcome": "success",
            "result_outcome": "success",
            "lifecycle_at_monotonic_ns": 5_000,
        }
    )

    execution = collector.export(job_id)["execution"]
    provenance = execution["provenance"]
    assert provenance["agent_tool_loop"]["status"] == "observed"
    assert provenance["agent_tool_loop"]["attributes"] == {"turns": 2, "tool_calls": 3}
    assert provenance["response_validation"]["status"] == "observed"
    assert provenance["persistence_commit"]["status"] == "observed"
    assert provenance["projection_commit"]["status"] == "observed"
    assert provenance["late_result_guard"]["attributes"] == {"outcome": "on_time"}
    assert execution["terminal"]["result_outcome"] == "success"
    persistence.close()


def test_durable_timeout_audit_is_not_misreported_as_provider_success(tmp_path) -> None:
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    committed = persistence.commit_final_and_enqueue(
        meeting_id="meeting-timeout-trace",
        final_id="timeout-final-1",
        segment_id="timeout-segment-1",
        text="我们周五发布，但条件还没确认。",
        normalized_text="我们周五发布，但条件还没确认。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="timeout-hash-1",
        now_ms=1_000,
    )
    job_id = committed["job_ids"]["intelligence"]
    claimed = persistence.claim_next_job(
        worker_id="timeout-worker",
        lane="intelligence",
        now_ms=4_000,
        lease_ms=30_000,
    )
    assert claimed is not None
    persistence.complete_job(
        job_id=job_id,
        worker_id="timeout-worker",
        now_ms=4_100,
        output={
            "applied": {"job_id": job_id, "coach_decision": {"status": "timed_out"}},
            "provider_availability": {
                "scope": "pi_coach",
                "admitted": True,
                "provider_attempted": True,
                "provider_attempt_count": 1,
                "terminal_status": "failed",
                "terminal_reason": "provider_timeout",
            },
            "coach": {
                "runtime_used": "pi",
                "delivery_status": "too_late",
                "late_result_discarded": True,
                "agent_metrics": {"late_result_discarded": True},
            },
        },
    )
    collector = app.state.pipeline_traces
    collector.record(job_id, "job_queued", meeting_id="meeting-timeout-trace", attributes={"lane": "intelligence"}, monotonic_ns=1_000)
    collector.record_route(job_id, "pi_coach", candidate_outcome="eligible", circuit_outcome="admitted", monotonic_ns=2_000)
    collector.record_provider_attempt(job_id, 1, branch="coach", runtime="pi", monotonic_ns=2_500)
    collector.record_provider_attempt_outcome(job_id, 1, "timeout", branch="coach", runtime="pi", monotonic_ns=3_000)

    app.state.record_v2_job_lifecycle(
        {
            "event": "terminal",
            "job_id": job_id,
            "meeting_id": "meeting-timeout-trace",
            "lane": "intelligence",
            "durable_status": "succeeded",
            "terminal_outcome": "success",
            "result_outcome": "success",
            "lifecycle_at_monotonic_ns": 5_000,
        }
    )

    execution = collector.export(job_id)["execution"]
    assert execution["terminal"]["outcome"] == "success"
    assert execution["terminal"]["result_outcome"] == "timeout"
    assert execution["provenance"]["late_result_guard"]["attributes"] == {
        "outcome": "discarded"
    }
    persistence.close()


def test_generic_lane_marks_candidate_and_reservation_not_required(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)
    app.state.record_v2_job_lifecycle(
        {
            "event": "terminal",
            "job_id": "correction-job",
            "meeting_id": "meeting-generic",
            "lane": "correction",
            "attempt_index": 1,
            "durable_status": "succeeded",
            "terminal_outcome": "success",
            "result_outcome": "success",
            "lifecycle_at_monotonic_ns": 1_000,
        }
    )
    provenance = app.state.pipeline_traces.export("correction-job")["execution"]["provenance"]
    assert provenance["candidate_gate"]["status"] == "not_required"
    assert provenance["reservation"]["status"] == "not_required"
    assert provenance["circuit_admission"]["status"] == "not_required"
    app.state.v2_persistence.close()


def test_claim_placeholder_can_be_refined_by_real_pi_attempt(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)
    app.state.record_v2_job_lifecycle(
        {
            "event": "job_claimed",
            "job_id": "pi-job",
            "meeting_id": "meeting-pi",
            "lane": "intelligence",
            "attempt_index": 1,
            "durable_status": "running",
            "lifecycle_at_monotonic_ns": 1_000,
        }
    )
    trace = app.state.pipeline_traces.get("pi-job")
    assert trace.execution_snapshot()["route"]["name"] == "unknown"
    assert trace.execution_snapshot()["provenance"]["provider_attempt_start"]["status"] == "not_observed"

    trace.record_route(
        "pi_coach",
        candidate_outcome="eligible",
        circuit_outcome="admitted",
        monotonic_ns=2_000,
    )
    trace.record_provider_attempt(
        1,
        branch="coach",
        runtime="pi",
        monotonic_ns=3_000,
    )
    execution = trace.execution_snapshot()
    assert execution["route"] == {
        "name": "pi_coach",
        "candidate_outcome": "eligible",
        "circuit_outcome": "admitted",
        "decided_at_monotonic_ns": 1_000,
    }
    assert execution["provenance"]["route_decision"]["status"] == "observed"
    assert execution["provenance"]["candidate_gate"]["status"] == "observed"
    assert execution["provenance"]["provider_attempt_start"]["status"] == "observed"
    app.state.v2_persistence.close()


def test_claim_lifecycle_callback_refines_unknown_route_from_durable_output(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)
    app.state.record_v2_job_lifecycle(
        {
            "event": "job_claimed",
            "job_id": "pi-job-terminal",
            "meeting_id": "meeting-pi-terminal",
            "lane": "intelligence",
            "attempt_index": 1,
            "durable_status": "running",
            "lifecycle_at_monotonic_ns": 1_000,
        }
    )

    app.state.record_v2_job_lifecycle(
        {
            "event": "terminal",
            "job_id": "pi-job-terminal",
            "meeting_id": "meeting-pi-terminal",
            "lane": "intelligence",
            "attempt_index": 1,
            "durable_status": "succeeded",
            "terminal_outcome": "success",
            "result_outcome": "success",
            "lifecycle_at_monotonic_ns": 2_000,
        }
    )

    execution = app.state.pipeline_traces.export("pi-job-terminal")["execution"]
    # There is no durable output in this synthetic callback, so the route is
    # refined from the terminal lifecycle event to the intelligence lane
    # rather than remaining at the claim-time ``unknown`` placeholder.
    assert execution["route"]["name"] == "direct_semantic"
    assert execution["route"]["candidate_outcome"] == "unknown"
    assert execution["route"]["circuit_outcome"] == "not_checked"
    assert execution["provenance"]["route_decision"]["status"] == "observed"
    app.state.v2_persistence.close()
