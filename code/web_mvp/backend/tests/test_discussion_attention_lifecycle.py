"""Current opportunity controls automatic contribution, not historical memory."""
from __future__ import annotations

import copy
import asyncio
import json

import pytest

from meeting_copilot_web_mvp.discussion_state_identity import state_input_fingerprint
from meeting_copilot_web_mvp.realtime_intelligence import IntelligenceResponseValidationError
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


CONTRACT = "discussion_attention.v1"


@pytest.fixture
def store(tmp_path):
    p = V2Persistence(tmp_path / "attention.db", semantic_projection_mode="llm_first")
    p.create_meeting(meeting_id="review", title=None, now_ms=0)
    for seq, text in enumerate(("这个方案是否允许删除历史？", "历史必须保留。", "这个操作会删历史吗？"), 1):
        p.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
            text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
            evidence_hash=f"h{seq}", now_ms=1000 + seq)
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")
    with p._write_transaction():
        p._ensure_discussion_locked("review", now_ms=2000)
        p._mark_discussion_semantic_checked_locked("review", 3, 2000)
    yield p
    p.close()


def packet(store):
    with store._write_transaction():
        metadata = store._freeze_participation_locked(meeting_id="review", now_ms=3000,
            intent="freeform", user_request="", topic_id=None, expected_basis_revision=None,
            selected_segment_ids=(), source_evidence=())
    metadata.update(task_mode="discussion_state", attention_contract=CONTRACT, attention_focus_segment_ids=["s2", "s3"], semantic_from_seq=3)
    return metadata


def attention(*, status="present", relation="same_topic", segment="s3", quote="这个操作会删历史吗？", previous=None):
    ref = {"evidence_segment_ids": ["s3"], "evidence_quote": "这个操作会删历史吗？"}
    return {"current_focus": {"observations": [ref], "relation": relation,
        "object": None, "setting": None, "purpose": None},
        "contribution_opportunity": {"status": status, "intent": "ask_question" if status == "present" else None,
            "reason": "需要确认当前操作是否满足保留历史的约束。" if status == "present" else "当前没有新的待决条件。",
            "evidence_segment_ids": [segment] if status == "present" else [],
            "evidence_quote": quote if status == "present" else "", "opportunity_id": previous}}


def apply(store, value, *, at=4000, metadata=None):
    metadata = metadata or packet(store)
    with store._write_transaction():
        return store._apply_current_attention_locked(meeting_id="review", attention=value,
            metadata=metadata, now_ms=at, excluding_job_id=None)


def test_legacy_fingerprint_is_exactly_preserved_but_new_contract_changes_identity():
    metadata = {"profile": "participation", "task_mode": "discussion_state", "topic_id": "topic:old",
        "analysis_basis_revision": 2, "anchor_seq": 3, "semantic_from_seq": 3,
        "input_context": {}, "evidence": [{"segment_id": "s3", "revision": 1, "evidence_hash": "h3"}]}
    old = state_input_fingerprint(metadata)
    assert old == "80575d9850ea2fee4bef907f12aa20972364f9458024687c837cf4906fecebca"
    new = state_input_fingerprint({**metadata, "attention_contract": CONTRACT, "attention_focus_segment_ids": ["s3"]})
    assert new is not None and new != old
    assert state_input_fingerprint({**metadata, "attention_contract": CONTRACT}) is None


def test_legacy_caught_up_upgrades_once_without_resetting_semantic_cursor(store):
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=10000)
    assert job is not None
    assert job["request_context"]["attention_contract"] == CONTRACT
    assert job["request_context"]["attention_focus_segment_ids"] == ["s2", "s3"]
    assert job["request_context"]["semantic_from_seq"] == 3
    assert store.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10001) is None
    store._conn.execute("UPDATE jobs SET status='failed',error_class='ReadTimeout' WHERE id=?", (job["id"],))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=100000, cooldown_ms=0) is None


def test_legacy_upgrade_obeys_l2_and_manual_priority(store):
    store._conn.execute("UPDATE jobs SET status='pending' WHERE kind='correction'")
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10000) is None
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")
    manual = store.enqueue_user_coach_request(meeting_id="review", user_request="请回看之前的方案。",
        intent="freeform", idempotency_key="priority", now_ms=10000)
    assert manual["trigger_type"] == "user_request"
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10001) is None


def test_legacy_does_not_implicitly_grant_automatic_opportunity(store):
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=10000) is None
    assert store.enqueue_user_coach_request(meeting_id="review", user_request="请回看之前的方案。",
        intent="freeform", idempotency_key="legacy-manual", now_ms=10001) is not None


