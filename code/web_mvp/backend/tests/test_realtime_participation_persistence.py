from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import _coach_due_work_items, _public_coach_agent_metrics, _valid_host_coach_source_retraction, create_app
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded, V2Persistence
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor
from discussion_attention_fixtures import state_response


@pytest.fixture
def store(tmp_path):
    persistence = V2Persistence(tmp_path / "participation.db", semantic_projection_mode="llm_first")
    persistence.create_meeting(meeting_id="review", title=None, now_ms=1_000)
    _append(persistence)
    yield persistence
    persistence.close()


def _append(store, suffix="1", text="支付成功后发放权益，通知可以稍后发送。", now_ms=None):
    return store.commit_final_and_enqueue(meeting_id="review", final_id=f"f{suffix}", segment_id=f"s{suffix}",
                                          text=text, normalized_text=text, started_at_ms=int(suffix) * 1_000,
                                          ended_at_ms=int(suffix) * 1_000 + 800, evidence_hash=f"h{suffix}", now_ms=now_ms if now_ms is not None else 2_000 + int(suffix))


def _request(store, **extra):
    return store.enqueue_user_coach_request(meeting_id="review", user_request="", intent="compare_options",
                                           idempotency_key="participation:1", now_ms=3_000, **extra)


def _result(job):
    return {"paragraph_revisions": [], "state_changes": [], "topic_update": None, "follow_up": None,
            "input_context": job["request_context"]["input_context"],
            "coach_decision": {"decision_id": "contribution:1", "run_id": "run:1", "status": "intervention", "runtime_used": "pi"},
            "coach_intervention": {"event_type": "question_to_user", "proposal_type": "professional_proposal", "recommendation": "可以将权益和通知的失败边界分开，通知独立重试。",
                                   "reason": "通知允许稍后发送，两者用户影响不同。", "evidence_segment_ids": ["s1"],
                                   "evidence_quote": "通知可以稍后发送", "supporting_facts": [{"text": "通知可以稍后发送", "source_ref": "s1"}]}}


def test_no_fast_request_freezes_topic_and_preserves_legacy_work_item_id(store):
    job = _request(store)
    metadata = job["request_context"]
    assert metadata["profile"] == "participation"
    assert metadata["intent"] == "compare_options"
    assert job["work_item_id"] == job["id"]
    assert job["work_item_id"] != metadata["topic_id"]
    assert metadata["analysis_basis_revision"] == 1
    assert metadata["evidence"] == [{"segment_id": "s1", "revision": 1, "evidence_hash": "h1"}]
    assert "备选" in job["user_request"]
    assert store.get_snapshot("review")["discussion"]["work_item_id"] == metadata["topic_id"]


def test_plain_append_does_not_expire_contribution_or_create_commitment(store):
    job = _request(store)
    _append(store, "2", "通知允许晚一些到达，这个需求不用同步等待。")
    assert store.discussion_request_state("review", job["request_context"]) == "valid"
    result = store.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=_result(job), now_ms=4_000)
    assert result["work_item"]["kind"] == "discussion"
    assert result["coach_intervention"]["topic_id"] == job["request_context"]["topic_id"]
    assert result["coach_intervention"]["supporting_facts"][0]["source_ref"] == "s1"
    assert store.list_agent_work_items("review", kind="commitment") == []
    repeated = store.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=_result(job), now_ms=4_100)
    assert repeated["idempotent"] is True


def test_topic_cas_changes_basis_cancels_job_and_rejects_late_projection(store):
    job = _request(store)
    topic = store.get_current_discussion("review")
    with pytest.raises(ValueError, match="expected_version"):
        store.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=None, now_ms=3_100)
    updated = store.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=topic["version"],
                                              now_ms=3_100, context_patch={"current_problem": "讨论退款后的权益撤销"})
    assert updated["context"]["analysis_basis_revision"] == 2
    assert store.get_job(job["id"])["status"] == "cancelled"
    with pytest.raises(RuntimeError, match="version conflict"):
        store.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=topic["version"], now_ms=3_200)
    with pytest.raises(IntelligenceEvidenceSuperseded):
        store.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=_result(job), now_ms=4_000)


def test_replay_is_stable_but_different_intent_same_key_conflicts(store):
    first = _request(store)
    assert _request(store)["id"] == first["id"]
    with pytest.raises(RuntimeError, match="idempotency conflict"):
        store.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question", idempotency_key="participation:1", now_ms=3_500)


def test_foreign_selected_evidence_rolls_back_topic_creation(store):
    with pytest.raises(ValueError, match="does not belong"):
        _request(store, selected_segment_ids=["other-meeting-segment"])
    assert store.get_current_discussion("review") is None


def test_old_due_scanner_does_not_schedule_discussion():
    assert _coach_due_work_items([], now_ms=5_000, durable_items=[{"kind": "discussion", "state": "ready", "next_check_at_ms": 1_000,
                                                                "latest_decision_id": "new", "created_at_ms": 1_000}]) == []


def test_host_source_retraction_requires_writer_identity_and_never_accepts_a_new_card():
    event = {"type": "meeting.intelligence.applied", "causation_id": "knowledge_context",
        "idempotency_key": "coach.knowledge_superseded:review:old",
        "payload": {"coach_decision": {"decision_id": "old:revoked", "status": "stale", "lifecycle_action": "retract",
            "lifecycle_refresh": True, "supersedes_decision_id": "old"},
            "coach_intervention": None, "follow_up": None}}
    assert _valid_host_coach_source_retraction(event)
    assert _valid_host_coach_source_retraction({**event, "causation_id": "discussion_context", "idempotency_key": "discussion.retract:old"})
    assert not _valid_host_coach_source_retraction({**event, "causation_id": "discussion_context"})
    for key, value in (("type", "meeting.other"), ("causation_id", "model"), ("idempotency_key", "external")):
        assert not _valid_host_coach_source_retraction({**event, key: value})
    for key, value in (("status", "intervention"), ("lifecycle_action", "retain"),
                       ("lifecycle_refresh", False), ("supersedes_decision_id", ""), ("decision_id", ""), ("decision_id", "old")):
        invalid = {**event, "payload": {**event["payload"], "coach_decision": {**event["payload"]["coach_decision"], key: value}}}
        assert not _valid_host_coach_source_retraction(invalid)
    for key in ("coach_intervention", "follow_up"):
        assert not _valid_host_coach_source_retraction({**event, "payload": {**event["payload"], key: {"say_this": "new card"}}})


def test_validation_diagnostics_keep_only_field_categories_without_claim_text():
    metrics = _public_coach_agent_metrics({"response_validation_claim_fields": {
        "title": ["number", "unknown", {"raw": "secret text"}],
        "recommendation": ["state", "state", "owner"], "assumptions": "untrusted raw response",
        "raw_response": ["number"], "reason": ["deadline", "product_name"]}})
    assert metrics["response_validation_claim_fields"] == {
        "title": ["number"], "recommendation": ["state", "owner"], "reason": ["deadline", "product_name"]}
    assert "secret" not in json.dumps(metrics) and "untrusted" not in json.dumps(metrics)


