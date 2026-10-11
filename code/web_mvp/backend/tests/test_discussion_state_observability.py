"""State-only consumer diagnostics and transport facts, with no network/model."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.discussion_state_diagnostics import (
    SCHEMA, DiscussionProjectionRejection, sanitize_state_failure_output,
)
from meeting_copilot_web_mvp.realtime_intelligence import RealtimeIntelligenceResponse
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor
from discussion_attention_fixtures import wire_packet, with_attention
from test_discussion_state_background import Provider, append, state_app as shared_state_app

state_app = shared_state_app


async def queue_state(app, now, *, text=None):
    p = app.state.v2_persistence
    for seq in range(1, 4):
        if text is None:
            append(p, seq, now=now - 4_000 + seq)
        else:
            append(p, seq, text, now=now - 4_000 + seq)
    # Only the actual state consumer is under test; per-final legacy/correction
    # fixtures are cancelled before any executor can claim them.
    p._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_other_lanes_disabled' WHERE status='pending'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    assert await app.state.refresh_due_coach_items() == 1
    return next(j for j in p.list_jobs(meeting_id="review") if j["request_context"].get("task_mode") == "discussion_state")


async def run_consumer(app, job, *, on_started=None):
    executor = app.state.v2_executor
    await executor.start()
    try:
        if on_started is not None:
            await on_started()
        for _ in range(150):
            current = app.state.v2_persistence.get_job(job["id"])
            if current["status"] in {"succeeded", "failed", "cancelled"}:
                return current
            await asyncio.sleep(.02)
        raise AssertionError("local state consumer did not finish")
    finally:
        await executor.stop()


def state_response(content):
    """Native state uses one JSON response, with no invented streaming clocks."""
    return httpx.Response(200, json={"id": "public-state-fixture", "model": "synthetic-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}})


def trace(app, job):
    return app.state.pipeline_traces.get(job["id"]).to_dict()


@pytest.mark.parametrize("case", ["success", "validation", "apply", "serialization", "formal_context"])
def test_actual_state_consumer_preserves_provider_facts_across_host_failures(state_app, monkeypatch, case):
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    calls = []
    before = None

    async def run():
        nonlocal before
        job = await queue_state(app, now)
        before = p.get_current_discussion("review")
        content = (await Provider().complete([])).content
        if case == "validation":
            payload = json.loads(content)
            payload["state_changes"][0]["evidence_quote"] = "private rejected model fixture"
            content = json.dumps(payload, ensure_ascii=False)
        if case == "apply":
            original = p._observe_discussion_semantics_locked
            def fail_after_partial_projection(**kwargs):
                original(**kwargs)
                raise ValueError("private exception body fixture")
            monkeypatch.setattr(p, "_observe_discussion_semantics_locked", fail_after_partial_projection)
        elif case == "serialization":
            def fail_serialization(_response):
                raise ValueError("private serialization fixture")
            monkeypatch.setattr(RealtimeIntelligenceResponse, "to_dict", fail_serialization)
        elif case == "formal_context":
            def fail_context(**_kwargs):
                raise ValueError("private provenance fixture")
            monkeypatch.setattr(app_module, "build_llm_first_event_context", fail_context)

        def gateway(request):
            calls.append(request.method)
            assert request.method == "POST" and request.url.path == "/v1/chat/completions"
            return state_response(with_attention(content, wire_packet(json.loads(request.content))))

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return job, await run_consumer(app, job)

    job, final = asyncio.run(run())
    assert calls == ["POST"] and final["attempts"] == final["max_attempts"] == 1
    execution = trace(app, job)["execution"]
    assert len(execution["provider_attempts"]) == 1
    attempt = execution["provider_attempts"][0]
    assert attempt["branch"] == "discussion_state" and attempt["runtime"] == "direct"
    assert attempt["outcome"] == ("validation_error" if case == "validation" else "success")
    assert attempt["started_at_monotonic_ns"] <= attempt["completed_at_monotonic_ns"]
    assert execution["provenance"]["provider_attempt_start"]["status"] == "observed"
    assert execution["provenance"]["provider_completed"]["reason"] != "provider_not_attempted"
    if case == "success":
        assert final["status"] == "succeeded" and final["output"]["usage"]["total_tokens"] == 80
        assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
        assert execution["provenance"]["response_validation"]["status"] == "observed"
        assert "provider_completed" in trace(app, job)["stages"]
        assert "provider_connected" not in trace(app, job)["stages"] and "first_token" not in trace(app, job)["stages"]
        assert final["output"]["ttft_ms"] is None and final["output"]["transport_mode"] == "non_streaming"
    else:
        assert final["status"] == "failed"
        assert final["error_class"] == ("intelligence_validation_evidence" if case == "validation" else "ValueError")
        safe = final["output"]
        assert safe["provider_attempt_count"] == 1 and safe["usage"]["total_tokens"] == 80
        diag = safe["failure_diagnostics"]
        expected_stage = {"validation": "provider_result", "apply": "host_projection",
            "serialization": "response_serialization", "formal_context": "formal_context"}[case]
        assert diag["failure_stage"] == expected_stage and diag["failure_source"] == "discussion_state_handler"
        assert diag["category"] == ("evidence" if case == "validation" else "unclassified")
        assert diag["rule"] == ("quote_not_verbatim" if case == "validation" else "unclassified")
        assert p.get_current_discussion("review") == before
        assert not any(e["causation_id"] == job["id"] for e in p._conn.execute("SELECT causation_id FROM meeting_events WHERE type='meeting.intelligence.applied'"))
        assert "private" not in json.dumps(safe)
        snapshot = TestClient(app).get("/v2/meetings/review/snapshot").json()
        public = next(j for j in snapshot["jobs"] if j["id"] == job["id"])
        assert public["failure_diagnostics"] == diag and public["provider_attempt_count"] == 1
        assert snapshot["runtime"]["ai"]["capabilities"]["discussion_state"]["decision"] == "failed"
        assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 120_000) is None


@pytest.mark.parametrize("case", ["budget", "stale"])
def test_state_consumer_pre_call_rejection_never_creates_provider_attempt(state_app, monkeypatch, case):
    app, now = state_app
    calls = []
    async def run():
        job = await queue_state(app, now)
        if case == "budget":
            monkeypatch.setattr(app_module, "get_degradation_controller", lambda: type("Denied", (),
                {"can_call_llm": lambda self: False, "level": 2})())
        else:
            monkeypatch.setattr(app.state.v2_persistence, "discussion_request_state", lambda *_args: "stale")
        def gateway(_request):
            calls.append(True)
            raise AssertionError("pre-call rejection reached transport")
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return job, await run_consumer(app, job)
    job, final = asyncio.run(run())
    assert calls == [] and trace(app, job)["execution"]["provider_attempts"] == []
    assert final["status"] == ("failed" if case == "budget" else "cancelled")
    if case == "budget":
        assert final["output"]["provider_attempt_count"] == 0
        assert final["output"]["usage"] is None


def test_state_consumer_post_dispatch_cancellation_retains_one_real_start_without_fake_ttft(state_app, monkeypatch):
    app, now = state_app
    calls = []
    async def run():
        job = await queue_state(app, now)
        started = asyncio.Event()
        async def gateway(_request):
            calls.append("POST")
            started.set()
            await asyncio.Event().wait()
        async def priority():
            await asyncio.wait_for(started.wait(), 1)
            app.state.provider_lane_registry.sync_deep_work({"foreground-fixture"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            try:
                return job, await run_consumer(app, job, on_started=priority)
            finally:
                app.state.provider_lane_registry.sync_deep_work(set())
    job, final = asyncio.run(run())
    assert calls == ["POST"] and final["status"] == "cancelled"
    execution = trace(app, job)["execution"]
    assert len(execution["provider_attempts"]) == 1
    assert execution["provider_attempts"][0]["outcome"] == "cancelled"
    assert execution["provider_attempts"][0]["completed_at_monotonic_ns"] is not None
    assert "first_token" not in trace(app, job)["stages"]
    assert app.state.v2_persistence.get_current_discussion("review")["context"].get("last_semantic_seq", 0) == 0


def failure_payload():
    return {"schema_version": SCHEMA, "task_mode": "discussion_state", "provider_attempt_count": 1,
        "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        "failure_diagnostics": {"category": "unclassified", "field_group": "unclassified", "rule": "unclassified",
            "paragraph_counts": {"new": 3, "context": 0, "retrieval": 0},
            "failure_stage": "host_projection", "failure_source": "discussion_state_handler"}}


@pytest.mark.parametrize("path", ["fail", "exhausted_retry"])
def test_state_only_failure_contract_survives_both_terminal_paths_and_strips_sensitive_fields(state_app, path):
    from test_discussion_state_background import prepare
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    payload = failure_payload()
    payload["private"] = "private exception fixture"
    payload["failure_diagnostics"]["raw_quote"] = "private source fixture"
    kwargs = dict(job_id=job["id"], worker_id="state", now_ms=now + 10, error_class="ValueError", failure_output=payload)
    final = p.fail_job(**kwargs) if path == "fail" else p.retry_job(**kwargs, next_attempt_at_ms=now + 30)
    assert final["status"] == "failed" and final["error_class"] == "ValueError"
    assert final["output"] == failure_payload()
    assert "private" not in json.dumps(final["output"])
    assert p.claim_next_job(worker_id="no-retry", lane="discussion_state", now_ms=now + 100, lease_ms=1_000) is None


@pytest.mark.parametrize("invalid", ["source", "stage", "attempt", "usage", "field"])
def test_state_execution_metadata_rejects_non_contract_values(invalid):
    payload = failure_payload()
    if invalid == "attempt":
        payload["provider_attempt_count"] = 2
    elif invalid == "usage":
        payload["usage"]["raw"] = "private usage fixture"
        assert sanitize_state_failure_output(payload) == failure_payload()
        return
    else:
        field = {"source": "failure_source", "stage": "failure_stage", "field": "field_group"}[invalid]
        payload["failure_diagnostics"][field] = "private arbitrary fixture"
    assert sanitize_state_failure_output(payload) is None


@pytest.mark.parametrize("case", ["other_kind", "other_task", "retry_wait"])
def test_state_failure_metadata_does_not_change_foreign_lanes_or_retry_eligibility(state_app, case):
    from test_discussion_state_background import prepare
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    if case == "other_kind":
        p._conn.execute("UPDATE jobs SET kind='correction' WHERE id=?", (job["id"],))
    elif case == "other_task":
        p._conn.execute("UPDATE jobs SET request_context_json='{}' WHERE id=?", (job["id"],))
    else:
        p._conn.execute("UPDATE jobs SET max_attempts=3 WHERE id=?", (job["id"],))
    final = p.retry_job(job_id=job["id"], worker_id="state", now_ms=now + 10,
        next_attempt_at_ms=now + 30, error_class="ValueError", failure_output=failure_payload())
    assert final["output"] is None
    assert final["status"] == ("retry_wait" if case == "retry_wait" else "failed")
    if case == "retry_wait":
        claimed = p.claim_next_job(worker_id="existing-retry", lane="discussion_state", now_ms=now + 40, lease_ms=1_000)
        assert claimed["id"] == job["id"] and claimed["attempts"] == 2


def test_known_host_rule_reaches_real_consumer_terminal_without_partial_topic_changes(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    first = {"paragraph_revisions": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "通知等待", "summary": "讨论通知等待",
            "evidence_segment_ids": ["s1"], "evidence_quote": "通知是否需要同步等待"},
        "state_changes": [{"type": "open_question", "operation": "add", "item_id": "q-notify",
            "content": "通知是否需要同步等待", "evidence_segment_ids": ["s1"],
            "evidence_quote": "通知是否需要同步等待", "confidence": .9}]}
    second = {"paragraph_revisions": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "退款流程", "summary": "讨论退款流程",
            "evidence_segment_ids": ["s4"], "evidence_quote": "现在讨论退款流程"},
        "state_changes": [{"type": "decision", "operation": "update", "item_id": "q-notify",
            "content": "现在讨论退款流程", "status": "candidate", "evidence_segment_ids": ["s4"],
            "evidence_quote": "现在讨论退款流程", "confidence": .9}]}
    calls = []
    async def run():
        def gateway(request):
            payload = first if not calls else second
            calls.append("POST")
            return state_response(with_attention(json.dumps(payload, ensure_ascii=False), wire_packet(json.loads(request.content))))
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            job1 = await queue_state(app, now, text="通知是否需要同步等待？结果页面如何显示？")
            assert (await run_consumer(app, job1))["status"] == "succeeded"
            append(p, 4, "现在讨论退款流程。退款什么时候到账？", now=now + 20)
            p._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_other_lanes_disabled' WHERE status='pending'")
            app.state.provider_lane_registry.sync_realtime_work(set())
            job2 = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 30,
                cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=0)
            before = p.list_agent_work_items("review", kind="discussion")
            final = await run_consumer(app, job2)
            return job2, final, before
    job, final, before = asyncio.run(run())
    assert calls == ["POST", "POST"] and final["status"] == "failed"
    assert final["error_class"] == "ValueError"
    diag = final["output"]["failure_diagnostics"]
    assert diag["failure_stage"] == "host_projection" and diag["category"] == "unclassified"
    assert diag["field_group"] == "state_changes" and diag["rule"] == "cross_topic_old_question_operation_invalid"
    assert p.list_agent_work_items("review", kind="discussion") == before
    assert trace(app, job)["execution"]["provider_attempts"][0]["outcome"] == "success"
    assert not p._conn.execute("SELECT 1 FROM meeting_events WHERE causation_id=? AND type='meeting.intelligence.applied'", (job["id"],)).fetchone()


def test_shared_projection_rejection_preserves_legacy_consumer_value_error_without_state_output(state_app):
    app, now = state_app
    p = app.state.v2_persistence
    append(p, 1, now=now)
    legacy = next(j for j in p.list_jobs(meeting_id="review") if j["kind"] == "intelligence")
    assert legacy["request_context"].get("task_mode") != "discussion_state"
    p._conn.execute("UPDATE jobs SET status='cancelled' WHERE id!=?", (legacy["id"],))
    p._conn.execute("UPDATE jobs SET max_attempts=1 WHERE id=?", (legacy["id"],))
    calls = []

    async def reject(job):
        calls.append(job["id"])
        error = DiscussionProjectionRejection("transcript_reference_missing")
        error.safe_failure_output = failure_payload()
        raise error

    async def unused(_job):
        raise AssertionError("unrelated lane must not run")

    async def run():
        executor = DurableJobExecutor(p, correction_handler=unused, suggestion_handler=unused,
            additional_handlers={"intelligence": reject}, poll_interval_ms=5)
        app.state.v2_executor = executor
        return await run_consumer(app, legacy)

    final = asyncio.run(run())
    assert calls == [legacy["id"]] and final["status"] == "failed"
    assert final["error_class"] == "ValueError" and final["attempts"] == 1
    assert final["output"] is None
