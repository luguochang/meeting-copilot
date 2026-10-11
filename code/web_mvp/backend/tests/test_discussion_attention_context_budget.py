"""Native state projection must budget the newly validated attention atomically."""
from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest

from discussion_attention_fixtures import wire_packet
from test_discussion_state_background import append, state_app as state_app  # noqa: F401
from test_discussion_state_observability import state_response


def attention(ref, quote, *, long=False):
    observed = {"evidence_segment_ids": [ref], "evidence_quote": quote}
    return {"current_focus": {"observations": [observed], "relation": "same_topic",
        "object": copy.deepcopy(observed) if long else None,
        "setting": copy.deepcopy(observed) if long else None,
        "purpose": copy.deepcopy(observed) if long else None},
        "contribution_opportunity": {"status": "none", "intent": None,
            "reason": "本轮正在核对已经回答的保留历史条件。",
            "evidence_segment_ids": [], "evidence_quote": "", "opportunity_id": None}}


async def native(app, now, payload):
    p = app.state.v2_persistence
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")
    p._conn.execute("UPDATE jobs SET status='cancelled' WHERE kind='intelligence' AND status='pending'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now, cooldown_ms=0,
        stable_ms=0, minimum_segments=1, minimum_characters=1, tail_catchup_ms=0)
    assert queued is not None
    job = p.claim_next_job(worker_id="budget-state", lane="discussion_state", now_ms=now, lease_ms=30_000)
    assert job["id"] == queued["id"]
    calls = []

    def gateway(request):
        packet = wire_packet(json.loads(request.content))
        calls.append(packet)
        assert request.method == "POST"
        return state_response(json.dumps(payload, ensure_ascii=False))

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
        app.state.streaming_llm_client = client
        result = await app.state.v2_discussion_state_job_handler_impl(job)
    assert len(calls) == result["provider_attempt_count"] == 1
    assert result["repair_attempted"] is False and result["usage"]["total_tokens"] == 80
    assert p.complete_job(job_id=job["id"], worker_id="budget-state", now_ms=now + 1, output=result)
    return job, result, calls


def history(app, now):
    """Make real closure proofs and canonical sources, then fill a bounded cache."""
    p = app.state.v2_persistence
    for seq in range(1, 8):
        append(p, seq, f"第{seq}项是否保留历史？第{seq}项保留历史。" + "边界必须兼容历史访问。" * 30,
            now=now - 10_000 + seq)
    changes = []
    for seq in range(1, 4):
        common = {"type": "open_question", "item_id": f"q{seq}", "confidence": .9,
            "evidence_segment_ids": [f"s{seq}"]}
        changes.extend([{**common, "operation": "add", "content": f"第{seq}项是否保留历史？",
            "evidence_quote": f"第{seq}项是否保留历史？"},
            {**common, "operation": "resolve", "content": f"第{seq}项保留历史。",
             "evidence_quote": f"第{seq}项保留历史。"}])
    changes.append({"type": "open_question", "operation": "add", "item_id": "q4", "confidence": .9,
        "content": "需要持续核对保留历史访问边界。" * 4,
        "evidence_segment_ids": ["s4"], "evidence_quote": "第4项是否保留历史？"})
    first = {"paragraph_revisions": [], "topic_update": {"operation": "add",
        "title": "历史保留的边界", "summary": "核对各项保留条件。",
        "evidence_segment_ids": ["s1"], "evidence_quote": "第1项是否保留历史？"},
        "state_changes": changes, "follow_up": None,
        "current_attention": attention("s7", "第7项保留历史。")}
    asyncio.run(native(app, now - 1_000, first))
    row = p.get_current_discussion("review")
    context = row["context"]
    assert context["last_semantic_seq"] == 7
    assert len(context["responded_points"]) == 3
    assert all(point["resolution_proof"] for point in context["responded_points"])
    # The projection cache holds bounded summaries; full entities/proofs remain
    # authoritative. Long synthetic summaries model a developed discussion.
    for seq in range(1, 5):
        context.setdefault("constraints", []).append({"id": f"bound{seq}",
            "text": "历史访问边界需要保留并核对。" * 30,
            "source_refs": ["s4"], "entity_type": "decision", "kind": "decision_fact",
            "status": "confirmed", "is_current_solution": False,
            "evidence": [{"segment_id": "s4", "revision": 1, "evidence_hash": "h4"}]})
    # Exactly the observed118 budget makes the new attention larger than the
    # available margin. This changes only an isolated cache summary fixture.
    padding = 5875 - len(json.dumps(context, ensure_ascii=False))
    assert 0 <= padding <= sum(500 - len(item["text"]) for item in context["constraints"])
    for item in context["constraints"]:
        amount = min(padding, 500 - len(item["text"]))
        item["text"] += "界" * amount
        padding -= amount
    assert padding == 0
    assert len(json.dumps(context, ensure_ascii=False)) == 5875
    p._conn.execute("UPDATE agent_work_items SET context_json=? WHERE work_item_id=?",
        (json.dumps(context, ensure_ascii=False), row["work_item_id"]))
    return row["work_item_id"]


