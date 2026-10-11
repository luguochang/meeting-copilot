"""Automatic due work must preserve intent, source scope and model cadence."""

import asyncio
import time

import pytest

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.automatic_coach_policy import (
    automatic_deep_coach_admission,
    automatic_intelligence_admission,
    automatic_task_due_admission,
)


def _policy(**extra):
    return automatic_task_due_admission(**{
        "policy": "low_frequency", "meeting_goal": "核对上线条件",
        "has_selected_evidence": False, "source_valid": True, "has_new_evidence": True,
        "decision_id": "original-card", "input_transcript_seq": 2,
        "prior_jobs": [], "now_ms": 100_000, **extra,
    })


@pytest.mark.parametrize("policy,goal,selected,valid", [
    ("off", "核对上线条件", True, True),
    ("low_frequency", None, False, True),
    ("standard", "核对上线条件", True, False),
])
def test_automatic_due_requires_enabled_intent_and_valid_original_source(policy, goal, selected, valid):
    assert not _policy(policy=policy, meeting_goal=goal, has_selected_evidence=selected,
                       source_valid=valid).allowed


@pytest.mark.parametrize("policy,created,after", [("low_frequency", 41_000, 101_000), ("standard", 81_000, 101_000)])
@pytest.mark.parametrize("trigger", ["delta", "answer_ready", "task_due"])
def test_all_automatic_model_attempts_share_due_and_answer_cadence(trigger, policy, created, after):
    prior = {"id": "paid", "trigger_type": trigger, "created_at_ms": created,
             "output": {"provider_attempt_count": 1}, "status": "failed"}
    assert not _policy(policy=policy, prior_jobs=[prior]).allowed
    assert _policy(policy=policy, prior_jobs=[prior], now_ms=after).allowed
    assert not automatic_deep_coach_admission(
        policy=policy, meeting_goal="核对上线条件", has_selected_evidence=False,
        prior_jobs=[prior], now_ms=100_000,
    ).allowed


def test_due_same_card_evidence_is_not_rearmed_after_failure_or_policy_skip():
    for status in ("failed", "succeeded", "cancelled", "running"):
        prior = {"id": "already-checked", "trigger_type": "task_due", "work_item_id": "original-card",
                 "input_transcript_seq": 2, "status": status, "created_at_ms": 1}
        assert not _policy(prior_jobs=[prior]).allowed
        assert _policy(prior_jobs=[prior], input_transcript_seq=3).allowed
    assert _policy(meeting_goal=None, has_selected_evidence=True).allowed
    assert _policy(has_new_evidence=False).allowed  # One explicit goal check, not a recurring timer.
    assert not _policy(meeting_goal=None, has_new_evidence=False).allowed


def test_urgent_does_not_reuse_cached_source_to_bypass_cadence_or_off():
    values = {"policy": "low_frequency", "prior_jobs": [{"id": "paid", "trigger_type": "task_due",
              "created_at_ms": 99_000, "output": {"provider_attempt_count": 1}}],
              "now_ms": 100_000, "current_job_id": "next", "urgent": True}
    assert not automatic_intelligence_admission(**values).allowed
    assert automatic_intelligence_admission(**values, has_new_evidence=True).allowed
    assert not automatic_intelligence_admission(**{**values, "policy": "off"}, has_new_evidence=True).allowed