def test_attention_only_noop_persists_without_changing_historical_problem_or_basis(store):
    before = store.get_current_discussion("review")
    saved = apply(store, attention(status="none", relation="interlude"))
    after = store.get_current_discussion("review")
    assert saved == after["context"]["current_attention"]
    assert saved["contribution_opportunity"]["status"] == "none"
    assert before["context"]["current_problem"] == after["context"]["current_problem"]
    assert before["context"]["analysis_basis_revision"] == after["context"]["analysis_basis_revision"]
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=10000) is None


def test_unknown_relation_can_still_have_an_explicit_current_risk(store):
    saved = apply(store, attention(relation="uncertain"))
    assert saved["opportunity_id"]
    job = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert job is not None
    assert job["request_context"]["current_attention"] == saved


def test_same_opportunity_reason_and_coverage_do_not_rearm_or_cancel(store):
    first = apply(store, attention())
    job = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert job is not None
    changed = attention(previous=first["opportunity_id"])
    changed["contribution_opportunity"]["reason"] = "请确认这个待决条件。"
    saved = apply(store, changed, at=11000)
    assert saved["opportunity_id"] == first["opportunity_id"]
    assert saved["control_revision"] == first["control_revision"]
    assert store.get_job(job["id"])["status"] == "pending"
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=50000, cooldown_ms=0) is None


def test_same_basis_new_opportunity_queues_once_after_original_cooldown(store):
    first = apply(store, attention())
    old = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert old is not None
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (old["id"],))
    second = apply(store, attention(segment="s1", quote="这个方案是否允许删除历史？"), at=11000)
    assert second["opportunity_id"] != first["opportunity_id"]
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=11001) is None
    latest = store.enqueue_discussion_update(meeting_id="review", now_ms=40000)
    assert latest is not None and latest["id"] != old["id"]
    assert latest["request_context"]["analysis_basis_revision"] == old["request_context"]["analysis_basis_revision"]
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=80000) is None


@pytest.mark.parametrize("running", [False, True])
def test_opportunity_revocation_cancels_automatic_but_not_manual(store, running):
    first = apply(store, attention())
    automatic = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert automatic is not None
    manual = store.enqueue_user_coach_request(meeting_id="review", user_request="回看之前为什么保留历史。",
        intent="freeform", idempotency_key="history", now_ms=11000)
    # Manual admission preempts automatic work; revive that isolated fake lease
    # to prove an attention change itself leaves the manual request intact.
    store._conn.execute("UPDATE jobs SET status=? WHERE id=?", ("running" if running else "pending", automatic["id"]))
    apply(store, attention(status="none", relation="interlude"), at=12000)
    assert store.get_job(automatic["id"])["status"] == "cancelled"
    assert store.get_job(manual["id"])["status"] == "pending"
    assert store.discussion_request_state("review", automatic["request_context"]) == "attention_superseded"
    assert store.discussion_request_state("review", manual["request_context"]) == "valid"
    assert first["opportunity_id"]


def test_none_to_present_restores_once_even_with_unchanged_legacy_semantics(store):
    first = apply(store, attention())
    old = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert old is not None
    apply(store, attention(status="none"), at=11000)
    restored = apply(store, attention(), at=12000)
    assert restored["opportunity_id"] != first["opportunity_id"]
    repeated = apply(store, attention(), at=12001)
    assert repeated["opportunity_id"] == restored["opportunity_id"]
    assert repeated["control_revision"] == restored["control_revision"]
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=40000) is not None
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=80000) is None


def test_unknown_previous_opportunity_is_rejected_without_mutation(store):
    before = store.get_current_discussion("review")
    with pytest.raises(IntelligenceResponseValidationError):
        apply(store, attention(previous="opportunity:unregistered"))
    assert store.get_current_discussion("review") == before


def test_attention_is_host_managed_in_public_patch(store):
    current = store.get_current_discussion("review")
    with pytest.raises(ValueError, match="host managed"):
        store.update_discussion_context(meeting_id="review", work_item_id=current["work_item_id"],
            expected_version=current["version"], now_ms=10000, context_patch={"current_attention": {}})


