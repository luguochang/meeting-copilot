"""A legally published multiline quote keeps the same native opportunity."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    run_realtime_coach_via_pi,
)

TEXTS = ["这个方案是否允许删除历史？", "历史必须保留。", "这个操作会删历史吗？"]
MULTILINE = "\n".join([*TEXTS, TEXTS[1]])


@pytest.fixture
def store(tmp_path, monkeypatch):
    from test_discussion_attention_lifecycle import store as source_fixture
    from meeting_copilot_web_mvp import v2_persistence

    monkeypatch.setattr(v2_persistence.time, "time_ns", lambda: 23_000_000_000)
    yield from source_fixture.__wrapped__(tmp_path)


def state_then_pi(store, *, quote=MULTILINE, evidence_ids=None):
    from test_discussion_attention_lifecycle import attention
    from test_participation_current_need import Runtime, request

    state = store.enqueue_discussion_state(meeting_id="review", now_ms=5000)
    assert state is not None
    claimed = store.claim_next_job(worker_id="state", lane="discussion_state", now_ms=5000, lease_ms=30_000)
    assert claimed["id"] == state["id"]
    applied = store.apply_intelligence_response(meeting_id="review", job_id=state["id"], now_ms=5001,
        response={"paragraph_revisions": [], "state_changes": [], "topic_update": None, "follow_up": None,
            "current_attention": attention()})
    assert store.complete_job(job_id=state["id"], worker_id="state", now_ms=5002, output={"applied": applied})
    first = applied["current_attention"]
    job = store.enqueue_discussion_update(meeting_id="review", now_ms=10_000)
    assert job is not None
    claimed = store.claim_next_job(worker_id="pi", lane="pi_deep", now_ms=10_000, lease_ms=30_000)
    assert claimed["id"] == job["id"]
    intervention = {"event_type": "question_to_user", "title": "确认历史保留约束", "urgency": "low",
        "recommendation": "可以先明确历史必须保留这个约束，再确认当前操作是否删除历史，然后决定是否执行？",
        "reason": "历史必须保留，当前操作是否删除历史尚需确认。",
        "evidence_segment_ids": ["s1", "s2", "s3"] if evidence_ids is None else evidence_ids,
        "evidence_quote": quote, "confidence": .9,
        "supporting_facts": [{"text": text, "source_ref": f"s{index}"} for index, text in enumerate(TEXTS, 1)],
        "assumptions": [], "analysis_kind": "participation", "proposal_type": "professional_proposal"}
    assessment = {"status": "has_current_need", "focus_evidence": [{"segment_id": "s3", "quote": TEXTS[2]}],
        "reason": "最新明确问题仍需要澄清历史保留条件。"}
    runtime = Runtime({"action": "intervention", "intervention": intervention, "current_need": assessment,
        "metrics": {"elapsed_ms": 500, "turns": 1, "tool_calls": 1,
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}})
    usage = []
    result = asyncio.run(run_realtime_coach_via_pi(request=request(store, job), pi_runtime=runtime,
        provider_config={"model": "fixture", "timeout_seconds": 25}, priority_mode="deep",
        on_usage=lambda charge, attempt: usage.append((attempt, charge))))
    assert len(runtime.calls) == len(usage) == 1
    assert result["intervention"].evidence_quote == quote
    card = result["intervention"].to_dict()
    projection = {"paragraph_revisions": [], "state_changes": [], "topic_update": None, "follow_up": None,
        "input_context": job["request_context"]["input_context"], "current_need": result["current_need"],
        "coach_decision": {"decision_id": "card:original", "run_id": "run:original", "runtime_used": "pi",
            "status": "intervention", "lifecycle_action": "retain"}, "coach_intervention": card}
    applied = store.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=projection, now_ms=10_001)
    assert applied["coach_intervention"]["evidence_quote"] == quote
    assert store.complete_job(job_id=job["id"], worker_id="pi", now_ms=10_002, output={"applied": applied})
    return job, first


def test_native_multiline_state_pi_state_preserves_original_active_card(store):
    from test_discussion_continuing_card import active_card, continuation, original_binding, original_history, retract_events

    old, first = state_then_pi(store)
    frozen = original_binding(store)
    request_before = copy.deepcopy(store.get_job(old["id"])["request_context"])
    state, response = continuation(store, first)
    applied = store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert applied["current_attention"]["opportunity_id"] == first["opportunity_id"]
    assert original_binding(store) == frozen
    assert active_card(store)["decision_id"] == "card:original"
    assert original_history(store)["lifecycle_action"] == "retain"
    assert not retract_events(store)
    assert store.get_job(old["id"])["request_context"] == request_before
    assert store.discussion_request_state("review", request_before) == "basis_superseded"
    assert json.loads(frozen["payload_json"])["analysis_basis_revision"] != store.get_current_discussion("review")["context"]["analysis_basis_revision"]


@pytest.mark.parametrize("quote,ids", [
    ("".join(TEXTS), None),
    (MULTILINE.replace("历史必须保留。", "历史可以删除。"), None),
    (MULTILINE, ["s1", "s2", "missing-source"]),
])
def test_invalid_multiline_pi_quote_is_rejected_before_any_card_publication(store, quote, ids):
    with pytest.raises(IntelligenceResponseValidationError):
        state_then_pi(store, quote=quote, evidence_ids=ids)
    assert store._conn.execute("SELECT COUNT(*) FROM coach_source_bindings").fetchone()[0] == 0
    assert store._latest_active_coach_intervention_decision_locked("review", excluding_decision_id="") is None


def test_missing_original_source_cannot_gain_multiline_retention(store):
    from test_discussion_continuing_card import continuation, original_binding, retract_events

    _, first = state_then_pi(store)
    # An isolated missing canonical source must not be replaced by the other
    # still-valid lines or the current opportunity's source.
    store._conn.execute("DELETE FROM transcript_segments WHERE segment_id='s1'")
    state, response = continuation(store, first)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] == 22_001
    assert retract_events(store)


def test_actual_revision_of_one_multiline_source_still_retracts(store):
    from test_discussion_continuing_card import original_binding, retract_events

    state_then_pi(store)
    assert store.commit_transcript_revision(meeting_id="review", segment_id="s2", expected_evidence_hash="h2",
        corrected_text="历史可以在审批后删除。", revision_id="changed2", now_ms=11_000)
    assert original_binding(store)["invalidated_at_ms"] == 11_000
    assert retract_events(store)


@pytest.mark.parametrize("quote", ["".join(TEXTS), MULTILINE.replace("历史必须保留。", "历史可以删除。")])
def test_corrupted_original_multiline_quote_fails_continuity_recheck(store, quote):
    from test_discussion_continuing_card import continuation, original_binding, retract_events

    old, first = state_then_pi(store)
    key = "meeting.intelligence.applied:" + old["id"]
    event = store._conn.execute("SELECT payload_json FROM meeting_events WHERE idempotency_key=?", (key,)).fetchone()
    payload = json.loads(event[0])
    # Fault injection only in the isolated test database: continuity cannot
    # trust the publication marker without rechecking the stored exact quote.
    payload["coach_intervention"]["evidence_quote"] = quote
    store._conn.execute("UPDATE meeting_events SET payload_json=? WHERE idempotency_key=?", (json.dumps(payload), key))
    state, response = continuation(store, first)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] == 22_001
    assert retract_events(store)
