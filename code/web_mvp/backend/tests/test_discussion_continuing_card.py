"""A validated continuing opportunity does not rewrite an existing card's history."""
from __future__ import annotations

import copy
import json

import pytest

from meeting_copilot_web_mvp.realtime_intelligence import IntelligenceResponseValidationError


QUOTE = "这个操作会删历史吗？"
NEW = "历史数据恢复需要管理员审批。"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from test_discussion_attention_lifecycle import store as source_fixture
    from meeting_copilot_web_mvp import v2_persistence

    monkeypatch.setattr(v2_persistence.time, "time_ns", lambda: 23_000_000_000)
    yield from source_fixture.__wrapped__(tmp_path)


def initial_attention(store):
    from test_discussion_attention_lifecycle import apply, attention

    return apply(store, attention())


def publish(store, *, manual=False, legacy=False, valid_until_ms=None):
    first = initial_attention(store)
    if manual:
        job = store.enqueue_user_coach_request(meeting_id="review", user_request="回看历史删除风险。",
            intent="freeform", idempotency_key="historical", now_ms=10_000)
    else:
        job = store.enqueue_discussion_update(meeting_id="review", now_ms=10_000)
    assert job is not None
    if legacy:
        metadata = copy.deepcopy(job["request_context"])
        metadata.pop("current_need_contract")
        metadata.pop("current_need_basis")
        store._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?",
            (json.dumps(metadata), job["id"]))
        job = store.get_job(job["id"])
    claimed = store.claim_next_job(worker_id="pi", lane="pi_deep", now_ms=10_000, lease_ms=30_000)
    assert claimed["id"] == job["id"]
    response = {"paragraph_revisions": [], "state_changes": [], "topic_update": None, "follow_up": None,
        "input_context": job["request_context"]["input_context"],
        "coach_decision": {"decision_id": "card:original", "run_id": "run:original", "runtime_used": "pi", "status": "intervention", "lifecycle_action": "retain"},
        "coach_intervention": {"event_type": "question_to_user", "title": "先确认历史保留边界", "urgency": "low",
            "recommendation": "可以先确认这个操作是否删除历史，以及恢复条件，再决定操作方案。",
            "reason": "历史保留条件尚未明确。", "evidence_segment_ids": ["s3"], "evidence_quote": QUOTE,
            "confidence": .9, "supporting_facts": [{"text": QUOTE, "source_ref": "s3"}], "assumptions": [],
            "analysis_kind": "participation", "proposal_type": "professional_proposal"}}
    if valid_until_ms is not None:
        response["coach_intervention"]["valid_until_ms"] = valid_until_ms
        response["coach_decision"]["valid_until_ms"] = valid_until_ms
    if not manual and not legacy:
        response["current_need"] = {"status": "has_current_need", "focus_evidence": [{"segment_id": "s3", "quote": QUOTE}],
            "reason": "最新明确问题仍需要澄清保留条件。"}
    applied = store.apply_intelligence_response(meeting_id="review", job_id=job["id"], response=response, now_ms=10_001)
    assert applied["coach_intervention"] is not None
    assert store.complete_job(job_id=job["id"], worker_id="pi", now_ms=10_002, output={"applied": applied})
    return job, first


def continuation(store, original, *, relation="same_topic", status="present", previous=True):
    store.commit_final_and_enqueue(meeting_id="review", final_id="f4", segment_id="s4", text=NEW,
        normalized_text=NEW, started_at_ms=4000, ended_at_ms=4800, evidence_hash="h4", now_ms=11_000)
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=22_000)
    assert job is not None
    value = {"current_focus": {"observations": [{"evidence_segment_ids": ["s4"], "evidence_quote": NEW}],
        "relation": relation, "object": None, "setting": None, "purpose": None},
        "contribution_opportunity": {"status": status, "intent": "ask_question" if status == "present" else None,
            "reason": "需要结合新条件继续澄清历史保留。" if status == "present" else "当前没有可靠贡献机会。",
            "evidence_segment_ids": ["s3"] if status == "present" else [],
            "evidence_quote": QUOTE if status == "present" else "",
            "opportunity_id": original["opportunity_id"] if previous and status == "present" else None}}
    response = {"paragraph_revisions": [], "state_changes": [{"type": "risk", "operation": "add",
        "item_id": "restore_approval", "content": "恢复需要管理员审批。", "status": "open", "confidence": .9,
        "evidence_segment_ids": ["s4"], "evidence_quote": NEW}], "topic_update": None, "follow_up": None,
        "current_attention": value}
    return job, response