@pytest.mark.parametrize("relation", ["same_topic", "interlude", "uncertain", "unknown"])
def test_mature_history_with_corrected_zero_cursor_is_not_bootstrap(store, relation):
    with store._write_transaction():
        store._upsert_entity_locked("review", "current_topic", {"id": "current-topic", "text": "保留历史方案",
            "status": "active", "evidence_segment_ids": [], "updated_at_ms": 3000}, current_transcript_seq=3)
        row = store._current_discussion_locked("review")
        context = json.loads(row["context_json"])
        context.update(last_semantic_seq=0, phase="needs_review")
        store._conn.execute("UPDATE agent_work_items SET context_json=? WHERE work_item_id=?", (json.dumps(context), row["work_item_id"]))
    raw = {"operation": "add", "title": "未经确认的场景", "summary": "猜测当前活动。",
        "evidence_segment_ids": ["s3"], "evidence_quote": "这个操作会删历史吗？"}
    metadata = packet(store)
    with store._write_transaction():
        effective = store._attention_topic_update_locked(meeting_id="review", topic_update=raw,
            attention=attention(relation=relation), metadata=metadata)
    assert effective is None or effective["operation"] == "update" and effective["title"] == "保留历史方案"


def test_revised_attention_source_revokes_opportunity(store):
    apply(store, attention())
    job = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert job is not None
    assert store.commit_transcript_revision(meeting_id="review", segment_id="s3", expected_evidence_hash="h3",
        corrected_text="这个操作不会删除历史。", revision_id="corrected", now_ms=11000)
    saved = store.get_current_discussion("review")["context"]["current_attention"]
    assert saved["contribution_opportunity"]["status"] == "uncertain"
    assert saved["opportunity_id"] is None
    assert store.get_job(job["id"])["status"] == "cancelled"


def test_hook_rechecks_frozen_source_revision_and_rolls_back(store):
    metadata = packet(store)
    assert store.commit_transcript_revision(meeting_id="review", segment_id="s3", expected_evidence_hash="h3",
        corrected_text="这个操作不会删除历史。", revision_id="newer", now_ms=5000)
    before = copy.deepcopy(store.get_current_discussion("review"))
    with pytest.raises(IntelligenceResponseValidationError, match="evidence superseded"):
        apply(store, attention(), at=6000, metadata=metadata)
    assert store.get_current_discussion("review") == before


def test_new_coverage_same_opportunity_does_not_rearm_or_require_latest_cursor_equality(store):
    saved = apply(store, attention())
    job = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert job is not None
    store.commit_final_and_enqueue(meeting_id="review", final_id="f4", segment_id="s4", text="请继续。",
        normalized_text="请继续。", started_at_ms=4000, ended_at_ms=4800, evidence_hash="h4", now_ms=11000)
    assert store.discussion_request_state("review", job["request_context"]) == "valid"
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")
    metadata = packet(store)
    metadata["attention_focus_segment_ids"] = ["s3", "s4"]
    updated = apply(store, attention(previous=saved["opportunity_id"]), at=14000, metadata=metadata)
    assert updated["covered_seq"] == 4
    assert updated["control_revision"] == saved["control_revision"]
    assert store.get_job(job["id"])["status"] == "pending"
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=50000) is None


def test_initial_short_bootstrap_is_not_a_legacy_upgrade(store):
    store.create_meeting(meeting_id="bootstrap", title=None, now_ms=0)
    store.commit_final_and_enqueue(meeting_id="bootstrap", final_id="hello", segment_id="hello", text="你好。",
        normalized_text="你好。", started_at_ms=0, ended_at_ms=800, evidence_hash="hello", now_ms=1000)
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")
    with store._write_transaction():
        store._ensure_discussion_locked("bootstrap", now_ms=2000)
    assert store.enqueue_discussion_state(meeting_id="bootstrap", now_ms=10000) is None


def test_background_patch_revokes_attention_and_refreshes_once_without_reopening_speech(store):
    apply(store, attention())
    before = store.get_current_discussion("review")
    store.update_discussion_context(meeting_id="review", work_item_id=before["work_item_id"],
        expected_version=before["version"], now_ms=5000, context_patch={"current_solution": "用户补充方案背景。"})
    invalidated = store.get_current_discussion("review")["context"]
    assert invalidated["last_semantic_seq"] == 3
    assert invalidated["current_attention"]["opportunity_id"] is None
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=10000)
    assert job is not None and job["request_context"]["semantic_from_seq"] == 3
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10001) is None
    store._conn.execute("UPDATE jobs SET status='failed',error_class='ReadTimeout' WHERE id=?", (job["id"],))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=100000, cooldown_ms=0) is None