def test_expired_professional_contribution_never_enters_legacy_due_fallback():
    payload = {"coach_intervention": {"say_this": "可以把两个失败边界分开。", "why_now": "通知允许稍后到达。",
        "decision_id": "professional", "coach_profile": "participation", "analysis_kind": "participation",
        "lifecycle_action": "retain", "valid_until_ms": 90_000}, "coach_decision": {"status": "intervention"}}
    events = [{"type": "meeting.intelligence.applied", "seq": 1, "payload": payload}]
    assert _coach_due_work_items(events, now_ms=120_000, durable_items=[]) == []
    # A genuine deadline-bearing legacy commitment remains eligible.
    due = _coach_due_work_items(events, now_ms=120_000, durable_items=[{
        "kind": "commitment", "state": "ready", "next_check_at_ms": 90_000,
        "latest_decision_id": "real-deadline", "work_item_id": "commitment-1", "created_at_ms": 1_000}])
    assert [item["item_id"] for item in due] == ["real-deadline"]


def test_professional_expiry_without_new_seq_enqueues_zero_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "0")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "0")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    old = now - 120_000
    try:
        p.create_meeting(meeting_id="review", title=None, now_ms=old)
        _append(p, now_ms=old + 1)
        job = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="compare_options",
            idempotency_key="old-professional", now_ms=old + 2)
        claimed = p.claim_next_job(worker_id="professional-expiry", lane="pi_deep", now_ms=old + 2, lease_ms=5_000)
        assert claimed["id"] == job["id"]
        result = _result(job)
        result["coach_decision"].update(origin="pi", lifecycle_action="retain", valid_until_ms=old + 90_000)
        result["coach_intervention"].update(lifecycle_action="retain", valid_until_ms=old + 90_000)
        # Simulate the original projection, then inspect expiry at current time.
        monkeypatch.setattr(p, "_projection_clock_ms", lambda: old + 3)
        applied = p.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=result, now_ms=old + 3)
        assert p.complete_job(job_id=job["id"], worker_id="professional-expiry", now_ms=old + 4, output={"applied": applied})
        jobs_before = {item["id"] for item in p.list_jobs(meeting_id="review")}
        assert asyncio.run(app.state.refresh_due_coach_items()) == 0
        assert asyncio.run(app.state.refresh_due_coach_items()) == 0
        assert {item["id"] for item in p.list_jobs(meeting_id="review")} == jobs_before
        assert p.list_agent_work_items("review", kind="commitment") == []
    finally:
        p.close()


def _establish_semantic_topic(store):
    job = store.list_jobs(meeting_id="review", lane="intelligence")[0]
    running = store.claim_next_job(worker_id="semantic", lane="intelligence", now_ms=2_900, lease_ms=5_000)
    assert running["id"] == job["id"]
    store.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=3_000,
        response={"paragraph_revisions": [], "state_changes": [], "follow_up": None,
                  "topic_update": {"operation": "add", "title": "支付后权益与通知", "summary": "比较权益和通知的失败边界",
                                   "evidence_segment_ids": ["s1"], "evidence_quote": "支付成功后发放权益"}})
    assert store.complete_job(job_id=job["id"], worker_id="semantic", now_ms=3_010, output={}) is not None


def _apply_semantic_turn(store, suffix, text, *, now_ms, topic=None, changes=()):
    committed = _append(store, str(suffix), text, now_ms=now_ms)
    job_id = committed["job_ids"]["intelligence"]
    running = store.claim_next_job(worker_id="semantic", lane="intelligence", now_ms=now_ms + 900, lease_ms=5_000)
    assert running["id"] == job_id
    result = store.apply_intelligence_response(meeting_id="review", job_id=job_id, now_ms=now_ms + 1_000,
        response={"paragraph_revisions": [], "state_changes": list(changes), "topic_update": topic, "follow_up": None})
    assert store.complete_job(job_id=job_id, worker_id="semantic", now_ms=now_ms + 1_010, output=result) is not None
    return result


def test_continuous_understanding_remembers_response_then_replaces_topic(store):
    _establish_semantic_topic(store)
    first = store.get_current_discussion("review")
    question = {"type": "open_question", "operation": "add", "item_id": "q-notification", "content": "通知是否需要同步等待？",
                "evidence_segment_ids": ["s2"], "evidence_quote": "通知是否需要同步等待", "confidence": 0.9}
    _apply_semantic_turn(store, 2, "通知是否需要同步等待？", now_ms=4_000, changes=[question])
    opened = store.get_current_discussion("review")
    assert opened["work_item_id"] == first["work_item_id"]
    assert opened["context"]["analysis_basis_revision"] == 2
    manual = _request(store)
    resolved = {**question, "operation": "resolve", "content": "通知不用同步等待", "evidence_segment_ids": ["s3"], "evidence_quote": "通知不用同步等待"}
    _apply_semantic_turn(store, 3, "通知不用同步等待，这个问题已经确认了。", now_ms=6_000, changes=[resolved])
    remembered = store.get_current_discussion("review")
    assert remembered["context"]["open_points"] == []
    assert remembered["context"]["responded_points"][0]["text"] == "通知不用同步等待"
    assert remembered["context"]["analysis_basis_revision"] == 3
    assert store.get_job(manual["id"])["status"] == "cancelled"
    _apply_semantic_turn(store, 4, "现在讨论退款后的权益撤销。", now_ms=8_000,
                         topic={"operation": "add", "title": "退款后的权益撤销", "summary": "开始讨论退款影响",
                                "evidence_segment_ids": ["s4"], "evidence_quote": "退款后的权益撤销"})
    current = store.get_current_discussion("review")
    assert current["work_item_id"] != first["work_item_id"]
    assert current["context"]["responded_points"] == []
    old = next(item for item in store.list_agent_work_items("review", kind="discussion") if item["work_item_id"] == first["work_item_id"])
    assert old["state"] == "superseded"


def _open_notification_questions(store):
    _establish_semantic_topic(store)
    _apply_semantic_turn(store, 2, "通知是否需要同步等待？结果页面如何显示？", now_ms=4_000,
        changes=[{"type": "open_question", "operation": "add", "item_id": item_id, "content": quote,
                  "evidence_segment_ids": ["s2"], "evidence_quote": quote, "confidence": .9}
                 for item_id, quote in (("q-notification", "通知是否需要同步等待"), ("q-page", "结果页面如何显示"))])
    return store.get_current_discussion("review")


def _refund_topic():
    return {"operation": "add", "title": "退款后的权益撤销", "summary": "开始讨论退款影响",
            "evidence_segment_ids": ["s3"], "evidence_quote": "现在讨论退款后的权益撤销"}


