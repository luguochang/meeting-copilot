"""Current-batch old answers retain their owner across a later topic switch.

The original red probe is preserved as an immutable artifact. These tests use
public constructed speech and local transport; they make no natural-timer claim.
"""
from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest

from meeting_copilot_web_mvp.discussion_state_diagnostics import DiscussionProjectionRejection
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded, IntelligenceProjectionError
from discussion_attention_fixtures import state_response as typed_state_response, wire_packet, with_attention
from test_discussion_state_background import append, state_app as shared_state_app
from test_discussion_state_observability import queue_state, run_consumer, state_response, trace

state_app = shared_state_app
ANSWER = "通知不需要同步等待，结果页面显示权益发放状态。"
NEXT_TOPIC = "现在讨论退款流程，退款什么时候到账？"


def _responses():
    first = {"paragraph_revisions": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "通知等待", "summary": "讨论通知等待",
            "evidence_segment_ids": ["s1"], "evidence_quote": "通知是否需要同步等待"},
        "state_changes": [{"type": "open_question", "operation": "add", "item_id": item_id,
            "content": quote, "evidence_segment_ids": ["s1"], "evidence_quote": quote, "confidence": .9}
            for item_id, quote in (("q-notify", "通知是否需要同步等待"), ("q-page", "结果页面如何显示"))]}
    second = {"paragraph_revisions": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "退款流程", "summary": "讨论退款流程",
            "evidence_segment_ids": ["s5"], "evidence_quote": "现在讨论退款流程"},
        "state_changes": [
            {"type": "open_question", "operation": "resolve", "item_id": "q-notify",
                "content": "通知不需要同步等待", "evidence_segment_ids": ["s4"],
                "evidence_quote": "通知不需要同步等待", "confidence": .9},
            {"type": "open_question", "operation": "add", "item_id": "q-refund",
                "content": "退款什么时候到账", "evidence_segment_ids": ["s5"],
                "evidence_quote": "退款什么时候到账", "confidence": .9}]}
    return first, second


def _projection_state(p):
    return {table: [dict(row) for row in p._conn.execute(f"SELECT * FROM {table} WHERE meeting_id='review' ORDER BY rowid")]
        for table in ("agent_work_items", "agent_work_item_evidence", "meeting_entities", "meeting_events")}


async def _prepare_split(app, now):
    p = app.state.v2_persistence
    first, _ = _responses()
    calls = []

    def gateway(request):
        assert request.method == "POST"
        calls.append("POST")
        return state_response(with_attention(json.dumps(first, ensure_ascii=False), wire_packet(json.loads(request.content))))

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
        app.state.streaming_llm_client = client
        job1 = await queue_state(app, now, text="通知是否需要同步等待？结果页面如何显示？")
        assert (await run_consumer(app, job1))["status"] == "succeeded"
    assert calls == ["POST"]
    old = p.get_current_discussion("review")
    append(p, 4, ANSWER, now=now + 20)
    append(p, 5, NEXT_TOPIC, now=now + 21)
    p._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_other_lanes_disabled' WHERE status='pending'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    # Local scheduling isolation only; production timer/cooldown are unchanged.
    job2 = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 30,
        cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=0)
    assert job2 is not None
    return old, job2


def _prepare(state_app, monkeypatch):
    app, now = state_app
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    old, job = asyncio.run(_prepare_split(app, now))
    return app, now, old, job


def _claim(p, job, now):
    running = p.claim_next_job(worker_id="split-state", lane="discussion_state", now_ms=now + 30, lease_ms=5_000)
    assert running["id"] == job["id"]
    return running


def test_current_frozen_old_question_response_before_new_topic_should_resolve_on_old_owner(state_app, monkeypatch):
    app, now = state_app
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    p = app.state.v2_persistence
    _, response = _responses()
    calls = []

    async def run():
        old, job = await _prepare_split(app, now)
        def gateway(request):
            calls.append("POST")
            return state_response(with_attention(json.dumps(response, ensure_ascii=False), wire_packet(json.loads(request.content))))
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return old, job, await run_consumer(app, job)

    old, job, final = asyncio.run(run())
    assert calls == ["POST"] and final["status"] == "succeeded"
    assert final["attempts"] == final["max_attempts"] == 1
    metadata = job["request_context"]
    assert metadata["topic_id"] == old["work_item_id"]
    assert metadata["semantic_from_seq"] == 3 and metadata["anchor_seq"] == 5
    assert trace(app, job)["execution"]["provider_attempts"][0]["outcome"] == "success"
    historical = next(row for row in p.list_agent_work_items("review", kind="discussion")
        if row["work_item_id"] == old["work_item_id"])
    assert historical["state"] == "superseded" and historical["context"]["phase"] == "closed"
    assert [point["id"] for point in historical["context"]["open_points"]] == ["q-page"]
    answered = historical["context"]["responded_points"]
    assert len(answered) == 1 and answered[0]["id"] == "q-notify"
    assert answered[0]["source_refs"] == ["s4"]
    assert answered[0]["evidence"] == [{"segment_id": "s4", "revision": 1, "evidence_hash": "h4"}]
    current = p.get_current_discussion("review")
    assert current["work_item_id"] != old["work_item_id"]
    assert current["context"]["responded_points"] == []
    assert [point["id"] for point in current["context"]["open_points"]] == ["q-refund"]
    assert current["context"]["open_points"][0]["source_refs"] == ["s5"]
    assert current["context"]["last_semantic_seq"] == 5