@pytest.mark.parametrize("add_fact", [False, True])
def test_native_larger_attention_compacts_history_without_losing_latest_state(state_app, monkeypatch, add_fact):
    app, now = state_app
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    p = app.state.v2_persistence
    history(app, now)
    quoted = "最新条件仍需要保留历史访问的边界。" * 17
    assert 125 < len(quoted) <= 300
    append(p, 8, "开始核对下一阶段的历史访问约束。", now=now - 500)
    append(p, 9, quoted, now=now - 400)
    before = p.list_transcript_segments("review", limit=1000)
    entities = [dict(row) for row in p._conn.execute("SELECT * FROM meeting_entities WHERE meeting_id='review'")]
    body = {"paragraph_revisions": [], "topic_update": None,
        "state_changes": [{"type": "decision", "operation": "add", "item_id": "new-bound",
            "content": "下一阶段仍保留历史访问。", "status": "confirmed", "confidence": .9,
            "evidence_segment_ids": ["s9"], "evidence_quote": quoted}] if add_fact else [],
        "follow_up": None, "current_attention": attention("s9", quoted, long=True)}
    _, result, packets = asyncio.run(native(app, now, body))
    context = p.get_current_discussion("review")["context"]
    saved = context["current_attention"]
    assert context["last_semantic_seq"] == saved["covered_seq"] == 9
    assert len(json.dumps(context, ensure_ascii=False)) <= 6000
    assert saved["current_focus"] == body["current_attention"]["current_focus"]
    assert saved["source_bindings"] == [{"segment_id": "s9", "revision": 1, "evidence_hash": "h9"}]
    assert p.list_transcript_segments("review", limit=1000) == before
    after = {row["entity_id"]: dict(row) for row in p._conn.execute("SELECT * FROM meeting_entities WHERE meeting_id='review'")}
    assert all(after[item["entity_id"]] == item for item in entities)
    assert result["applied"]["state_change_count"] == int(add_fact)
    assert packets[0]["state_task"]["pending_segment_ids"] == ["s8", "s9"]
    assert result["applied"]["coach_intervention"] is None


def test_native_protected_context_over_budget_still_rolls_back_every_projection(state_app, monkeypatch):
    app, now = state_app
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    p = app.state.v2_persistence
    work_item_id = history(app, now)
    context = p.get_current_discussion("review")["context"]
    for field in ("constraints", "open_points", "responded_points"):
        context[field] = []
    context["current_problem"] = "已经记录的历史问题。" * 80
    context["current_solution"] = "已经确认的历史方案。" * 80
    context["proposals"] = [{"say_this": "保留历史引用的既有建议。" * 60,
        "source_refs": ["s1"], "kind": "ai_proposal"} for _ in range(3)]
    assert len(context["current_problem"]) <= 1000 and len(context["current_solution"]) <= 1000
    assert len(json.dumps(context, ensure_ascii=False)) < 6000
    p._conn.execute("UPDATE agent_work_items SET context_json=? WHERE work_item_id=?",
        (json.dumps(context, ensure_ascii=False), work_item_id))
    quoted = "最新条件仍需要保留历史访问的边界。" * 17
    append(p, 8, "开始核对下一阶段的历史访问约束。", now=now - 500)
    append(p, 9, quoted, now=now - 400)
    before = p.get_current_discussion("review")
    events = p._conn.execute("SELECT COUNT(*) FROM meeting_events WHERE meeting_id='review'").fetchone()[0]
    body = {"paragraph_revisions": [], "topic_update": None, "state_changes": [],
        "follow_up": None, "current_attention": attention("s9", quoted, long=True)}
    with pytest.raises(ValueError, match="6000") as raised:
        asyncio.run(native(app, now, body))
    assert raised.value.safe_failure_output["failure_diagnostics"]["failure_stage"] == "host_projection"
    assert raised.value.safe_failure_output["usage"]["total_tokens"] == 80
    assert raised.value.safe_failure_output["provider_attempt_count"] == 1
    assert p.get_current_discussion("review") == before
    assert p._conn.execute("SELECT COUNT(*) FROM meeting_events WHERE meeting_id='review'").fetchone()[0] == events
