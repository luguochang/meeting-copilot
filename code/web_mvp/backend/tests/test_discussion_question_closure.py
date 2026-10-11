"""Closure must prove question identity and source order, without buying another call."""
from __future__ import annotations

import json
import time

import pytest

from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded, IntelligenceProjectionError, V2Persistence
from discussion_attention_fixtures import state_response


@pytest.fixture
def closure_store(tmp_path):
    p = V2Persistence(tmp_path / "closure.db", semantic_projection_mode="llm_first")
    p.create_meeting(meeting_id="review", title=None, now_ms=1)
    yield p
    p.close()


def append(p, seq, text):
    now = time.time_ns() // 1_000_000
    p.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
        text=text, normalized_text=text, started_at_ms=seq * 1_000, ended_at_ms=seq * 1_000 + 800,
        evidence_hash=f"h{seq}", now_ms=now - 5_000, enqueue_intelligence=False)


def change(operation, quote, source, *, item_id="q", status=None):
    return {"type": "open_question", "operation": operation, "item_id": item_id,
        "content": quote, "status": status, "evidence_segment_ids": [source],
        "evidence_quote": quote, "confidence": .9}


def apply(p, changes, *, topic=None):
    now = time.time_ns() // 1_000_000
    job = p.enqueue_discussion_state(meeting_id="review", now_ms=now,
        cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=0)
    assert job is not None
    running = p.claim_next_job(worker_id="closure", lane="discussion_state", now_ms=now, lease_ms=5_000)
    assert running["id"] == job["id"]
    response = {"paragraph_revisions": [], "topic_update": topic, "state_changes": changes,
        "follow_up": None, "input_context": job["request_context"]["input_context"]}
    response = state_response(p, running, response)
    result = p.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=response, now_ms=now)
    assert p.complete_job(job_id=job["id"], worker_id="closure", now_ms=now + 1, output={"applied": result})
    return job, response, result


@pytest.mark.parametrize("operation,status", [("resolve", None), ("update", "answered"),
    ("add", "resolved"), ("update", "closed"), ("update", "done")])
def test_unknown_terminal_question_rejected_while_topic_and_fact_survive(closure_store, operation, status):
    p = closure_store
    append(p, 1, "通知可以异步。现在讨论通知时序。")
    topic = {"operation": "add", "title": "通知时序", "summary": "讨论通知时序",
        "evidence_segment_ids": ["s1"], "evidence_quote": "讨论通知时序"}
    fact = {**change("add", "通知可以异步", "s1", item_id="fact"), "type": "decision", "status": "confirmed"}
    job, response, result = apply(p, [change(operation, "通知可以异步", "s1", status=status), fact], topic=topic)
    assert result["state_change_count"] == 0
    projection = result["state_projection"]
    assert projection["accepted_operations"] == [fact]
    assert projection["rejected_operations"][0]["rule"] == "question_not_open_or_paired"
    assert projection["rejected_operations"][0]["message"]
    discussion = p.get_current_discussion("review")["context"]
    assert discussion["current_problem"] == "通知时序"
    assert discussion["open_points"] == discussion["responded_points"] == []
    assert len(discussion["constraints"]) == 1
    assert not p._conn.execute("SELECT 1 FROM meeting_entities WHERE meeting_id='review' AND entity_id='q'").fetchone()
    event = p._conn.execute("SELECT payload_json FROM meeting_events WHERE idempotency_key=?",
        (f"meeting.intelligence.applied:{job['id']}",)).fetchone()
    assert json.loads(event[0])["state_projection"] == projection
    repeated = p.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=response,
        now_ms=time.time_ns() // 1_000_000)
    assert repeated["idempotent"] and repeated["state_projection"] == projection


@pytest.mark.parametrize("same_segment", [False, True])
def test_explicit_same_batch_add_then_resolve_is_accepted(closure_store, same_segment):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？" + ("通知不用同步等待。" if same_segment else ""))
    if not same_segment:
        append(p, 2, "通知不用同步等待。")
    changes = [change("add", "通知需要同步等待吗", "s1"),
        change("resolve", "通知不用同步等待", "s1" if same_segment else "s2")]
    _, _, result = apply(p, changes)
    assert result["state_change_count"] == 2
    assert result["state_projection"]["rejected_operations"] == []
    context = p.get_current_discussion("review")["context"]
    assert context["open_points"] == []
    assert context["responded_points"][0]["id"] == "q"
    assert context["responded_points"][0]["resolution_proof"]["question_quote"] == "通知需要同步等待吗"


