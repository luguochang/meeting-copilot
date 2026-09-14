"""Regression coverage for the Provider-backed Pi lifecycle refresh path."""

import asyncio
import time

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.realtime_intelligence import (
    CoachIntervention,
    RealtimeIntelligenceResponse,
    build_realtime_coach_provenance_decision,
)


def test_production_due_refresh_uses_bounded_formal_event_pagination(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://provider.example.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "test-only-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    app_module.llm_service.clear_runtime_config()
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    persistence.create_meeting(
        meeting_id="due-refresh-pagination",
        title="Due refresh pagination",
        now_ms=1_000,
    )
    page_calls: list[tuple[int, int]] = []
    original_list_event_page = persistence.list_event_page

    def bounded_page(meeting_id, *, after_seq=0, limit=200):
        assert limit <= app_module.MAX_EVENT_PAGE_LIMIT
        page_calls.append((after_seq, limit))
        return original_list_event_page(
            meeting_id,
            after_seq=after_seq,
            limit=limit,
        )

    def oversized_legacy_read(*_args, **_kwargs):
        raise AssertionError("due refresh must not bypass bounded event pagination")

    monkeypatch.setattr(persistence, "list_event_page", bounded_page)
    monkeypatch.setattr(persistence, "list_events", oversized_legacy_read)

    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert page_calls == [(0, app_module.MAX_EVENT_PAGE_LIMIT)]


def test_explicit_resolution_refreshes_pi_and_supersedes_active_card(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def fake_intelligence(**_kwargs):
        now = time.perf_counter()
        return {
            "response": RealtimeIntelligenceResponse((), None, (), None),
            "transport_mode": "test",
            "ttft_ms": 1.0,
            "timings": {
                "started_at": now,
                "connected_at": now,
                "first_token_at": now,
                "completed_at": now,
            },
            "usage": None,
            "model": "test-realtime-model",
        }

    pi_calls = 0

    async def fake_pi_coach(**kwargs):
        nonlocal pi_calls
        pi_calls += 1
        kwargs["before_attempt"](1)
        request = kwargs["request"]
        paragraph = request.new_paragraphs[0]
        intervention = CoachIntervention(
            event_type="commitment_risk",
            title="补齐发布条件",
            recommendation="先确认回滚负责人和验收条件。",
            reason="当前发布承诺缺少可验证条件。",
            evidence_segment_ids=(paragraph.id,),
            evidence_quote=paragraph.text,
            urgency="high",
            confidence=0.99,
        )
        result = build_realtime_coach_provenance_decision(
            request=request,
            origin="pi",
            status="intervention",
            status_reason="intervention_submitted",
            decision_reason="条件仍未闭环。",
            intervention=intervention,
        )
        now_ms = time.time_ns() // 1_000_000
        result.update(
            {
                "transport_mode": "pi_agent_jsonl",
                "model": "test-realtime-model",
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "fallback_error_code": None,
                "fallback_reason": None,
                "ttft_ms": 20.0,
                "decision_latency_ms": 40.0,
                "timings": {
                    "clock": "unix_epoch_ms",
                    "started_at_ms": now_ms,
                    "first_token_at_ms": now_ms + 20,
                    "completed_at_ms": now_ms + 40,
                },
                "agent_metrics": {"turns": 1, "tool_calls": 1},
            }
        )
        return result

    monkeypatch.setattr(app_module, "run_realtime_intelligence", fake_intelligence)
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()
    persistence = app.state.v2_persistence
    meeting_id = "pi-lifecycle-refresh"
    base_ms = time.time_ns() // 1_000_000

    persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="pi-lifecycle-refresh-final-1",
        segment_id="pi-lifecycle-refresh-segment-1",
        text="我们一定周五上线，但回滚负责人还没定。",
        normalized_text="我们一定周五上线，但回滚负责人还没定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="pi-lifecycle-refresh-hash-1",
        source_track="microphone",
        now_ms=base_ms,
    )
    first_job = persistence.claim_next_job(
        worker_id="pi-lifecycle-refresh-worker-1",
        lane="intelligence",
        now_ms=base_ms + 1_000,
        lease_ms=30_000,
    )
    assert first_job is not None
    first_output = asyncio.run(app.state.v2_intelligence_job_handler_impl(first_job))
    persistence.complete_job(
        job_id=first_job["id"],
        worker_id="pi-lifecycle-refresh-worker-1",
        now_ms=base_ms + 100,
        output=first_output,
    )
    app.state.record_v2_job_lifecycle(
        {
            "event": "terminal",
            "job_id": first_job["id"],
            "meeting_id": meeting_id,
            "lane": "intelligence",
            "durable_status": "succeeded",
            "terminal_outcome": "success",
            "result_outcome": "success",
        }
    )
    first_decision = first_output["applied"]["coach_decision"]
    assert first_decision["status"] == "intervention"
    assert first_decision["origin"] == "pi"
    first_execution = app.state.pipeline_traces.export(first_job["id"])["execution"]
    first_provenance = first_execution["provenance"]
    assert first_provenance["agent_tool_loop"]["status"] == "observed"
    assert first_provenance["agent_tool_loop"]["attributes"] == {
        "turns": 1,
        "tool_calls": 1,
    }
    assert first_provenance["response_validation"]["status"] == "observed"
    assert first_provenance["persistence_commit"]["status"] == "observed"
    assert first_provenance["projection_commit"]["status"] == "observed"
    assert first_provenance["late_result_guard"]["attributes"] == {
        "outcome": "on_time"
    }

    persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="pi-lifecycle-refresh-final-2",
        segment_id="pi-lifecycle-refresh-segment-2",
        text="回滚负责人是王工，安全测试和压测已经通过，问题已经解决。",
        normalized_text="回滚负责人是王工，安全测试和压测已经通过，问题已经解决。",
        started_at_ms=1_100,
        ended_at_ms=2_000,
        evidence_hash="pi-lifecycle-refresh-hash-2",
        source_track="system_audio",
        now_ms=base_ms + 200,
    )
    second_job = persistence.claim_next_job(
        worker_id="pi-lifecycle-refresh-worker-2",
        lane="intelligence",
        now_ms=base_ms + 1_210,
        lease_ms=30_000,
    )
    assert second_job is not None
    second_output = asyncio.run(app.state.v2_intelligence_job_handler_impl(second_job))
    persistence.complete_job(
        job_id=second_job["id"],
        worker_id="pi-lifecycle-refresh-worker-2",
        now_ms=base_ms + 300,
        output=second_output,
    )

    assert pi_calls == 2
    second_decision = second_output["applied"]["coach_decision"]
    assert second_decision["status"] == "protected_silent"
    assert second_decision["origin"] == "pi"
    assert second_decision["runtime_used"] == "pi"
    assert second_decision["pi_provider_attempted"] is True
    assert second_decision["lifecycle_action"] == "deprioritize"
    assert second_decision["lifecycle_refresh"] is True
    assert second_decision["supersedes_decision_id"] == first_decision["decision_id"]
    assert second_decision["lifecycle_refresh_evidence_segment_ids"] == [
        "pi-lifecycle-refresh-segment-2"
    ]
    assert second_output["applied"]["coach_intervention"] is None
    assert app_module._latest_formal_coach_follow_up(
        persistence.list_events(meeting_id)
    ) is None


