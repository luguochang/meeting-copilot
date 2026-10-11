from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.realtime_intelligence import IntelligenceResponseValidationError
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded, IntelligenceProjectionError
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor
from meeting_copilot_web_mvp.streaming_llm_provider import ChatCompletionResult, ProviderTimings, TokenUsage, TransportMode
from fastapi.testclient import TestClient
from discussion_attention_fixtures import with_attention, wire_packet


@pytest.fixture
def state_app(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    now = time.time_ns() // 1_000_000
    app.state.v2_persistence.create_meeting(meeting_id="review", title=None, now_ms=now - 100_000)
    app.state.meeting_preparation_store.save("review", live_analysis_profile="participation", updated_at_ms=now)
    yield app, now
    app.state.v2_persistence.close()


def append(p, seq, text="通知可以稍后发送，本轮先确认支付后的权益发放。", *, now):
    return p.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
        text=text, normalized_text=text, started_at_ms=seq * 1_000, ended_at_ms=seq * 1_000 + 800,
        evidence_hash=f"h{seq}", now_ms=now)


def prepare(app, now, count=3, *, text=None):
    p = app.state.v2_persistence
    for seq in range(1, count + 1):
        if text is None:
            append(p, seq, now=now - 4_000 + seq)
        else:
            append(p, seq, text, now=now - 4_000 + seq)
    job = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
    assert job is not None
    running = p.claim_next_job(worker_id="state", lane="discussion_state", now_ms=now, lease_ms=5_000)
    assert running["id"] == job["id"]
    # This test isolates semantic transport from the unrelated per-final coach jobs.
    app.state.provider_lane_registry.sync_realtime_work(set())
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE meeting_id='review' AND kind='intelligence' AND id!=?", (job["id"],))
    return running


class Provider:
    def __init__(self, content=None, on_complete=None):
        self.calls = []
        self.content = content
        self.on_complete = on_complete

    async def complete(self, messages, **parameters):
        self.calls.append({"messages": messages, "parameters": parameters})
        if self.on_complete:
            self.on_complete()
        result = self.content or json.dumps({"paragraph_revisions": [], "follow_up": None,
            "topic_update": {"operation": "add", "title": "权益和通知失败边界", "summary": "确认支付后权益发放与通知时序",
                             "evidence_segment_ids": ["s1"], "evidence_quote": "通知可以稍后发送"},
            "state_changes": [{"type": "decision", "operation": "add", "item_id": "candidate-notify",
                "content": "通知可以稍后发送", "status": "candidate", "evidence_segment_ids": ["s1"],
                "evidence_quote": "通知可以稍后发送", "confidence": 0.9}]}, ensure_ascii=False)
        if messages:
            result = with_attention(result, json.loads(messages[1]["content"]))
        return ChatCompletionResult(content=result, transport_mode=TransportMode.STREAMING, fallback_reason=None,
            timings=ProviderTimings(started_at=1.0, connected_at=1.0, first_token_at=1.01, completed_at=1.02),
            usage=TokenUsage(prompt_tokens=50, completion_tokens=30, total_tokens=80), response_id="synthetic", model="synthetic-model", finish_reason="stop")


def test_short_or_unstable_input_and_cooldown_enqueue_no_model_work(state_app):
    app, now = state_app
    p = app.state.v2_persistence
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now) is None
    append(p, 1, "好的。", now=now - 10_000)
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now) is None
    append(p, 2, now=now - 8_000)
    append(p, 3, now=now - 2_000)
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now) is None
    first = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 1_000)
    assert first is not None and first["max_attempts"] == 1
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 2_000) is None
    assert p.claim_next_job(worker_id="deep", lane="pi_deep", now_ms=now + 2_000, lease_ms=1_000) is None
    assert len([j for j in p.list_jobs(meeting_id="review") if j["request_context"].get("task_mode") == "discussion_state"]) == 1


def test_state_handler_one_real_parser_call_projects_without_pi_or_fast(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now, 6)
    provider = Provider(on_complete=lambda: append(p, 7, "这是冻结之后的新段落，不应计入已理解游标。", now=now + 10))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert len(provider.calls) == result["provider_attempt_count"] == 1
    assert result["repair_attempted"] is False
    assert result["applied"]["coach_intervention"] is None and result["applied"]["coach_decision"] is None
    context = p.get_current_discussion("review")["context"]
    assert context["current_problem"] == "权益和通知失败边界"
    assert context["current_solution"] == "候选方案（尚未宣告采用）：通知可以稍后发送"
    assert context["last_semantic_seq"] == 6
    assert p.get_job(job["id"])["status"] == "running"  # It must not cancel its own successful projection.
    packet = json.loads(provider.calls[0]["messages"][1]["content"])
    assert len(packet["new_paragraphs"]) == 6
    assert packet["context_paragraphs"] == packet["retrieval_paragraphs"] == []
    assert packet["allow_paragraph_revisions"] is False
    assert packet["rolling_state"]["topic"] is None
    assert packet["state_task"]["pending_segment_ids"] == [f"s{i}" for i in range(1, 7)]
    assert packet["state_task"]["semantic_from_seq"] == 0
    assert "只维护本场讨论状态" in provider.calls[0]["messages"][0]["content"]
    assert "不能等最终决定才记住方案" in provider.calls[0]["messages"][0]["content"]
    assert "同批较早原文提问" in provider.calls[0]["messages"][0]["content"]
    assert result["state_interpretation"] == {"topic_operation": "add", "topic_evidence_segment_ids": ["s1"],
        "state_operations": [{"type": "decision", "operation": "add", "item_id": "candidate-notify", "evidence_segment_ids": ["s1"]}]}
    assert p.list_agent_work_items("review", kind="commitment") == []
    assert p.complete_job(job_id=job["id"], worker_id="state", now_ms=now + 20, output=result)
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 61_000) is None  # One short unseen paragraph.