@pytest.mark.parametrize("operation,status", [("resolve", None), ("update", "answered"), ("add", "closed")])
def test_existing_question_later_negative_answer_closes_without_new_question(closure_store, operation, status):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？")
    apply(p, [change("add", "通知需要同步等待吗", "s1")])
    append(p, 2, "通知不用同步等待。")
    _, _, result = apply(p, [change(operation, "通知不用同步等待", "s2", status=status)])
    assert result["state_change_count"] == 1
    assert result["state_projection"]["accepted_operations"][0]["operation"] == "resolve"
    assert not p.get_current_discussion("review")["context"]["open_points"]


@pytest.mark.parametrize("question,answer_source,texts,rule", [
    ("入口在哪", "s1", ["已经进门。", "入口在哪？"], "answer_not_after_question"),
    ("通知需要同步等待吗", "s1", ["通知不用同步等待。通知需要同步等待吗？"], "answer_not_after_question"),
    ("通知需要同步等待吗", "s1", ["通知需要同步等待吗？通知不用同步等待。通知不用同步等待。"], "question_answer_quote_ambiguous"),
    ("通知需要同步等待吗", "s1", ["通知需要同步等待吗？"], "answer_not_after_question"),
])
def test_unproven_order_preserves_accepted_add(closure_store, question, answer_source, texts, rule):
    p = closure_store
    for seq, text in enumerate(texts, 1):
        append(p, seq, text)
    answer = "已经进门" if len(texts) == 2 else "通知需要同步等待吗" if "不用" not in texts[0] else "通知不用同步等待"
    _, _, result = apply(p, [change("add", question, f"s{len(texts)}"), change("resolve", answer, answer_source)])
    assert result["state_change_count"] == 1
    assert result["state_projection"]["rejected_operations"][0]["rule"] == rule
    context = p.get_current_discussion("review")["context"]
    assert context["open_points"][0]["id"] == "q" and context["responded_points"] == []


def test_resolve_before_declared_add_cannot_claim_pair(closure_store):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？通知不用同步等待。")
    _, _, result = apply(p, [change("resolve", "通知不用同步等待", "s1"),
        change("add", "通知需要同步等待吗", "s1")])
    assert result["state_change_count"] == 1
    assert result["state_projection"]["rejected_operations"][0]["rule"] == "question_not_open_or_paired"
    assert p.get_current_discussion("review")["context"]["open_points"][0]["id"] == "q"


def test_edited_original_question_snapshot_cannot_prove_closure(closure_store):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？")
    apply(p, [change("add", "通知需要同步等待吗", "s1")])
    # Original question falls outside this new frozen packet. Its stored proof
    # must nevertheless match the current canonical revision/hash.
    p._conn.execute("UPDATE transcript_segments SET revision=2,evidence_hash='changed' WHERE segment_id='s1'")
    append(p, 2, "通知不用同步等待。")
    _, _, result = apply(p, [change("resolve", "通知不用同步等待", "s2")])
    assert result["state_projection"]["rejected_operations"][0]["rule"] == "question_evidence_unverifiable"
    assert p.get_current_discussion("review")["context"]["open_points"]


def test_frozen_current_revision_mutation_still_aborts_atomic_projection(closure_store):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？通知不用同步等待。")
    now = time.time_ns() // 1_000_000
    job = p.enqueue_discussion_state(meeting_id="review", now_ms=now, stable_ms=0, minimum_segments=1, minimum_characters=0)
    p._conn.execute("UPDATE transcript_segments SET revision=2,evidence_hash='changed' WHERE segment_id='s1'")
    with pytest.raises(IntelligenceEvidenceSuperseded):
        p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now,
            response={"state_changes": [change("add", "通知需要同步等待吗", "s1")], "topic_update": None,
                "paragraph_revisions": [], "follow_up": None, "input_context": job["request_context"]["input_context"]})


