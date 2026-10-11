"""Material authorization refresh is distinct from canonical speech re-reading.

These are isolated persistence/scheduler regressions with public synthetic text.
Semantic state is supplied as local test data; no provider or executor is run.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from meeting_copilot_web_mvp.automatic_coach_policy import automatic_discussion_admission, discussion_understanding_ready
from meeting_copilot_web_mvp.knowledge_context import retrieve_meeting_knowledge, source_evidence_state
from meeting_copilot_web_mvp.user_memory import input_context_state
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded, V2Persistence
from test_discussion_state_background import append, state_app as shared_state_app
from test_meeting_materials import imported, materials as shared_materials, use
from test_realtime_participation_persistence import _result
from discussion_attention_fixtures import state_response

materials = shared_materials
state_app = shared_state_app


@pytest.fixture
def understood_materials(materials):
    repository, _, _ = materials
    material_set, attachment, version = imported(materials, content="下载结果只允许读取本人的已完成任务，通知可以稍后发送。", filename="public.txt")
    p = V2Persistence(repository.library.database_path, semantic_projection_mode="llm_first")
    p.create_meeting(meeting_id="review", title="公开资料游标测试", now_ms=1_000)
    repository.bind("review", material_set["id"])
    for seq in range(1, 4):
        append(p, seq, "支付成功后发放权益，通知可以稍后发送。", now=2_000 + seq)
    p._conn.execute("UPDATE jobs SET status='cancelled',error_class='isolated_other_lanes' WHERE status='pending'")
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=10_000)
    running = p.claim_next_job(worker_id="state", lane="discussion_state", now_ms=10_000, lease_ms=5_000)
    assert running["id"] == queued["id"]
    p.apply_intelligence_response(meeting_id="review", job_id=running["id"], now_ms=10_010,
        response=state_response(p, running, {"paragraph_revisions": [], "follow_up": None,
            "topic_update": {"operation": "add", "title": "支付与通知", "summary": "讨论通知等待",
                "evidence_segment_ids": ["s1"], "evidence_quote": "通知可以稍后发送"},
            "state_changes": [{"type": "open_question", "operation": "add", "item_id": "q-notify",
                "content": "通知是否需要同步等待", "evidence_segment_ids": ["s1"],
                "evidence_quote": "通知可以稍后发送", "confidence": .9}]}))
    assert p.complete_job(job_id=running["id"], worker_id="state", now_ms=10_020, output={})
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    yield p, repository, material_set, attachment, version, queued
    p.close()


def _state_jobs(p):
    return [job for job in p.list_jobs(meeting_id="review") if job["request_context"].get("task_mode") == "discussion_state"]


def _append_next_batch(p):
    for seq in range(4, 7):
        append(p, seq, "通知不需要同步等待，继续核对下载结果的处理边界。", now=70_000 + seq)
    p._conn.execute("UPDATE jobs SET status='cancelled',error_class='isolated_other_lanes' WHERE status='pending'")


def _enable(repository, material_set, attachment, version):
    return use(repository, material_set, attachment, version)


def _finish_attention_refresh(p, at):
    """One native read of changed material authority; canonical speech stays understood."""
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=at) is None
    job = p.enqueue_discussion_state(meeting_id="review", now_ms=at)
    assert job is not None and job["max_attempts"] == 1
    assert job["request_context"]["semantic_from_seq"] == 3
    assert job["request_context"]["anchor_seq"] == 3
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=at + 1) is None
    running = p.claim_next_job(worker_id="material-refresh", lane="discussion_state", now_ms=at, lease_ms=5_000)
    assert running["id"] == job["id"]
    applied = p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=at + 2,
        response=state_response(p, running, {"paragraph_revisions": [], "follow_up": None,
            "topic_update": None, "state_changes": []}))
    assert p.complete_job(job_id=job["id"], worker_id="material-refresh", now_ms=at + 3, output=applied)
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=at + 4) is None
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    return job


def test_material_enable_keeps_understood_cursor_and_only_new_batch_is_pending(understood_materials):
    p, repository, material_set, attachment, version, _ = understood_materials
    _append_next_batch(p)
    _enable(repository, material_set, attachment, version)
    marked = p.get_current_discussion("review")
    assert marked["context"]["phase"] == "needs_review" and marked["context"]["last_semantic_seq"] == 3
    job = p.enqueue_discussion_state(meeting_id="review", now_ms=75_000)
    assert job is not None
    metadata = job["request_context"]
    assert metadata["discussion_context"]["last_semantic_seq"] == 3
    assert metadata["semantic_from_seq"] == 3
    assert metadata["anchor_seq"] == 6
    seq_by_id = {row["segment_id"]: int(row["transcript_seq"]) for row in p._conn.execute("SELECT segment_id,transcript_seq FROM transcript_segments WHERE meeting_id='review'")}
    assert sorted(item["segment_id"] for item in metadata["evidence"] if seq_by_id[item["segment_id"]] > metadata["semantic_from_seq"]) == ["s4", "s5", "s6"]
    assert {item["segment_id"] for item in metadata["evidence"]} == {"s1", "s2", "s3", "s4", "s5", "s6"}
    assert metadata["analysis_basis_revision"] == marked["context"]["analysis_basis_revision"]
    assert metadata["input_context"]["material_context_revision"] == repository.get_set(material_set["id"])["context_revision"]
    assert p.discussion_request_state("review", metadata) == "valid"


def test_material_only_refresh_cancels_old_input_and_never_reopens_card_or_calls_state(understood_materials):
    p, repository, material_set, attachment, version, _ = understood_materials
    published = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="compare_options",
        idempotency_key="public-card", now_ms=11_000)
    claimed = p.claim_next_job(worker_id="public-card", lane="pi_deep", now_ms=11_000, lease_ms=5_000)
    assert claimed["id"] == published["id"]
    result = p.apply_intelligence_response(meeting_id="review", job_id=published["id"], response=_result(published), now_ms=11_010)
    assert p.complete_job(job_id=published["id"], worker_id="public-card", now_ms=11_020, output=result)
    queued_old = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
        idempotency_key="old-input-pending", now_ms=12_000)
    before = p.get_current_discussion("review")
    _enable(repository, material_set, attachment, version)
    assert p.get_job(queued_old["id"])["status"] == "cancelled"
    assert p.discussion_request_state("review", queued_old["request_context"]) != "valid"
    binding = p._conn.execute("SELECT * FROM coach_source_bindings WHERE meeting_id='review' AND decision_id='contribution:1'").fetchone()
    assert binding is not None and binding["invalidated_at_ms"] is not None
    assert any(json.loads(event["payload_json"]).get("coach_decision", {}).get("lifecycle_action") == "retract"
        for event in p._conn.execute("SELECT payload_json FROM meeting_events WHERE meeting_id='review' AND causation_id='knowledge_context' AND type='meeting.intelligence.applied'"))
    _finish_attention_refresh(p, 75_000)
    current = p.get_current_discussion("review")
    assert current["context"]["last_semantic_seq"] == 3 and current["context"]["phase"] == "discussing"
    assert current["context"]["analysis_basis_revision"] == before["context"]["analysis_basis_revision"] + 1
    assert current["context"]["open_points"] == before["context"]["open_points"]
    assert current["context"]["responded_points"] == before["context"]["responded_points"]
    assert input_context_state(p._conn, "review", current["input_context"]) == "valid"
    assert len(_state_jobs(p)) == 2
    assert p._conn.execute("SELECT invalidated_at_ms FROM coach_source_bindings WHERE decision_id='contribution:1'").fetchone()[0] == binding["invalidated_at_ms"]
    assert p.get_job(queued_old["id"])["status"] == "cancelled"
    assert discussion_understanding_ready(current, p.list_jobs(meeting_id="review"))
    evidence = retrieve_meeting_knowledge(repository.library, "review", "下载", include_attachments=True)
    assert evidence and source_evidence_state(p._conn, "review", evidence) == "valid"
    automatic = p.enqueue_discussion_update(meeting_id="review", now_ms=75_010, source_evidence=evidence)
    assert automatic is not None and automatic["attempts"] == 0
    assert automatic["request_context"]["analysis_basis_revision"] == current["context"]["analysis_basis_revision"]
    assert automatic["request_context"]["source_evidence"] == evidence
    assert p.discussion_request_state("review", automatic["request_context"]) == "valid"
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=110_000, source_evidence=evidence) is None


def test_material_version_refresh_preserves_cursor_sources_and_automatic_cooldown(understood_materials):
    p, repository, material_set, attachment, version, _ = understood_materials
    enabled = _enable(repository, material_set, attachment, version)
    _finish_attention_refresh(p, 75_000)
    v1_sources = retrieve_meeting_knowledge(repository.library, "review", "下载", include_attachments=True)
    assert v1_sources
    first = p.enqueue_discussion_update(meeting_id="review", now_ms=75_010, source_evidence=v1_sources)
    assert first is not None
    v2 = repository.add_version(material_set["id"], attachment["id"], "下载结果需要再次核对本人权限，通知可以稍后发送。",
        expected_revision=enabled["revision"], base_version_id=version["id"])
    current_attachment = repository.get_attachment(material_set["id"], attachment["id"])
    _enable(repository, material_set, current_attachment, v2)
    assert p.get_job(first["id"])["status"] == "cancelled"
    assert p.discussion_request_state("review", first["request_context"]) != "valid"
    assert source_evidence_state(p._conn, "review", v1_sources) != "valid"
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=75_020) is None
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    v2_sources = retrieve_meeting_knowledge(repository.library, "review", "下载", include_attachments=True)
    assert v2_sources and all(source["version_id"] == v2["id"] for source in v2_sources)
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=75_025, source_evidence=v2_sources) is None
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=105_020, source_evidence=v2_sources) is None
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=134_999) is None
    _finish_attention_refresh(p, 135_000)
    second = p.enqueue_discussion_update(meeting_id="review", now_ms=135_020, source_evidence=v2_sources)
    assert second is not None and second["request_context"]["analysis_basis_revision"] > first["request_context"]["analysis_basis_revision"]
    assert p.discussion_request_state("review", second["request_context"]) == "valid"
    assert len(_state_jobs(p)) == 3


def test_material_change_still_revokes_pending_state_and_refreezes_current_authorization(understood_materials):
    p, repository, material_set, attachment, version, _ = understood_materials
    _append_next_batch(p)
    enabled = _enable(repository, material_set, attachment, version)
    old = p.enqueue_discussion_state(meeting_id="review", now_ms=75_000)
    assert old is not None and old["request_context"]["semantic_from_seq"] == 3
    newer = repository.add_version(material_set["id"], attachment["id"], "下载任务需要核对本人权限后再提供结果。",
        expected_revision=enabled["revision"], base_version_id=version["id"])
    attachment = repository.get_attachment(material_set["id"], attachment["id"])
    _enable(repository, material_set, attachment, newer)
    assert p.get_job(old["id"])["status"] == "cancelled"
    assert p.discussion_request_state("review", old["request_context"]) != "valid"
    with pytest.raises(IntelligenceEvidenceSuperseded):
        p.apply_intelligence_response(meeting_id="review", job_id=old["id"], now_ms=75_100,
            response={"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None})
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=75_100) is None
    fresh = p.enqueue_discussion_state(meeting_id="review", now_ms=140_000)
    assert fresh is not None and fresh["request_context"]["semantic_from_seq"] == 3
    assert fresh["request_context"]["input_context"]["material_context_revision"] > old["request_context"]["input_context"]["material_context_revision"]
    assert p.discussion_request_state("review", fresh["request_context"]) == "valid"


def test_original_revision_still_resets_cursor_rechecks_without_new_speech_and_rejects_stale_job(understood_materials):
    p, repository, material_set, attachment, version, _ = understood_materials
    _enable(repository, material_set, attachment, version)
    refresh = p.enqueue_discussion_state(meeting_id="review", now_ms=75_000)
    assert refresh is not None
    stale = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question", idempotency_key="before-original-change", now_ms=75_010)
    assert p.commit_transcript_revision(meeting_id="review", segment_id="s1", expected_evidence_hash="h1",
        corrected_text="支付成功后发放权益，通知不可以稍后发送。", revision_id="public-correction", now_ms=76_000)
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 0
    assert p.get_job(refresh["id"])["status"] == "cancelled"
    assert p.get_job(stale["id"])["status"] == "cancelled"
    with pytest.raises(IntelligenceEvidenceSuperseded):
        p.apply_intelligence_response(meeting_id="review", job_id=stale["id"], now_ms=76_010, response=_result(stale))
    fresh = p.enqueue_discussion_state(meeting_id="review", now_ms=80_000)
    assert fresh is not None and fresh["request_context"]["semantic_from_seq"] == 0
    assert {item["segment_id"] for item in fresh["request_context"]["evidence"]} == {"s1", "s2", "s3"}
    assert next(item["revision"] for item in fresh["request_context"]["evidence"] if item["segment_id"] == "s1") == 2
    assert p.discussion_request_state("review", fresh["request_context"]) == "valid"


def _single_short_revision(materials, *, revision_ms=76_000):
    repository, _, _ = materials
    material_set, attachment, version = imported(materials, content="通知需要独立核对失败边界。", filename="public.txt")
    p = V2Persistence(repository.library.database_path, semantic_projection_mode="llm_first")
    try:
        p.create_meeting(meeting_id="review", title="公开短修订测试", now_ms=1_000)
        repository.bind("review", material_set["id"])
        text = "会议里，我们需要核对支付成功后的权益发放，短信通知可以异步处理。页面重新打开时先查询当前结果，通知失败不应让已完成的权益重复发放。还需要确认网络超时后的查询路径，以及用户重新提交时服务端如何辨别同一付款请求。今天先讨论处理边界和状态展示，暂时不讨论负责人、预算或最终上线日期。"
        assert len(text) == 138
        append(p, 1, text, now=2_000)
        p._conn.execute("UPDATE jobs SET status='cancelled',error_class='isolated_other_lanes' WHERE status='pending'")
        initial = p.enqueue_discussion_state(meeting_id="review", now_ms=10_000)
        claimed = p.claim_next_job(worker_id="state", lane="discussion_state", now_ms=10_000, lease_ms=5_000)
        assert claimed["id"] == initial["id"]
        p.apply_intelligence_response(meeting_id="review", job_id=initial["id"], now_ms=10_010,
            response=state_response(p, initial, {"paragraph_revisions": [], "follow_up": None, "state_changes": [],
                "topic_update": {"operation": "add", "title": "通知边界", "summary": "讨论通知处理方式",
                    "evidence_segment_ids": ["s1"], "evidence_quote": "短信通知可以异步处理"}}))
        assert p.complete_job(job_id=initial["id"], worker_id="state", now_ms=10_020, output={})
        assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 1
        revised = "通知不能异步处理。"
        assert len(revised) == 9
        assert p.commit_transcript_revision(meeting_id="review", segment_id="s1", expected_evidence_hash="h1",
            corrected_text=revised, revision_id="public-short-revision", now_ms=revision_ms)
        return p, repository, material_set, attachment, version
    except BaseException:
        p.close()
        raise


@pytest.mark.parametrize("enable_material", [False, True])
def test_short_original_revision_retains_full_recheck_intent_across_material_refresh(materials, enable_material):
    p, repository, material_set, attachment, version = _single_short_revision(materials)
    try:
        if enable_material:
            _enable(repository, material_set, attachment, version)
        before = p.get_current_discussion("review")
        assert before["context"]["phase"] == "needs_review" and before["context"]["last_semantic_seq"] == 0
        assert (input_context_state(p._conn, "review", before["input_context"]) != "valid") == enable_material
        # Canonical revision rechecks retain their own two-second stability gate.
        assert p.enqueue_discussion_state(meeting_id="review", now_ms=77_999) is None
        fresh = p.enqueue_discussion_state(meeting_id="review", now_ms=78_000)
        assert fresh is not None
        assert fresh["request_context"]["semantic_from_seq"] == 0
        assert fresh["request_context"]["anchor_seq"] == 1
        assert [(source["segment_id"], source["revision"]) for source in fresh["request_context"]["evidence"]] == [("s1", 2)]
        assert p.discussion_request_state("review", fresh["request_context"]) == "valid"
        assert len(_state_jobs(p)) == 2
    finally:
        p.close()


@pytest.mark.parametrize("defer_gate", ["revision_cooldown_bypass", "manual_first"])
def test_short_revision_intent_survives_deferred_admission_and_manual_first_freeze(materials, defer_gate):
    p, repository, material_set, attachment, version = _single_short_revision(
        materials, revision_ms=16_000 if defer_gate == "revision_cooldown_bypass" else 76_000)
    try:
        _enable(repository, material_set, attachment, version)
        if defer_gate == "revision_cooldown_bypass":
            assert p.enqueue_discussion_state(meeting_id="review", now_ms=17_999) is None
            assert p.get_current_discussion("review")["context"]["phase"] == "needs_review"
            fresh = p.enqueue_discussion_state(meeting_id="review", now_ms=18_000)
            assert fresh is not None and fresh["created_at_ms"] - 10_000 < 60_000
        else:
            # This uses the normal manual freezer before the state timer.
            manual = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
                idempotency_key="short-revision-manual-first", now_ms=80_000)
            assert manual["request_context"]["discussion_context"]["phase"] == "needs_review"
            assert manual["request_context"]["discussion_context"]["last_semantic_seq"] == 0
            assert p.enqueue_discussion_state(meeting_id="review", now_ms=80_001) is None
            assert p.get_current_discussion("review")["context"]["phase"] == "needs_review"
            claimed = p.claim_next_job(worker_id="manual", lane="pi_deep", now_ms=80_010, lease_ms=5_000)
            assert claimed["id"] == manual["id"]
            assert p.cancel_job(job_id=manual["id"], worker_id="manual", now_ms=80_020,
                error_class="isolated_manual_cancelled")["status"] == "cancelled"
            fresh = p.enqueue_discussion_state(meeting_id="review", now_ms=80_100)
        assert fresh is not None and fresh["request_context"]["semantic_from_seq"] == 0
        assert p.discussion_request_state("review", fresh["request_context"]) == "valid"
        assert len(_state_jobs(p)) == 2
    finally:
        p.close()


@pytest.mark.parametrize("existing_discussion", [False, True])
@pytest.mark.parametrize("enable_material", [False, True])
def test_initial_short_input_without_revision_does_not_bypass_default_threshold(materials, existing_discussion, enable_material):
    repository, _, _ = materials
    material_set, attachment, version = imported(materials, content="通知需要独立核对失败边界。", filename="public.txt")
    p = V2Persistence(repository.library.database_path, semantic_projection_mode="llm_first")
    try:
        p.create_meeting(meeting_id="review", title="公开初始短输入", now_ms=1_000)
        repository.bind("review", material_set["id"])
        append(p, 1, "通知不能异步处理。", now=2_000)
        if existing_discussion:
            p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
                idempotency_key="initial-short-user-request", now_ms=3_000)
            assert "last_semantic_seq" not in p.get_current_discussion("review")["context"]
        p._conn.execute("UPDATE jobs SET status='cancelled',error_class='isolated_other_lanes' WHERE status='pending'")
        if enable_material:
            _enable(repository, material_set, attachment, version)
        assert p.enqueue_discussion_state(meeting_id="review", now_ms=80_000) is None
        assert _state_jobs(p) == []
        current = p.get_current_discussion("review")
        if existing_discussion:
            assert current["context"]["phase"] == "discussing" and not current["context"].get("last_semantic_seq")
        else:
            assert current is None
    finally:
        p.close()


def test_empty_meeting_does_not_create_discussion_or_model_job(materials):
    repository, _, _ = materials
    p = V2Persistence(repository.library.database_path, semantic_projection_mode="llm_first")
    try:
        p.create_meeting(meeting_id="review", title=None, now_ms=1_000)
        assert p.enqueue_discussion_state(meeting_id="review", now_ms=75_000) is None
        assert p.get_current_discussion("review") is None and _state_jobs(p) == []
    finally:
        p.close()


def test_state_zero_never_admits_implicit_understanding_and_policy_off_remains_gate(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "0")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    app.state.meeting_preparation_store.save("review", proactive_suggestion_policy="off", updated_at_ms=now)
    material_services = (app.state.meeting_materials_repository, app.state.meeting_material_importer,
                         app.state.meeting_material_jobs)
    repository, _, _ = material_services
    material_set, attachment, version = imported(material_services, content="下载结果需核对本人权限。", filename="public.txt")
    repository.bind("review", material_set["id"])
    for seq in range(1, 4):
        append(p, seq, now=now - 5_000)
    p._conn.execute("UPDATE jobs SET status='cancelled',error_class='isolated_other_lanes' WHERE status='pending'")
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
    claimed = p.claim_next_job(worker_id="local-fixture", lane="discussion_state", now_ms=now, lease_ms=5_000)
    assert claimed["id"] == queued["id"]
    p.apply_intelligence_response(meeting_id="review", job_id=queued["id"], now_ms=now,
        response=state_response(p, queued, {"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None}))
    assert p.complete_job(job_id=queued["id"], worker_id="local-fixture", now_ms=now, output={})
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    _enable(repository, material_set, attachment, version)
    app.state.provider_lane_registry.sync_realtime_work(set())
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert len(_state_jobs(p)) == 1
    current = p.get_current_discussion("review")
    assert current["context"]["phase"] == "needs_review" and current["context"]["last_semantic_seq"] == 3
    assert all(job["attempts"] == 0 for job in p.list_jobs(meeting_id="review") if job["id"] != queued["id"])
    assert not automatic_discussion_admission(policy="off", prior_jobs=[], now_ms=now).allowed