@pytest.mark.parametrize("model,api_style,expect_low,count", [
    ("gpt-6-sol", "responses", True, 3),
    ("gpt-6-sol-2026-10-08", "responses", True, 12),
    ("gpt-6-sol.preview", "responses", True, 3),
    ("gpt-6-solar", "responses", False, 3),
    ("gpt-6-sol_preview", "responses", False, 3),
    ("gpt-6-sol.", "responses", False, 3),
    ("deepseek-flash", "responses", False, 3),
    ("gpt-6-sol", "chat_completions", False, 3),
])
def test_formal_state_handler_effort_reaches_final_wire_and_projects_once(
    state_app, monkeypatch, model, api_style, expect_low, count,
):
    app, now = state_app
    monkeypatch.setenv("LLM_GATEWAY_MODEL", model)
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", api_style)
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    config = app_module.llm_service.LlmConfig.from_env()
    assert config is not None and not config.is_mock
    job = prepare(app, now, count, text="通知可以稍后发送，本轮先确认支付后的权益发放。" * 3 if count == 12 else None)
    calls = []
    observed_timings = []
    monkeypatch.setattr(app_module, "observe_provider_timing", lambda *_args, **kwargs: observed_timings.append(kwargs["timings"]))

    async def run():
        valid_state = (await Provider().complete([])).content

        def gateway(request):
            body = json.loads(request.content)
            calls.append(body)
            assert request.method == "POST"
            assert request.url.path == ("/v1/responses" if api_style == "responses" else "/v1/chat/completions")
            assert body["stream"] is False
            typed_state = with_attention(valid_state, wire_packet(body))
            if api_style == "responses":
                payload = {"id": "synthetic-state-wire", "model": model, "status": "completed",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": typed_state}]}],
                    "usage": {"input_tokens": 50, "output_tokens": 30, "total_tokens": 80}}
            else:
                payload = {"id": "synthetic-state-wire", "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": typed_state},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}}
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return await app.state.v2_discussion_state_job_handler_impl(job)

    result = asyncio.run(run())
    assert len(calls) == result["provider_attempt_count"] == 1
    assert result["repair_attempted"] is False and result["usage"]["total_tokens"] == 80
    body = calls[0]
    assert body["model"] == model and body["stream"] is False
    assert result["transport_mode"] == "non_streaming"
    assert result["ttft_ms"] is None
    assert observed_timings[0]["connected_at"] is observed_timings[0]["first_token_at"] is None
    if api_style == "responses":
        assert body["store"] is False
        assert body.get("reasoning") == ({"effort": "low"} if expect_low else None)
        assert "reasoning_effort" not in body
        assert body["max_output_tokens"] >= (1_024 if count == 12 else 768)
        packet = json.loads(body["input"][0]["content"])
    else:
        assert "reasoning_effort" not in body and "reasoning" not in body
        assert body["max_completion_tokens"] >= 768
        packet = json.loads(body["messages"][1]["content"])
    system = body["instructions"] if api_style == "responses" else body["messages"][0]["content"]
    # Stage11.89 versions answer-only quotes and uncertain reply handling;
    # older 11.79/11.85 full-system freezes remain historical evidence.
    assert len(system.encode("utf-8")) == 7169
    assert __import__("hashlib").sha256(system.encode("utf-8")).hexdigest() == "7f6f91090befaaabb217947e173dc49def839b094c6bd080526c884529b8d9fc"
    assert len(packet["state_task"]["pending_segment_ids"]) == count
    assert result["state_interpretation"]["state_operations"] == [{"type": "decision", "operation": "add",
        "item_id": "candidate-notify", "evidence_segment_ids": ["s1"]}]
    assert result["applied"]["coach_intervention"] is None and result["applied"]["coach_decision"] is None
    p = app.state.v2_persistence
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == count
    assert p.complete_job(job_id=job["id"], worker_id="state", now_ms=now + 20, output=result)
    capability = TestClient(app).get("/v2/meetings/review/snapshot").json()["runtime"]["ai"]["capabilities"]["discussion_state"]
    assert capability["decision"] == "succeeded" and capability["job_id"] == job["id"]