def test_same_batch_question_answer_before_new_topic_keeps_old_owner(closure_store):
    p = closure_store
    append(p, 1, "先讨论通知。")
    topic = {"operation": "add", "title": "通知", "summary": "讨论通知",
        "evidence_segment_ids": ["s1"], "evidence_quote": "讨论通知"}
    apply(p, [], topic=topic)
    old = p.get_current_discussion("review")
    append(p, 2, "通知需要同步等待吗？")
    append(p, 3, "通知不用同步等待。")
    append(p, 4, "现在讨论退款流程。")
    topic = {"operation": "add", "title": "退款", "summary": "讨论退款流程",
        "evidence_segment_ids": ["s4"], "evidence_quote": "讨论退款流程"}
    _, _, result = apply(p, [change("add", "通知需要同步等待吗", "s2"),
        change("resolve", "通知不用同步等待", "s3")], topic=topic)
    assert result["state_change_count"] == 2 and result["state_projection"]["rejected_operation_count"] == 0
    historical = next(item for item in p.list_agent_work_items("review", kind="discussion") if item["work_item_id"] == old["work_item_id"])
    assert historical["context"]["open_points"] == []
    assert historical["context"]["responded_points"][0]["id"] == "q"
    assert p.get_current_discussion("review")["context"]["responded_points"] == []


def test_bad_quote_still_fails_whole_structural_validation(closure_store):
    p = closure_store
    append(p, 1, "现在讨论通知时序。")
    with pytest.raises(IntelligenceProjectionError, match="evidence quote is not present"):
        apply(p, [change("resolve", "原文没有的回答", "s1")], topic={"operation": "add", "title": "通知",
            "summary": "通知时序", "evidence_segment_ids": ["s1"], "evidence_quote": "讨论通知"})
    assert not p._conn.execute("SELECT 1 FROM meeting_entities WHERE meeting_id='review'").fetchone()


def test_frozen_actual_asr_counterexample_does_not_close_unknown_question(closure_store):
    p = closure_store
    # Stage11.83 retained failure: entry narration predates the location
    # question. These are offline source excerpts, never scene prompt rules.
    append(p, 1, "一进门之后，我首先看到地上熟悉的六芒星，还有珠宝展示柜。")
    append(p, 2, "司机直接在路边停了，我看不到酒店的入口在哪儿？")
    result = apply(p, [change("resolve", "看不到酒店的入口在哪儿", "s2", item_id="hotel_entrance_location")],
        topic={"operation": "add", "title": "酒店测评", "summary": "讨论入住体验",
            "evidence_segment_ids": ["s2"], "evidence_quote": "司机直接在路边停了"})[2]
    assert result["state_projection"]["rejected_operations"][0]["rule"] == "question_not_open_or_paired"
    context = p.get_current_discussion("review")["context"]
    assert context["current_problem"] == "酒店测评" and context["responded_points"] == []


def test_answer_question_source_revision_invalidates_responded_proof(closure_store):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？")
    append(p, 2, "通知不用同步等待。")
    apply(p, [change("add", "通知需要同步等待吗", "s1"), change("resolve", "通知不用同步等待", "s2")])
    assert p.get_current_discussion("review")["context"]["responded_points"]
    with p._write_transaction():
        p._conn.execute("UPDATE transcript_segments SET revision=2,evidence_hash='changed' WHERE segment_id='s1'")
        p._observe_discussion_revision_locked("review", "s1", time.time_ns() // 1_000_000)
    context = p.get_current_discussion("review")["context"]
    assert context["responded_points"] == [] and context["phase"] == "needs_review"


def test_same_text_question_ids_are_not_substituted_by_content_dedup(closure_store):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？")
    append(p, 2, "通知不用同步等待。")
    apply(p, [change("add", "通知需要同步等待吗", "s1", item_id="q-a"),
        change("add", "通知需要同步等待吗", "s1", item_id="q-b"),
        change("resolve", "通知不用同步等待", "s2", item_id="q-b")])
    context = p.get_current_discussion("review")["context"]
    assert [point["id"] for point in context["open_points"]] == ["q-a"]
    assert [point["id"] for point in context["responded_points"]] == ["q-b"]
    rows = p._conn.execute("SELECT entity_id,status FROM meeting_entities WHERE kind='open_question' ORDER BY entity_id").fetchall()
    assert [(row["entity_id"], row["status"]) for row in rows] == [("q-a", "candidate"), ("q-b", "answered")]


def test_rejection_diagnostic_is_bounded_and_does_not_echo_claim_text(closure_store):
    p = closure_store
    append(p, 1, "通知可以异步。")
    changes = [change("resolve", "通知可以异步", "s1", item_id=f"q-{index}") for index in range(12)]
    for item in changes:
        item["content"] = "不应出现在诊断里的模型声称"
        item["resolution_proof"] = {"unsafe": "模型不能提供宿主证明"}
    projection = apply(p, changes)[2]["state_projection"]
    assert projection["rejected_operation_count"] == 12
    assert len(projection["rejected_operations"]) == 8 and projection["rejections_truncated"]
    assert "模型声称" not in json.dumps(projection, ensure_ascii=False)


def test_future_source_outside_frozen_packet_cannot_be_planted_as_question(closure_store):
    p = closure_store
    append(p, 1, "先讨论通知。")
    now = time.time_ns() // 1_000_000
    job = p.enqueue_discussion_state(meeting_id="review", now_ms=now, stable_ms=0, minimum_segments=1, minimum_characters=0)
    append(p, 2, "通知需要同步等待吗？")
    with pytest.raises(IntelligenceEvidenceSuperseded, match="outside frozen input"):
        p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now,
            response={"paragraph_revisions": [], "follow_up": None, "topic_update": None,
                "state_changes": [change("add", "通知需要同步等待吗", "s2")],
                "input_context": job["request_context"]["input_context"]})
    assert not p._conn.execute("SELECT 1 FROM meeting_entities WHERE meeting_id='review'").fetchone()