def test_input_memory_change_refreshes_current_authorization_once_without_cursor_reset(store):
    from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
    from meeting_copilot_web_mvp.user_memory import UserMemory

    apply(store, attention())
    before = store.get_current_discussion("review")
    repository = KnowledgeRepository(store.database_path)
    try:
        memory = UserMemory(repository)
        candidate = memory.create(scope="meeting", scope_id="review", scene="general", kind="term",
            memory_key="历史保留", value="历史保留")
        memory.update(candidate["id"], expected_revision=candidate["revision"], status="active")
        marked = store.get_current_discussion("review")
        assert marked["context"]["last_semantic_seq"] == 3
        assert marked["context"]["current_attention"]["opportunity_id"] is None
        job = store.enqueue_discussion_state(meeting_id="review", now_ms=10000)
        assert job is not None
        assert job["request_context"]["semantic_from_seq"] == 3
        assert job["request_context"]["input_context"] != before["input_context"]
        assert store.enqueue_discussion_state(meeting_id="review", now_ms=10001) is None
        store._conn.execute("UPDATE jobs SET status='failed',error_class='ReadTimeout' WHERE id=?", (job["id"],))
        assert store.enqueue_discussion_state(meeting_id="review", now_ms=100000, cooldown_ms=0) is None
    finally:
        repository.close()


def test_automatic_publication_rechecks_opportunity_after_provider_return(store):
    from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded
    from test_realtime_participation_persistence import _result

    apply(store, attention())
    job = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert job is not None
    apply(store, attention(status="none"), at=11000)
    # A late fake provider result cannot revive a cancelled lease or bypass
    # the same transaction's attention/source admission fence.
    store._conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    with pytest.raises(IntelligenceEvidenceSuperseded):
        store.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=_result(job), now_ms=12000)
    assert store._conn.execute("SELECT COUNT(*) FROM coach_source_bindings WHERE job_id=?", (job["id"],)).fetchone()[0] == 0


@pytest.fixture
def native_app(tmp_path, monkeypatch):
    from test_discussion_state_background import state_app
    yield from state_app.__wrapped__(tmp_path, monkeypatch)


def test_native_state_packet_keeps_current_opportunity_source_older_than_latest_twelve(native_app, monkeypatch):
    from meeting_copilot_web_mvp import app as app_module
    from test_discussion_attention_contract import Provider, core

    app, now = native_app
    p = app.state.v2_persistence
    texts = {1: "这个方案是否允许删除历史？", 2: "历史必须保留。", 3: "这个操作会删历史吗？"}
    for seq, text in texts.items():
        p.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
            text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
            evidence_hash=f"h{seq}", now_ms=now - 20000 + seq)
    with p._write_transaction():
        p._ensure_discussion_locked("review", now_ms=now - 19000)
        p._mark_discussion_semantic_checked_locked("review", 3, now - 19000)
    original = apply(p, attention(segment="s1", quote=texts[1]), at=now - 18000)
    for seq in range(4, 17):
        text = f"第{seq}项继续核对保留历史的边界。"
        p.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
            text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
            evidence_hash=f"h{seq}", now_ms=now - 16000 + seq)
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
    assert queued is not None
    assert queued["request_context"]["attention_focus_segment_ids"] == ["s15", "s16"]
    job = p.claim_next_job(worker_id="state", lane="discussion_state", now_ms=now, lease_ms=5000)
    assert job["id"] == queued["id"]
    app.state.provider_lane_registry.sync_realtime_work(set())
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='intelligence' AND id!=?", (job["id"],))
    value = attention(segment="s1", quote=texts[1], previous=original["opportunity_id"])
    value["current_focus"]["observations"] = [{"evidence_segment_ids": ["s16"], "evidence_quote": "第16项继续核对保留历史的边界。"}]
    provider = Provider(json.dumps(core(value), ensure_ascii=False))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert len(provider.calls) == result["provider_attempt_count"] == 1
    wire = json.loads(provider.calls[0][0][1]["content"])
    visible = {item["id"]: item["text"] for group in ("new_paragraphs", "context_paragraphs", "retrieval_paragraphs") for item in wire[group]}
    assert visible["s1"] == texts[1]
    assert len(visible) == len(queued["request_context"]["evidence"]) == 13
    assert [item["segment_id"] for item in queued["request_context"]["evidence"]] == ["s1", *[f"s{seq}" for seq in range(5, 17)]]
    assert result["applied"]["current_attention"]["opportunity_id"] == original["opportunity_id"]


def test_inherited_opportunity_cannot_change_its_contribution_intent(store):
    saved = apply(store, attention())
    before = store.get_current_discussion("review")
    changed = attention(previous=saved["opportunity_id"])
    changed["contribution_opportunity"]["intent"] = "compare_options"
    with pytest.raises(IntelligenceResponseValidationError, match="intent"):
        apply(store, changed)
    assert store.get_current_discussion("review") == before