def test_state_fresh_eight_and_earlier_readonly_evidence_are_distinct(state_app, monkeypatch):
    app, now = state_app
    job = prepare(app, now, 12)
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    packet = json.loads(provider.calls[0]["messages"][1]["content"])
    assert [item["id"] for item in packet["new_paragraphs"]] == [f"s{i}" for i in range(5, 13)]
    assert [item["id"] for item in packet["context_paragraphs"]] == ["s2", "s3", "s4"]
    assert [item["id"] for item in packet["retrieval_paragraphs"]] == ["s1"]
    assert len({item["id"] for field in ("new_paragraphs", "context_paragraphs", "retrieval_paragraphs") for item in packet[field]}) == 12
    for group in ("new_paragraphs", "context_paragraphs", "retrieval_paragraphs"):
        for item in packet[group]:
            seq = int(item["id"][1:])
            assert item["transcript_seq"] == seq
            assert (item["start_ms"], item["end_ms"]) == (seq * 1_000, seq * 1_000 + 800)
            assert item["revision"] == 1


def test_partial_state_projection_exposes_only_accepted_operations_and_safe_diagnostic(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    content = {"paragraph_revisions": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "通知时序", "summary": "讨论通知时序",
            "evidence_segment_ids": ["s1"], "evidence_quote": "通知可以稍后发送"},
        "state_changes": [{"type": "open_question", "operation": "resolve", "item_id": "unknown-q",
            "content": "通知可以稍后发送", "evidence_segment_ids": ["s1"], "evidence_quote": "通知可以稍后发送", "confidence": .9}]}
    provider = Provider(content=json.dumps(content, ensure_ascii=False))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert len(provider.calls) == result["provider_attempt_count"] == 1 and not result["repair_attempted"]
    assert result["state_interpretation"]["topic_operation"] == "add"
    assert result["state_interpretation"]["state_operations"] == []
    assert result["declared_state_operations"][0]["operation"] == "resolve"
    assert result["state_projection"] == result["applied"]["state_projection"]
    assert p.complete_job(job_id=job["id"], worker_id="state", now_ms=now + 20, output=result)
    snapshot = TestClient(app).get("/v2/meetings/review/snapshot").json()
    capability = snapshot["runtime"]["ai"]["capabilities"]["discussion_state"]
    assert capability["decision"] == "succeeded"
    assert capability["label"] == "部分问题闭环待核对"
    assert capability["projection_diagnostics"] == {"rejected_operation_count": 1}
    assert "未标为已回应" in capability["detail"]
    assert snapshot["discussion"]["context"]["current_problem"] == "通知时序"
    assert snapshot["discussion"]["context"]["responded_points"] == []
    events = p.list_events(meeting_id="review", after_seq=0, limit=200)
    assert not any(event["type"] == "meeting.open_question.updated" for event in events)
    applied_event = next(event for event in events if event["type"] == "meeting.intelligence.applied")
    assert applied_event["payload"]["state_projection"] == result["state_projection"]