def test_quote_of_old_question_is_not_answer_and_does_not_discard_new_topic(closure_store):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？")
    apply(p, [change("add", "通知需要同步等待吗", "s1")], topic={"operation": "add", "title": "通知",
        "summary": "讨论通知等待", "evidence_segment_ids": ["s1"], "evidence_quote": "通知需要同步等待吗"})
    old = p.get_current_discussion("review")
    append(p, 2, "现在讨论退款流程。")
    _, _, result = apply(p, [change("resolve", "通知需要同步等待吗", "s1")],
        topic={"operation": "add", "title": "退款", "summary": "讨论退款流程",
            "evidence_segment_ids": ["s2"], "evidence_quote": "讨论退款流程"})
    assert result["state_change_count"] == 0
    assert result["state_projection"]["rejected_operations"][0]["rule"] == "answer_not_after_question"
    current = p.get_current_discussion("review")
    assert current["context"]["current_problem"] == "退款" and current["work_item_id"] != old["work_item_id"]
    historical = next(item for item in p.list_agent_work_items("review", kind="discussion") if item["work_item_id"] == old["work_item_id"])
    assert historical["context"]["open_points"][0]["id"] == "q" and historical["context"]["responded_points"] == []


@pytest.mark.parametrize("entity_was_reused", [False, True])
def test_legacy_question_quote_fallback_requires_same_persisted_text_and_sources(closure_store, entity_was_reused):
    p = closure_store
    append(p, 1, "通知需要同步等待吗？结果页面如何显示？")
    apply(p, [change("add", "通知需要同步等待吗", "s1")])
    # Simulate an old JSON point without the newly introduced quote field.
    row = p.get_current_discussion("review")
    context = row["context"]
    context["open_points"][0].pop("evidence_quote")
    p._conn.execute("UPDATE agent_work_items SET context_json=? WHERE work_item_id=?",
        (json.dumps(context, ensure_ascii=False), row["work_item_id"]))
    if entity_was_reused:
        p._conn.execute("UPDATE meeting_entities SET text='结果页面如何显示',evidence_json=? WHERE meeting_id='review' AND entity_id='q'",
            (json.dumps({"segment_ids": ["s1"], "quote": "结果页面如何显示"}, ensure_ascii=False),))
    append(p, 2, "通知不用同步等待。")
    projection = apply(p, [change("resolve", "通知不用同步等待", "s2")])[2]["state_projection"]
    if entity_was_reused:
        assert projection["rejected_operations"][0]["rule"] == "question_evidence_unverifiable"
        assert p.get_current_discussion("review")["context"]["open_points"]
    else:
        assert projection["rejected_operation_count"] == 0
        assert p.get_current_discussion("review")["context"]["responded_points"][0]["id"] == "q"