@pytest.mark.parametrize("case", ["wrong_type", "update", "unknown_id", "old_history", "unquoted"])
def test_split_response_rejects_bad_closure_without_discarding_valid_topic(state_app, monkeypatch, case):
    app, now, old, job = _prepare(state_app, monkeypatch)
    p = app.state.v2_persistence
    _claim(p, job, now)
    _, response = _responses()
    response = typed_state_response(p, job, response)
    change = response["state_changes"][0]
    if case == "wrong_type":
        change["type"] = "risk"
    elif case == "update":
        change.update(operation="update", status="answered")
        # Cross-topic status aliases cannot replace explicit owner-bound resolve.
        change.update(content="退款什么时候到账", evidence_segment_ids=["s5"], evidence_quote="退款什么时候到账")
    elif case == "unknown_id":
        change["item_id"] = "q-unknown"
    elif case == "old_history":
        change.update(content="通知是否需要同步等待", evidence_segment_ids=["s1"], evidence_quote="通知是否需要同步等待")
    else:
        change["evidence_quote"] = "本场不存在的回应"
    before = _projection_state(p)
    if case in {"unknown_id", "old_history", "update"}:
        applied = p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now + 40, response=response)
        expected_rule = {"unknown_id": "question_not_open_or_paired", "old_history": "answer_not_after_question",
            "update": "cross_topic_requires_explicit_resolve"}[case]
        assert applied["state_projection"]["rejected_operations"][0]["rule"] == expected_rule
        assert applied["state_change_count"] == 1  # Valid new-topic question survives.
        assert p.get_current_discussion("review")["context"]["current_problem"] == "退款流程"
        historical = next(item for item in p.list_agent_work_items("review", kind="discussion") if item["work_item_id"] == old["work_item_id"])
        assert {point["id"] for point in historical["context"]["open_points"]} == {"q-notify", "q-page"}
        assert historical["context"]["responded_points"] == []
    else:
        with pytest.raises(IntelligenceProjectionError if case == "unquoted" else ValueError):
            p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now + 40, response=response)
        assert _projection_state(p) == before
        assert p.get_current_discussion("review")["work_item_id"] == old["work_item_id"]



@pytest.mark.parametrize("case", ["wrong_owner", "from_boundary", "not_frozen", "after_anchor", "revision", "hash", "legacy"])
def test_split_response_frozen_identity_and_window_cannot_be_bypassed(state_app, monkeypatch, case):
    app, now, old, job = _prepare(state_app, monkeypatch)
    p = app.state.v2_persistence
    _claim(p, job, now)
    metadata = copy.deepcopy(job["request_context"])
    _, response = _responses()
    response = typed_state_response(p, job, response)
    # Ownership/revision guards and the stricter frozen-source membership
    # guard fail before projection; original semantic-window guards stay intact.
    expected = (IntelligenceEvidenceSuperseded if case in {"wrong_owner", "not_frozen", "revision", "hash"}
        else DiscussionProjectionRejection)
    if case == "wrong_owner":
        metadata["topic_id"] = "discussion:another-owner"
        expected = IntelligenceEvidenceSuperseded
    elif case == "from_boundary":
        metadata["semantic_from_seq"] = 4
    elif case == "not_frozen":
        metadata["evidence"] = [item for item in metadata["evidence"] if item["segment_id"] != "s4"]
    elif case == "after_anchor":
        append(p, 6, ANSWER, now=now + 31)
        # Even a matching frozen revision/hash cannot expand the pending bound.
        metadata["evidence"].append({"segment_id": "s6", "revision": 1, "evidence_hash": "h6"})
        response["state_changes"][0]["evidence_segment_ids"] = ["s6"]
    elif case in {"revision", "hash"}:
        field, value = ("revision", 2) if case == "revision" else ("evidence_hash", "changed-h4")
        p._conn.execute(f"UPDATE transcript_segments SET {field}=? WHERE meeting_id='review' AND segment_id='s4'", (value,))
        expected = IntelligenceEvidenceSuperseded
    else:
        metadata = {}
    p._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?", (json.dumps(metadata), job["id"]))
    before = _projection_state(p)
    with pytest.raises(expected):
        p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now + 40, response=response)
    assert _projection_state(p) == before
    assert p.get_current_discussion("review")["work_item_id"] == old["work_item_id"]


def test_advancing_manual_checked_cursor_does_not_replace_frozen_semantic_window(state_app, monkeypatch):
    app, now, old, job = _prepare(state_app, monkeypatch)
    p = app.state.v2_persistence
    _claim(p, job, now)
    context = copy.deepcopy(old["context"])
    context["last_checked_seq"] = 5
    p._conn.execute("UPDATE agent_work_items SET context_json=? WHERE work_item_id=?", (json.dumps(context), old["work_item_id"]))
    _, response = _responses()
    response = typed_state_response(p, job, response)
    p.apply_intelligence_response(meeting_id="review", job_id=job["id"], now_ms=now + 40, response=response)
    historical = next(row for row in p.list_agent_work_items("review", kind="discussion") if row["work_item_id"] == old["work_item_id"])
    assert historical["context"]["responded_points"][0]["source_refs"] == ["s4"]
    assert p.get_current_discussion("review")["context"]["responded_points"] == []