def test_legacy_idempotent_applied_event_does_not_invent_accepted_model_operations(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    with p._write_transaction():
        p._append_event_locked(meeting_id="review", event_type="meeting.intelligence.applied",
            aggregate_type="meeting_intelligence", aggregate_id=job["id"], occurred_at_ms=now,
            idempotency_key=f"meeting.intelligence.applied:{job['id']}",
            payload={"job_id": job["id"], "source": "llm_first", "state_change_count": 1},
            correlation_id="review", causation_id=job["id"])
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert result["applied"]["idempotent"] is True and "state_projection" not in result["applied"]
    assert result["state_projection"]["legacy_projection_unavailable"] is True
    assert result["state_projection"]["accepted_operations"] == []
    assert result["state_interpretation"] == {"topic_operation": None,
        "topic_evidence_segment_ids": [], "state_operations": []}
    assert result["declared_state_operations"][0]["operation"] == "add"
    assert result["formal_event_context"]["topic_evidence"] is None
    assert p._conn.execute("SELECT COUNT(*) FROM meeting_entities WHERE meeting_id='review'").fetchone()[0] == 0


def test_state_validation_failure_does_not_retry_provider(state_app, monkeypatch):
    app, now = state_app
    job = prepare(app, now)
    provider = Provider(content="{invalid json")
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    with pytest.raises(IntelligenceResponseValidationError):
        asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert len(provider.calls) == 1
    assert app.state.v2_persistence.get_current_discussion("review")["context"].get("last_semantic_seq", 0) == 0
    assert app.state.provider_lane_registry.try_acquire_reconcile() is not None


@pytest.mark.parametrize("rule", ["quote_not_verbatim", "unknown_reference"])
def test_failed_state_handler_executor_and_http_keep_safe_actual_call_metadata(state_app, monkeypatch, rule):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    response = {"paragraph_revisions": [], "follow_up": None, "topic_update": None,
        "state_changes": [{"type": "decision", "operation": "add", "item_id": "synthetic-item",
            "content": "通知可以稍后发送", "status": "candidate", "confidence": .9,
            "evidence_segment_ids": ["s1" if rule == "quote_not_verbatim" else "not-in-request"],
            "evidence_quote": "private rejected quote" if rule == "quote_not_verbatim" else "通知可以稍后发送"}]}
    provider = Provider(content=json.dumps(response, ensure_ascii=False))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    with pytest.raises(IntelligenceResponseValidationError) as caught:
        asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    error = caught.value
    error.safe_failure_output["raw_response"] = "private rejected model response"
    error.safe_failure_output["failure_diagnostics"]["quote"] = "private original quote"
    executor = DurableJobExecutor(p, correction_handler=lambda _job: None, suggestion_handler=lambda _job: None,
        now_ms=lambda: now + 20)
    asyncio.run(executor._retry_after_handler_failure(job, "state", error, resume_generation=0))
    final = p.get_job(job["id"])
    assert final["status"] == "failed" and final["attempts"] == 1
    assert final["error_class"] == "intelligence_validation_evidence"
    assert final["output"]["provider_attempt_count"] == len(provider.calls) == 1
    assert final["output"]["failure_diagnostics"] == {"category": "evidence", "field_group": "state_changes",
        "rule": rule, "paragraph_counts": {"new": 3, "context": 0, "retrieval": 0},
        "failure_stage": "provider_result", "failure_source": "discussion_state_handler",
        "validation_phase": "core_contract"}
    assert final["output"]["usage"]["total_tokens"] == 80
    assert "private rejected quote" not in json.dumps(final["output"])
    assert "not-in-request" not in json.dumps(final["output"])
    assert "private rejected model response" not in json.dumps(final["output"])
    assert "private original quote" not in json.dumps(final["output"])
    assert p.get_current_discussion("review")["context"].get("last_semantic_seq", 0) == 0
    snapshot = TestClient(app).get("/v2/meetings/review/snapshot").json()
    failed = next(item for item in snapshot["jobs"] if item["id"] == job["id"])
    assert failed["failure_diagnostics"]["rule"] == rule
    capability = snapshot["runtime"]["ai"]["capabilities"]["discussion_state"]
    assert capability["state"] == "paused" and capability["decision"] == "failed"
    assert capability["error_class"] == "evidence_rejected"
    assert capability["job_id"] == job["id"]
    assert p.claim_next_job(worker_id="no-retry", lane="discussion_state", now_ms=now + 1_000, lease_ms=5_000) is None
    # max_attempts=1 also applies across timer-created jobs for this failed input.
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 60_000) is None
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 120_000) is None
    assert len(provider.calls) == 1
    assert len([item for item in p.list_jobs(meeting_id="review") if item["request_context"].get("task_mode") == "discussion_state"]) == 1


def test_latest_state_status_is_independent_of_old_success_and_stale_basis(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: Provider())
    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert p.complete_job(job_id=job["id"], worker_id="state", now_ms=now + 20, output=result)
    assert p.get_discussion_state_capability("review")["decision"] == "succeeded"
    assert result["discussion_identity"]["analysis_basis_revision"] != job["request_context"]["analysis_basis_revision"]
    success = TestClient(app).get("/v2/meetings/review/snapshot").json()["runtime"]["ai"]["capabilities"]["discussion_state"]
    assert success["decision"] == "succeeded" and success["job_id"] == job["id"]
    assert success["analysis_basis_revision"] == result["discussion_identity"]["analysis_basis_revision"]
    append(p, 4, "新的讨论补充了短信失败的处理条件，还需要明确收到回执后如何确认通知状态，以及新的权益是否也按当前候选流程处理。" * 3, now=now + 61_000)
    assert p.get_discussion_state_capability("review")["decision"] == "not_started"
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 65_000)
    assert queued is not None
    assert p.get_discussion_state_capability("review")["decision"] == "pending"
    running = p.claim_next_job(worker_id="later-state", lane="discussion_state", now_ms=now + 65_000, lease_ms=5_000)
    assert running["id"] == queued["id"]
    assert p.get_discussion_state_capability("review")["decision"] == "running"
    assert p.fail_job(job_id=queued["id"], worker_id="later-state", now_ms=now + 65_020,
        error_class="intelligence_validation_evidence")
    assert p.get_discussion_state_capability("review")["decision"] == "failed"
    topic = p.get_current_discussion("review")
    p.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=topic["version"],
        context_patch={"current_problem": "更正后的新议题"}, now_ms=now + 65_030)
    assert p.get_discussion_state_capability("review")["decision"] == "not_started"