def original_binding(store):
    return dict(store._conn.execute("SELECT * FROM coach_source_bindings WHERE decision_id='card:original'").fetchone())


def active_card(store):
    return store._latest_active_coach_intervention_decision_locked("review", excluding_decision_id="")


def original_history(store):
    from meeting_copilot_web_mvp.app import _bounded_formal_coach_history

    return next(row for row in _bounded_formal_coach_history(store.list_events("review", limit=1000), limit=1000)
        if row["decision_id"] == "card:original")


def retract_events(store):
    rows = store._conn.execute("SELECT payload_json FROM meeting_events WHERE type='meeting.intelligence.applied'").fetchall()
    return [json.loads(row[0]) for row in rows if (json.loads(row[0]).get("coach_decision") or {}).get("lifecycle_action") == "retract"]


def test_native_same_opportunity_new_fact_retains_original_card_without_rebinding(store):
    old, first = publish(store)
    frozen_job = store._conn.execute("SELECT request_context_json FROM jobs WHERE id=?", (old["id"],)).fetchone()[0]
    frozen_binding = original_binding(store)
    before_basis = store.get_current_discussion("review")["context"]["analysis_basis_revision"]
    state, response = continuation(store, first)
    result = store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert result["current_attention"]["opportunity_id"] == first["opportunity_id"]
    assert store.get_current_discussion("review")["context"]["analysis_basis_revision"] == before_basis + 1
    assert original_binding(store) == frozen_binding
    assert not retract_events(store)
    assert active_card(store)["decision_id"] == "card:original"
    assert original_history(store)["lifecycle_action"] == "retain"
    assert store._conn.execute("SELECT request_context_json FROM jobs WHERE id=?", (old["id"],)).fetchone()[0] == frozen_job
    assert store.discussion_request_state("review", old["request_context"]) == "basis_superseded"
    repeated = store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_002)
    assert repeated["idempotent"] is True
    assert original_binding(store) == frozen_binding
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=60_000, cooldown_ms=0) is None


@pytest.mark.parametrize("relation,status,previous", [
    ("changed", "present", True), ("interlude", "present", True),
    ("uncertain", "present", True), ("unknown", "present", True),
    ("same_topic", "none", False), ("same_topic", "uncertain", False),
    ("same_topic", "present", False),
])
def test_changed_unknown_or_not_explicitly_inherited_opportunity_still_retracts(store, relation, status, previous):
    _, first = publish(store)
    state, response = continuation(store, first, relation=relation, status=status, previous=previous)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] == 22_001
    assert retract_events(store)
    assert active_card(store) is None
    assert original_history(store)["lifecycle_status"] == "retracted"


@pytest.mark.parametrize("manual,legacy", [(True, False), (False, True)])
def test_historical_or_legacy_card_does_not_gain_modern_continuation_exemption(store, manual, legacy):
    _, first = publish(store, manual=manual, legacy=legacy)
    state, response = continuation(store, first)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] == 22_001
    assert retract_events(store)


def test_old_frozen_source_revision_change_prevents_retention_even_when_quote_source_is_current(store):
    _, first = publish(store)
    # Simulate an existing revision before the new state freezes its sources;
    # the original job's unrelated frozen support must not silently be rebound.
    store._conn.execute("UPDATE transcript_segments SET revision=revision+1,evidence_hash='new-h1' WHERE segment_id='s1'")
    state, response = continuation(store, first)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] == 22_001
    assert retract_events(store)