def test_answer_before_topic_switch_updates_only_the_existing_question_owner(store):
    old = _open_notification_questions(store)
    _apply_semantic_turn(store, 3, "通知不用同步等待，这个问题已经确认了。现在讨论退款后的权益撤销。退款何时到账？", now_ms=6_000,
        topic=_refund_topic(), changes=[
            {"type": "open_question", "operation": "resolve", "item_id": "q-notification", "content": "通知不用同步等待",
             "evidence_segment_ids": ["s3"], "evidence_quote": "通知不用同步等待", "confidence": .9},
            {"type": "open_question", "operation": "add", "item_id": "q-refund", "content": "退款何时到账",
             "evidence_segment_ids": ["s3"], "evidence_quote": "退款何时到账", "confidence": .9}])
    current = store.get_current_discussion("review")
    historical = next(item for item in store.list_agent_work_items("review", kind="discussion") if item["work_item_id"] == old["work_item_id"])
    assert historical["state"] == "superseded" and historical["context"]["phase"] == "closed"
    assert [point["id"] for point in historical["context"]["open_points"]] == ["q-page"]
    response = historical["context"]["responded_points"][0]
    assert response["id"] == "q-notification" and response["text"] == "通知不用同步等待"
    assert response["source_refs"] == ["s3"]
    assert response["evidence"] == [{"segment_id": "s3", "revision": 1, "evidence_hash": "h3"}]
    assert current["work_item_id"] != old["work_item_id"]
    assert current["context"]["responded_points"] == []
    assert [point["id"] for point in current["context"]["open_points"]] == ["q-refund"]


@pytest.mark.parametrize("kind,operation,status,source,quote", [
    ("risk", "resolve", None, "s3", "通知不用同步等待"),
    ("open_question", "add", None, "s3", "通知不用同步等待"),
])
def test_invalid_old_question_change_at_topic_switch_rolls_back_every_projection(store, kind, operation, status, source, quote):
    old = _open_notification_questions(store)
    committed = _append(store, "3", "通知不用同步等待。现在讨论退款后的权益撤销。", now_ms=6_000)
    job_id = committed["job_ids"]["intelligence"]
    assert store.claim_next_job(worker_id="semantic", lane="intelligence", now_ms=6_900, lease_ms=5_000)["id"] == job_id
    before = store.list_agent_work_items("review", kind="discussion")
    before_events = store._conn.execute("SELECT COUNT(*) FROM meeting_events WHERE meeting_id='review'").fetchone()[0]
    with pytest.raises(ValueError, match="cross-topic old question requires explicit"):
        store.apply_intelligence_response(meeting_id="review", job_id=job_id, now_ms=7_000,
            response={"paragraph_revisions": [], "follow_up": None, "topic_update": _refund_topic(),
                "state_changes": [{"type": kind, "operation": operation, "status": status, "item_id": "q-notification",
                    "content": "通知不用同步等待", "evidence_segment_ids": [source], "evidence_quote": quote, "confidence": .9}]})
    assert store.list_agent_work_items("review", kind="discussion") == before
    assert store.get_current_discussion("review")["work_item_id"] == old["work_item_id"]
    assert store._conn.execute("SELECT COUNT(*) FROM meeting_events WHERE meeting_id='review'").fetchone()[0] == before_events
    assert store.get_job(job_id)["status"] == "running"


@pytest.mark.parametrize("foreign", [False, True])
def test_new_topic_same_batch_answer_cannot_resolve_unknown_or_foreign_old_question(store, foreign):
    old = _open_notification_questions(store)
    item_id = "q-foreign" if foreign else "q-new-same-batch"
    other_before = None
    if foreign:
        store.create_meeting(meeting_id="other", title=None, now_ms=1_000)
        store.commit_final_and_enqueue(meeting_id="other", final_id="other-final", segment_id="other-source", text="其他会议的问题",
            normalized_text="其他会议的问题", started_at_ms=1_000, ended_at_ms=1_800, evidence_hash="other-hash", now_ms=2_000,
            enqueue_intelligence=False)
        request = store.enqueue_user_coach_request(meeting_id="other", user_request="", intent="compare_options", idempotency_key="other", now_ms=3_000)
        topic = store.get_current_discussion("other")
        store.update_discussion_context(meeting_id="other", work_item_id=request["request_context"]["topic_id"], expected_version=topic["version"], now_ms=3_100,
            context_patch={"open_points": [{"id": item_id, "entity_type": "open_question", "text": "其他会议的问题", "source_refs": ["other-source"]}]})
        other_before = store.list_agent_work_items("other", kind="discussion")
    _apply_semantic_turn(store, 3, "旧题先暂停。现在讨论退款后的权益撤销。退款不是即时到账，这是本批问题的回答。", now_ms=6_000,
        topic=_refund_topic(), changes=[{"type": "open_question", "operation": "resolve", "item_id": item_id,
            "content": "退款不是即时到账", "evidence_segment_ids": ["s3"], "evidence_quote": "退款不是即时到账", "confidence": .9}])
    historical = next(item for item in store.list_agent_work_items("review", kind="discussion") if item["work_item_id"] == old["work_item_id"])
    assert historical["context"]["responded_points"] == []
    assert {point["id"] for point in historical["context"]["open_points"]} == {"q-notification", "q-page"}
    # Previously an unknown ID could invent an answered question in the new
    # topic. Stage11.85 requires a persisted question or an explicit add pair.
    assert store.get_current_discussion("review")["context"]["responded_points"] == []
    if foreign:
        assert store.list_agent_work_items("other", kind="discussion") == other_before


def test_topic_pause_without_explicit_resolution_keeps_old_questions_unanswered(store):
    old = _open_notification_questions(store)
    _apply_semantic_turn(store, 3, "旧题先暂停，这不是已经解决。现在讨论退款后的权益撤销。", now_ms=6_000, topic=_refund_topic())
    historical = next(item for item in store.list_agent_work_items("review", kind="discussion") if item["work_item_id"] == old["work_item_id"])
    assert historical["context"]["responded_points"] == []
    assert {point["id"] for point in historical["context"]["open_points"]} == {"q-notification", "q-page"}
    assert store.get_current_discussion("review")["context"]["responded_points"] == []


def test_solution_projection_keeps_candidate_status_negation_and_source_revision(store):
    _establish_semantic_topic(store)
    decision = {"type": "decision", "operation": "add", "item_id": "solution-order", "content": "通知与权益先放在同一个同步流程里",
                "status": "candidate", "evidence_segment_ids": ["s2"], "evidence_quote": "同一个同步流程", "confidence": 0.9}
    _apply_semantic_turn(store, 2, "通知与权益先放在同一个同步流程里，这还只是候选方案。", now_ms=4_000, changes=[decision])
    projected = store.get_current_discussion("review")
    assert projected["context"]["current_solution"] == "候选方案（尚未宣告采用）：通知与权益先放在同一个同步流程里"
    point = projected["context"]["constraints"][0]
    assert point["kind"] == "decision_candidate" and point["status"] == "candidate"
    assert point["source_refs"] == ["s2"]
    assert point["evidence"] == [{"segment_id": "s2", "revision": 1, "evidence_hash": "h2"}]
    job = _request(store)
    negative = {**decision, "operation": "update", "content": "本轮不采用通知与权益同步重试", "evidence_segment_ids": ["s3"], "evidence_quote": "不采用通知与权益同步重试"}
    _apply_semantic_turn(store, 3, "本轮不采用通知与权益同步重试，但最终实施方式还需讨论。", now_ms=6_000, changes=[negative])
    changed = store.get_current_discussion("review")
    assert "不采用" in changed["context"]["current_solution"]
    assert changed["context"]["analysis_basis_revision"] == projected["context"]["analysis_basis_revision"] + 1
    assert store.get_job(job["id"])["status"] == "cancelled"
    assert store.list_agent_work_items("review", kind="commitment") == []


