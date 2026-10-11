from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
import pytest

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.realtime_intelligence import CoachIntervention, RealtimeIntelligenceResponse, build_realtime_coach_provenance_decision
from meeting_copilot_web_mvp.user_memory import UserMemory


@pytest.mark.parametrize(("preparation_policy", "auto_allowed"), [(None, False), ("off", False), ("standard", True)])
def test_explicit_meeting_policy_overrides_memory_and_off_stops_automatic_pi_but_keeps_manual_visible(tmp_path, monkeypatch, preparation_policy, auto_allowed):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST", "0")
    config = app_module.llm_service.LlmConfig(base_url="https://example.test", api_key="test-key", model="test-model", is_mock=True)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_kw: None)
    calls = []

    async def semantic(**kwargs):
        now = time.perf_counter()
        return {"response": RealtimeIntelligenceResponse((), None, (), None), "transport_mode": "test", "usage": None, "model": "test-model",
            "ttft_ms": 1.0, "timings": {"started_at": now, "connected_at": now, "first_token_at": now, "completed_at": now}}

    async def coach(**kwargs):
        request = kwargs["request"]
        calls.append(request.trigger_type)
        if kwargs.get("before_attempt"):
            kwargs["before_attempt"](1)
        paragraph = request.new_paragraphs[0] if request.new_paragraphs else request.context_paragraphs[-1]
        intervention = CoachIntervention(event_type="commitment_risk", title="确认发布边界",
            recommendation="我先核对回滚负责人，再确认发布安排。", reason="当前承诺还缺少执行边界。",
            evidence_segment_ids=(paragraph.id,), evidence_quote=paragraph.text, urgency="high", confidence=0.99)
        result = build_realtime_coach_provenance_decision(request=request, origin="pi", status="intervention", intervention=intervention)
        now = time.time_ns() // 1_000_000
        return {**result, "transport_mode": "pi_agent_jsonl", "runtime_requested": "pi", "runtime_used": "pi", "model": "test-model",
            "ttft_ms": 10.0, "decision_latency_ms": 20.0, "agent_metrics": {"turns": 1, "tool_calls": 1},
            "timings": {"clock": "unix_epoch_ms", "started_at_ms": now, "first_token_at_ms": now + 10, "completed_at_ms": now + 20}}

    monkeypatch.setattr(app_module, "run_realtime_intelligence", semantic)
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", coach)
    app = app_module.create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()
    persistence = app.state.v2_persistence
    try:
        committed = persistence.commit_final_and_enqueue(meeting_id="policy", final_id="f1", segment_id="s1",
            text="我们一定周五上线，但回滚负责人还没定。", normalized_text="我们一定周五上线，但回滚负责人还没定。",
            source_track="microphone", started_at_ms=0, ended_at_ms=1000, evidence_hash="policy-hash", now_ms=time.time_ns() // 1_000_000)
        memory = UserMemory(app.state.knowledge_repository)
        candidate = memory.create(scope="global", kind="preference", memory_key="suggestion_policy", value="off")
        memory.update(candidate["id"], expected_revision=1, status="active")
        client = TestClient(app)
        if preparation_policy:
            response = client.put("/v2/meetings/policy/preparation", json={"proactive_suggestion_policy": preparation_policy})
            assert response.status_code == 200
        automatic = persistence.get_job(committed["job_ids"]["intelligence"])
        asyncio.run(app.state.v2_intelligence_job_handler_impl(automatic))
        assert len(calls) == (1 if auto_allowed else 0)
        created = client.post("/v2/meetings/policy/coach/request", headers={"Idempotency-Key": "manual-policy"}, json={"request": "请检查当前发布安排"})
        assert created.status_code == 202
        manual = persistence.get_job(created.json()["job"]["id"])
        asyncio.run(app.state.v2_intelligence_job_handler_impl(manual))
        assert calls[-1] == "user_request"
        snapshot = client.get("/v2/meetings/policy/snapshot").json()
        assert snapshot["coach_intervention"] is not None
        assert snapshot["coach_decision"]["trigger_type"] == "user_request"
        assert any(item.get("trigger_type") == "user_request" for item in snapshot["coach_history"])
    finally:
        app.state.knowledge_repository.close()
        persistence.close()