def test_invalid_new_attention_rolls_back_basis_and_binding_atomically(store):
    _, first = publish(store)
    state, response = continuation(store, first)
    before = store.get_current_discussion("review")
    binding = original_binding(store)
    response["current_attention"]["current_focus"]["observations"][0]["evidence_quote"] = "不存在的改写原话"
    with pytest.raises(IntelligenceResponseValidationError):
        store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert store.get_current_discussion("review") == before
    assert original_binding(store) == binding
    assert not retract_events(store)


def test_retained_card_still_retracts_on_actual_source_revision(store):
    _, first = publish(store)
    state, response = continuation(store, first)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] is None
    assert store.commit_transcript_revision(meeting_id="review", segment_id="s3", expected_evidence_hash="h3",
        corrected_text="这个操作不会删除历史。", revision_id="revised3", now_ms=23_000)
    assert original_binding(store)["invalidated_at_ms"] == 23_000
    assert active_card(store) is None
    assert original_history(store)["lifecycle_status"] == "retracted"


@pytest.mark.parametrize("status", ["pending", "running"])
def test_preservation_does_not_authorize_queued_or_running_old_basis_work(store, status):
    old, first = publish(store)
    state, response = continuation(store, first)
    row = dict(store._conn.execute("SELECT * FROM jobs WHERE id=?", (old["id"],)).fetchone())
    row.update(id="old-basis-in-flight", idempotency_key="old-basis-in-flight", status=status,
        completed_at_ms=None, output_json=None, lease_owner="another" if status == "running" else None)
    names = list(row)
    store._conn.execute("INSERT INTO jobs(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")",
        [row[name] for name in names])
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] is None
    assert store.get_job("old-basis-in-flight")["status"] == "cancelled"
    assert store.get_job("old-basis-in-flight")["error_class"] == "discussion_constraints_changed"
    assert store.discussion_request_state("review", old["request_context"]) == "basis_superseded"


def test_old_input_binding_must_still_match_before_retention(store):
    _, first = publish(store)
    old = original_binding(store)
    binding = json.loads(old["input_context_json"])
    binding["memory_fingerprint"] = "obsolete-memory-version"
    store._conn.execute("UPDATE coach_source_bindings SET input_context_json=? WHERE decision_id='card:original'",
        (json.dumps(binding),))
    state, response = continuation(store, first)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] == 22_001
    assert retract_events(store)


@pytest.mark.parametrize("valid_until_ms,retained", [(22_001, False), (30_000, True)])
def test_continuing_card_keeps_original_expiry_and_never_extends_it(store, valid_until_ms, retained):
    _, first = publish(store, valid_until_ms=valid_until_ms)
    frozen = original_binding(store)
    state, response = continuation(store, first)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    after = original_binding(store)
    assert after["payload_json"] == frozen["payload_json"]
    assert after["created_at_ms"] == frozen["created_at_ms"]
    assert json.loads(after["payload_json"])["valid_until_ms"] == valid_until_ms
    assert (after["invalidated_at_ms"] is None) is retained


@pytest.mark.parametrize("missing", ["current_need", "successful_job"])
def test_card_without_original_typed_success_proof_is_withdrawn(store, missing):
    old, first = publish(store)
    if missing == "current_need":
        row = store._conn.execute("SELECT payload_json FROM meeting_events WHERE idempotency_key=?",
            ("meeting.intelligence.applied:" + old["id"],)).fetchone()
        payload = json.loads(row[0])
        payload.pop("current_need")
        store._conn.execute("UPDATE meeting_events SET payload_json=? WHERE idempotency_key=?",
            (json.dumps(payload), "meeting.intelligence.applied:" + old["id"]))
    else:
        store._conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (old["id"],))
    state, response = continuation(store, first)
    store.apply_intelligence_response(meeting_id="review", job_id=state["id"], response=response, now_ms=22_001)
    assert original_binding(store)["invalidated_at_ms"] == 22_001
    assert retract_events(store)
    assert active_card(store) is None