def test_successful_state_topic_switch_projects_its_applied_identity_after_http_reload(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    first = prepare(app, now)
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: Provider())
    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(first))
    assert p.complete_job(job_id=first["id"], worker_id="state", now_ms=now + 20, output=result)
    old = p.get_current_discussion("review")
    text = "我们换个议题，明天的演示已经确认安排在主会议室，演示不涉及支付或短信。"
    append(p, 4, text, now=now + 30)
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 40,
        cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=0)
    assert queued["request_context"]["topic_id"] == old["work_item_id"]
    running = p.claim_next_job(worker_id="switched-state", lane="discussion_state", now_ms=now + 40, lease_ms=5_000)
    response = {"paragraph_revisions": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "演示安排", "summary": "明天的演示已经确认安排在主会议室",
            "evidence_segment_ids": ["s4"], "evidence_quote": text},
        "state_changes": [{"type": "decision", "operation": "add", "item_id": "demo-confirmed", "status": "confirmed",
            "content": "明天的演示已经确认安排在主会议室", "evidence_segment_ids": ["s4"], "evidence_quote": text, "confidence": .95}]}
    provider = Provider(content=json.dumps(response, ensure_ascii=False))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    switched = asyncio.run(app.state.v2_discussion_state_job_handler_impl(running))
    assert p.complete_job(job_id=running["id"], worker_id="switched-state", now_ms=now + 60, output=switched)
    current = p.get_current_discussion("review")
    assert current["work_item_id"] != old["work_item_id"]
    capability = TestClient(app).get("/v2/meetings/review/snapshot").json()["runtime"]["ai"]["capabilities"]["discussion_state"]
    assert capability["decision"] == "succeeded" and capability["job_id"] == running["id"]
    assert capability["topic_id"] == current["work_item_id"] == switched["discussion_identity"]["topic_id"]
    assert capability["last_semantic_seq"] == 4


@pytest.mark.parametrize("quote_valid", [True, False])
def test_state_handler_keeps_known_old_question_response_during_topic_switch(state_app, monkeypatch, quote_valid):
    app, now = state_app
    p = app.state.v2_persistence
    first = prepare(app, now, text="通知是否需要同步等待？结果页面如何显示？")
    initial_response = {"paragraph_revisions": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "通知等待方式", "summary": "讨论通知与页面",
            "evidence_segment_ids": ["s1"], "evidence_quote": "通知是否需要同步等待"},
        "state_changes": [{"type": "open_question", "operation": "add", "item_id": item_id, "content": quote,
            "evidence_segment_ids": ["s1"], "evidence_quote": quote, "confidence": .9}
            for item_id, quote in (("q-notification", "通知是否需要同步等待"), ("q-page", "结果页面如何显示"))]}
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: Provider(content=json.dumps(initial_response, ensure_ascii=False)))
    first_result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(first))
    assert p.complete_job(job_id=first["id"], worker_id="state", now_ms=now + 20, output=first_result)
    old = p.get_current_discussion("review")
    text = "通知不用同步等待，这个问题已经回答。现在讨论退款流程。退款什么时候到账？"
    append(p, 4, text, now=now + 30)
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 40,
        cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=0)
    running = p.claim_next_job(worker_id="switched-state", lane="discussion_state", now_ms=now + 40, lease_ms=5_000)
    assert running["id"] == queued["id"]
    response = {"paragraph_revisions": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "退款流程", "summary": "讨论退款到账",
            "evidence_segment_ids": ["s4"], "evidence_quote": "现在讨论退款流程"},
        "state_changes": [
            {"type": "open_question", "operation": "resolve", "item_id": "q-notification", "content": "通知不用同步等待",
             "evidence_segment_ids": ["s4"], "evidence_quote": "通知不用同步等待" if quote_valid else "本轮未提供的回答", "confidence": .9},
            {"type": "open_question", "operation": "add", "item_id": "q-refund", "content": "退款什么时候到账",
             "evidence_segment_ids": ["s4"], "evidence_quote": "退款什么时候到账", "confidence": .9}]}
    provider = Provider(content=json.dumps(response, ensure_ascii=False))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    before = p.list_agent_work_items("review", kind="discussion")
    if not quote_valid:
        with pytest.raises(IntelligenceResponseValidationError):
            asyncio.run(app.state.v2_discussion_state_job_handler_impl(running))
        assert p.list_agent_work_items("review", kind="discussion") == before
        assert p.get_current_discussion("review")["work_item_id"] == old["work_item_id"]
    else:
        result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(running))
        assert p.complete_job(job_id=running["id"], worker_id="switched-state", now_ms=now + 60, output=result)
        current = p.get_current_discussion("review")
        historical = next(item for item in p.list_agent_work_items("review", kind="discussion") if item["work_item_id"] == old["work_item_id"])
        assert [point["id"] for point in historical["context"]["open_points"]] == ["q-page"]
        answered = historical["context"]["responded_points"][0]
        assert answered["id"] == "q-notification" and answered["source_refs"] == ["s4"]
        assert answered["evidence"] == [{"segment_id": "s4", "revision": 1, "evidence_hash": "h4"}]
        assert current["context"]["responded_points"] == []
        assert [point["id"] for point in current["context"]["open_points"]] == ["q-refund"]
        snapshot = TestClient(app).get("/v2/meetings/review/snapshot").json()
        assert snapshot["discussion"]["work_item_id"] == current["work_item_id"]
        assert snapshot["runtime"]["ai"]["capabilities"]["discussion_state"]["decision"] == "succeeded"
        assert result["state_interpretation"]["state_operations"][0]["operation"] == "resolve"
    assert len(provider.calls) == 1
    packet = json.loads(provider.calls[0]["messages"][1]["content"])
    assert {point["id"] for point in packet["rolling_state"]["open_items"]} == {"q-notification", "q-page"}
    assert {point["id"] for point in packet["rolling_state"]["discussion"]["open_points"]} == {"q-notification", "q-page"}
    assert [paragraph["id"] for paragraph in packet["new_paragraphs"]] == ["s4"]
    assert "宿主将这条回应归入旧题，不复制到新题" in provider.calls[0]["messages"][0]["content"]
    assert "state_changes只维护新议题" not in provider.calls[0]["messages"][0]["content"]


