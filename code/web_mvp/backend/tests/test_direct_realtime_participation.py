"""Constructed native regression cases; no microphone or external model calls.

Direct automatic Pi must not depend on successful State or settled L2, while
its frozen current-need evidence and publication fences remain enforceable.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import replace

import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    parse_realtime_coach_response,
    run_realtime_coach_via_pi,
)
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded
from test_live_analysis_profile import _direct_final, profile_app as profile_app
from test_participation_current_need import Runtime, request


TEXTS = (
    "用户付款后应立即获得权益，通知可以稍后发送，目前先确定成功和失败的业务边界。",
    "通知失败时要重试，但是不应该阻止已经成功的权益发放，这个处理需要和前端一起确认。",
    "那如果通知服务暂时不可用，我们怎么区分付款成功和通知失败，让用户能够正确恢复？",
)

# Frozen synthetic discussion and terminal text from the real SDK r09/r14
# probes of 2026-10-11. Replaying it here makes no new provider call.
RECORDED_INPUT = (
    "用户付款后要立即看见权益，短信通知可以稍后送达。",
    "现在候选方案把权益发放和短信发送放在同一同步流程，任何一步失败就整体重试。",
    "大家想尽快上线，但尚未讨论短信失败后的整体重试会不会重复发放已经生效的权益。页面展示也要与权益状态一致。",
)
RECORDED_PROPOSALS = {
    "r09": {"event_type": "decision_readiness", "title": "拆开权益与通知的成功边界", "confidence": .92,
        "recommendation": "建议把权益生效作为付款后的成功边界，短信改为可独立重试的通知；上线验收时用短信失败场景验证权益只发一次、页面仍显示已生效，代价是要处理通知积压。",
        "reason": "付款后即时可见与短信可延迟的要求不同；同步整体重试把短信故障传导到发放，还可能重复发放，需明确成功边界及幂等验收。"},
    "r14": {"event_type": "execution_gap", "title": "拆分权益与短信的重试边界", "confidence": .93,
        "recommendation": "建议把权益生效和短信通知拆开：付款后先确保权益幂等生效、页面按已确认状态展示，短信单独重试；这样短信故障不会拖慢或重复发放权益。",
        "reason": "付款后权益须立即可见而短信可延后；若共用同步重试边界，短信失败会牵连权益发放，甚至让已生效权益重复处理。"},
}


def seed(app, client):
    assert client.put("/v2/meetings/review/preparation", json={
        "live_analysis_profile": "participation", "proactive_suggestion_policy": "standard",
    }).status_code == 200
    for seq, text in enumerate(TEXTS, 1):
        _direct_final(app.state.v2_persistence, seq, enqueue_intelligence=False, text=text)
    return time.time_ns() // 1_000_000


def claimed(p, now):
    queued = p.enqueue_realtime_participation(meeting_id="review", now_ms=now)
    assert queued is not None
    running = p.claim_next_job(worker_id="direct-fixture", lane="pi_deep", now_ms=now, lease_ms=30_000)
    assert running is not None and running["id"] == queued["id"]
    return running


def finish(p, job, now):
    assert p.complete_job(job_id=job["id"], worker_id="direct-fixture", now_ms=now + 1, output={})


def need(*, source="s3", quote=TEXTS[2], status="has_current_need"):
    return {"status": status, "focus_evidence": [{"segment_id": source, "quote": quote}],
        "reason": "最新提问仍需要明确付款成功与通知失败的恢复边界。"}


def result(assessment=None, *, silent=False):
    return {"action": "silent" if silent else "intervention", "current_need": assessment or need(),
        "decision_reason": "该问题已经回应，暂不重复。" if silent else None,
        "intervention": None if silent else {
            "event_type": "question_to_user", "title": "分开付款与通知的恢复边界", "urgency": "low",
            "confidence": .95, "recommendation": "建议分别定义付款成功与通知失败的状态，先确认通知重试会不会重复发放权益。",
            "reason": "当前正在问付款成功与通知失败该怎么区分。", "evidence_segment_ids": ["s3"],
            "evidence_quote": TEXTS[2], "supporting_facts": [{"text": TEXTS[2], "source_ref": "s3"}],
            "assumptions": [],
        }, "metrics": {"turns": 1, "tool_calls": 1,
            "tool_names": ["keep_silent" if silent else "submit_intervention"]}}


def native_runtime(app, monkeypatch, value, *, after=None):
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid", api_key="synthetic-test-key",
        model="synthetic-model", timeout_seconds=25, is_mock=True)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_k: None)
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST", "0")

    class CompletingRuntime(Runtime):
        async def evaluate(self, payload):
            response = await super().evaluate(payload)
            if after is not None:
                after()
            return response

    runtime = app.state.pi_coach_runtime = CompletingRuntime(copy.deepcopy(value))
    app.state.streaming_llm_client = object()  # A semantic fallback cannot send HTTP.
    return runtime


@pytest.mark.parametrize("state_failed", [False, True])
def test_pending_correction_and_absent_or_failed_state_do_not_block_native_pi(profile_app, monkeypatch, state_failed):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    if state_failed:
        state = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
        running = p.claim_next_job(worker_id="failed-state", lane="discussion_state", now_ms=now, lease_ms=30_000)
        assert state is not None and running["id"] == state["id"]
        assert p.fail_job(job_id=running["id"], worker_id="failed-state", now_ms=now + 1,
            error_class="provider_timeout")
    assert p.has_pending_discussion_corrections("review")
    job = claimed(p, now + 2)
    metadata = job["request_context"]
    assert metadata["automatic_direct"] is True and metadata.get("automatic_attention") is not True
    assert metadata["profile"] == "participation" and job["trigger_type"] == "discussion_update"
    assert metadata["current_need_basis"] == {"attention_covered_seq": 0, "packet_anchor_seq": 3,
        "focus_segment_ids": ["s2", "s3"], "unassessed_segment_ids": ["s2", "s3"]}
    runtime = native_runtime(app, monkeypatch, result())
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1
    assert output["coach"]["status"] == "intervention", output["coach"]
    assert output["applied"]["current_need"] == need()
    assert output["applied"]["coach_intervention"]["job_id"] == job["id"]
    assert p.get_current_discussion("review")["context"].get("last_semantic_seq", 0) == 0
    assert p.has_pending_discussion_corrections("review")


@pytest.mark.parametrize("bad", ["missing", "historical_focus", "nonverbatim", "answered"])
def test_direct_need_must_judge_frozen_recent_focus_before_intervention(profile_app, bad):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    job = claimed(p, now)
    assessment = {"historical_focus": need(source="s1", quote=TEXTS[0]),
        "nonverbatim": need(quote="付款已经成功且通知恢复完成。"), "answered": need(status="already_answered")}
    value = result(assessment.get(bad))
    if bad == "missing":
        value.pop("current_need")
    runtime = Runtime(value)
    with pytest.raises(IntelligenceResponseValidationError, match="current need"):
        asyncio.run(run_realtime_coach_via_pi(request=request(p, job), pi_runtime=runtime,
            provider_config={"model": "fixture", "timeout_seconds": 25}, priority_mode="deep"))
    assert len(runtime.calls) == 1
    assert p._conn.execute("SELECT COUNT(*) FROM meeting_events WHERE type='meeting.intelligence.applied'").fetchone()[0] == 0


def test_same_input_never_repeats_and_short_new_tail_obeys_cooldown_and_settling(profile_app):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    first = claimed(p, now)
    finish(p, first, now)
    _direct_final(p, 4, enqueue_intelligence=False, text="重试会重复发放吗？")
    p._conn.execute("UPDATE transcript_segments SET updated_at_ms=? WHERE segment_id='s4'", (now + 25_000,))
    assert p.enqueue_realtime_participation(meeting_id="review", now_ms=now + 29_000) is None
    assert p.enqueue_realtime_participation(meeting_id="review", now_ms=now + 31_000) is None
    second = p.enqueue_realtime_participation(meeting_id="review", now_ms=now + 35_000)
    assert second is not None and second["id"] != first["id"]
    assert second["request_context"]["current_need_basis"]["focus_segment_ids"] == ["s3", "s4"]
    running = p.claim_next_job(worker_id="direct-fixture", lane="pi_deep", now_ms=now + 35_000, lease_ms=30_000)
    assert running["id"] == second["id"]
    finish(p, running, now + 35_000)
    # Background bookkeeping alone is not new speech/material authorization.
    row = p._current_discussion_locked("review")
    context = json.loads(row["context_json"])
    context["last_semantic_seq"] = 4
    p._conn.execute("UPDATE agent_work_items SET context_json=? WHERE work_item_id=?",
        (json.dumps(context), row["work_item_id"]))
    assert p.enqueue_realtime_participation(meeting_id="review", now_ms=now + 120_000) is None


@pytest.mark.parametrize("manual", [False, True])
def test_active_contribution_or_user_request_blocks_new_automatic_queue(profile_app, manual):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    if manual:
        pending = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
            idempotency_key="direct-manual-priority", now_ms=now)
    else:
        pending = p.enqueue_realtime_participation(meeting_id="review", now_ms=now)
    assert pending is not None
    assert p.enqueue_realtime_participation(meeting_id="review", now_ms=now + 40_000) is None
    assert p.get_job(pending["id"])["status"] == "pending"


def test_direct_admission_cancels_running_background_state_and_returns_owned_id(profile_app):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    state = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
    running = p.claim_next_job(worker_id="background-fixture", lane="discussion_state", now_ms=now, lease_ms=30_000)
    assert state is not None and running["id"] == state["id"]
    direct = p.enqueue_realtime_participation(meeting_id="review", now_ms=now + 1)
    assert direct is not None and direct["request_context"]["preempted_job_ids"] == [state["id"]]
    cancelled = p.get_job(state["id"])
    assert cancelled["status"] == "cancelled" and cancelled["error_class"] == "preempted_by_realtime_participation"
    assert cancelled["lease_owner"] is None


@pytest.mark.parametrize("switch", ["enabled", "automatic_off", "direct_off", "policy_off"])
def test_native_scheduler_uses_direct_by_default_and_respects_switches(profile_app, monkeypatch, switch):
    app, client = profile_app
    seed(app, client)
    native_runtime(app, monkeypatch, result())
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "0")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "0" if switch == "automatic_off" else "1")
    if switch == "direct_off":
        monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_DIRECT_ENABLED", "0")
    else:
        monkeypatch.delenv("MEETING_COPILOT_DISCUSSION_DIRECT_ENABLED", raising=False)
    if switch == "policy_off":
        assert client.put("/v2/meetings/review/preparation", json={"proactive_suggestion_policy": "off"}).status_code == 200
    added = asyncio.run(app.state.refresh_due_coach_items())
    jobs = [j for j in app.state.v2_persistence.list_jobs(meeting_id="review")
        if j["request_context"].get("automatic_direct") is True]
    assert added == len(jobs) == (1 if switch == "enabled" else 0)


def test_real_correction_during_native_pi_completion_cannot_publish_old_direct_card(profile_app, monkeypatch):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    job = claimed(p, now)
    original = p._conn.execute("SELECT evidence_hash FROM transcript_segments WHERE segment_id='s3'").fetchone()

    def revise():
        p.commit_transcript_revision(meeting_id="review", segment_id="s3",
            expected_evidence_hash=original["evidence_hash"], corrected_text="通知不会重复发放权益，当前问题已经回答。",
            revision_id="direct-correction-boundary", now_ms=now + 1)

    runtime = native_runtime(app, monkeypatch, result(), after=revise)
    with pytest.raises(IntelligenceEvidenceSuperseded):
        asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1
    published = p._conn.execute("SELECT payload_json FROM meeting_events WHERE type='meeting.intelligence.applied' "
        "AND aggregate_id=?", (job["id"],)).fetchall()
    assert not any(json.loads(row["payload_json"]).get("coach_intervention") for row in published)


def test_direct_can_explicitly_stay_silent_after_latest_question_is_answered(profile_app, monkeypatch):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    job = claimed(p, now)
    assessment = need(status="already_answered")
    runtime = native_runtime(app, monkeypatch, result(assessment, silent=True))
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1
    assert output["applied"]["current_need"] == assessment
    assert output["applied"]["coach_intervention"] is None
    assert p.get_current_discussion("review")["context"].get("last_semantic_seq", 0) == 0


@pytest.mark.parametrize("arrival", ["before_attempt", "while_generating"])
def test_new_speech_before_call_rejects_old_focus_but_bounded_generation_is_not_starved(profile_app, monkeypatch, arrival):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    job = claimed(p, now)

    def append():
        _direct_final(p, 4, enqueue_intelligence=False, text="先继续核对通知重试和权益是否重复发放。")

    runtime = native_runtime(app, monkeypatch, result(), after=append if arrival == "while_generating" else None)
    if arrival == "before_attempt":
        append()
        assert p.discussion_request_state("review", job["request_context"], require_latest=True) == "focus_superseded"
        output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
        assert runtime.calls == []
        assert output["applied"]["coach_intervention"] is None
    else:
        output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
        assert len(runtime.calls) == 1
        assert output["applied"]["coach_intervention"]["job_id"] == job["id"]


def test_direct_ended_meeting_never_admits_new_work(profile_app):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    p._conn.execute("UPDATE meetings SET state='ended' WHERE id='review'")
    assert p.enqueue_realtime_participation(meeting_id="review", now_ms=now) is None


def test_first_short_question_enters_pi_after_ten_seconds_without_repeating(profile_app):
    app, client = profile_app
    assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"}).status_code == 200
    p = app.state.v2_persistence
    text = "支付成功但通知失败，是否要回滚支付？"
    _direct_final(p, 1, enqueue_intelligence=False, text=text)
    now = time.time_ns() // 1_000_000
    p._conn.execute("UPDATE transcript_segments SET updated_at_ms=? WHERE segment_id='s1'", (now,))
    assert p.enqueue_realtime_participation(meeting_id="review", now_ms=now + 5_000) is None
    job = claimed(p, now + 10_000)
    assert job["request_context"]["current_need_basis"]["focus_segment_ids"] == ["s1"]
    finish(p, job, now + 10_000)
    assert p.enqueue_realtime_participation(meeting_id="review", now_ms=now + 90_000) is None


def test_native_direct_first_pi_payload_preserves_all_twelve_frozen_segments(profile_app, monkeypatch):
    app, client = profile_app
    seed(app, client)
    p = app.state.v2_persistence
    for seq in range(4, 13):
        _direct_final(p, seq, enqueue_intelligence=False, text=TEXTS[2] + f"第{seq}段。")
    now = time.time_ns() // 1_000_000
    job = claimed(p, now)
    frozen_ids = {item["segment_id"] for item in job["request_context"]["evidence"]}
    assert frozen_ids == {f"s{seq}" for seq in range(1, 13)}
    value = result(need(source="s12"))
    value["intervention"]["evidence_segment_ids"] = ["s12"]
    value["intervention"]["supporting_facts"][0]["source_ref"] = "s12"
    runtime = native_runtime(app, monkeypatch, value)
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1
    assert output["coach"]["status"] == "intervention", output["coach"]
    context = runtime.calls[0]["context"]
    visible = context["context_paragraphs"] + context["retrieval_paragraphs"]
    assert len(visible) == 12
    assert {item["id"] for item in visible} == frozen_ids


def test_direct_frozen_basis_time_survives_public_snapshot_history_followup_and_sse(profile_app, monkeypatch):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    job = claimed(p, now)
    frozen_at = job["request_context"]["frozen_at_ms"]
    elapsed = 1_750
    # A deterministic native clock catches the old 2.5-second-origin formula
    # applied to direct's 10-second deadline, which would incorrectly yield 0.
    monkeypatch.setattr(app_module.time, "time_ns", lambda: (frozen_at + elapsed) * 1_000_000)
    runtime = native_runtime(app, monkeypatch, result())
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1 and output["coach"]["status"] == "intervention"
    assert p.complete_job(job_id=job["id"], worker_id="direct-fixture", now_ms=frozen_at + elapsed, output=output)

    response = client.get("/v2/meetings/review/snapshot")
    assert response.status_code == 200
    snapshot = response.json()
    history = [item for item in snapshot["coach_history"] if item["job_id"] == job["id"]]
    assert len(history) == 1
    for public_card in (snapshot["coach_intervention"], snapshot["coach_decision"], snapshot["follow_up"], history[0]):
        assert public_card["job_id"] == job["id"]
        assert public_card["frozen_at_ms"] == frozen_at
        assert public_card["soft_deadline_at_ms"] == frozen_at + 10_000
        assert public_card["valid_until_ms"] == frozen_at + 30_000
        assert public_card["soft_cutoff_elapsed_ms"] == elapsed

    sse = client.get("/v2/meetings/review/events", params={"once": True, "limit": 100},
        headers={"Accept": "text/event-stream"})
    assert sse.status_code == 200 and sse.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line.removeprefix("data: ")) for line in sse.text.splitlines() if line.startswith("data: ")]
    applied = [event for event in events if event["type"] == "meeting.intelligence.applied"
        and event["aggregate_id"] == job["id"]]
    assert len(applied) == 1
    for field in ("coach_intervention", "coach_decision", "follow_up"):
        assert applied[0]["payload"][field]["frozen_at_ms"] == frozen_at
        assert applied[0]["payload"][field]["soft_cutoff_elapsed_ms"] == elapsed


@pytest.mark.parametrize("recording", ["r09", "r14"])
def test_recorded_sdk_design_proposals_remain_proposals_and_publish_unchanged(profile_app, monkeypatch, recording):
    app, client = profile_app
    assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"}).status_code == 200
    p = app.state.v2_persistence
    for seq, text in enumerate(RECORDED_INPUT, 1):
        _direct_final(p, seq, enqueue_intelligence=False, text=text)
    job = claimed(p, time.time_ns() // 1_000_000)
    assert job["request_context"]["proposal_contract"] == "participation_proposal.v2"
    value = result()
    value["intervention"].update(RECORDED_PROPOSALS[recording], urgency="high",
        evidence_segment_ids=["s1", "s2", "s3"], evidence_quote="\n".join(RECORDED_INPUT),
        supporting_facts=[{"source_ref": f"s{seq}", "text": text} for seq, text in enumerate(RECORDED_INPUT, 1)])
    value["current_need"] = {"status": "has_current_need",
        "focus_evidence": [{"segment_id": "s2", "quote": RECORDED_INPUT[1]},
            {"segment_id": "s3", "quote": RECORDED_INPUT[2].split("。")[0] + "。"}],
        "reason": "同步整体重试可能让短信故障影响权益生效，当前需要明确重试边界。"}
    runtime = native_runtime(app, monkeypatch, value)
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1 and output["coach"]["status"] == "intervention", output["coach"]
    public = client.get("/v2/meetings/review/snapshot").json()["coach_intervention"]
    assert public["question"] == RECORDED_PROPOSALS[recording]["recommendation"]
    assert public["analysis_kind"] == "participation" and public["proposal_type"] == "professional_proposal"
    assert output["applied"]["coach_intervention"]["recommendation"] == RECORDED_PROPOSALS[recording]["recommendation"]
    assert not output["coach"]["agent_metrics"].get("quantity_reference_normalization")


@pytest.mark.parametrize("field", ["supporting_fact", "title", "reason"])
def test_v2_proposal_permission_cannot_turn_fabricated_facts_into_sources(profile_app, field):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    job = claimed(p, now)
    value = result()
    if field == "supporting_fact":
        value["intervention"]["supporting_facts"] = [{"source_ref": "s3", "text": "通知服务已经通过500并发验收。"}]
    else:
        value["intervention"][field] = "通知服务已经通过500并发验收。"
    context = request(p, job)
    with pytest.raises(IntelligenceResponseValidationError):
        parse_realtime_coach_response(json.dumps({"intervention": value["intervention"]}, ensure_ascii=False), request=context)
    runtime = Runtime(value)
    task = run_realtime_coach_via_pi(request=context, pi_runtime=runtime,
        provider_config={"model": "fixture", "timeout_seconds": 25}, priority_mode="realtime")
    if field == "reason":
        # Existing host explanation repair can replace a rejected explanation
        # with the source quote. It must never keep the fabricated statement.
        repaired = asyncio.run(task)
        assert repaired["intervention"].reason == "原文依据：\n" + TEXTS[2]
        assert "500" not in repaired["intervention"].reason
    else:
        with pytest.raises(IntelligenceResponseValidationError):
            asyncio.run(task)
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("metadata_shape", ["direct_marker_only", "v2_without_direct"])
def test_proposal_v2_permission_requires_both_direct_and_explicit_contract(profile_app, metadata_shape):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    job = claimed(p, now)
    context = request(p, job)
    metadata = dict(context.request_metadata)
    metadata.pop("current_need_contract", None)
    metadata.pop("current_need_basis", None)
    if metadata_shape == "direct_marker_only":
        metadata.pop("proposal_contract")
    else:
        metadata.pop("automatic_direct")
    context = replace(context, request_metadata=metadata)
    value = result()
    value["intervention"]["recommendation"] = "建议把通知恢复等待上限设为500毫秒，再核对权益发放结果。"
    runtime = Runtime(value)
    with pytest.raises(IntelligenceResponseValidationError, match="number"):
        asyncio.run(run_realtime_coach_via_pi(request=context, pi_runtime=runtime,
            provider_config={"model": "fixture", "timeout_seconds": 25}, priority_mode="realtime"))
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("timeout_shape", ["direct_late_intervention", "direct_explicit_timeout", "old_fallback"])
def test_timeout_projection_uses_frozen_origin_and_preserves_old_deadline_fallback(profile_app, monkeypatch, timeout_shape):
    app, client = profile_app
    now = seed(app, client)
    p = app.state.v2_persistence
    direct = timeout_shape != "old_fallback"
    job = claimed(p, now) if direct else p.enqueue_user_coach_request(meeting_id="review", user_request="",
        intent="ask_question", idempotency_key="old-timeout-clock", now_ms=now)
    assert job is not None
    elapsed, soft_window = (12_000, 10_000) if direct else (3_000, 2_500)
    observed = now + elapsed
    monkeypatch.setattr(app_module.time, "time_ns", lambda: observed * 1_000_000)
    late_card = timeout_shape == "direct_late_intervention"
    decision = {"decision_id": "direct-clock-timeout", "origin": "pi", "runtime_used": "pi",
        "status": "intervention" if late_card else "timed_out", "status_reason": "soft_deadline_exceeded",
        "soft_deadline_at_ms": now + soft_window, "completed_at_ms": observed,
        # This fixture applies a constructed timeout, without a paid call.
        "pi_provider_attempted": False, "llm_called": False, "llm_call_status": "not_called",
        "lifecycle_action": "deprioritize"}
    if direct:
        decision["frozen_at_ms"] = now
    applied = p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=observed,
        response={"paragraph_revisions": [], "state_changes": [], "topic_update": None, "follow_up": None,
            "input_context": job["request_context"]["input_context"], "coach_decision": decision,
            "coach_intervention": result()["intervention"] if late_card else None})
    assert applied["coach_intervention"] is None
    assert applied["coach_decision"]["status"] == "timed_out"
    assert applied["coach_decision"]["soft_cutoff_elapsed_ms"] == elapsed
    assert applied["coach_decision"]["soft_timeout_projection_at_ms"] == observed
    assert client.get("/v2/meetings/review/snapshot").json()["coach_decision"]["soft_cutoff_elapsed_ms"] == elapsed


def test_recorded_r16_unknown_assumption_is_marked_once_and_cannot_become_supporting_fact(profile_app, monkeypatch):
    app, client = profile_app
    assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"}).status_code == 200
    p = app.state.v2_persistence
    later_input = (
        "刚才通知失败不要重新发权益已经讨论过，大家同意短信和权益分开处理，这个问题已回答，不要重复提。",
        "现在讨论付款接口超时：支付服务可能已完成扣款，但调用方还没收到结果。",
        "候选方案先查询支付状态，查询暂时不可用时，页面如何提示和是否让用户重新付款还没确定。",
    )
    for seq, text in enumerate((*RECORDED_INPUT, *later_input), 1):
        _direct_final(p, seq, enqueue_intelligence=False, text=text)
    job = claimed(p, time.time_ns() // 1_000_000)
    assumption = "支付状态查询暂不可用时，系统无法确认该笔交易是否扣款成功。"
    value = result()
    value["intervention"].update(event_type="decision_readiness", title="明确支付未知态的重付边界", confidence=.94,
        recommendation="建议把“支付结果未知”作为独立状态：查询暂不可用时先提示正在核实并提供稍后查看入口，暂不引导再次付款；待确认失败后再开放重付，可以吗？",
        reason="超时不等于支付失败；查询也不可用时若允许发起新付款，可能重复扣款。需要明确未知状态的交互边界，同时保留后续核实路径。",
        evidence_segment_ids=["s1", "s5", "s6"], evidence_quote="\n".join((RECORDED_INPUT[0], *later_input[1:])),
        supporting_facts=[{"source_ref": source, "text": text} for source, text in
            (("s1", RECORDED_INPUT[0]), ("s5", later_input[1]), ("s6", later_input[2]))], assumptions=[assumption])
    value["current_need"] = {"status": "has_current_need",
        "focus_evidence": [{"segment_id": "s5", "quote": later_input[1]}, {"segment_id": "s6", "quote": later_input[2]}],
        "reason": "最新讨论尚未确定支付结果未知时的页面提示与再次付款规则，直接影响重复扣款风险。"}
    runtime = native_runtime(app, monkeypatch, value)
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1 and output["coach"]["status"] == "intervention", output["coach"]
    public = client.get("/v2/meetings/review/snapshot").json()["coach_intervention"]
    assert public["assumptions"] == ["需要核对：" + assumption]
    assert public["proposal_type"] == "professional_proposal"
    assert all(fact["text"] != assumption for fact in public["supporting_facts"])
    forged = copy.deepcopy(value["intervention"])
    forged["supporting_facts"] = [{"source_ref": "s6", "text": assumption}]
    with pytest.raises(IntelligenceResponseValidationError, match="supporting fact"):
        parse_realtime_coach_response(json.dumps({"intervention": forged}, ensure_ascii=False), request=request(p, job))