@pytest.mark.parametrize(("status", "kind", "prefix"), [
    ("candidate", "decision_candidate", "候选方案（尚未宣告采用）："),
    ("adopted", "decision_adopted", "已明确采用的方案："),
    ("confirmed", "decision_fact", ""),
])
def test_decision_fact_candidate_and_adopted_have_distinct_solution_projection(store, status, kind, prefix):
    _establish_semantic_topic(store)
    text = "通知与权益分别记录状态。"
    _apply_semantic_turn(store, 2, text, now_ms=4_000, changes=[{
        "type": "decision", "operation": "add", "item_id": "notice-state", "content": text,
        "status": status, "evidence_segment_ids": ["s2"], "evidence_quote": text, "confidence": 0.9}])
    context = store.get_current_discussion("review")["context"]
    assert context["constraints"][0]["kind"] == kind
    assert context["current_solution"] == (prefix + text if prefix else "")
    assert context["constraints"][0]["is_current_solution"] is (status != "confirmed")
    assert context["constraints"][0]["source_refs"] == ["s2"]


def test_confirmed_arrangement_does_not_replace_existing_candidate(store):
    _establish_semantic_topic(store)
    _apply_semantic_turn(store, 2, "当前候选是通知与权益分别记录状态。", now_ms=4_000, changes=[{
        "type": "decision", "operation": "add", "item_id": "candidate-state", "content": "通知与权益分别记录状态",
        "status": "candidate", "evidence_segment_ids": ["s2"], "evidence_quote": "通知与权益分别记录状态", "confidence": 0.9}])
    _apply_semantic_turn(store, 3, "演示安排已经确认。", now_ms=6_000, changes=[{
        "type": "decision", "operation": "add", "item_id": "known-arrangement", "content": "演示安排已经确认",
        "status": "confirmed", "evidence_segment_ids": ["s3"], "evidence_quote": "演示安排已经确认", "confidence": 0.9}])
    context = store.get_current_discussion("review")["context"]
    assert context["current_solution"] == "候选方案（尚未宣告采用）：通知与权益分别记录状态"
    assert [point["source_refs"] for point in context["constraints"]] == [["s2"], ["s3"]]


@pytest.mark.parametrize("closed_status", ["rejected", "cancelled", "resolved"])
def test_inactive_solution_is_not_promoted_to_current_plan(store, closed_status):
    _establish_semantic_topic(store)
    decision = {"type": "decision", "operation": "add", "item_id": "solution-order", "content": "采用通知与权益同步重试",
                "status": "candidate", "evidence_segment_ids": ["s2"], "evidence_quote": "通知与权益同步重试", "confidence": 0.9}
    _apply_semantic_turn(store, 2, "是否采用通知与权益同步重试，还是待定。", now_ms=4_000, changes=[decision])
    first = store.get_current_discussion("review")
    closed = {**decision, "operation": "update", "status": closed_status, "content": "通知与权益同步重试方案已被否决",
              "evidence_segment_ids": ["s3"], "evidence_quote": "方案已被否决"}
    _apply_semantic_turn(store, 3, "通知与权益同步重试方案已被否决。", now_ms=6_000, changes=[closed])
    current = store.get_current_discussion("review")
    assert current["context"]["current_solution"] == ""
    assert current["context"]["constraints"][0]["status"] == "inactive"
    assert current["context"]["analysis_basis_revision"] == first["context"]["analysis_basis_revision"] + 1


def test_solution_canonical_correction_removes_stale_interpretation_before_next_request(store):
    _establish_semantic_topic(store)
    decision = {"type": "decision", "operation": "add", "item_id": "solution-order", "content": "通知可以异步发送",
                "status": "candidate", "evidence_segment_ids": ["s2"], "evidence_quote": "通知可以异步发送", "confidence": 0.9}
    _apply_semantic_turn(store, 2, "当前候选方案是通知可以异步发送。", now_ms=4_000, changes=[decision])
    previous = store.get_current_discussion("review")
    assert store.commit_transcript_revision(meeting_id="review", segment_id="s2", expected_evidence_hash="h2",
        corrected_text="当前候选方案是通知不可以异步发送。", revision_id="solution-negation", now_ms=5_100)
    current = store.get_current_discussion("review")
    assert current["context"]["current_solution"] == ""
    assert current["context"]["constraints"] == []
    assert current["context"]["analysis_basis_revision"] == previous["context"]["analysis_basis_revision"] + 1
    assert _request(store)["request_context"]["discussion_context"]["current_solution"] == ""


def test_selected_history_still_freezes_current_anchor_and_correction_invalidates(store):
    _append(store, "2", "通知的失败不能影响已经支付成功的权益。")
    job = _request(store, selected_segment_ids=["s1"])
    assert [item["segment_id"] for item in job["request_context"]["evidence"]] == ["s1", "s2"]
    assert job["request_context"]["anchor_seq"] == job["input_transcript_seq"] == 2
    assert store.commit_transcript_revision(meeting_id="review", segment_id="s2", expected_evidence_hash="h2",
                                           corrected_text="通知的失败需要撤销已经支付成功的权益。", revision_id="r2", now_ms=3_200)
    assert store.get_job(job["id"])["status"] == "cancelled"
    assert store.discussion_request_state("review", job["request_context"]) != "valid"


def test_working_context_cannot_write_host_proposals_or_opportunity_time(store):
    _request(store)
    topic = store.get_current_discussion("review")
    for patch in ({"proposals": ["pretend proposal"]}, {"opportunity_formed_at_ms": 1}):
        with pytest.raises(ValueError, match="host managed"):
            store.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=topic["version"],
                                            now_ms=3_100, context_patch=patch)


def test_wire_budget_uses_utf8_and_preserves_frozen_source_quotes(store, monkeypatch):
    import json
    from meeting_copilot_web_mvp import user_memory
    from meeting_copilot_web_mvp.participation_contract import normalize_participation_metadata
    monkeypatch.setattr(user_memory, "memory_context", lambda *args: {"hotwords": [{"value": "中文导航" * 100}] * 100, "terms": [], "fingerprint": "synthetic"})
    quote = "通知可以稍后发送" * 100
    source = {"source_type": "document", "source_id": "synthetic-only", "quote": quote}
    job = _request(store, source_evidence=[source])
    metadata = job["request_context"]
    assert len(json.dumps(metadata, ensure_ascii=False).encode("utf-8")) <= 48_000
    assert normalize_participation_metadata("participation", metadata)["source_evidence"][0]["quote"] == quote
    assert metadata["navigation_truncated"] is True
    assert len(metadata["user_memory"]["hotwords"]) == 20