def test_waiting_state_job_does_not_masquerade_as_running_coach_analysis(state_app):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET status='retry_wait' WHERE id=?", (job["id"],))
    capabilities = TestClient(app).get("/v2/meetings/review/snapshot").json()["runtime"]["ai"]["capabilities"]
    assert capabilities["discussion_state"]["decision"] == "retry_wait"
    assert capabilities["discussion_state"]["state"] == "idle"
    assert capabilities["proactive_suggestions"]["state"] != "busy"


def test_manual_preempts_state_job_and_late_projection_is_rejected(state_app):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    manual = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question", now_ms=now + 1,
                                         idempotency_key="manual-priority")
    assert job["id"] in manual["request_context"]["preempted_job_ids"]
    assert p.get_job(job["id"])["status"] == "cancelled"
    with pytest.raises(IntelligenceEvidenceSuperseded):
        p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now + 2,
            response={"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None})


@pytest.mark.parametrize("mutation", ["revision", "basis"])
def test_state_source_and_basis_mutation_invalidates_job(state_app, mutation):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    if mutation == "revision":
        p.commit_transcript_revision(meeting_id="review", segment_id="s1", expected_evidence_hash="h1", corrected_text="通知不可以稍后发送。", revision_id="rev", now_ms=now + 1)
    else:
        topic = p.get_current_discussion("review")
        p.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=topic["version"],
                                    context_patch={"current_problem": "开始讨论退款流程"}, now_ms=now + 1)
    assert p.discussion_request_state("review", job["request_context"]) != "valid"
    assert p.get_job(job["id"])["status"] == "cancelled"


def test_meeting_end_and_provider_disconnect_stop_background_without_call(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", lambda: None)
    with pytest.raises(IntelligenceEvidenceSuperseded, match="disconnected"):
        asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    p.end_meeting(meeting_id="review", now_ms=now + 1)
    assert p.discussion_request_state("review", job["request_context"]) == "meeting_ended"
    with pytest.raises(IntelligenceEvidenceSuperseded):
        p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now + 2,
            response={"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None})


def test_state_apply_cannot_smuggle_coach_or_correction(state_app):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    with pytest.raises(IntelligenceProjectionError, match="may only project"):
        p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now + 1,
            response={"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None,
                      "coach_decision": {"decision_id": "smuggled", "status": "intervention"}})


def test_successful_state_can_recheck_corrected_original_scope_without_new_speech(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    output = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert p.complete_job(job_id=job["id"], worker_id="state", now_ms=now + 1, output=output)
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 61_000) is None
    assert p.commit_transcript_revision(meeting_id="review", segment_id="s1", expected_evidence_hash="h1",
        corrected_text="通知不可以稍后发送，本轮先确认支付后的权益发放。", revision_id="correct-state", now_ms=now + 62_000)
    topic = p.get_current_discussion("review")
    assert topic["context"]["phase"] == "needs_review"
    assert topic["context"]["last_semantic_seq"] == 0
    retry = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 65_000)
    assert retry is not None
    assert any(item["segment_id"] == "s1" and item["revision"] == 2 for item in retry["request_context"]["evidence"])


def test_legacy_state_scheduler_can_be_disabled_and_insufficient_inputs_call_zero_times(state_app, monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_DIRECT_ENABLED", "0")
    app, now = state_app
    p = app.state.v2_persistence
    append(p, 1, "好的。", now=now - 5_000)
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    asyncio.run(app.state.refresh_due_coach_items())
    assert provider.calls == []
    assert not any(j["request_context"].get("task_mode") == "discussion_state" for j in p.list_jobs(meeting_id="review"))
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "0")
    append(p, 2, now=now - 5_000)
    append(p, 3, now=now - 5_000)
    asyncio.run(app.state.refresh_due_coach_items())
    assert provider.calls == []
    assert not any(j["request_context"].get("task_mode") == "discussion_state" for j in p.list_jobs(meeting_id="review"))


