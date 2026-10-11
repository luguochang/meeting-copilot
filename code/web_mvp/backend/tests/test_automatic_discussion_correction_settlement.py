"""State-first attention admission and correction recovery; no external calls.

The named understood fixture explicitly selects the compatibility path. Direct
participation's independent L2/State admission is covered in its own tests.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.automatic_coach_policy import discussion_understanding_ready
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor
from meeting_copilot_web_mvp.v2_persistence import _public_job_error_message
from test_discussion_short_tail_catchup import append, seed_understood
from test_discussion_state_background import state_app as shared_state_app
from discussion_attention_fixtures import state_response

state_app = shared_state_app


class SilentPi:
    available = True

    def __init__(self):
        self.calls = []

    async def evaluate(self, payload):
        self.calls.append(payload)
        return {"action": "silent", "decision_reason": "No additional grounded proposal is needed.",
            "metrics": {"turns": 1, "tool_calls": 1, "tool_names": ["remain_silent"]}}


@pytest.fixture
def understood_app(state_app, monkeypatch):
    app, now = state_app
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_DIRECT_ENABLED", "0")
    seed_understood(app.state.v2_persistence, at=now - 3_000)
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid", api_key="synthetic-test-key",
        model="synthetic-model", timeout_seconds=25, is_mock=True)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_kw: None)
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST", "0")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    app.state.pi_coach_runtime = SilentPi()
    app.state.streaming_llm_client = object()
    yield app, now, config


def add_correction(p, at, *, status="pending"):
    append(p, 9, "另一个处理边界还需要确认。", at, correction=True)
    job = next(j for j in p.list_jobs(meeting_id="review") if j["kind"] == "correction")
    # Only arrange durable worker state; no Provider or live runtime mutation.
    if status != "pending":
        p._conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job["id"]))
    return p.get_job(job["id"])


def auto_jobs(p):
    return [j for j in p.list_jobs(meeting_id="review")
        if j["trigger_type"] == "discussion_update" and not j["request_context"].get("task_mode")]


def refresh_understanding(p, at):
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=at,
        cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=0)
    assert queued is not None
    claimed = p.claim_next_job(worker_id="new-state", lane="discussion_state", now_ms=at, lease_ms=30_000)
    assert claimed["id"] == queued["id"]
    p.apply_intelligence_response(meeting_id="review", job_id=claimed["id"], now_ms=at + 1,
        response=state_response(p, claimed, {"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None}))
    assert p.complete_job(job_id=claimed["id"], worker_id="new-state", now_ms=at + 2,
        output={"provider_attempt_count": 1})
    return claimed


@pytest.mark.parametrize("status", ["pending", "running", "retry_wait"])
def test_state_first_attention_admission_keeps_known_same_meeting_l2_independent(understood_app, status):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    correction = add_correction(p, now, status=status)
    discussion = p.get_current_discussion("review")
    assert discussion_understanding_ready(discussion, p.list_jobs(meeting_id="review"))
    legacy = {**discussion, "context": {**discussion["context"], "current_attention": None}}
    assert not discussion_understanding_ready(legacy, p.list_jobs(meeting_id="review"))
    queued = p.enqueue_discussion_update(meeting_id="review", now_ms=now + 1)
    assert queued is not None
    assert queued["request_context"]["current_need_contract"] == "participation_need.v1"
    assert p.get_job(correction["id"])["status"] == status
    assert len(auto_jobs(p)) == 1 and app.state.pi_coach_runtime.calls == []


@pytest.mark.parametrize("status", ["pending", "running", "retry_wait"])
def test_state_first_scheduler_admits_typed_attention_without_changing_l2_work(understood_app, monkeypatch, status):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    correction = add_correction(p, now, status=status)
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "0")
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    assert len(auto_jobs(p)) == 1
    assert auto_jobs(p)[0]["request_context"]["automatic_attention"] is True
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert p.get_job(correction["id"])["status"] == status


def test_enqueue_transaction_closes_scheduler_to_correction_race(understood_app, monkeypatch):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    original = app_module.automatic_discussion_admission

    def race(**kwargs):
        decision = original(**kwargs)
        add_correction(p, now)
        return decision

    monkeypatch.setattr(app_module, "automatic_discussion_admission", race)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    assert len(auto_jobs(p)) == 1
    assert auto_jobs(p)[0]["request_context"]["current_need_contract"] == "participation_need.v1"
    assert next(j for j in p.list_jobs(meeting_id="review") if j["kind"] == "correction")["status"] == "pending"


@pytest.mark.parametrize("status", ["pending", "running", "retry_wait"])
@pytest.mark.parametrize("contract", ["modern", "legacy"])
def test_already_queued_auto_defers_before_sdk_without_spending_attempt(understood_app, monkeypatch, status, contract):
    import json

    app, now, config = understood_app
    p = app.state.v2_persistence
    queued = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    if contract == "legacy":
        for key in ("automatic_attention", "current_need_contract", "current_need_basis"):
            queued["request_context"].pop(key)
        p._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?",
            (json.dumps(queued["request_context"]), queued["id"]))
    correction = add_correction(p, now + 1, status=status)
    running = p.claim_next_job(worker_id="auto", lane="pi_deep", now_ms=now + 2, lease_ms=30_000)
    if contract == "modern":
        monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: replace(config, is_mock=False)))
        monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
        monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")

        class AssessedSilentPi(SilentPi):
            async def evaluate(self, payload):
                result = await super().evaluate(payload)
                result["metrics"]["usage"] = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
                result["current_need"] = {"status": "has_current_need", "focus_evidence": [{"segment_id": "s8",
                    "quote": p._conn.execute("SELECT normalized_text FROM transcript_segments WHERE segment_id='s8'").fetchone()[0]}],
                    "reason": "冻结原话仍需核对，当前没有额外可贡献内容。"}
                return result

        app.state.pi_coach_runtime = AssessedSilentPi()
        output = asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
        assert output["coach"]["pi_provider_attempted"] is True
        assert output["applied"]["coach_intervention"] is None
        assert len(app.state.pi_coach_runtime.calls) == 1
        assert len(app.state.pipeline_traces.get(running["id"]).execution_snapshot()["provider_attempts"]) == 1
        usage = app.state.settings_usage_repository.list_usage(session_id="review")
        assert len(usage) == 1 and usage[0]["total_tokens"] == 30
        assert p.get_job(correction["id"])["status"] == status
        return
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
    assert getattr(raised.value, "preserve_attempt", False) is True
    executor = DurableJobExecutor(p, correction_handler=lambda _j: {}, suggestion_handler=lambda _j: {})
    asyncio.run(executor._retry_after_handler_failure(running, "auto", raised.value, resume_generation=0))
    deferred = p.get_job(queued["id"])
    assert deferred["status"] == "retry_wait" and deferred["attempts"] == 0
    assert deferred["error_class"] == "AutomaticCoachCorrectionDeferred"
    projected = next(j for j in TestClient(app).get("/v2/meetings/review/snapshot").json()["jobs"] if j["id"] == queued["id"])
    assert projected["error_class"] == "AutomaticCoachCorrectionDeferred"
    assert _public_job_error_message(projected["error_class"]) == "正在等待本场文字校对，完成后继续分析。"
    assert app.state.pi_coach_runtime.calls == []
    assert app.state.settings_usage_repository.list_usage(session_id="review") == []


@pytest.mark.parametrize("contract", ["modern", "legacy"])
def test_last_provider_guard_does_not_turn_local_wait_into_silent_success(understood_app, monkeypatch, contract):
    import json

    app, now, config = understood_app
    p = app.state.v2_persistence
    queued = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    if contract == "legacy":
        for key in ("automatic_attention", "current_need_contract", "current_need_basis"):
            queued["request_context"].pop(key)
        p._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?",
            (json.dumps(queued["request_context"]), queued["id"]))
    running = p.claim_next_job(worker_id="auto", lane="pi_deep", now_ms=now, lease_ms=30_000)
    entered = []

    def introduce_correction():
        if not entered:
            entered.append(True)
            add_correction(p, now + 1)
        return True

    # Budget admission runs immediately before before_coach_attempt records an
    # actual attempt. It represents another thread committing L2 during setup.
    monkeypatch.setattr(app_module, "get_degradation_controller", lambda: SimpleNamespace(can_call_llm=introduce_correction))
    if contract == "modern":
        monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: replace(config, is_mock=False)))
        monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
        monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")

        class AssessedSilentPi(SilentPi):
            async def evaluate(self, payload):
                result = await super().evaluate(payload)
                result["metrics"]["usage"] = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
                result["current_need"] = {"status": "has_current_need", "focus_evidence": [{"segment_id": "s8",
                    "quote": p._conn.execute("SELECT normalized_text FROM transcript_segments WHERE segment_id='s8'").fetchone()[0]}],
                    "reason": "冻结原话仍需核对，当前没有额外可贡献内容。"}
                return result

        app.state.pi_coach_runtime = AssessedSilentPi()
        output = asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
        assert output["coach"]["pi_provider_attempted"] is True
        assert output["applied"]["coach_intervention"] is None
        assert entered == [True] and len(app.state.pi_coach_runtime.calls) == 1
        trace = app.state.pipeline_traces.get(running["id"]).execution_snapshot()
        assert len(trace["provider_attempts"]) == 1
        usage = app.state.settings_usage_repository.list_usage(session_id="review")
        assert len(usage) == 1 and usage[0]["total_tokens"] == 30
        assert next(j for j in p.list_jobs(meeting_id="review") if j["kind"] == "correction")["status"] == "pending"
        return
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
    assert getattr(raised.value, "preserve_attempt", False) is True
    assert app.state.pi_coach_runtime.calls == []
    trace = app.state.pipeline_traces.get(running["id"]).execution_snapshot()
    assert trace["provider_attempts"] == []


def test_revised_cancel_restores_once_after_new_successful_state_inside_thirty_seconds(understood_app):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    old = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    assert p.commit_transcript_revision(meeting_id="review", segment_id="s8", expected_evidence_hash="h8",
        corrected_text="第8项现在要核对通知不成功时的补偿边界。", revision_id="new-basis", now_ms=now + 100)
    assert p.get_job(old["id"])["error_class"] == "discussion_evidence_revised"
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 101) is None
    refresh_understanding(p, now + 2_101)
    fresh = p.enqueue_discussion_update(meeting_id="review", now_ms=now + 2_104)
    assert fresh is not None and fresh["id"] != old["id"]
    assert fresh["request_context"]["analysis_basis_revision"] > old["request_context"]["analysis_basis_revision"]
    assert next(e["revision"] for e in fresh["request_context"]["evidence"] if e["segment_id"] == "s8") == 2
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 2_105) is None
    assert len(auto_jobs(p)) == 2 and app.state.pi_coach_runtime.calls == []


@pytest.mark.parametrize("attempt_count", [0, 1])
def test_scheduler_recovers_revised_cancel_with_durable_actual_attempt_output(understood_app, monkeypatch, attempt_count):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    old = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    # The cancellation cause is produced by the native canonical revision,
    # while this isolated row arranges either durable Provider-count shape.
    p._conn.execute("UPDATE jobs SET output_json=? WHERE id=?",
        ('{"provider_attempt_count":%d,"coach":{"pi_provider_attempted":%s}}' % (attempt_count, "true" if attempt_count else "false"), old["id"]))
    assert p.commit_transcript_revision(meeting_id="review", segment_id="s8", expected_evidence_hash="h8",
        corrected_text="第8项现在要核对通知不成功时的补偿边界。", revision_id="scheduler-new-basis", now_ms=now + 100)
    refresh_understanding(p, now + 2_101)
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "0")
    monkeypatch.setattr(app_module.time, "time_ns", lambda: (now + 2_104) * 1_000_000)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    new = auto_jobs(p)
    assert len(new) == 2 and any(j["id"] != old["id"] and j["status"] == "pending" for j in new)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert app.state.pi_coach_runtime.calls == []


def test_recovery_uses_successful_memory_without_waiting_for_every_new_formal_anchor(understood_app):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    old = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    assert p.commit_transcript_revision(meeting_id="review", segment_id="s8", expected_evidence_hash="h8",
        corrected_text="第8项需要明确补偿边界。", revision_id="continuous-new-basis", now_ms=now + 100)
    refresh_understanding(p, now + 2_101)
    append(p, 9, "讨论还在继续，需要补充下一项。", now + 2_103)
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 8
    fresh = p.enqueue_discussion_update(meeting_id="review", now_ms=now + 2_104)
    assert fresh is not None and fresh["id"] != old["id"] and fresh["input_transcript_seq"] == 9


@pytest.mark.parametrize("contract", ["modern", "legacy"])
def test_waiting_auto_resumes_once_after_l2_settles_without_revision(understood_app, monkeypatch, contract):
    import json

    app, now, config = understood_app
    p = app.state.v2_persistence
    queued = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    if contract == "legacy":
        for key in ("automatic_attention", "current_need_contract", "current_need_basis"):
            queued["request_context"].pop(key)
        p._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?",
            (json.dumps(queued["request_context"]), queued["id"]))
    correction_job = add_correction(p, now + 1)
    running = p.claim_next_job(worker_id="auto", lane="pi_deep", now_ms=now + 2, lease_ms=30_000)
    if contract == "modern":
        monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: replace(config, is_mock=False)))
        monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
        monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")

        class AssessedSilentPi(SilentPi):
            async def evaluate(self, payload):
                result = await super().evaluate(payload)
                result["metrics"]["usage"] = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
                result["current_need"] = {"status": "has_current_need", "focus_evidence": [{"segment_id": "s8",
                    "quote": p._conn.execute("SELECT normalized_text FROM transcript_segments WHERE segment_id='s8'").fetchone()[0]}],
                    "reason": "冻结原话仍需核对，当前没有额外可贡献内容。"}
                return result

        app.state.pi_coach_runtime = AssessedSilentPi()
        output = asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
        assert output["coach"]["pi_provider_attempted"] is True
        assert len(app.state.pi_coach_runtime.calls) == 1
        usage = app.state.settings_usage_repository.list_usage(session_id="review")
        assert len(usage) == 1 and usage[0]["total_tokens"] == 30
        assert p.complete_job(job_id=queued["id"], worker_id="auto", now_ms=now + 3, output=output)
        assert p.get_job(correction_job["id"])["status"] == "pending"
        assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 120_000) is None
        return
    with pytest.raises(app_module.AutomaticCoachCorrectionDeferred) as raised:
        asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
    executor = DurableJobExecutor(p, correction_handler=lambda _j: {}, suggestion_handler=lambda _j: {})
    asyncio.run(executor._retry_after_handler_failure(running, "auto", raised.value, resume_generation=0))
    correction = p.claim_next_job(worker_id="l2", lane="correction", now_ms=now + 3, lease_ms=30_000)
    assert p.complete_job(job_id=correction["id"], worker_id="l2", now_ms=now + 4, output={})
    deferred = p.get_job(queued["id"])
    claimed = p.claim_next_job(worker_id="auto", lane="pi_deep",
        now_ms=deferred["next_attempt_at_ms"], lease_ms=30_000)
    assert claimed["id"] == queued["id"] and claimed["attempts"] == 1
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(claimed))
    assert output["coach"]["pi_provider_attempted"] is True
    assert len(app.state.pi_coach_runtime.calls) == 1
    assert p.complete_job(job_id=claimed["id"], worker_id="auto", now_ms=deferred["next_attempt_at_ms"] + 1, output=output)
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 120_000) is None


@pytest.mark.parametrize("status,reason", [("failed", "TimeoutError"), ("cancelled", "preempted_by_user_request"),
    ("succeeded", None)])
def test_normal_terminal_attempt_retains_cooldown_even_after_new_basis(understood_app, status, reason):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    old = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    p._conn.execute("UPDATE jobs SET status=?,error_class=? WHERE id=?", (status, reason, old["id"]))
    current = p.get_current_discussion("review")
    p.update_discussion_context(meeting_id="review", work_item_id=current["work_item_id"],
        expected_version=current["version"], now_ms=now + 1, context_patch={"current_solution": "候选方案需要补充条件。"})
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 2) is None
    refresh_understanding(p, now + 3)
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 30_000) is not None


def test_same_basis_failed_version_never_loops_on_scheduler_ticks(understood_app):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    old = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    p._conn.execute("UPDATE jobs SET status='failed',error_class='TimeoutError' WHERE id=?", (old["id"],))
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 120_000) is None
    assert len(auto_jobs(p)) == 1


def test_manual_priority_and_other_meeting_correction_remain_independent(understood_app):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    p.create_meeting(meeting_id="other", title=None, now_ms=now)
    p.commit_final_and_enqueue(meeting_id="other", final_id="other-f", segment_id="other-s", text="其他会议。",
        normalized_text="其他会议。", started_at_ms=0, ended_at_ms=1000, evidence_hash="other-h", now_ms=now,
        enqueue_intelligence=False, enqueue_answer=False)
    automatic = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    assert automatic is not None
    manual = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
        idempotency_key="manual", now_ms=now + 1)
    assert p.get_job(automatic["id"])["error_class"] == "preempted_by_user_request"
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 60_000) is None
    assert p.get_job(manual["id"])["status"] == "pending"


class CorrectionAdmitted(Exception):
    pass


@pytest.mark.parametrize("status", ["pending", "retry_wait", "running"])
def test_unattempted_automatic_wait_does_not_make_correction_wait_back(understood_app, monkeypatch, status):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    auto = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    p._conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, auto["id"]))
    if status == "running":
        # Claim-stage trace exists, but a Provider attempt has never started.
        app.state.pipeline_traces.create(trace_id=auto["id"], meeting_id="review", job_id=auto["id"])
    add_correction(p, now + 1)
    correction = p.claim_next_job(worker_id="l2", lane="correction", now_ms=now + 2, lease_ms=30_000)
    settings = app.state.settings_usage_repository.get_settings()
    settings["asr"]["l2_correction_enabled"] = True
    app.state.settings_usage_repository.replace_settings(settings)

    def stop_after_admission(*_args, **_kwargs):
        raise CorrectionAdmitted()

    monkeypatch.setattr(app_module.llm_service, "correction_config", stop_after_admission)
    with pytest.raises(CorrectionAdmitted):
        asyncio.run(app.state.v2_correction_job_handler_async_impl(correction))
    assert app.state.pi_coach_runtime.calls == []


def test_inflight_manual_priority_is_preserved_when_l2_arrives(understood_app):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
        idempotency_key="manual", now_ms=now)
    manual = p.claim_next_job(worker_id="manual", lane="pi_deep", now_ms=now, lease_ms=30_000)
    add_correction(p, now + 1)
    correction = p.claim_next_job(worker_id="l2", lane="correction", now_ms=now + 2, lease_ms=30_000)
    settings = app.state.settings_usage_repository.get_settings()
    settings["asr"]["l2_correction_enabled"] = True
    app.state.settings_usage_repository.replace_settings(settings)
    with pytest.raises(app_module.CorrectionProviderPriorityDeferred) as raised:
        asyncio.run(app.state.v2_correction_job_handler_async_impl(correction))
    assert manual["id"] in raised.value.blocking_job_ids


def test_normal_silent_sdk_result_is_evaluated_once_and_budget_zero_blocks_next_basis(understood_app, monkeypatch):
    app, now, config = understood_app
    p = app.state.v2_persistence
    queued = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    running = p.claim_next_job(worker_id="pi", lane="pi_deep", now_ms=now, lease_ms=30_000)
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
    assert len(app.state.pi_coach_runtime.calls) == 1
    assert output["coach"]["pi_provider_attempted"] is True
    assert len(app.state.pipeline_traces.get(running["id"]).execution_snapshot()["provider_attempts"]) == 1
    assert output["applied"]["coach_intervention"] is None
    assert p.complete_job(job_id=queued["id"], worker_id="pi", now_ms=now + 1, output=output)
    assert p.enqueue_discussion_update(meeting_id="review", now_ms=now + 120_000) is None
    current = p.get_current_discussion("review")
    p.update_discussion_context(meeting_id="review", work_item_id=current["work_item_id"],
        expected_version=current["version"], now_ms=now + 30_000, context_patch={"current_solution": "候选条件更新。"})
    refresh_understanding(p, now + 30_000)
    new = p.enqueue_discussion_update(meeting_id="review", now_ms=now + 30_001)
    running = p.claim_next_job(worker_id="budget", lane="pi_deep", now_ms=now + 30_001, lease_ms=30_000)
    assert running["id"] == new["id"]
    settings = app.state.settings_usage_repository.get_settings()
    settings["budget"]["session_limit_cny"] = 0
    app.state.settings_usage_repository.replace_settings(settings)
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: replace(config, is_mock=False)))
    monkeypatch.setattr(app_module.time, "time_ns", lambda: (now + 30_002) * 1_000_000)
    # The existing routed handler projects a local budget failure envelope;
    # it does not propagate HTTPException out of this private handler seam.
    rejected = asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
    assert rejected["coach"]["pi_provider_attempted"] is False
    assert app.state.pipeline_traces.get(running["id"]).execution_snapshot()["provider_attempts"] == []
    assert len(app.state.pi_coach_runtime.calls) == 1


def test_actual_started_fake_pi_keeps_priority_over_later_correction(understood_app):
    app, now, _ = understood_app
    p = app.state.v2_persistence
    queued = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    running = p.claim_next_job(worker_id="pi", lane="pi_deep", now_ms=now, lease_ms=30_000)
    settings = app.state.settings_usage_repository.get_settings()
    settings["asr"]["l2_correction_enabled"] = True
    app.state.settings_usage_repository.replace_settings(settings)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class StartedPi(SilentPi):
            async def evaluate(self, payload):
                entered.set()
                await release.wait()
                return await super().evaluate(payload)

        app.state.pi_coach_runtime = StartedPi()
        task = asyncio.create_task(app.state.v2_intelligence_job_handler_impl(running))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            add_correction(p, now + 1)
            correction = p.claim_next_job(worker_id="l2", lane="correction", now_ms=now + 2, lease_ms=30_000)
            with pytest.raises(app_module.CorrectionProviderPriorityDeferred) as raised:
                await app.state.v2_correction_job_handler_async_impl(correction)
            assert queued["id"] in raised.value.blocking_job_ids
        finally:
            release.set()
            await task

    asyncio.run(scenario())
    assert len(app.state.pi_coach_runtime.calls) == 1