def test_excess_source_budget_rolls_back_without_silently_truncating_quotes(store):
    source = {"source_type": "document", "source_id": "synthetic-only", "quote": "中" * 18_000}
    with pytest.raises(ValueError, match="上下文预算"):
        _request(store, source_evidence=[source])
    assert store.get_current_discussion("review") is None
    assert not any(job["trigger_type"] == "user_request" for job in store.list_jobs(meeting_id="review", lane="intelligence"))


def test_automatic_discussion_is_deep_once_per_basis_and_manual_preempts(store):
    _establish_semantic_topic(store)
    # This positive automatic case assumes the fixture's L2 check has settled.
    correction = store.claim_next_job(worker_id="fixture-l2", lane="correction",
        now_ms=3_200, lease_ms=5_000)
    if correction is None or correction["meeting_id"] != "review":
        raise AssertionError("expected this fixture's correction job")
    segment_id = correction["evidence_segment_id"]
    store.mark_correction_segments_no_change(meeting_id="review", segment_ids=[segment_id],
        max_input_transcript_seq=correction["input_transcript_seq"], now_ms=3_201,
        expected_evidence_hashes={segment_id: correction["evidence_hash"]})
    if store.complete_job(job_id=correction["id"], worker_id="fixture-l2", now_ms=3_201,
            output={"fixture": "unchanged"}) is None:
        raise AssertionError("fixture correction did not complete")
    if store.has_pending_discussion_corrections("review"):
        raise AssertionError("fixture correction work remains active")
    # The positive automatic case now also requires one trusted state snapshot,
    # rather than assuming legacy topic naming establishes a contribution.
    state = store.enqueue_discussion_state(meeting_id="review", now_ms=3_300, stable_ms=0,
        minimum_segments=1, minimum_characters=0)
    if state is None:
        raise AssertionError("expected the fixture's natural attention upgrade")
    claimed = store.claim_next_job(worker_id="fixture-state", lane="discussion_state", now_ms=3_300, lease_ms=5_000)
    if claimed is None or claimed["id"] != state["id"]:
        raise AssertionError("fixture state lease mismatch")
    response = state_response(store, claimed, {"paragraph_revisions": [], "topic_update": None,
        "state_changes": [], "follow_up": None})
    store.apply_intelligence_response(meeting_id="review", job_id=claimed["id"], response=response, now_ms=3_301)
    if not store.complete_job(job_id=claimed["id"], worker_id="fixture-state", now_ms=3_302, output={}):
        raise AssertionError("fixture state did not complete")
    automatic = store.enqueue_discussion_update(meeting_id="review", now_ms=3_500)
    assert automatic["trigger_type"] == "discussion_update"
    assert automatic["deadline_at_ms"] == 33_500
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=40_000) is None
    running = store.claim_next_job(worker_id="analysis", lane="pi_deep", now_ms=3_600, lease_ms=5_000)
    assert running["id"] == automatic["id"]
    manual = _request(store)
    assert manual["request_context"]["preempted_job_ids"] == [automatic["id"]]
    assert store.get_job(automatic["id"])["status"] == "cancelled"
    assert store.get_job(manual["id"])["status"] == "pending"
    with pytest.raises(IntelligenceEvidenceSuperseded):
        store.apply_intelligence_response(meeting_id="review", job_id=automatic["id"], response=_result(automatic), now_ms=4_000)


def test_executor_expected_id_does_not_cancel_another_manual_in_a_race(store):
    executor = DurableJobExecutor(store, correction_handler=lambda job: {}, suggestion_handler=lambda job: {},
                                  additional_handlers={"pi_deep": lambda job: {}})
    executor._active_jobs["pi_deep"] = {"id": "another-manual", "meeting_id": "review"}
    assert executor.supersede_running("pi_deep", meeting_id="review", replacement_job_id="new-manual", expected_job_id="old-automatic") is False
    assert not executor._supersede_events["pi_deep"].is_set()
    executor._active_jobs["pi_deep"] = {"id": "old-automatic", "meeting_id": "review"}
    assert executor.supersede_running("pi_deep", meeting_id="review", replacement_job_id="new-manual", expected_job_id="old-automatic") is True


def test_preempted_legacy_automatic_job_also_cannot_publish_late(store):
    legacy = store.enqueue_user_coach_request(meeting_id="review", user_request="旧版自动补充", idempotency_key="legacy:auto", now_ms=2_900)
    with store._write_transaction():
        store._conn.execute("UPDATE jobs SET trigger_type='answer_ready',status='running',lease_owner='analysis',lease_until_ms=9000 WHERE id=?", (legacy["id"],))
    manual = _request(store)
    assert legacy["id"] in manual["request_context"]["preempted_job_ids"]
    with pytest.raises(IntelligenceEvidenceSuperseded, match="preempted"):
        store.apply_intelligence_response(meeting_id="review", job_id=legacy["id"], response=_result(manual), now_ms=3_100)


def test_atomic_create_bind_replays_same_result_and_conflict_does_not_create_meeting(store, tmp_path):
    from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
    from meeting_copilot_web_mvp.meeting_materials import MeetingMaterialsRepository
    library = KnowledgeRepository(store.database_path)
    try:
        materials = MeetingMaterialsRepository(library)
        material_set = materials.create_set()
        created = store.create_meeting(meeting_id="prepared", title="评审准备", now_ms=5_000, material_set_id=material_set["id"],
            expected_material_set_revision=material_set["revision"], idempotency_key="create:prepared", creation_fingerprint="same")
        replay = store.create_meeting(meeting_id="prepared", title="评审准备", now_ms=5_100, material_set_id=material_set["id"],
            expected_material_set_revision=material_set["revision"], idempotency_key="create:prepared", creation_fingerprint="same")
        assert created["id"] == replay["id"]
        assert replay["material_set"]["meeting_id"] == "prepared"
        with pytest.raises(RuntimeError, match="idempotency conflict"):
            store.create_meeting(meeting_id="different", title="另一个会", now_ms=5_200, material_set_id=material_set["id"],
                expected_material_set_revision=material_set["revision"], idempotency_key="create:prepared", creation_fingerprint="changed")
        with pytest.raises(KeyError):
            store.get_meeting("different")
    finally:
        library.close()


def test_intent_endpoint_without_fast_and_versioned_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    try:
        store = app.state.v2_persistence
        store.create_meeting(meeting_id="review", title=None, now_ms=1_000)
        _append(store)
        client = TestClient(app)
        response = client.post("/v2/meetings/review/coach/request", json={"intent": "ask_question", "request": "", "answer_id": None})
        assert response.status_code == 202, response.text
        job = response.json()["job"]
        assert job["request_context"]["profile"] == "participation"
        topic = client.get("/v2/meetings/review/work-items?kind=discussion").json()["items"][0]
        missing = client.patch(f"/v2/meetings/review/work-items/{topic['work_item_id']}", json={"state": "resolved"})
        assert missing.status_code == 422
        closed = client.patch(f"/v2/meetings/review/work-items/{topic['work_item_id']}", json={"state": "resolved", "expected_version": topic["version"]})
        assert closed.status_code == 200, closed.text
        assert store.get_job(job["id"])["status"] == "cancelled"
    finally:
        app.state.v2_persistence.close()