def test_current_opportunity_support_union_obeys_sixteen_source_budget(store):
    store.commit_final_and_enqueue(meeting_id="review", final_id="f4", segment_id="s4", text="补充保留历史方案。",
        normalized_text="补充保留历史方案。", started_at_ms=4000, ended_at_ms=4800, evidence_hash="h4", now_ms=1004)
    value = attention(segment="s1", quote="这个方案是否允许删除历史？")
    value["contribution_opportunity"]["evidence_segment_ids"] = ["s1", "s2", "s3", "s4"]
    metadata = packet(store)
    metadata["attention_focus_segment_ids"] = ["s3", "s4"]
    apply(store, value, metadata=metadata)
    for seq in range(5, 17):
        store.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}", text="继续核对边界。",
            normalized_text="继续核对边界。", started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
            evidence_hash=f"h{seq}", now_ms=1000 + seq)
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=10000)
    assert job is not None
    assert len(job["request_context"]["evidence"]) == 16
    assert job["request_context"]["anchor_seq"] == 16
    assert job["request_context"]["attention_focus_segment_ids"] == ["s15", "s16"]


def test_explicit_manual_selection_does_not_require_current_opportunity_source_union(store):
    apply(store, attention(segment="s1", quote="这个方案是否允许删除历史？"))
    for seq in range(4, 17):
        store.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}", text="继续核对边界。",
            normalized_text="继续核对边界。", started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
            evidence_hash=f"h{seq}", now_ms=1000 + seq)
    job = store.enqueue_user_coach_request(meeting_id="review", user_request="回看选定原话。",
        intent="freeform", selected_segment_ids=["s5"], idempotency_key="selected", now_ms=10000)
    assert {item["segment_id"] for item in job["request_context"]["evidence"]} == {"s5", "s16"}
    assert "automatic_attention" not in job["request_context"]


@pytest.mark.parametrize("status", ["none", "uncertain"])
def test_valid_unavailable_attention_does_not_start_repeated_state_refresh(store, status):
    apply(store, attention(status=status))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10000) is None
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=100000, cooldown_ms=0) is None


def test_revoked_opportunity_has_a_normal_public_cancellation_status(store):
    apply(store, attention())
    job = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert job is not None
    apply(store, attention(status="none"), at=11000)
    summary = next(item for item in store.get_snapshot("review")["jobs"] if item["id"] == job["id"])
    assert summary["status"] == "cancelled"
    assert summary["error_class"] == "discussion_attention_changed"


def test_background_attention_refresh_waits_for_original_state_cooldown(store):
    queued = store.enqueue_discussion_state(meeting_id="review", now_ms=10000)
    assert queued is not None
    job = store.claim_next_job(worker_id="first-state", lane="discussion_state", now_ms=10000, lease_ms=5000)
    assert job["id"] == queued["id"]
    store.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=10001,
        response={"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None,
            "current_attention": attention()})
    assert store.complete_job(job_id=job["id"], worker_id="first-state", now_ms=10002, output={})
    current = store.get_current_discussion("review")
    store.update_discussion_context(meeting_id="review", work_item_id=current["work_item_id"],
        expected_version=current["version"], now_ms=10020, context_patch={"current_solution": "补充背景约束。"})
    assert store.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10020) is None
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=69999) is None
    next_job = store.enqueue_discussion_state(meeting_id="review", now_ms=70000)
    assert next_job is not None and next_job["request_context"]["semantic_from_seq"] == 3
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=70001) is None


def test_legacy_contract_upgrade_waits_for_original_state_cooldown(store):
    queued = store.enqueue_discussion_state(meeting_id="review", now_ms=10000)
    assert queued is not None
    legacy = dict(queued["request_context"])
    for key in ("attention_contract", "attention_focus_segment_ids", "state_input_fingerprint"):
        legacy.pop(key, None)
    # Preserve a historical successful four-field row. This isolated migration
    # fixture does not claim that a new-contract response omitted attention.
    store._conn.execute("UPDATE jobs SET status='succeeded',request_context_json=? WHERE id=?",
        (json.dumps(legacy), queued["id"]))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10020) is None
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=69999) is None
    upgraded = store.enqueue_discussion_state(meeting_id="review", now_ms=70000)
    assert upgraded is not None and upgraded["request_context"]["attention_contract"] == CONTRACT
    assert upgraded["request_context"]["semantic_from_seq"] == 3