def _case(tmp_path, monkeypatch, *, policy="low_frequency", goal=None):
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1", api_key="synthetic-only",
        model="synthetic-model", timeout_seconds=20, is_mock=True,
    )
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda cls: config))
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    def forbidden(*args, **kwargs):
        raise AssertionError("this automatic case must not enter any model")
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", forbidden)
    app = app_module.create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    p = app.state.v2_persistence
    mid = "automatic-due-synthetic"
    now = time.time_ns() // 1_000_000
    p.commit_final_and_enqueue(meeting_id=mid, final_id="final-old", segment_id="old",
        text="计划周五上线，但回滚负责人尚未确认。", normalized_text="计划周五上线，但回滚负责人尚未确认。",
        started_at_ms=0, ended_at_ms=1000, evidence_hash="old-hash", source_track="microphone", now_ms=now)
    app.state.meeting_preparation_store.save(mid, meeting_goal=goal,
        proactive_suggestion_policy=policy, updated_at_ms=now)
    with p._write_transaction():
        p._append_event_locked(meeting_id=mid, event_type="meeting.intelligence.applied",
            aggregate_type="meeting_intelligence", aggregate_id="original-job", occurred_at_ms=now,
            idempotency_key="original-card-event", payload={"source": "llm_first", "llm_called": True,
                "coach_decision": {"decision_id": "original-card", "status": "intervention",
                    "lifecycle_action": "retain", "origin": "pi", "runtime_used": "pi",
                    "pi_provider_attempted": True},
                "coach_intervention": {"event_type": "commitment_risk", "title": "确认回滚负责人",
                    "recommendation": "确认回滚负责人后再承诺上线。", "reason": "回滚负责人尚未确认。",
                    "origin": "pi", "runtime_used": "pi", "pi_provider_attempted": True,
                    "status": "intervention", "lifecycle_action": "retain", "evidence_segment_ids": ["old"],
                    "evidence_quote": "回滚负责人尚未确认", "valid_until_ms": now-1}})
    return app, p, mid, now


def _fresh(p, mid, now, text, *, track="microphone"):
    p.commit_final_and_enqueue(meeting_id=mid, final_id="final-fresh", segment_id="fresh",
        text=text, normalized_text=text, started_at_ms=1001, ended_at_ms=2000,
        evidence_hash="fresh-hash", source_track=track, now_ms=now+1)


@pytest.mark.parametrize("policy,goal", [("low_frequency", None), ("off", "核对上线条件")])
def test_scheduler_and_queued_execution_both_suppress_automatic_due_without_permission(tmp_path, monkeypatch, policy, goal):
    app, p, mid, now = _case(tmp_path, monkeypatch, policy=policy, goal=goal)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    job = p.enqueue_due_coach_refresh(meeting_id=mid, decision_id="original-card", now_ms=now+2)
    assert job is not None
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert output["provider_attempt_count"] == 0
    assert output["transport_mode"] == "automatic_coach_policy_suppressed"
    assert output["coach"]["suppression_reason"] in {"disabled_by_meeting_policy", "task_due_without_goal_or_evidence"}


def test_goal_due_is_queued_once_and_not_rearmed_by_scheduler_ticks(tmp_path, monkeypatch):
    app, p, mid, now = _case(tmp_path, monkeypatch, goal="核对上线条件")
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    due = [j for j in p.list_jobs(meeting_id=mid) if j["trigger_type"] == "task_due"]
    assert len(due) == 1


@pytest.mark.parametrize("track", ["microphone", "system_audio"])
def test_fresh_closure_is_local_and_wrong_track_cannot_close_or_spend(tmp_path, monkeypatch, track):
    app, p, mid, now = _case(tmp_path, monkeypatch, policy="off")
    _fresh(p, mid, now, "回滚负责人已经确认，由值班负责人负责，周五之前完成验证。", track=track)
    count = asyncio.run(app.state.refresh_due_coach_items())
    if track == "system_audio":
        assert count == 0
        return
    assert count == 1
    job = next(j for j in p.list_jobs(meeting_id=mid) if j["trigger_type"] == "task_due")
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert output["provider_attempt_count"] == 0
    assert output["transport_mode"] == "local_lifecycle_resolution"
    decision = output["applied"]["coach_decision"]
    assert decision["supersedes_decision_id"] == "original-card"
    assert decision["llm_called"] is False
    assert output["applied"]["work_item"]["state"] == "resolved"
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0


def test_legacy_card_revision_change_is_not_upgraded_by_latest_anchor(tmp_path, monkeypatch):
    app, p, mid, now = _case(tmp_path, monkeypatch, goal="核对上线条件")
    _fresh(p, mid, now, "下一项讨论日志采集。")
    job = p.enqueue_due_coach_refresh(meeting_id=mid, decision_id="original-card", now_ms=now+2)
    with p._write_transaction():
        p._conn.execute("UPDATE transcript_segments SET revision=revision+1, updated_at_ms=? WHERE meeting_id=? AND segment_id='old'",
                        (now+3, mid))
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert output["coach"]["suppression_reason"] == "task_due_source_superseded"
    assert output["provider_attempt_count"] == 0