@pytest.mark.parametrize("selected", [[], ["s1"]])
def test_handler_uses_only_frozen_scope_and_respects_context_limit(tmp_path, monkeypatch, selected):
    from meeting_copilot_web_mvp import app as app_module
    from meeting_copilot_web_mvp.realtime_intelligence import MAX_CONTEXT_PARAGRAPHS
    class RequestCaptured(Exception):
        pass
    captured = []
    original = app_module.RealtimeIntelligenceRequest.from_payload
    def capture(cls, **payload):
        captured.append(original(**payload))
        raise RequestCaptured()
    monkeypatch.setattr(app_module.RealtimeIntelligenceRequest, "from_payload", classmethod(capture))
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    try:
        p = app.state.v2_persistence
        p.create_meeting(meeting_id="review", title=None, now_ms=1_000)
        for suffix in range(1, 7):
            _append(p, str(suffix), f"支付与通知方案第 {suffix} 个讨论点。")
        job = _request(p, selected_segment_ids=selected)
        _append(p, "7", "这是请求冻结之后新增加的讨论内容。")
        with pytest.raises(RequestCaptured):
            asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
        request = captured[0]
        assert len(request.context_paragraphs) <= MAX_CONTEXT_PARAGRAPHS
        assert request.new_paragraphs == ()
        assert request.rolling_state["current_answer"] is None
        assert set(request.paragraphs_by_id) == {item["segment_id"] for item in job["request_context"]["evidence"]}
        assert "s7" not in request.paragraphs_by_id
        if selected:
            assert set(request.paragraphs_by_id) == {"s1", "s6"}
        else:
            assert len(request.retrieval_paragraphs) == 3
    finally:
        app.state.v2_persistence.close()


def test_participation_events_replace_snapshot_card_and_restore_typed_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    try:
        p.create_meeting(meeting_id="review", title=None, now_ms=now)
        _append(p, now_ms=now + 1)
        client = TestClient(app)
        previous_id = None
        for index, intent in enumerate(("ask_question", "compare_options", "formulate_response", "silent", "failed"), start=1):
            job = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent=intent if intent in {"ask_question", "compare_options", "formulate_response"} else "freeform",
                idempotency_key=f"snapshot:{index}", now_ms=now + index * 10)
            running = p.claim_next_job(worker_id="snapshot", lane="pi_deep", now_ms=now + index * 10 + 1, lease_ms=5_000)
            assert running["id"] == job["id"]
            response = _result(job)
            decision_id = f"contribution:{index}"
            common = {"decision_id": decision_id, "origin": "pi", "runtime_requested": "pi", "runtime_used": "pi",
                      "pi_provider_attempted": True, "llm_called": True, "llm_call_status": "called", "prompt_profile": "participation",
                      "lifecycle_action": "retain", "valid_until_ms": now + 90_000}
            response["coach_decision"].update(common)
            response["coach_intervention"].update({**common, "status": "intervention", "analysis_kind": "participation",
                                                  "proposal_type": "professional_proposal", "assumptions": ["这是供讨论的备选方式"],
                                                  "say_this": f"本轮 {intent} 的表达"})
            if intent in {"silent", "failed"}:
                response["coach_intervention"] = None
                response["coach_decision"].update(status="protected_silent" if intent == "silent" else "failed", lifecycle_action="deprioritize")
            applied = p.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=response, now_ms=now + index * 10 + 2)
            formal_context = {"source": "llm_first", "llm_called": True, "job_id": job["id"], "batch_id": f"batch:{index}",
                              "provider": "synthetic", "model": "synthetic", "evidence": {"segment_ids": ["s1"], "quote": "通知可以稍后发送"}}
            assert p.complete_job(job_id=job["id"], worker_id="snapshot", now_ms=now + index * 10 + 3,
                                  output={"formal_event_context": formal_context, "applied": applied})
            snapshot_response = client.get("/v2/meetings/review/snapshot")
            assert snapshot_response.status_code == 200, snapshot_response.text
            snapshot = snapshot_response.json()
            card = snapshot["coach_intervention"]
            if intent in {"silent", "failed"}:
                assert card["decision_id"] == previous_id
                continue
            assert snapshot["coach_decision"]["job_id"] == card["job_id"] == job["id"]
            assert card["decision_id"] == decision_id
            assert card["say_this"] == f"本轮 {intent} 的表达"
            assert card["coach_profile"] == card["analysis_kind"] == "participation"
            assert card["proposal_type"] == "professional_proposal"
            assert card["topic_id"] == job["request_context"]["topic_id"]
            assert card["analysis_basis_revision"] == job["request_context"]["analysis_basis_revision"]
            assert card["supporting_facts"] == response["coach_intervention"]["supporting_facts"]
            assert card["assumptions"] == ["这是供讨论的备选方式"]
            assert snapshot["coach_history"][-1]["decision_id"] == decision_id
            if previous_id is not None:
                assert card["supersedes_decision_id"] == previous_id
                assert snapshot["coach_history"][-2]["lifecycle_status"] == "superseded"
            previous_id = decision_id
            # The next read must not rely on this process's cached provenance.
            app.state.llm_first_event_contexts.clear()
    finally:
        p.close()


def _coverage_attachment(app):
    from meeting_copilot_web_mvp.document_import import extract_document
    from meeting_copilot_web_mvp.knowledge_context import retrieve_meeting_knowledge

    repository = app.state.meeting_materials_repository
    material_set = repository.create_set()
    content = ("通知允许异步发送。\n\n" + "需要考虑补偿恢复与重试边界。" * 100).encode("utf-8")
    attachment = app.state.meeting_material_importer.stage(material_set["id"], "coverage.txt", content, content_type="text/plain")
    queue = app.state.meeting_material_jobs
    conversion = queue.claim()
    assert queue.finish(conversion, extract_document("coverage.txt", content, "text/plain"))
    attachment = repository.get_attachment(material_set["id"], attachment["id"])
    attachment = repository.usage(material_set["id"], attachment["id"], enabled=True,
        version_id=attachment["latest_version_id"], selected_span_ids=None, expected_revision=attachment["revision"])
    repository.bind("review", material_set["id"])
    evidence = retrieve_meeting_knowledge(repository.library, "review", "通知", include_attachments=True)[:1]
    assert evidence
    return repository, material_set, attachment, evidence


def _project_coverage_card(p, job, now, *, failed=False):
    running = p.claim_next_job(worker_id="coverage", lane="pi_deep", now_ms=now + 1, lease_ms=5_000)
    assert running["id"] == job["id"]
    response = _result(job)
    response["source_evidence"] = job["request_context"]["source_evidence"]
    common = {"decision_id": f"coverage:{job['id']}", "origin": "pi", "runtime_requested": "pi", "runtime_used": "pi",
        "pi_provider_attempted": True, "llm_called": True, "llm_call_status": "called", "prompt_profile": "participation",
        "lifecycle_action": "retain", "valid_until_ms": now + 90_000}
    response["coach_decision"].update(common)
    response["coach_intervention"].update(common, analysis_kind="participation", material_context={"untrusted_model_claim": "read all"})
    if failed:
        response["coach_intervention"] = None
        response["coach_decision"].update(status="failed", lifecycle_action="deprioritize")
    applied = p.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=response, now_ms=now + 2)
    formal = {"source": "llm_first", "llm_called": True, "job_id": job["id"], "batch_id": job["id"],
        "provider": "synthetic", "model": "synthetic", "evidence": {"segment_ids": ["s1"], "quote": "通知可以稍后发送"}}
    assert p.complete_job(job_id=job["id"], worker_id="coverage", now_ms=now + 3, output={"formal_event_context": formal, "applied": applied})
    return applied


