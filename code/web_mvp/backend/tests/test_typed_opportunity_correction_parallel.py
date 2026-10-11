"""Native typed contributions progress while unchanged canonical L2 is active."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor

OLD = "通知失败时如何处理补偿？"
LATEST = "补偿失败后应该怎么通知用户？"


class Pi:
    available = True

    def __init__(self, app):
        self.app = app
        self.calls = []
        self.active_leases = []

    async def evaluate(self, payload):
        self.calls.append(payload)
        self.active_leases.append(self.app.state.provider_lane_registry.active_deep_count)
        return {"action": "intervention", "current_need": {
            "status": "has_current_need", "focus_evidence": [{"segment_id": "s9", "quote": LATEST}],
            "reason": "最新交流明确要求澄清补偿失败后的通知条件。"},
            "intervention": {"event_type": "question_to_user", "title": "澄清补偿失败后的通知条件",
                "recommendation": "可以先确认补偿失败后如何通知用户，再明确通知失败时的补偿处理边界？",
                "reason": "最新交流正在询问补偿失败后的通知方式。", "urgency": "low", "confidence": .9,
                "evidence_segment_ids": ["s8", "s9"], "evidence_quote": OLD + "\n" + LATEST,
                "analysis_kind": "participation", "proposal_type": "professional_proposal",
                "supporting_facts": [{"text": OLD, "source_ref": "s8"}, {"text": LATEST, "source_ref": "s9"}],
                "assumptions": []},
            "metrics": {"elapsed_ms": 500, "turns": 1, "tool_calls": 1, "tool_names": ["submit_intervention"],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}}


@pytest.fixture
def typed_app(tmp_path, monkeypatch):
    from test_discussion_state_background import Provider, state_app
    from test_discussion_short_tail_catchup import append

    fixture = state_app.__wrapped__(tmp_path, monkeypatch)
    app, now = next(fixture)
    p = app.state.v2_persistence
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid", api_key="synthetic-test-key",
        model="synthetic-model", timeout_seconds=25, is_mock=True)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_kw: None)
    for key in ("MEETING_COPILOT_REALTIME_COACH_ENABLED", "MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED"):
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST", "0")
    for seq in range(1, 9):
        append(p, seq, OLD, now - 9000 + seq)
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now - 3000)
    assert queued is not None
    running = p.claim_next_job(worker_id="native-state", lane="discussion_state", now_ms=now - 3000, lease_ms=30_000)
    assert running["id"] == queued["id"]
    provider = Provider(content=json.dumps({"paragraph_revisions": [], "state_changes": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": "通知失败补偿边界", "summary": "核对通知失败后的补偿条件",
            "evidence_segment_ids": ["s8"], "evidence_quote": OLD}}, ensure_ascii=False))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kw: provider)
    output = asyncio.run(app.state.v2_discussion_state_job_handler_impl(running))
    assert p.complete_job(job_id=running["id"], worker_id="native-state", now_ms=now - 2900, output=output)
    assert output["provider_attempt_count"] == len(provider.calls) == 1
    attention = p.get_current_discussion("review")["context"]["current_attention"]
    assert attention["contract"] == "discussion_attention.v1" and attention["contribution_opportunity"]["status"] == "present"
    assert attention["covered_seq"] == 8
    app.state.pi_coach_runtime = Pi(app)
    # Scheduler stays enabled. The new text is not yet stable for another
    # State job; its latest need is assessed in the same eventual Pi call.
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "1")
    monkeypatch.setattr(app_module.time, "time_ns", lambda: now * 1_000_000)
    yield app, now
    fixture.close()


def add_correction(p, now, *, status="pending", seq=9):
    from test_discussion_short_tail_catchup import append

    append(p, seq, LATEST if seq == 9 else "还有另一个通知条件需要讨论。", now, correction=True)
    correction = next(job for job in p.list_jobs(meeting_id="review") if job["kind"] == "correction"
        and job["input_transcript_seq"] == seq)
    if status == "running":
        claimed = p.claim_next_job(worker_id="native-l2", lane="correction", now_ms=now, lease_ms=30_000)
        assert claimed["id"] == correction["id"]
    elif status == "retry_wait":
        p._conn.execute("UPDATE jobs SET status='retry_wait',next_attempt_at_ms=? WHERE id=?", (now + 1000, correction["id"]))
    return p.get_job(correction["id"])


def automatic_jobs(p):
    return [job for job in p.list_jobs(meeting_id="review") if job["trigger_type"] == "discussion_update"
        and job["request_context"].get("task_mode") != "discussion_state"]


def execute_automatic(app, now, queued):
    p = app.state.v2_persistence
    running = p.claim_next_job(worker_id="native-pi", lane="pi_deep", now_ms=now, lease_ms=30_000)
    assert running["id"] == queued["id"]
    executor = DurableJobExecutor(p, correction_handler=lambda _j: {}, suggestion_handler=lambda _j: {},
        additional_handlers={"pi_deep": app.state.v2_intelligence_job_handler_impl}, now_ms=lambda: now + 1)
    asyncio.run(executor._execute_claimed_job("pi_deep", "native-pi", running))
    return p.get_job(queued["id"])


@pytest.mark.parametrize("status", ["pending", "running", "retry_wait"])
def test_real_scheduler_native_executor_publishes_before_global_l2_settles(typed_app, status):
    app, now = typed_app
    p = app.state.v2_persistence
    correction = add_correction(p, now, status=status)
    canonical = p.list_transcript_segments("review", limit=1000)
    admitted = []
    for _ in range(3):
        asyncio.run(app.state.refresh_due_coach_items())
        admitted = automatic_jobs(p)
        if admitted:
            break
    assert len(admitted) == 1
    metadata = admitted[0]["request_context"]
    assert metadata["automatic_attention"] is True and metadata["current_need_contract"] == "participation_need.v1"
    assert metadata["current_need_basis"]["unassessed_segment_ids"] == ["s9"]
    completed = execute_automatic(app, now, admitted[0])
    assert completed["status"] == "succeeded"
    assert completed["output"]["applied"]["current_need"]["status"] == "has_current_need"
    assert completed["output"]["applied"]["coach_intervention"] is not None
    assert len(app.state.pi_coach_runtime.calls) == 1 and app.state.pi_coach_runtime.active_leases == [1]
    assert app.state.provider_lane_registry.active_deep_count == 0
    assert p.get_job(correction["id"])["status"] == status
    assert p.list_transcript_segments("review", limit=1000) == canonical
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 8
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert len(automatic_jobs(p)) == 1


@pytest.mark.parametrize("introduced_at", ["handler", "provider"])
def test_new_uncorrected_canonical_during_queue_or_provider_setup_does_not_block_typed_job(typed_app, monkeypatch, introduced_at):
    app, now = typed_app
    p = app.state.v2_persistence
    from test_discussion_short_tail_catchup import append

    append(p, 9, LATEST, now, correction=False)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    queued = automatic_jobs(p)[0]
    calls = []

    def commit_l2():
        if not calls:
            calls.append(add_correction(p, now + 1, seq=10))
        return True

    if introduced_at == "handler":
        commit_l2()
    else:
        monkeypatch.setattr(app_module, "get_degradation_controller", lambda: SimpleNamespace(can_call_llm=commit_l2))
    completed = execute_automatic(app, now, queued)
    assert completed["status"] == "succeeded"
    assert completed["output"]["applied"]["coach_intervention"] is not None
    assert len(app.state.pi_coach_runtime.calls) == 1 and len(calls) == 1
    assert p.get_job(calls[0]["id"])["status"] == "pending"
    assert completed["output"]["applied"]["current_need"]["focus_evidence"] == [{"segment_id": "s9", "quote": LATEST}]