def test_old_card_does_not_become_active_again_after_a_new_card(tmp_path, monkeypatch):
    app, p, mid, now = _case(tmp_path, monkeypatch, goal="核对上线条件")
    with p._write_transaction():
        p._append_event_locked(meeting_id=mid, event_type="meeting.intelligence.applied",
            aggregate_type="meeting_intelligence", aggregate_id="new-job", occurred_at_ms=now+1,
            idempotency_key="new-card-event", payload={"source": "llm_first", "llm_called": True,
              "coach_decision": {"decision_id": "new-card", "status": "intervention", "lifecycle_action": "retain"},
              "coach_intervention": {"evidence_segment_ids": ["old"], "evidence_quote": "回滚负责人尚未确认",
                                     "valid_until_ms": now+100_000}})
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0


@pytest.mark.parametrize("text", [
    "由轮值组负责回滚复核，明天也无法完成，测试没有通过。",
    "回滚负责人已经确认，但验收条件尚未满足，仍需要验证。",
    "由值班组负责回滚复核，明天处理，测试未通过。",
    "回滚条件不满足，由轮值组负责，明天复核。",
])
def test_unresolved_new_details_cannot_locally_close_card(tmp_path, monkeypatch, text):
    app, p, mid, now = _case(tmp_path, monkeypatch, policy="off")
    _fresh(p, mid, now, text)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0


def test_another_track_does_not_rearm_the_same_checked_card(tmp_path, monkeypatch):
    app, p, mid, now = _case(tmp_path, monkeypatch, goal="核对上线条件")
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    _fresh(p, mid, now, "系统音频正在播放另一场讨论。", track="system_audio")
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    jobs = [j for j in p.list_jobs(meeting_id=mid) if j["trigger_type"] == "task_due"]
    assert len(jobs) == 1
    # Even a independently queued job anchored to the other track must skip.
    job = p.enqueue_due_coach_refresh(meeting_id=mid, decision_id="original-card", now_ms=now+2)
    assert job is not None
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert output["coach"]["suppression_reason"] == "task_due_evidence_already_checked"
    assert output["provider_attempt_count"] == 0


def test_due_provider_receives_original_sources_and_only_their_track(tmp_path, monkeypatch):
    app, p, mid, now = _case(tmp_path, monkeypatch, goal="核对上线条件")
    _fresh(p, mid, now, "回滚方案仍在复核中。")
    p.commit_final_and_enqueue(meeting_id=mid, final_id="other-final", segment_id="other",
        text="系统音频正在播放新闻。", normalized_text="系统音频正在播放新闻。",
        started_at_ms=2001, ended_at_ms=3000, evidence_hash="other-hash", source_track="system_audio", now_ms=now+2)
    job = p.enqueue_due_coach_refresh(meeting_id=mid, decision_id="original-card", now_ms=now+3)
    captured = []

    class CapturedRequest(Exception):
        pass

    async def capture(**kwargs):
        kwargs["before_attempt"](1)
        captured.append(kwargs["request"])
        raise CapturedRequest()

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", capture)
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *a, **k: None)
    # Stop at the fake model boundary; the real handler has built and admitted
    # the request. No provider/network implementation is entered.
    try:
        asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    except CapturedRequest:
        pass
    assert len(captured) == 1
    request = captured[0]
    assert [item.id for item in request.new_paragraphs] == ["fresh"]
    assert [item.id for item in request.context_paragraphs] == ["old"]
    assert not request.retrieval_paragraphs
    assert request.rolling_state["current_answer"] is None
    assert all("other" not in window.segment_ids for window in request.semantic_windows)


@pytest.mark.parametrize("capture_active", [True, False])
def test_policy_suppression_is_not_provider_failure_or_waiting_for_recording(capture_active):
    status = app_module._coach_runtime_capability(
        enabled=True, provider_configured=True, active=False, capture_active=capture_active,
        requested_runtime="pi", latest_job={"output": {
            "transport_mode": "automatic_coach_policy_suppressed", "coach": {
                "status": "suppressed", "suppression_reason": "task_due_without_goal_or_evidence"}}},
    )
    assert status["state"] == "idle"
    assert status["label"] == "本轮无新介入"
    assert "error_class" not in status