def test_manual_first_topic_add_on_new_evidence_retracts_card_and_isolates_working_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    try:
        p.create_meeting(meeting_id="review", title=None, now_ms=now)
        _append(p, text="支付成功后发放权益，通知可以稍后发送，但通知失败恢复边界没有讲清楚。", now_ms=now + 1)
        manual = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="compare_options",
            idempotency_key="manual-first", now_ms=now + 2)
        _project_coverage_card(p, manual, now + 2)
        old = p.get_current_discussion("review")
        assert old["context"]["proposals"]
        client = TestClient(app)
        assert client.get("/v2/meetings/review/snapshot").json()["coach_intervention"]["job_id"] == manual["id"]
        _append(p, "2", "上个议题已经结束，开始讨论下周培训的时间安排。培训时间已经确认，安排已更新。", now_ms=now + 10)
        state = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 11, cooldown_ms=0, stable_ms=0, minimum_segments=1)
        assert p.claim_next_job(worker_id="topic-state", lane="discussion_state", now_ms=now + 12, lease_ms=5_000)["id"] == state["id"]
        applied = p.apply_intelligence_response(meeting_id="review", job_id=state["id"], now_ms=now + 13,
            response=state_response(p, state, {"paragraph_revisions": [], "follow_up": None, "state_changes": [
                {"type": "open_question", "operation": "add", "item_id": "old-recovery", "content": "通知失败恢复边界没有讲清楚",
                 "status": "unresolved", "evidence_segment_ids": ["s1", "s2"], "evidence_quote": "恢复边界没有讲清楚", "confidence": 0.9},
                {"type": "decision", "operation": "add", "item_id": "new-confirmed-arrangement", "content": "培训时间已经确认，安排已更新",
                 "status": "confirmed", "evidence_segment_ids": ["s2"], "evidence_quote": "培训时间已经确认，安排已更新", "confidence": 0.9}],
                "topic_update": {"operation": "add", "title": "培训时间安排", "summary": "新议题是培训时间安排",
                    "evidence_segment_ids": ["s2"], "evidence_quote": "开始讨论下周培训的时间安排"}}, status="none"))
        assert p.complete_job(job_id=state["id"], worker_id="topic-state", now_ms=now + 14, output={"applied": applied})
        current = p.get_current_discussion("review")
        assert current["work_item_id"] != old["work_item_id"]
        assert current["context"]["current_problem"] == "培训时间安排"
        assert not current["context"].get("proposals") and current["context"]["open_points"] == []
        assert current["context"]["current_solution"] == ""
        assert current["context"]["constraints"][0]["kind"] == "decision_fact"
        assert current["context"]["constraints"][0]["source_refs"] == ["s2"]
        historical = p.list_agent_work_items("review", kind="discussion", state="superseded")[0]
        assert historical["work_item_id"] == old["work_item_id"]
        assert historical["context"]["phase"] == "closed"
        assert historical["context"]["open_points"][0]["id"] == "old-recovery"
        assert historical["context"]["open_points"][0]["source_refs"] == ["s1"]
        snapshot = client.get("/v2/meetings/review/snapshot").json()
        assert snapshot["coach_intervention"] is None and snapshot["follow_up"] is None
        old_card = next(card for card in snapshot["coach_history"] if card["job_id"] == manual["id"])
        assert old_card["lifecycle_action"] == "retract"
    finally:
        p.close()


@pytest.mark.parametrize("operation,status", [("resolve", None), ("update", "answered")])
def test_answered_point_without_previous_persisted_question_is_rejected(store, operation, status):
    job = _request(store)
    initial = store.get_current_discussion("review")
    assert initial["context"]["open_points"] == []
    store._conn.execute("UPDATE jobs SET status='cancelled' WHERE meeting_id='review' AND trigger_type='delta'")
    result = _apply_semantic_turn(store, 2, "问题已经答复：通知可以稍后发送。", now_ms=5_000,
        changes=[{"type": "open_question", "operation": operation, "item_id": "timing-question", "content": "通知可以稍后发送",
                  "status": status, "evidence_segment_ids": ["s2"], "evidence_quote": "通知可以稍后发送", "confidence": 0.9}])
    current = store.get_current_discussion("review")
    assert current["work_item_id"] == initial["work_item_id"]
    assert current["context"]["responded_points"] == []
    assert current["context"]["open_points"] == []
    assert result["state_projection"]["rejected_operations"][0]["rule"] == "question_not_open_or_paired"
    assert current["context"]["analysis_basis_revision"] == initial["context"]["analysis_basis_revision"]
    assert store.get_job(job["id"])["status"] != "cancelled"


def test_first_semantic_naming_keeps_provisional_topic_and_update_advances_basis(store):
    _request(store)
    initial = store.get_current_discussion("review")
    delta = next(job for job in store.list_jobs(meeting_id="review", lane="intelligence") if job["trigger_type"] == "delta")
    running = store.claim_next_job(worker_id="semantic", lane="intelligence", now_ms=3_000, lease_ms=5_000)
    assert running["id"] == delta["id"]
    store.apply_intelligence_response(meeting_id="review", job_id=delta["id"], now_ms=3_001,
        response={"paragraph_revisions": [], "state_changes": [], "follow_up": None,
            "topic_update": {"operation": "add", "title": "支付后的权益与通知", "summary": "明确当前讨论的主题名称",
                "evidence_segment_ids": ["s1"], "evidence_quote": "通知可以稍后发送"}})
    assert store.complete_job(job_id=delta["id"], worker_id="semantic", now_ms=3_002, output={})
    named = store.get_current_discussion("review")
    assert named["work_item_id"] == initial["work_item_id"]
    assert named["context"]["analysis_basis_revision"] > initial["context"]["analysis_basis_revision"]
    _apply_semantic_turn(store, 2, "继续明确通知失败恢复边界。", now_ms=5_000,
        topic={"operation": "update", "title": "支付后的权益与通知恢复边界", "summary": "完善同一议题的讨论重点",
            "evidence_segment_ids": ["s2"], "evidence_quote": "明确通知失败恢复边界"})
    updated = store.get_current_discussion("review")
    assert updated["work_item_id"] == named["work_item_id"]
    assert updated["context"]["analysis_basis_revision"] > named["context"]["analysis_basis_revision"]
    _apply_semantic_turn(store, 3, "仍继续讨论通知恢复边界。", now_ms=7_000,
        topic={"operation": "add", "title": "支付后的权益与通知恢复边界", "summary": "继续已有命名的议题",
            "evidence_segment_ids": ["s3"], "evidence_quote": "继续讨论通知恢复边界"})
    continued = store.get_current_discussion("review")
    assert continued["work_item_id"] == updated["work_item_id"]
    assert continued["context"]["analysis_basis_revision"] == updated["context"]["analysis_basis_revision"]


