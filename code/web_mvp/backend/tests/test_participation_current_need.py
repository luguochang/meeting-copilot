"""An automatic contribution must assess the latest frozen exchange in one Pi call."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    RealtimeIntelligenceRequest,
    run_realtime_coach_via_pi,
)
from meeting_copilot_web_mvp.v2_persistence import (
    IntelligenceEvidenceSuperseded,
    IntelligenceProjectionError,
    V2Persistence,
)

CONTRACT = "participation_need.v1"
OLD = "这款双人餐包含照烧鸡和牛肉。"
LATEST = "来三百六十度原地转转，我看什么向圈转。"


def append(store, seq, text, *, now=10000):
    store.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
        text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
        evidence_hash=f"h{seq}", now_ms=now - 9000 + seq)
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='correction'")


def seed(value, *, now=10000):
    for seq in range(1, 9):
        append(value, seq, OLD if seq == 8 else "正在讨论套餐的选择和展示。", now=now)
    with value._write_transaction():
        value._ensure_discussion_locked("review", now_ms=now - 8000)
        value._mark_discussion_semantic_checked_locked("review", 8, now - 8000)
        metadata = value._freeze_participation_locked(meeting_id="review", now_ms=now - 7000,
            intent="freeform", user_request="", topic_id=None, expected_basis_revision=None,
            selected_segment_ids=(), source_evidence=())
        metadata.update(attention_contract="discussion_attention.v1", attention_focus_segment_ids=["s7", "s8"])
        value._apply_current_attention_locked(meeting_id="review", metadata=metadata, now_ms=now - 6000,
            excluding_job_id=None, attention={"current_focus": {
                "observations": [{"evidence_segment_ids": ["s8"], "evidence_quote": OLD}],
                "relation": "same_topic", "object": None, "setting": None, "purpose": None},
                "contribution_opportunity": {"status": "present", "intent": "ask_question",
                    "reason": "套餐的可替换条件需要进一步确认。", "evidence_segment_ids": ["s8"],
                    "evidence_quote": OLD, "opportunity_id": None}})
    append(value, 9, LATEST, now=now)


@pytest.fixture
def store(tmp_path):
    value = V2Persistence(tmp_path / "need.db", semantic_projection_mode="llm_first")
    value.create_meeting(meeting_id="review", title=None, now_ms=0)
    seed(value)
    yield value
    value.close()


def queued(store):
    job = store.enqueue_discussion_update(meeting_id="review", now_ms=10000)
    assert job is not None
    return job


def need(status="has_current_need", *, source="s9", quote=LATEST):
    return {"status": status, "focus_evidence": [{"segment_id": source, "quote": quote}],
        "reason": "最新原话说明本次机会是否仍需要回应。"}


def intervention():
    return {"event_type": "question_to_user", "title": "明确套餐替换条件", "urgency": "low",
        "recommendation": "可以先明确套餐菜品能否替换及替换条件，再决定怎么展示选择，避免承诺不确定的搭配？",
        "reason": "菜品搭配影响选择，需要核对替换条件。", "evidence_segment_ids": ["s8"],
        "evidence_quote": OLD, "confidence": .9, "supporting_facts": [{"text": OLD, "source_ref": "s8"}],
        "assumptions": [], "analysis_kind": "participation", "proposal_type": "professional_proposal"}


def projection(job, assessment, *, silent=False):
    result = {"paragraph_revisions": [], "state_changes": [], "topic_update": None, "follow_up": None,
        "input_context": job["request_context"]["input_context"],
        "coach_decision": {"decision_id": "need:decision", "run_id": "need:run", "runtime_used": "pi",
            "status": "protected_silent" if silent else "intervention"},
        "coach_intervention": None if silent else intervention()}
    if assessment is not None:
        result["current_need"] = assessment
    return result


def request(store, job):
    rows = store._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id='review' ORDER BY transcript_seq").fetchall()
    ids = {item["segment_id"] for item in job["request_context"]["evidence"]}
    paragraphs = [{"id": row["segment_id"], "text": row["normalized_text"] or row["text"],
        "revision": row["revision"], "start_ms": row["started_at_ms"], "end_ms": row["ended_at_ms"],
        "correction_status": "no_change"} for row in rows if row["segment_id"] in ids]
    return RealtimeIntelligenceRequest.from_payload(meeting_id="review", state_revision=1,
        coach_profile="participation", trigger_type=job["trigger_type"], user_request=job["user_request"],
        request_metadata=job["request_context"], new_paragraphs=[], context_paragraphs=paragraphs[-3:],
        retrieval_paragraphs=paragraphs[:-3],
        rolling_state={"discussion": job["request_context"]["discussion_context"]})


class Runtime:
    available = True
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def evaluate(self, payload):
        self.calls.append(payload)
        return copy.deepcopy(self.result)


def run(store, job, assessment, *, silent=False):
    value = {"action": "silent" if silent else "intervention", "intervention": None if silent else intervention(),
        "decision_reason": "已离开原机会。" if silent else None,
        "metrics": {"elapsed_ms": 500, "turns": 1, "tool_calls": 1,
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}}
    if assessment is not None:
        value["current_need"] = assessment
    runtime, usage = Runtime(value), []
    task = run_realtime_coach_via_pi(request=request(store, job), pi_runtime=runtime,
        provider_config={"model": "fixture", "timeout_seconds": 25}, priority_mode="deep",
        on_usage=lambda charge, attempt: usage.append((attempt, charge)))
    return task, runtime, usage


def test_automatic_packet_freezes_coverage_gap_without_blocking_latest_input(store):
    metadata = queued(store)["request_context"]
    assert metadata["current_attention"]["covered_seq"] == 8
    assert metadata["anchor_seq"] == 9
    assert metadata["current_need_contract"] == CONTRACT
    assert metadata["current_need_basis"] == {"attention_covered_seq": 8, "packet_anchor_seq": 9,
        "focus_segment_ids": ["s8", "s9"], "unassessed_segment_ids": ["s9"]}


@pytest.mark.parametrize("status", ["already_answered", "moved_on", "uncertain"])
def test_latest_exchange_cannot_validate_an_old_opportunity_intervention(store, status):
    job = queued(store)
    with pytest.raises(IntelligenceProjectionError, match="current need"):
        store.apply_intelligence_response(meeting_id="review", job_id=job["id"],
            response=projection(job, need(status)), now_ms=10010)
    assert store._conn.execute("SELECT COUNT(*) FROM meeting_events WHERE type='meeting.intelligence.applied'").fetchone()[0] == 0


@pytest.mark.parametrize("assessment", [None, need(source="s8", quote=OLD), need(source="s9", quote=OLD),
    {**need(), "status": "fabricated"}, {**need(), "extra": "unbounded"}])
def test_paid_invalid_assessment_is_rejected_once_after_usage(store, assessment):
    task, runtime, usage = run(store, queued(store), assessment)
    with pytest.raises(IntelligenceResponseValidationError, match="current need"):
        asyncio.run(task)
    assert len(runtime.calls) == 1
    assert usage == [(1, {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})]


def test_sdk_paid_terminal_rejection_preserves_reported_usage_without_repair(store):
    from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachRuntimeError

    calls, charges = [], []

    class RejectedRuntime:
        async def evaluate(self, payload):
            calls.append(payload)
            raise PiCoachRuntimeError("current need terminal rejected", code="invalid_agent_action",
                metrics={"turns": 1, "tool_calls": 1, "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    with pytest.raises(PiCoachRuntimeError, match="current need"):
        asyncio.run(run_realtime_coach_via_pi(request=request(store, queued(store)),
            pi_runtime=RejectedRuntime(), provider_config={"model": "fixture", "timeout_seconds": 25},
            on_usage=lambda charge, attempt: charges.append((attempt, charge))))
    assert len(calls) == 1
    assert charges == [(1, {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})]


@pytest.mark.parametrize("latest", ["那如果套餐里有人不吃牛肉，该怎么处理？", "没有。", "这个替换操作会删掉之前的选择吗？"])
def test_same_opportunity_can_contribute_using_earlier_long_evidence_without_extra_call(store, latest):
    store._conn.execute("UPDATE transcript_segments SET text=?,normalized_text=? WHERE segment_id='s9'", (latest, latest))
    job = queued(store)
    task, runtime, usage = run(store, job, need(quote=latest))
    result = asyncio.run(task)
    assert result["intervention"].evidence_segment_ids == ("s8",)
    assert result["current_need"] == need(quote=latest)
    assert len(runtime.calls) == len(usage) == 1
    assert runtime.calls[0]["context"]["request_metadata"]["current_need_basis"]["packet_anchor_seq"] == 9
    assert store.get_current_discussion("review")["context"]["last_semantic_seq"] == 8


@pytest.mark.parametrize("status", ["already_answered", "moved_on", "uncertain", "has_current_need"])
def test_no_incremental_value_can_still_be_silent_with_latest_assessment(store, status):
    job = queued(store)
    task, runtime, usage = run(store, job, need(status), silent=True)
    result = asyncio.run(task)
    assert result["intervention"] is None
    assert result["current_need"] == need(status)
    applied = store.apply_intelligence_response(meeting_id="review", job_id=job["id"],
        response=projection(job, result["current_need"], silent=True), now_ms=10010)
    assert applied["current_need"] == result["current_need"]
    assert len(runtime.calls) == len(usage) == 1


def test_continuous_append_does_not_starve_a_source_valid_current_opportunity(store):
    job = queued(store)
    append(store, 10, "这个套餐的替换条件还需要确认，继续讨论。")
    applied = store.apply_intelligence_response(meeting_id="review", job_id=job["id"],
        response=projection(job, need()), now_ms=10010)
    assert applied["coach_intervention"]["evidence_segment_ids"] == ["s8"]
    assert applied["current_need"] == need()
    assert store.enqueue_discussion_update(meeting_id="review", now_ms=50000, cooldown_ms=0) is None
    repeated = store.apply_intelligence_response(meeting_id="review", job_id=job["id"],
        response=projection(job, need()), now_ms=10011)
    assert repeated["idempotent"] is True and repeated["current_need"] == need()


def test_frozen_latest_assessment_revision_cannot_publish_old_result(store):
    job = queued(store)
    store.commit_transcript_revision(meeting_id="review", segment_id="s9", expected_evidence_hash="h9",
        corrected_text="当前问题已经回应，开始别的话题。", revision_id="revised9", now_ms=10005)
    with pytest.raises(IntelligenceEvidenceSuperseded):
        store.apply_intelligence_response(meeting_id="review", job_id=job["id"],
            response=projection(job, need()), now_ms=10010)


def test_manual_historical_request_remains_compatible_without_new_assessment(store):
    job = store.enqueue_user_coach_request(meeting_id="review", user_request="请回看套餐问题。", intent="freeform",
        idempotency_key="historical", now_ms=10000)
    assert "current_need_contract" not in job["request_context"]
    task, runtime, usage = run(store, job, None)
    result = asyncio.run(task)
    assert result["intervention"] is not None
    assert "current_need" not in result
    assert len(runtime.calls) == len(usage) == 1


def test_historical_queued_automatic_packet_without_new_contract_is_compatible(store):
    job = queued(store)
    for key in ("current_need_contract", "current_need_basis"):
        job["request_context"].pop(key, None)
    store._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?", (json.dumps(job["request_context"]), job["id"]))
    applied = store.apply_intelligence_response(meeting_id="review", job_id=job["id"],
        response=projection(job, None), now_ms=10010)
    assert applied["coach_intervention"] is not None
    assert "current_need" not in applied


@pytest.fixture
def native_app(tmp_path, monkeypatch):
    from test_discussion_state_background import state_app
    yield from state_app.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("silent", [False, True])
def test_actual_app_pi_chain_preserves_need_assessment_to_transaction(native_app, monkeypatch, silent):
    from meeting_copilot_web_mvp import app as app_module

    app, now = native_app
    p = app.state.v2_persistence
    seed(p, now=now)
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='intelligence'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    queued_job = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    job = p.claim_next_job(worker_id="automatic", lane="pi_deep", now_ms=now, lease_ms=30000)
    assert job["id"] == queued_job["id"]
    expected = need("moved_on" if silent else "has_current_need")
    runtime = Runtime({"action": "silent" if silent else "intervention",
        "intervention": None if silent else intervention(), "current_need": expected,
        "decision_reason": "最新交流已离开该机会。" if silent else None,
        "metrics": {"turns": 1, "tool_calls": 1, "tool_names": ["keep_silent" if silent else "submit_intervention"],
            "elapsed_ms": 500, "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}})
    app.state.pi_coach_runtime = runtime
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_k: None)
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1
    assert output["applied"]["current_need"] == expected
    assert (output["applied"]["coach_intervention"] is None) is silent
    actual_packet = runtime.calls[0]["context"]
    assert actual_packet["request_metadata"]["current_need_basis"]["unassessed_segment_ids"] == ["s9"]
    assert {item["id"] for item in actual_packet["context_paragraphs"]} == {"s7", "s8", "s9"}
    event = p._conn.execute("SELECT payload_json FROM meeting_events WHERE type='meeting.intelligence.applied' AND aggregate_id=?", (job["id"],)).fetchone()
    assert json.loads(event["payload_json"])["current_need"] == expected
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 8
