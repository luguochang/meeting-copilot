"""Native Pi freshness fences while an unrelated correction remains active."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded
from test_discussion_state_background import state_app as shared_state_app
from test_participation_current_need import Runtime, intervention, need, seed


LATEST = "那如果套餐里有人不吃牛肉，可以替换成什么？"
USAGE = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}


@pytest.fixture
def native_app(tmp_path, monkeypatch):
    yield from shared_state_app.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def ready_app(native_app, monkeypatch):
    app, now = native_app
    p = app.state.v2_persistence
    seed(p, now=now)
    # Arrange the new exchange before its first automatic packet is frozen.
    p._conn.execute("UPDATE transcript_segments SET text=?,normalized_text=? WHERE segment_id='s9'", (LATEST, LATEST))
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE kind='intelligence'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid",
        api_key="synthetic-test-key", model="synthetic-model", timeout_seconds=25, is_mock=False)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_k: None)
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST", "0")
    # Exercise the native usage ledger for simulated paid completions; the
    # existing mock-config branch deliberately excludes them from accounting.
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    app.state.streaming_llm_client = object()  # A fallback cannot issue HTTP.
    return app, now


def queued(app, now):
    p = app.state.v2_persistence
    job = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    assert job is not None
    running = p.claim_next_job(worker_id="freshness", lane="pi_deep", now_ms=now, lease_ms=30000)
    assert running["id"] == job["id"]
    return running


def activate_correction(p):
    row = p._conn.execute("SELECT id FROM jobs WHERE kind='correction' AND evidence_segment_id='s9'").fetchone()
    assert row is not None
    p._conn.execute("UPDATE jobs SET status='running',attempts=1 WHERE id=?", (row["id"],))
    return row["id"]


def revise(p, source, now):
    old = p._conn.execute("SELECT evidence_hash FROM transcript_segments WHERE segment_id=?", (source,)).fetchone()
    p.commit_transcript_revision(meeting_id="review", segment_id=source,
        expected_evidence_hash=old["evidence_hash"], corrected_text="当前套餐问题已经回答，开始另一个议题。",
        revision_id="freshness-revision-" + source, now_ms=now + 1)


def fake_pi(*, status="has_current_need", on_complete=None):
    result = {"action": "intervention", "intervention": intervention(), "current_need": need(status, quote=LATEST),
        "metrics": {"turns": 1, "tool_calls": 1, "tool_names": ["submit_intervention"],
            "elapsed_ms": 500, "usage": USAGE}}

    class CompletingRuntime(Runtime):
        async def evaluate(self, payload):
            response = await super().evaluate(payload)
            if on_complete is not None:
                on_complete()
            return response

    return CompletingRuntime(result)


def published_cards(p, job_id):
    rows = p._conn.execute("SELECT payload_json FROM meeting_events WHERE type='meeting.intelligence.applied' "
        "AND aggregate_id=?", (job_id,)).fetchall()
    return [json.loads(row["payload_json"])["coach_intervention"] for row in rows
        if json.loads(row["payload_json"]).get("coach_intervention") is not None]


def coach_usage(app):
    return [row for row in app.state.settings_usage_repository.list_usage(session_id="review")
        if row["purpose"] == "realtime_coach"]


@pytest.mark.parametrize("source", ["s8", "s9"])
def test_revision_at_last_provider_boundary_has_zero_pi_calls(ready_app, monkeypatch, source):
    app, now = ready_app
    p = app.state.v2_persistence
    job = queued(app, now)
    runtime = app.state.pi_coach_runtime = fake_pi()
    entered = []

    def revise_at_budget_boundary():
        if not entered:
            entered.append(True)
            revise(p, source, now)
        return True

    monkeypatch.setattr(app_module, "get_degradation_controller",
        lambda: SimpleNamespace(can_call_llm=revise_at_budget_boundary))
    with pytest.raises(IntelligenceEvidenceSuperseded):
        asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert entered == [True]
    assert runtime.calls == []
    assert coach_usage(app) == []
    assert app.state.pipeline_traces.get(job["id"]).execution_snapshot()["provider_attempts"] == []
    assert published_cards(p, job["id"]) == []


@pytest.mark.parametrize("source", ["s8", "s9"])
def test_revision_after_paid_pi_completion_keeps_one_usage_and_no_card(ready_app, source):
    app, now = ready_app
    p = app.state.v2_persistence
    job = queued(app, now)
    activate_correction(p)
    runtime = app.state.pi_coach_runtime = fake_pi(on_complete=lambda: revise(p, source, now))
    with pytest.raises(IntelligenceEvidenceSuperseded):
        asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == 1
    charges = coach_usage(app)
    assert len(charges) == 1 and charges[0]["total_tokens"] == 30
    assert len(app.state.pipeline_traces.get(job["id"]).execution_snapshot()["provider_attempts"]) == 1
    assert published_cards(p, job["id"]) == []


@pytest.mark.parametrize("contract", ["modern", "legacy", "marker_only", "mismatched"])
def test_new_running_correction_after_enqueue_changes_no_canonical_version(ready_app, contract):
    app, now = ready_app
    p = app.state.v2_persistence
    job = queued(app, now)
    runtime = app.state.pi_coach_runtime = fake_pi()
    frozen = [(item["segment_id"], item["revision"], item["evidence_hash"]) for item in job["request_context"]["evidence"]]
    correction_id = activate_correction(p)
    if contract != "modern":
        if contract in {"legacy", "marker_only"}:
            job["request_context"].pop("current_need_contract")
            job["request_context"].pop("current_need_basis")
            if contract == "legacy":
                job["request_context"].pop("automatic_attention")
        else:
            job["request_context"]["current_need_contract"] = "participation_need.v0"
        p._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?",
            (json.dumps(job["request_context"]), job["id"]))
        with pytest.raises(app_module.AutomaticCoachCorrectionDeferred):
            asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
        assert runtime.calls == [] and coach_usage(app) == []
        assert published_cards(p, job["id"]) == []
    else:
        output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
        assert len(runtime.calls) == len(coach_usage(app)) == 1
        assert output["applied"]["current_need"]["status"] == "has_current_need"
        assert len(published_cards(p, job["id"])) == 1
        assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 8
    assert p.get_job(correction_id)["status"] == "running"
    actual = {row["segment_id"]: (row["revision"], row["evidence_hash"]) for row in p._conn.execute(
        "SELECT segment_id,revision,evidence_hash FROM transcript_segments WHERE meeting_id='review'")}
    assert all(actual[source] == (revision, digest) for source, revision, digest in frozen)


def test_new_correction_at_last_provider_boundary_does_not_block_typed_need(ready_app, monkeypatch):
    app, now = ready_app
    p = app.state.v2_persistence
    job = queued(app, now)
    runtime = app.state.pi_coach_runtime = fake_pi()
    entered = []

    def start_correction_without_revision():
        if not entered:
            entered.append(activate_correction(p))
        return True

    monkeypatch.setattr(app_module, "get_degradation_controller",
        lambda: SimpleNamespace(can_call_llm=start_correction_without_revision))
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(entered) == len(runtime.calls) == len(coach_usage(app)) == 1
    assert output["applied"]["current_need"]["status"] == "has_current_need"
    assert len(published_cards(p, job["id"])) == 1
    assert p.get_job(entered[0])["status"] == "running"


@pytest.mark.parametrize("status", ["already_answered", "moved_on", "uncertain"])
def test_unassessed_exchange_cannot_revive_old_opportunity_during_correction(ready_app, status):
    app, now = ready_app
    p = app.state.v2_persistence
    job = queued(app, now)
    activate_correction(p)
    runtime = app.state.pi_coach_runtime = fake_pi(status=status)
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(runtime.calls) == len(coach_usage(app)) == 1
    assert coach_usage(app)[0]["total_tokens"] == 30
    assert output["applied"]["coach_intervention"] is None
    assert published_cards(p, job["id"]) == []
    actual_packet = runtime.calls[0]["context"]["request_metadata"]
    assert actual_packet["current_need_basis"]["unassessed_segment_ids"] == ["s9"]
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 8