def test_material_coverage_is_frozen_per_decision_and_survives_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    now = time.time_ns() // 1_000_000
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    p = app.state.v2_persistence
    try:
        p.create_meeting(meeting_id="review", title=None, now_ms=now)
        _append(p, now_ms=now + 1)
        _, _, _, evidence = _coverage_attachment(app)
        job = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="compare_options",
            idempotency_key="coverage:success", now_ms=now + 10, source_evidence=evidence)
        frozen = job["request_context"]["material_context"]
        coverage = frozen["attachment_catalog"][0]["coverage"]
        assert coverage["provided_span_count"] == 1 and coverage["complete_read"] is False
        assert coverage["provided_characters"] < coverage["authorized_characters"]
        applied = _project_coverage_card(p, job, now + 10)
        for field in ("coach_decision", "coach_intervention", "follow_up"):
            assert applied[field]["material_context"] == frozen
        snapshot = TestClient(app).get("/v2/meetings/review/snapshot").json()
        for field in ("coach_decision", "coach_intervention", "follow_up"):
            assert snapshot[field]["material_context"] == frozen
        assert snapshot["coach_history"][-1]["material_context"] == frozen
        assert "untrusted_model_claim" not in json.dumps(frozen)
        p.close()
        reloaded = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
        try:
            snapshot = TestClient(reloaded).get("/v2/meetings/review/snapshot").json()
            assert snapshot["coach_intervention"]["job_id"] == job["id"]
            assert snapshot["coach_intervention"]["material_context"] == frozen
            failed_job = reloaded.state.v2_persistence.enqueue_user_coach_request(meeting_id="review", user_request="", intent="freeform",
                idempotency_key="coverage:failure", now_ms=now + 20, source_evidence=[])
            assert failed_job["request_context"]["material_context"] != frozen
            _project_coverage_card(reloaded.state.v2_persistence, failed_job, now + 20, failed=True)
            snapshot = TestClient(reloaded).get("/v2/meetings/review/snapshot").json()
            assert snapshot["coach_intervention"]["job_id"] == job["id"]
            assert snapshot["coach_intervention"]["material_context"] == frozen
            assert snapshot["coach_history"][-1]["material_context"] == frozen
        finally:
            reloaded.state.v2_persistence.close()
    finally:
        if not p._closed:
            p.close()


def test_material_scope_change_retracts_current_card_but_keeps_original_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    now = time.time_ns() // 1_000_000
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    p = app.state.v2_persistence
    try:
        p.create_meeting(meeting_id="review", title=None, now_ms=now)
        _append(p, now_ms=now + 1)
        repository, material_set, attachment, evidence = _coverage_attachment(app)
        job = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="compare_options",
            idempotency_key="coverage:scope", now_ms=now + 10, source_evidence=evidence)
        frozen = job["request_context"]["material_context"]
        _project_coverage_card(p, job, now + 10)
        repository.usage(material_set["id"], attachment["id"], enabled=False, version_id=None,
            selected_span_ids=None, expected_revision=attachment["revision"])
        assert p.get_material_context("review")["attachment_catalog"] == []
        snapshot = TestClient(app).get("/v2/meetings/review/snapshot").json()
        assert snapshot["coach_intervention"] is None and snapshot["follow_up"] is None
        history = next(item for item in snapshot["coach_history"] if item["job_id"] == job["id"])
        assert history["material_context"] == frozen
        assert history["lifecycle_status"] == "retracted"
    finally:
        p.close()


@pytest.mark.parametrize("case_id", ["requirement_business_boundary", "architecture_tradeoff_with_material", "answered_point_progression", "topic_switch_and_silence"])
def test_value_fixture_replay_preserves_interpretation_scope_and_lifecycle(tmp_path, case_id):
    fixture = json.loads((Path(__file__).parent / "fixtures/realtime_participation_value_scenarios.json").read_text(encoding="utf-8"))
    case = next(item for item in fixture["cases"] if item["id"] == case_id)
    p = V2Persistence(tmp_path / "value-fixture.db", semantic_projection_mode="llm_first")
    try:
        p.create_meeting(meeting_id="review", title=None, now_ms=1_000)
        previous = None
        requests = []
        for index, turn in enumerate(case["turns"], start=1):
            now = 2_000 + index * 3_000
            committed = p.commit_final_and_enqueue(meeting_id="review", final_id=f"fixture-final-{index}", segment_id=turn["segment_id"],
                text=turn["text"], normalized_text=turn["text"], started_at_ms=index * 10_000, ended_at_ms=index * 10_000 + 8_000,
                evidence_hash=hashlib.sha256(turn["text"].encode()).hexdigest(), now_ms=now)
            running = p.claim_next_job(worker_id="fixture-semantic", lane="intelligence", now_ms=now + 900, lease_ms=5_000)
            assert running["id"] == committed["job_ids"]["intelligence"]
            applied = p.apply_intelligence_response(meeting_id="review", job_id=running["id"], now_ms=now + 1_000,
                response={"paragraph_revisions": [], "follow_up": None, **turn["semantic"]})
            assert p.complete_job(job_id=running["id"], worker_id="fixture-semantic", now_ms=now + 1_010, output=applied)
            current = p.get_current_discussion("review")
            context = current["context"]
            checks = turn.get("snapshot_checks") or {}
            for check, field in (("current_problem_contains", "current_problem"), ("current_solution_contains", "current_solution")):
                if check in checks:
                    assert checks[check] in context[field]
            if checks.get("current_solution_empty"):
                assert context["current_solution"] == ""
            if "responded_contains" in checks:
                assert any(checks["responded_contains"] in item["text"] for item in context["responded_points"])
            if "open_excludes" in checks:
                assert not any(checks["open_excludes"] in item["text"] for item in context["open_points"])
            if checks.get("basis_advanced"):
                assert context["analysis_basis_revision"] > previous["context"]["analysis_basis_revision"]
            if checks.get("topic_same_as_previous"):
                assert current["work_item_id"] == previous["work_item_id"]
            if checks.get("topic_changes_from_previous"):
                assert current["work_item_id"] != previous["work_item_id"]
            if checks.get("prior_discussion_superseded"):
                assert any(item["work_item_id"] == previous["work_item_id"] and item["state"] == "superseded"
                           for item in p.list_agent_work_items("review", kind="discussion"))
                assert all(p.get_job(job_id)["status"] == "cancelled" for job_id in requests)
            if "coach_request" in turn:
                job = p.enqueue_user_coach_request(meeting_id="review", intent=turn["coach_request"]["intent"], user_request="",
                    now_ms=now + 1_100, idempotency_key=f"fixture:{case_id}:{index}")
                assert job["request_context"]["discussion_context"] == context
                requests.append(job["id"])
            previous = current
        assert p.list_agent_work_items("review", kind="commitment") == []
    finally:
        p.close()