def test_production_worker_lifecycle_refresh_supersedes_old_card(tmp_path, monkeypatch):
    """The durable worker must project resolution as a superseding silent decision."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def fake_intelligence(**_kwargs):
        now = time.perf_counter()
        return {
            "response": RealtimeIntelligenceResponse((), None, (), None),
            "transport_mode": "test",
            "ttft_ms": 1.0,
            "timings": {
                "started_at": now,
                "connected_at": now,
                "first_token_at": now,
                "completed_at": now,
            },
            "usage": None,
            "model": "test-realtime-model",
        }

    pi_calls = 0

    async def fake_pi_coach(**kwargs):
        nonlocal pi_calls
        pi_calls += 1
        kwargs["before_attempt"](1)
        request = kwargs["request"]
        paragraph = request.new_paragraphs[0]
        intervention = CoachIntervention(
            event_type="commitment_risk",
            title="补齐发布条件",
            recommendation="先确认回滚负责人和验收条件。",
            reason="当前发布承诺缺少可验证条件。",
            evidence_segment_ids=(paragraph.id,),
            evidence_quote=paragraph.text,
            urgency="high",
            confidence=0.99,
        )
        result = build_realtime_coach_provenance_decision(
            request=request,
            origin="pi",
            status="intervention",
            status_reason="intervention_submitted",
            decision_reason="条件仍未闭环。",
            intervention=intervention,
        )
        now_ms = time.time_ns() // 1_000_000
        result.update(
            {
                "transport_mode": "pi_agent_jsonl",
                "model": "test-realtime-model",
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "fallback_error_code": None,
                "fallback_reason": None,
                "ttft_ms": 20.0,
                "decision_latency_ms": 40.0,
                "timings": {
                    "clock": "unix_epoch_ms",
                    "started_at_ms": now_ms,
                    "first_token_at_ms": now_ms + 20,
                    "completed_at_ms": now_ms + 40,
                },
                "agent_metrics": {"turns": 1, "tool_calls": 1},
            }
        )
        return result

    monkeypatch.setattr(app_module, "run_realtime_intelligence", fake_intelligence)
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()
    persistence = app.state.v2_persistence
    executor = app.state.v2_executor
    assert executor is not None
    meeting_id = "pi-lifecycle-worker-production"
    # This test exercises the durable Pi lifecycle path itself. The production
    # local_reflex fallback is covered separately; disable it here so the
    # first job reaches the fake Pi runtime and creates the card to supersede.
    monkeypatch.setattr(app_module, "build_local_reflex_intervention", lambda *_args, **_kwargs: None)
    class PiRuntime:
        async def evaluate(self, _payload):
            raise AssertionError("the routed Pi fake should handle this test")

    app.state.pi_coach_runtime = PiRuntime()
    now_ms = time.time_ns() // 1_000_000
    first = persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="pi-lifecycle-worker-final-1",
        segment_id="pi-lifecycle-worker-segment-1",
        text="我们一定周五上线，但回滚负责人还没定。",
        normalized_text="我们一定周五上线，但回滚负责人还没定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="pi-lifecycle-worker-hash-1",
        source_track="microphone",
        now_ms=now_ms,
    )
    first_job_id = first["job_ids"]["intelligence"]

    async def wait_for_job(job_id: str, timeout_s: float = 4.0) -> dict:
        deadline = time.perf_counter() + timeout_s
        while True:
            current = persistence.get_job(job_id)
            if current["status"] in {"succeeded", "failed", "cancelled"}:
                return current
            if time.perf_counter() >= deadline:
                raise AssertionError(f"worker did not settle {job_id}: {current['status']!r}")
            await asyncio.sleep(0.01)

    async def run_production_flow() -> tuple[dict, dict]:
        await executor.start()
        try:
            first_job = await wait_for_job(first_job_id)
            assert first_job["status"] == "succeeded"
            second = persistence.commit_final_and_enqueue(
                meeting_id=meeting_id,
                final_id="pi-lifecycle-worker-final-2",
                segment_id="pi-lifecycle-worker-segment-2",
                text="回滚负责人是王工，安全测试和压测已经通过，问题已经解决。",
                normalized_text="回滚负责人是王工，安全测试和压测已经通过，问题已经解决。",
                started_at_ms=1_100,
                ended_at_ms=2_000,
                evidence_hash="pi-lifecycle-worker-hash-2",
                source_track="system_audio",
                now_ms=time.time_ns() // 1_000_000,
            )
            second_job_id = second["job_ids"]["intelligence"]
            executor.wake("intelligence")
            second_job = await wait_for_job(second_job_id)
            return first_job, second_job
        finally:
            await executor.stop()

    first_job, second_job = asyncio.run(run_production_flow())
    assert pi_calls == 2
    assert first_job["output"]["coach"]["status"] == "intervention"
    second_coach = second_job["output"]["coach"]
    assert second_job["status"] == "succeeded"
    assert second_coach["status"] == "protected_silent"
    assert second_coach["lifecycle_refresh"] is True
    assert second_coach["lifecycle_action"] == "deprioritize"
    assert second_coach["runtime_requested"] == "pi"
    assert second_coach["runtime_used"] == "pi"
    assert second_coach["intervention"] is None
    assert second_coach["supersedes_decision_id"] == (
        first_job["output"]["coach"]["decision_id"]
    )
    events = persistence.list_events(meeting_id)
    applied = [event for event in events if event["type"] == "meeting.intelligence.applied"]
    assert len(applied) == 2
    assert applied[-1]["payload"]["coach_decision"]["lifecycle_refresh"] is True
    assert applied[-1]["payload"]["coach_intervention"] is None


def test_unrelated_or_weak_resolution_does_not_refresh_pi(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="unrelated-resolution",
        state_revision=1,
        new_paragraphs=[
            {
                "id": "fresh",
                "text": "另一个模块的测试已经通过。",
                "source_track": "system_audio",
            }
        ],
        context_paragraphs=[],
        rolling_state={"open_items": []},
    )
    assert app_module._pi_lifecycle_resolution_signal(
        request,
        {"title": "回滚负责人", "reason": "负责人仍未确认"},
    ) is False


def test_due_without_new_transcript_uses_pi_deep_recheck(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )
    provider_calls = 0

    async def fake_coach(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        assert kwargs["priority_mode"] == "deep"
        kwargs["before_attempt"](0)
        request = kwargs["request"]
        paragraph = request.context_paragraphs[-1]
        intervention = CoachIntervention(
            event_type="commitment_risk",
            title="复核回滚负责人",
            recommendation="我先确认回滚负责人，再确认周五是否可以上线。",
            reason="持久事项到期，需要复核原先的上线条件。",
            evidence_segment_ids=(paragraph.id,),
            evidence_quote=paragraph.text,
            urgency="medium",
            confidence=0.95,
        )
        result = build_realtime_coach_provenance_decision(
            request=request,
            origin="pi",
            status="intervention",
            status_reason="intervention_submitted",
            decision_reason="到期事项需要重新查证。",
            intervention=intervention,
        )
        result.update(
            {
                "transport_mode": "pi_agent_jsonl",
                "provider_lane": "pi_deep",
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "model": "test-realtime-model",
                "ttft_ms": 120.0,
                "decision_latency_ms": 800.0,
                "agent_metrics": {"turns": 2, "tool_calls": 2},
            }
        )
        return result

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.pi_coach_runtime = object()
    persistence = app.state.v2_persistence
    now_ms = time.time_ns() // 1_000_000
    persistence.commit_final_and_enqueue(
        meeting_id="due-local-recheck",
        final_id="due-local-final-1",
        segment_id="due-local-segment-1",
        text="周五上线，但回滚负责人还没确认。",
        normalized_text="周五上线，但回滚负责人还没确认。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="due-local-hash-1",
        source_track="microphone",
        now_ms=now_ms,
    )
    with persistence._write_transaction():
        persistence._append_event_locked(
            meeting_id="due-local-recheck",
            event_type="meeting.intelligence.applied",
            aggregate_type="meeting_intelligence",
            aggregate_id="due-local-origin-job",
            occurred_at_ms=now_ms,
            idempotency_key="due-local-origin-event",
            payload={
                "coach_decision": {
                    "decision_id": "due-local-decision-1",
                    "status": "intervention",
                    "lifecycle_action": "retain",
                },
                "coach_intervention": {
                    "event_type": "commitment_risk",
                    "title": "回滚负责人",
                    "evidence_segment_ids": ["due-local-segment-1"],
                    "evidence_quote": "回滚负责人还没确认",
                    "valid_until_ms": now_ms - 1,
                },
            },
        )
    due_job = persistence.enqueue_due_coach_refresh(
        meeting_id="due-local-recheck",
        decision_id="due-local-decision-1",
        now_ms=now_ms,
    )

    assert due_job is not None
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(due_job))

    assert provider_calls == 1
    coach = output["coach"]
    assert coach["provider_lane"] == "pi_deep"
    assert coach["runtime_requested"] == "pi"
    assert coach["runtime_used"] == "pi"
    assert coach["pi_provider_attempted"] is True
    assert coach["intervention"] is not None
    assert coach["soft_deadline_at_ms"] is None


def _resolution_request(text: str):
    return app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="natural-resolution",
        state_revision=1,
        new_paragraphs=[
            {"id": "fresh", "text": text, "source_track": "microphone"}
        ],
        context_paragraphs=[],
        rolling_state={"open_items": []},
    )


def test_natural_owner_deadline_answer_refreshes_active_card() -> None:
    request = _resolution_request(
        "监控阈值由值班负责人今天下午六点前修改，我负责复核，下周一的复盘不受影响。"
    )
    assert app_module._pi_lifecycle_resolution_signal(
        request,
        {
            "title": "监控阈值具体由谁来改？",
            "recommendation": "确认阈值负责人和时间。",
        },
    ) is True


def test_duplicate_question_quote_does_not_close_active_card() -> None:
    request = _resolution_request("C复盘报告我们下周一发，但监控阈值谁来改？还没定。")
    assert app_module._pi_lifecycle_resolution_signal(
        request,
        {
            "title": "监控阈值改动责任未闭环",
            "recommendation": "请确认这件事具体由谁负责？",
            "evidence_quote": "C复盘报告我们下周一发，但监控阈值谁来改？还没定。",
        },
    ) is False


def test_timeout_local_reflex_is_lifecycle_active_only_after_pi_attempt() -> None:
    meeting_id = "local-reflex-lifecycle"
    timeout_fallback = {
        "type": "meeting.intelligence.applied",
        "payload": {
            "coach_decision": {
                "decision_id": "decision-timeout-fallback",
                "status": "intervention",
                "origin": "local_reflex",
                "runtime_requested": "pi",
                "runtime_used": "local_reflex",
                "pi_provider_attempted": True,
                "fallback_reason": "provider_timeout",
                "status_reason": "provider_timeout_local_reflex",
                "lifecycle_action": "retain",
                "valid_until_ms": 99_999,
            },
            "coach_intervention": {
                "status": "intervention",
                "origin": "local_reflex",
                "runtime_used": "local_reflex",
                "pi_provider_attempted": True,
                "lifecycle_action": "retain",
                "title": "先补齐执行责任",
                "say_this": "先确认负责人和截止时间。",
                "evidence_quote": "负责人还没有确定。",
            },
        },
    }
    pre_provider_reflex = {
        "type": "meeting.intelligence.applied",
        "payload": {
            "coach_decision": {
                **timeout_fallback["payload"]["coach_decision"],
                "decision_id": "decision-pre-provider",
                "runtime_requested": "local_reflex",
                "pi_provider_attempted": False,
                "fallback_reason": None,
            },
            "coach_intervention": {
                **timeout_fallback["payload"]["coach_intervention"],
                "pi_provider_attempted": False,
            },
        },
    }

    class _Events:
        def __init__(self, events):
            self.events = events

        def list_events(self, _meeting_id, *, limit):
            assert limit == app_module.DEFAULT_EVENT_PAGE_LIMIT
            return self.events

    active = app_module._latest_active_pi_coach_intervention(
        _Events([timeout_fallback]), meeting_id, now_ms=1_000
    )
    assert active is not None
    assert active[0]["origin"] == "local_reflex"

    assert (
        app_module._latest_active_pi_coach_intervention(
            _Events([pre_provider_reflex]), meeting_id, now_ms=1_000
        )
        is None
    )


def test_natural_owner_only_answer_is_too_weak() -> None:
    request = _resolution_request("回滚负责人是王工。")
    assert app_module._pi_lifecycle_resolution_signal(
        request,
        {"title": "回滚负责人", "reason": "回滚条件还没有确认"},
    ) is False


def test_generic_completion_without_card_topic_does_not_refresh() -> None:
    request = _resolution_request("安全测试已经通过，问题已经解决。")
    assert app_module._pi_lifecycle_resolution_signal(
        request,
        {"title": "监控阈值", "reason": "阈值负责人和时间未确认"},
    ) is False


def test_user_request_trace_accounts_for_non_speech_input_boundary(tmp_path) -> None:
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    meeting_id = "user-request-provenance"
    committed = app.state.commit_v2_final(
        meeting_id,
        {
            "event_type": "final",
            "segment_id": "user-request-segment-1",
            "text": "周五发布前需要确认回滚负责人。",
            "normalized_text": "周五发布前需要确认回滚负责人。",
            "start_ms": 0,
            "end_ms": 1_000,
            "source_track": "microphone",
        },
    )
    user_job = app.state.v2_persistence.enqueue_user_coach_request(
        meeting_id=meeting_id,
        user_request="请检查当前会议还有哪些未闭环事项。",
        idempotency_key="user-request-provenance-1",
        now_ms=2_000,
    )

    app.state.record_v2_job_lifecycle(
        {
            "event": "job_claimed",
            "job_id": user_job["id"],
            "meeting_id": meeting_id,
            "lane": "intelligence",
            "lifecycle_at_monotonic_ns": 3_000,
        }
    )

    trace = app.state.pipeline_traces.export(user_job["id"])
    speech_stage = trace["execution"]["provenance"]["speech_endpoint"]
    assert speech_stage["status"] == "not_required"
    assert speech_stage["reason"] == "speech_endpoint_not_required_for_user_request"
    assert committed["job_ids"]["intelligence"] != user_job["id"]
