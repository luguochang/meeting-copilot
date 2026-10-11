"""Native scheduler opportunity priority, with durable and Provider fairness."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor
from test_discussion_state_background import Provider
from test_discussion_short_tail_catchup import append
from test_typed_opportunity_correction_parallel import (
    Pi, automatic_jobs, typed_app as shared_typed_app,
)
from discussion_attention_fixtures import state_response

LATEST = "补偿失败后应该怎么通知用户？"


class CurrentPi(Pi):
    def __init__(self, app, *, silent=False):
        super().__init__(app)
        self.silent = silent

    async def evaluate(self, payload):
        result = await super().evaluate(payload)
        result["current_need"]["focus_evidence"] = [{"segment_id": "s11", "quote": LATEST}]
        result["intervention"]["evidence_segment_ids"] = ["s8", "s11"]
        result["intervention"]["supporting_facts"][1]["source_ref"] = "s11"
        if self.silent:
            result.update(action="silent", intervention=None, decision_reason="本轮无需新增贡献。")
            result["current_need"]["status"] = "already_answered"
            result["metrics"]["tool_names"] = ["keep_silent"]
        return result


def state_jobs(p):
    return [job for job in p.list_jobs(meeting_id="review")
            if job["request_context"].get("task_mode") == "discussion_state"]


def append_due(p, now, *, start=9):
    for seq in range(start, start + 3):
        append(p, seq, LATEST, now - 9000 + seq)


@pytest.fixture
def due_app(tmp_path, monkeypatch):
    fixture = shared_typed_app.__wrapped__(tmp_path, monkeypatch)
    app, initial_now = next(fixture)
    # Ordinary State eligibility needs both three fresh formal segments and
    # the unchanged 60-second cooldown after the previous semantic read.
    now = initial_now + 61_000
    append_due(app.state.v2_persistence, now)
    monkeypatch.setattr(app_module.time, "time_ns", lambda: now * 1_000_000)
    app.state.pi_coach_runtime = CurrentPi(app)
    yield app, now
    fixture.close()


def executor(app, now):
    return DurableJobExecutor(app.state.v2_persistence, correction_handler=lambda _j: {},
        suggestion_handler=lambda _j: {}, additional_handlers={
            "pi_deep": app.state.v2_intelligence_job_handler_impl,
            "discussion_state": app.state.v2_discussion_state_job_handler_impl}, now_ms=lambda: now + 1)


def claim(app, now, lane):
    return app.state.v2_persistence.claim_next_job(worker_id=f"native-{lane}", lane=lane,
        now_ms=now, lease_ms=30_000)


async def execute(app, now, lane, running):
    await executor(app, now)._execute_claimed_job(lane, f"native-{lane}", running)
    return app.state.v2_persistence.get_job(running["id"])


@pytest.mark.parametrize("silent", [False, True])
@pytest.mark.parametrize("next_tick_before_pi", [False, True])
def test_ready_opportunity_gets_first_tick_and_native_pi_runs_before_next_state(
    due_app, monkeypatch, silent, next_tick_before_pi,
):
    app, now = due_app
    p = app.state.v2_persistence
    app.state.pi_coach_runtime = CurrentPi(app, silent=silent)
    canonical = copy.deepcopy(p.list_transcript_segments("review", limit=1000))
    provider = Provider(content=json.dumps({"paragraph_revisions": [], "state_changes": [],
        "topic_update": None, "follow_up": None}))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kw: provider)

    async def run():
        assert await app.state.refresh_due_coach_items() == 1
        jobs = automatic_jobs(p)
        assert len(jobs) == 1, "ready opportunity must precede an otherwise due new State"
        assert len(state_jobs(p)) == 1
        assert jobs[0]["request_context"]["current_need_basis"]["unassessed_segment_ids"] == ["s10", "s11"]
        state_task = None
        if next_tick_before_pi:
            # Existing deep reservations must still prevent a new background
            # State from entering its Provider even if it is queued next tick.
            assert await app.state.refresh_due_coach_items() == 1
            assert len(automatic_jobs(p)) == 1 and len(state_jobs(p)) == 2
            state = claim(app, now, "discussion_state")
            state_task = asyncio.create_task(execute(app, now, "discussion_state", state))
            await asyncio.sleep(0.06)
            assert provider.calls == []
            assert not state_task.done()
        pi = claim(app, now, "pi_deep")
        completed = await execute(app, now, "pi_deep", pi)
        assert completed["status"] == "succeeded"
        applied = completed["output"]["applied"]
        assert applied["current_need"]["status"] == ("already_answered" if silent else "has_current_need")
        assert (applied["coach_intervention"] is None) is silent
        assert len(app.state.pi_coach_runtime.calls) == 1
        assert app.state.pi_coach_runtime.active_leases == [1]
        if state_task is None:
            assert await app.state.refresh_due_coach_items() == 1
            state = claim(app, now, "discussion_state")
            state_result = await execute(app, now, "discussion_state", state)
        else:
            state_result = await asyncio.wait_for(state_task, 2)
        assert state_result["status"] == "succeeded"
        assert state_result["output"]["provider_attempt_count"] == len(provider.calls) == 1
        assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 11
        assert app.state.provider_lane_registry.active_deep_count == 0
        assert await app.state.refresh_due_coach_items() == 0
        assert len(automatic_jobs(p)) == 1

    asyncio.run(run())
    assert p.list_transcript_segments("review", limit=1000) == canonical


@pytest.mark.parametrize("case", ["automatic_off", "policy_off", "nonpresent_none", "nonpresent_uncertain", "revised", "expired"])
def test_unadmitted_opportunity_keeps_native_state_catchup(due_app, monkeypatch, case):
    app, now = due_app
    p = app.state.v2_persistence
    initial_count = len(state_jobs(p))
    if case == "automatic_off":
        monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "0")
    elif case == "policy_off":
        app.state.meeting_preparation_store.save("review", live_analysis_profile="participation",
            proactive_suggestion_policy="off", updated_at_ms=now)
    elif case.startswith("nonpresent_"):
        queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
        running = claim(app, now, "discussion_state")
        assert running["id"] == queued["id"]
        content = state_response(p, running, {"paragraph_revisions": [], "state_changes": [],
            "topic_update": None, "follow_up": None}, status=case.removeprefix("nonpresent_"))
        monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kw: Provider(content=json.dumps(content)))
        assert asyncio.run(execute(app, now, "discussion_state", running))["status"] == "succeeded"
        now += 61_000
        append_due(p, now, start=12)
        monkeypatch.setattr(app_module.time, "time_ns", lambda: now * 1_000_000)
        initial_count += 1
    elif case == "revised":
        assert p.commit_transcript_revision(meeting_id="review", segment_id="s8", expected_evidence_hash="h8",
            corrected_text="通知失败时暂时不做补偿。", revision_id="revision-8", now_ms=now)
        now += 2100
        monkeypatch.setattr(app_module.time, "time_ns", lambda: now * 1_000_000)
    elif case == "expired":
        prior = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
        assert prior is not None
        now = prior["deadline_at_ms"] + 1
        assert claim(app, now, "pi_deep") is None
        assert p.get_job(prior["id"])["error_class"] == "deadline_exceeded"
        monkeypatch.setattr(app_module.time, "time_ns", lambda: now * 1_000_000)
    previous_automatic = len(automatic_jobs(p))
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    assert len(state_jobs(p)) == initial_count + 1
    assert len(automatic_jobs(p)) == previous_automatic
    assert app.state.pi_coach_runtime.calls == []


@pytest.mark.parametrize("status", ["pending", "running"])
def test_existing_state_keeps_priority_and_is_not_cancelled(due_app, status):
    app, now = due_app
    p = app.state.v2_persistence
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
    assert queued is not None
    if status == "running":
        assert claim(app, now, "discussion_state")["id"] == queued["id"]
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert p.get_job(queued["id"])["status"] == status
    assert automatic_jobs(p) == []
    assert app.state.pi_coach_runtime.calls == []


def test_manual_work_remains_ahead_of_new_automatic_and_state(due_app):
    app, now = due_app
    p = app.state.v2_persistence
    manual = p.enqueue_user_coach_request(meeting_id="review", user_request="请帮我核对通知失败边界。",
        intent="freeform", idempotency_key="manual-priority", now_ms=now)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert p.get_job(manual["id"])["status"] == "pending"
    assert len(state_jobs(p)) == 1 and automatic_jobs(p) == []
    assert app.state.pi_coach_runtime.calls == []