def test_legacy_state_executor_background_consumer_runs_scheduler_job_without_pi(state_app, monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_DIRECT_ENABLED", "0")
    app, now = state_app
    p = app.state.v2_persistence
    for seq in range(1, 4):
        append(p, seq, now=now - 35_000 + seq)
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    async def run():
        assert await app.state.refresh_due_coach_items() == 1
        job = next(job for job in p.list_jobs(meeting_id="review") if job["request_context"].get("task_mode") == "discussion_state")
        executor = app.state.v2_executor
        await executor.start()
        try:
            for _ in range(100):
                if p.get_job(job["id"])["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.02)
            completed = p.get_job(job["id"])
            assert completed["status"] == "succeeded", completed
            assert completed["output"]["task_mode"] == "discussion_state"
            assert completed["output"]["provider_attempt_count"] == 1
        finally:
            await executor.stop()
    asyncio.run(run())
    assert len(provider.calls) == 1
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 3


def test_legacy_state_first_scheduler_same_tick_does_not_buy_automatic_pi(state_app, monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_DIRECT_ENABLED", "0")
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    for seq in range(1, 4):
        append(p, seq, now=now - 5_000)
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE meeting_id='review'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    jobs = p.list_jobs(meeting_id="review", lane="intelligence")
    assert len([job for job in jobs if job["request_context"].get("task_mode") == "discussion_state"]) == 1
    assert not any(job["trigger_type"] == "discussion_update" and not job["request_context"].get("task_mode") for job in jobs)


def test_legacy_state_first_scheduler_waits_for_terminal_state_and_rejects_needs_review(state_app, monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_DIRECT_ENABLED", "0")
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    job = prepare(app, now)
    # Settle unchanged fake L2 before testing the independent state lease gate.
    # Keep the shared state helper and every original assertion unchanged.
    for _ in p.list_jobs(meeting_id="review", lane="correction"):
        correction = p.claim_next_job(worker_id="fixture-l2", lane="correction",
            now_ms=now, lease_ms=5_000)
        if correction is None or correction["meeting_id"] != "review":
            raise AssertionError("expected this fixture's correction job")
        segment_id = correction["evidence_segment_id"]
        unchanged = p.mark_correction_segments_no_change(meeting_id="review", segment_ids=[segment_id],
            max_input_transcript_seq=correction["input_transcript_seq"], now_ms=now,
            expected_evidence_hashes={segment_id: correction["evidence_hash"]})
        if unchanged != [segment_id]:
            raise AssertionError("fixture correction evidence did not match")
        if p.complete_job(job_id=correction["id"], worker_id="fixture-l2", now_ms=now,
                output={"fixture": "unchanged"}) is None:
            raise AssertionError("fixture correction did not complete")
    if p.has_pending_discussion_corrections("review"):
        raise AssertionError("fixture correction work remains active")
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    output = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert p.get_current_discussion("review")["context"]["phase"] == "discussing"
    # Successful projection alone is not enough while the state job holds its lease.
    assert p.get_job(job["id"])["status"] == "running"
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert p.complete_job(job_id=job["id"], worker_id="state", now_ms=now + 1, output=output)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    automatic = [item for item in p.list_jobs(meeting_id="review", lane="intelligence")
                 if item["trigger_type"] == "discussion_update" and not item["request_context"].get("task_mode")]
    assert len(automatic) == 1 and automatic[0]["request_context"]["analysis_basis_revision"] == 2
    assert len(provider.calls) == 1  # Scheduling an automatic job has not executed Pi.
    topic = p.get_current_discussion("review")
    p.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=topic["version"],
        context_patch={"phase": "needs_review"}, now_ms=now + 2)
    assert p.get_job(automatic[0]["id"])["status"] == "cancelled"
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert len([item for item in p.list_jobs(meeting_id="review", lane="intelligence")
                if item["trigger_type"] == "discussion_update" and not item["request_context"].get("task_mode")]) == 1


@pytest.mark.parametrize("lane", ["deep", "realtime"])
def test_provider_is_cancelled_when_priority_request_arrives(state_app, monkeypatch, lane):
    app, now = state_app
    job = prepare(app, now)
    cancelled = []
    class SlowProvider:
        async def complete(self, _messages, **_parameters):
            getattr(app.state.provider_lane_registry, f"reserve_{lane}")("manual-priority-other-meeting")
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: SlowProvider())
    started = time.monotonic()
    with pytest.raises(IntelligenceEvidenceSuperseded, match="preempted"):
        asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert cancelled == [True]
    assert time.monotonic() - started < 1.0


def test_state_handler_projects_while_existing_correction_is_active(state_app, monkeypatch):
    app, now = state_app
    job = prepare(app, now)
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    correction = app.state.provider_lane_registry.try_acquire_correction()
    assert correction is not None

    async def run():
        # A permanently occupied correction lane previously consumed the
        # entire 30s durable budget without even calling this Mock Provider.
        return await asyncio.wait_for(app.state.v2_discussion_state_job_handler_impl(job), timeout=0.5)

    try:
        result = asyncio.run(run())
        assert result["provider_attempt_count"] == len(provider.calls) == 1
        assert app.state.v2_persistence.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
        assert app.state.provider_lane_registry.active_correction_count == 1
    finally:
        correction.release()


def test_new_correction_does_not_cancel_running_state_provider(state_app, monkeypatch):
    app, now = state_app
    job = prepare(app, now)
    correction_leases = []

    class SlowProvider(Provider):
        async def complete(self, messages, **parameters):
            correction = app.state.provider_lane_registry.try_acquire_correction()
            assert correction is not None
            correction_leases.append(correction)
            await asyncio.sleep(0.25)  # Cross the handler's 0.2s preemption check.
            return await super().complete(messages, **parameters)

    provider = SlowProvider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    try:
        result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
        assert result["provider_attempt_count"] == len(provider.calls) == 1
        assert app.state.v2_persistence.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    finally:
        for lease in correction_leases:
            lease.release()


def test_manual_request_still_cancels_running_state_provider_and_waits_for_ack(state_app, monkeypatch):
    app, now = state_app
    job = prepare(app, now)
    cancelled = []

    class SlowProvider:
        async def complete(self, _messages, **_parameters):
            app.state.v2_persistence.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
                now_ms=now + 1, idempotency_key="manual-during-provider")
            # Mirror the API/durable executor's priority reservation rather
            # than leaving a persistence-only fixture absent from the registry.
            app.state.sync_deep_provider_reservations()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cancelled.append(True)

    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: SlowProvider())
    with pytest.raises(IntelligenceEvidenceSuperseded, match="preempted"):
        asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert cancelled == [True]
    assert app.state.v2_persistence.get_job(job["id"])["error_class"] == "preempted_by_user_request"
    assert app.state.provider_lane_registry.has_urgent_work
    app.state.provider_lane_registry.sync_deep_work(set())
    lease = app.state.provider_lane_registry.try_acquire_reconcile()
    assert lease is not None
    lease.release()


def test_actual_correction_revision_still_cancels_running_state(state_app, monkeypatch):
    app, now = state_app
    job = prepare(app, now)
    correction_leases, cancelled = [], []

    class RevisingProvider:
        async def complete(self, _messages, **_parameters):
            registry = app.state.provider_lane_registry
            assert registry.try_acquire_discussion_state() is None
            correction = registry.try_acquire_correction()
            assert correction is not None
            correction_leases.append(correction)
            app.state.v2_persistence.commit_transcript_revision(meeting_id="review", segment_id="s1",
                expected_evidence_hash="h1", corrected_text="通知不可以稍后发送。", revision_id="concurrent-correction", now_ms=now + 1)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: RevisingProvider())
    try:
        with pytest.raises(IntelligenceEvidenceSuperseded, match="preempted"):
            asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
        assert cancelled == [True]
        assert app.state.v2_persistence.get_job(job["id"])["status"] == "cancelled"
        assert app.state.v2_persistence.get_current_discussion("review")["context"].get("last_semantic_seq", 0) == 0
    finally:
        for lease in correction_leases:
            lease.release()
    lease = app.state.provider_lane_registry.try_acquire_reconcile()
    assert lease is not None
    lease.release()


@pytest.mark.parametrize("change", ["manual", "revision"])
def test_native_nonstream_http_request_unwinds_before_releasing_state_lease(state_app, change):
    app, now = state_app
    job = prepare(app, now)
    unwound, posts = [], []

    async def run():
        async def gateway(request):
            posts.append(request)
            assert json.loads(request.content)["stream"] is False
            registry = app.state.provider_lane_registry
            assert registry.try_acquire_discussion_state() is None
            if change == "manual":
                app.state.v2_persistence.enqueue_user_coach_request(
                    meeting_id="review", user_request="", intent="ask_question",
                    now_ms=now + 1, idempotency_key="real-local-transport-preemption")
                app.state.sync_deep_provider_reservations()
            else:
                app.state.v2_persistence.commit_transcript_revision(
                    meeting_id="review", segment_id="s1", expected_evidence_hash="h1",
                    corrected_text="Changed fact invalidates the frozen basis.",
                    revision_id="real-local-transport-revision", now_ms=now + 1)
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                # Cancellation ACK precedes lease release; a second state cannot enter.
                assert registry.try_acquire_discussion_state() is None
                unwound.append(True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            with pytest.raises(IntelligenceEvidenceSuperseded):
                await app.state.v2_discussion_state_job_handler_impl(job)
            assert not client.is_closed
    asyncio.run(run())
    assert len(posts) == len(unwound) == 1
    app.state.provider_lane_registry.sync_deep_work(set())
    lease = app.state.provider_lane_registry.try_acquire_discussion_state()
    assert lease is not None
    lease.release()
