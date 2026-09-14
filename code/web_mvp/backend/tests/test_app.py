import asyncio
import builtins
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

from fastapi.testclient import TestClient
import pytest

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.asr_live_repository import JsonFileAsrLiveSessionRepository
from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller
from meeting_copilot_web_mvp.repository import JsonFileSessionRepository
from meeting_copilot_web_mvp.realtime_intelligence import (
    CoachIntervention,
    RealtimeIntelligenceResponse,
    build_realtime_coach_provenance_decision,
)
from meeting_copilot_web_mvp.realtime_provider_circuit import RealtimeProviderCircuit
from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository, SqliteSessionRepository
from meeting_copilot_web_mvp.v2_persistence import (
    IntelligenceEvidenceSuperseded,
    V2Persistence,
)
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor
from meeting_copilot_web_mvp.application_schema import APPLICATION_SCHEMA_VERSION


REPO_ROOT = Path(__file__).resolve().parents[4]


async def _wait_for_v2_job_status(
    persistence: V2Persistence,
    job_id: str,
    status: str,
    *,
    timeout_s: float = 3.0,
) -> dict:
    async with asyncio.timeout(timeout_s):
        while True:
            job = persistence.get_job(job_id)
            if job["status"] == status:
                return job
            await asyncio.sleep(0.005)


def test_dedupe_strings_handles_empty_values_and_preserves_order():
    assert app_module._dedupe_strings([]) == []
    assert app_module._dedupe_strings(["first", "second"]) == ["first", "second"]
    assert app_module._dedupe_strings(["first", "second", "first", "third", "second"]) == [
        "first",
        "second",
        "third",
    ]


def test_realtime_coach_budget_reserves_projection_time_and_honors_provider_cap():
    remaining_ms, provider_timeout_ms = app_module._realtime_coach_budget_ms(
        deadline_at_ms=10_000,
        now_ms=4_000,
        configured_timeout_seconds=20,
    )

    assert remaining_ms == 6_000
    assert provider_timeout_ms == 5_250
    assert provider_timeout_ms <= remaining_ms - app_module.REALTIME_COACH_PROJECTION_RESERVE_MS

    remaining_ms, provider_timeout_ms = app_module._realtime_coach_budget_ms(
        deadline_at_ms=10_000,
        now_ms=9_500,
        configured_timeout_seconds=20,
    )
    assert remaining_ms == 500
    assert provider_timeout_ms == 0


def test_realtime_intelligence_debounce_defaults_to_immediate_pi_window(monkeypatch):
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_INTELLIGENCE_DEBOUNCE_MS", raising=False)
    assert app_module._realtime_intelligence_debounce_ms() == 0

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_INTELLIGENCE_DEBOUNCE_MS", "750")
    assert app_module._realtime_intelligence_debounce_ms() == 750


def test_realtime_intelligence_debounce_override_requires_pi_runtime(monkeypatch):
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", raising=False)
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", raising=False)
    assert app_module._configured_intelligence_debounce_for_app("llm_first") == 0

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "direct")
    assert app_module._configured_intelligence_debounce_for_app("llm_first") is None
    assert app_module._configured_intelligence_debounce_for_app("legacy") is None


@pytest.mark.parametrize("value", ["-1", "751", "not-an-integer"])
def test_realtime_intelligence_debounce_rejects_unsafe_configuration(monkeypatch, value):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_INTELLIGENCE_DEBOUNCE_MS", value)
    with pytest.raises(RuntimeError, match="MEETING_COPILOT_REALTIME_INTELLIGENCE_DEBOUNCE_MS"):
        app_module._realtime_intelligence_debounce_ms()


def test_realtime_coach_soft_cutoff_is_absolute_and_keeps_projection_reserve():
    soft_deadline = app_module._realtime_coach_soft_deadline_at_ms(
        final_committed_at_ms=10_000,
        fallback_created_at_ms=1,
    )
    assert soft_deadline == 10_000 + app_module.REALTIME_COACH_SOFT_CUTOFF_MS

    remaining_ms, provider_budget_ms = app_module._realtime_coach_soft_budget_ms(
        soft_deadline_at_ms=soft_deadline,
        now_ms=10_500,
    )
    assert remaining_ms == app_module.REALTIME_COACH_SOFT_CUTOFF_MS - 500
    assert provider_budget_ms == remaining_ms - app_module.REALTIME_COACH_SOFT_PROJECTION_RESERVE_MS

    # A malformed final timestamp falls back to job creation, never to an
    # unbounded provider timeout.
    assert app_module._realtime_coach_soft_deadline_at_ms(
        final_committed_at_ms="bad",
        fallback_created_at_ms=2_000,
    ) == 2_000 + app_module.REALTIME_COACH_SOFT_CUTOFF_MS


def test_v2_local_reflex_projects_without_provider_or_pi_budget(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")

    def forbidden_provider_config(_cls):
        raise AssertionError("local reflex must run before Provider configuration")

    async def forbidden_provider_call(**_kwargs):
        raise AssertionError("local reflex must not start semantic Provider work")

    class ForbiddenPiRuntime:
        async def evaluate(self, _payload):
            raise AssertionError("local reflex must not start Pi")

    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(forbidden_provider_config),
    )
    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_provider_call)
    app.state.pi_coach_runtime = ForbiddenPiRuntime()

    committed_at_ms = time.time_ns() // 1_000_000
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="local-reflex-providerless-meeting",
        final_id="local-reflex-providerless-final",
        segment_id="local-reflex-providerless-segment",
        text="今天先到这里。",
        normalized_text="今天先到这里。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="local-reflex-providerless-hash",
        source_track="microphone",
        now_ms=committed_at_ms,
    )
    intelligence_job_id = committed["job_ids"]["intelligence"]
    job = app.state.v2_persistence.get_job(intelligence_job_id)

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert output["transport_mode"] == "local_reflex"
    assert output["provider_attempt_count"] == 0
    assert output["usage"] is None
    assert output["semantic"] == {
        "status": "suppressed_by_local_reflex",
        "error_class": None,
    }
    coach = output["coach"]
    assert coach["status"] == "intervention"
    assert coach["origin"] == "local_reflex"
    assert coach["runtime_requested"] == "local_reflex"
    assert coach["runtime_used"] == "local_reflex"
    assert coach["local_reflex_kind"] == "missing_next_step"
    assert coach["pi_provider_attempted"] is False
    assert coach["intervention"]["event_type"] == "execution_gap"
    assert coach["intervention"]["evidence_quote"] == "今天先到这里"
    assert coach["intervention"]["evidence_segment_ids"] == [
        "local-reflex-providerless-segment"
    ]
    assert coach["valid_until_ms"] > coach["completed_at_ms"]

    events = app.state.v2_persistence.list_events(
        "local-reflex-providerless-meeting",
        limit=1_000,
    )
    applied = [
        event for event in events if event["type"] == "meeting.intelligence.applied"
    ]
    assert len(applied) == 1
    payload = applied[0]["payload"]
    assert payload["source"] == "local_reflex"
    assert payload["llm_called"] is False
    assert payload["llm_call_status"] == "not_called"
    assert payload["origin"] == "local_reflex"
    assert payload["runtime_used"] == "local_reflex"
    assert payload["local_reflex_kind"] == "missing_next_step"
    assert payload["pi_provider_attempted"] is False
    assert payload["evidence"]["quote"] == "今天先到这里"
    assert payload["coach_decision"]["pi_provider_attempted"] is False
    assert payload["coach_intervention"]["local_reflex_kind"] == "missing_next_step"
    assert applied[0]["occurred_at_ms"] - committed_at_ms < 2_500
    assert app.state.v2_persistence.recent_local_reflex_kinds(
        "local-reflex-providerless-meeting",
        since_ms=0,
    ) == {"missing_next_step"}
    assert app.state.v2_persistence.recent_coach_episode_priorities(
        "local-reflex-providerless-meeting",
        since_ms=0,
    ) == {}
    assert not any(
        event["type"].startswith("meeting.realtime_provider.reservation")
        for event in events
    )
    assert app.state.provider_priority_arbiter.active_realtime_count == 0
    assert app.state.provider_priority_arbiter.pending_realtime_count == 0

    # Snapshot diagnostics may inspect optional configuration after the job is
    # already complete; keep that unrelated read provider-less for API checks.
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: None),
    )
    client = TestClient(app)
    snapshot_response = client.get(
        "/v2/meetings/local-reflex-providerless-meeting/snapshot"
    )
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot["follow_up"]["origin"] == "local_reflex"
    assert snapshot["follow_up"]["local_reflex_kind"] == "missing_next_step"
    assert snapshot["follow_up"]["coach_event_type"] == "execution_gap"
    assert snapshot["coach_decision"]["origin"] == "local_reflex"
    event_response = client.get(
        "/v2/meetings/local-reflex-providerless-meeting/events",
        params={"after_seq": 0, "limit": 1_000},
    )
    assert event_response.status_code == 200
    public_applied = next(
        event
        for event in event_response.json()["events"]
        if event["type"] == "meeting.intelligence.applied"
    )
    assert public_applied["payload"]["source"] == "local_reflex"
    assert public_applied["payload"]["local_reflex_kind"] == "missing_next_step"
    assert public_applied["payload"]["llm_called"] is False


def test_v2_transcript_delta_reaches_production_handler(
    tmp_path,
    monkeypatch,
):
    """The production worker accepts the trigger contract used by live ASR."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    committed = persistence.commit_final_and_enqueue(
        meeting_id="transcript-delta-handler-meeting",
        final_id="transcript-delta-handler-final",
        segment_id="transcript-delta-handler-segment",
        text="今天先到这里。",
        normalized_text="今天先到这里。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="transcript-delta-handler-hash",
        source_track="microphone",
        now_ms=time.time_ns() // 1_000_000,
    )
    job_id = committed["job_ids"]["intelligence"]
    persistence._conn.execute(
        "UPDATE jobs SET trigger_type = 'transcript_delta' WHERE id = ?",
        (job_id,),
    )
    persistence._conn.commit()

    job = persistence.get_job(job_id)
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert output["coach"]["status"] == "intervention"
    assert output["coach"]["runtime_used"] == "local_reflex"
    assert output["applied"]


def test_v2_real_asr_owner_gap_reaches_local_reflex_production_handler(
    tmp_path,
    monkeypatch,
):
    """Natural ASR owner-gap wording must yield an evidence-bound action card."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence

    def forbidden_provider_config(_cls):
        raise AssertionError("owner-gap reflex must not wait for Provider config")

    async def forbidden_provider_call(**_kwargs):
        raise AssertionError("owner-gap reflex must not start semantic Provider work")

    class ForbiddenPiRuntime:
        async def evaluate(self, _payload):
            raise AssertionError("owner-gap reflex must not start Pi")

    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(forbidden_provider_config),
    )
    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_provider_call)
    app.state.pi_coach_runtime = ForbiddenPiRuntime()

    text = (
        "我们计划周五上线，但是回滚负责人还没有确定，"
        "发布前还需要确认回滚条件和具体负责人。"
    )
    committed = persistence.commit_final_and_enqueue(
        meeting_id="real-asr-owner-gap-handler-meeting",
        final_id="real-asr-owner-gap-handler-final",
        segment_id="real-asr-owner-gap-handler-segment",
        text=text,
        normalized_text=text,
        started_at_ms=0,
        ended_at_ms=4_000,
        evidence_hash="real-asr-owner-gap-handler-hash",
        source_track="microphone",
        now_ms=time.time_ns() // 1_000_000,
    )

    job = persistence.get_job(committed["job_ids"]["intelligence"])
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert output["transport_mode"] == "local_reflex"
    assert output["provider_attempt_count"] == 0
    assert output["coach"]["status"] == "intervention"
    assert output["coach"]["runtime_used"] == "local_reflex"
    assert output["coach"]["local_reflex_kind"] == "missing_next_step"
    intervention = output["coach"]["intervention"]
    assert intervention["event_type"] == "execution_gap"
    assert intervention["evidence_quote"] == text.rstrip("。")
    assert intervention["evidence_segment_ids"] == [
        "real-asr-owner-gap-handler-segment"
    ]

    applied = next(
        event
        for event in persistence.list_events(
            "real-asr-owner-gap-handler-meeting", limit=1_000
        )
        if event["type"] == "meeting.intelligence.applied"
    )
    assert applied["payload"]["coach_decision"]["origin"] == "local_reflex"
    assert applied["payload"]["coach_intervention"]["local_reflex_kind"] == (
        "missing_next_step"
    )


def test_v2_local_reflex_rechecks_a_vad_split_close_from_latest_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence

    def forbidden_provider_config(_cls):
        raise AssertionError("fresh split evidence must take the local reflex path")

    async def forbidden_provider_call(**_kwargs):
        raise AssertionError("split closing reflex must not start semantic Provider work")

    class ForbiddenPiRuntime:
        async def evaluate(self, _payload):
            raise AssertionError("split closing reflex must not start Pi")

    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(forbidden_provider_config),
    )
    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_provider_call)
    app.state.pi_coach_runtime = ForbiddenPiRuntime()

    observed_at_ms = time.time_ns() // 1_000_000
    first_committed_at_ms = (
        observed_at_ms - app_module.REALTIME_COACH_SOFT_CUTOFF_MS - 1_000
    )
    second_committed_at_ms = observed_at_ms - 800
    first = persistence.commit_final_and_enqueue(
        meeting_id="local-reflex-vad-split-meeting",
        final_id="local-reflex-vad-split-final-1",
        segment_id="local-reflex-vad-split-segment-1",
        text="我们已经把方案讨论",
        normalized_text="我们已经把方案讨论",
        started_at_ms=100,
        ended_at_ms=36_200,
        evidence_hash="local-reflex-vad-split-hash-1",
        source_track="microphone",
        now_ms=first_committed_at_ms,
    )
    second = persistence.commit_final_and_enqueue(
        meeting_id="local-reflex-vad-split-meeting",
        final_id="local-reflex-vad-split-final-2",
        segment_id="local-reflex-vad-split-segment-2",
        text="完了今天先到这里",
        normalized_text="完了今天先到这里",
        started_at_ms=36_200,
        ended_at_ms=37_700,
        evidence_hash="local-reflex-vad-split-hash-2",
        source_track="microphone",
        now_ms=second_committed_at_ms,
    )

    assert second["job_ids"]["intelligence"] == first["job_ids"]["intelligence"]
    paragraph = persistence.list_semantic_paragraphs(
        "local-reflex-vad-split-meeting"
    )["paragraphs"][0]
    assert paragraph["revision"] == 2
    assert paragraph["text"] == "我们已经把方案讨论完了今天先到这里"

    job = persistence.claim_next_job(
        worker_id="local-reflex-vad-split-worker",
        lane="intelligence",
        now_ms=observed_at_ms,
        lease_ms=30_000,
    )
    assert job is not None
    assert job["id"] == first["job_ids"]["intelligence"]
    assert job["input_version"] == 2
    assert job["evidence_segment_id"] == "local-reflex-vad-split-segment-2"
    assert job["final_committed_at_ms"] == first_committed_at_ms
    assert job["deadline_at_ms"] == (
        first_committed_at_ms + app_module.INTELLIGENCE_REALTIME_BUDGET_MS
    )
    assert observed_at_ms >= (
        int(job["final_committed_at_ms"])
        + app_module.REALTIME_COACH_SOFT_CUTOFF_MS
    )

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert output["transport_mode"] == "local_reflex"
    assert output["provider_attempt_count"] == 0
    assert output["coach"]["pi_provider_attempted"] is False
    assert output["coach"]["local_reflex_kind"] == "missing_next_step"
    assert output["coach"]["intervention"]["evidence_quote"] == "完了今天先到这里"
    assert output["coach"]["intervention"]["evidence_segment_ids"] == [
        "local-reflex-vad-split-segment-2"
    ]
    assert output["coach"]["soft_deadline_at_ms"] == (
        second_committed_at_ms + app_module.REALTIME_COACH_SOFT_CUTOFF_MS
    )
    completed = persistence.complete_job(
        job_id=job["id"],
        worker_id="local-reflex-vad-split-worker",
        now_ms=time.time_ns() // 1_000_000,
        output=output,
    )
    assert completed is not None
    assert completed["status"] == "succeeded"
    events = persistence.list_events("local-reflex-vad-split-meeting", limit=1_000)
    applied = [
        event for event in events if event["type"] == "meeting.intelligence.applied"
    ]
    assert len(applied) == 1
    assert applied[0]["payload"]["source"] == "local_reflex"
    assert applied[0]["payload"]["pi_provider_attempted"] is False


def test_v2_local_reflex_durable_executor_bypasses_blocked_provider_lane(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    provider_lanes = app.state.provider_lane_registry
    blocker = provider_lanes.try_acquire_realtime("blocked-provider-lane")
    assert blocker is not None

    def forbidden_provider_config(_cls):
        raise AssertionError("local reflex must not read Provider configuration")

    async def forbidden_provider_call(**_kwargs):
        raise AssertionError("local reflex must not start semantic Provider work")

    def forbidden_lane_call(*_args, **_kwargs):
        raise AssertionError("local reflex must not reserve or acquire the Provider lane")

    class ForbiddenPiRuntime:
        async def evaluate(self, _payload):
            raise AssertionError("local reflex must not start Pi")

    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(forbidden_provider_config),
    )
    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_provider_call)
    monkeypatch.setattr(provider_lanes, "reserve_realtime", forbidden_lane_call)
    monkeypatch.setattr(provider_lanes, "try_acquire_realtime", forbidden_lane_call)
    app.state.pi_coach_runtime = ForbiddenPiRuntime()

    committed_at_ms = time.time_ns() // 1_000_000
    committed = persistence.commit_final_and_enqueue(
        meeting_id="local-reflex-executor-meeting",
        final_id="local-reflex-executor-final",
        segment_id="local-reflex-executor-segment",
        text="今天先到这里。",
        normalized_text="今天先到这里。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="local-reflex-executor-hash",
        source_track="microphone",
        now_ms=committed_at_ms,
    )
    intelligence_job_id = committed["job_ids"]["intelligence"]
    claimed_jobs: list[dict] = []

    async def intelligence_handler(job: dict):
        durable_job = persistence.get_job(job["id"])
        assert durable_job["status"] == "running"
        assert durable_job["attempts"] == 1
        assert durable_job["lease_owner"]
        assert durable_job["lease_until_ms"] > time.time_ns() // 1_000_000
        claimed_jobs.append(durable_job)
        return await app.state.v2_intelligence_job_handler_impl(job)

    async def scenario() -> dict:
        executor = DurableJobExecutor(
            persistence,
            correction_handler=lambda job: {"job_id": job["id"]},
            suggestion_handler=lambda job: {"job_id": job["id"]},
            additional_handlers={"intelligence": intelligence_handler},
            worker_id="local-reflex-real-executor",
            poll_interval_ms=5,
        )
        try:
            await executor.start()
            executor.wake("intelligence")
            return await _wait_for_v2_job_status(
                persistence,
                intelligence_job_id,
                "succeeded",
            )
        finally:
            await executor.stop()

    try:
        intelligence_job = asyncio.run(scenario())
        assert len(claimed_jobs) == 1
        assert intelligence_job["attempts"] == 1
        assert intelligence_job["output"]["transport_mode"] == "local_reflex"
        assert intelligence_job["output"]["provider_attempt_count"] == 0
        events = persistence.list_events("local-reflex-executor-meeting", limit=1_000)
        applied = [
            event
            for event in events
            if event["type"] == "meeting.intelligence.applied"
        ]
        assert len(applied) == 1
        timing = applied[0]["payload"]["timing"]
        assert timing["valid"] is True
        assert timing["final_to_projection_ms"] == (
            timing["projected_at_ms"] - timing["final_committed_at_ms"]
        )
        assert 0 <= timing["final_to_projection_ms"] < 2_500
        assert timing["job_started_at_ms"] is not None
        assert (
            timing["final_committed_at_ms"]
            <= timing["job_started_at_ms"]
            <= timing["decision_completed_at_ms"]
            <= timing["projected_at_ms"]
        )
        assert provider_lanes.active_realtime_count == 1
        assert not any(
            event["type"].startswith("meeting.realtime_provider.reservation")
            for event in events
        )
        assert persistence.recent_coach_episode_priorities(
            "local-reflex-executor-meeting",
            since_ms=0,
        ) == {}
    finally:
        blocker.release()
        provider_lanes.release_realtime_reservation("blocked-provider-lane")


def test_v2_local_reflex_same_kind_active_card_is_silently_suppressed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    provider_lanes = app.state.provider_lane_registry

    def forbidden_provider_config(_cls):
        raise AssertionError("cooldown suppression must not read Provider configuration")

    async def forbidden_provider_call(**_kwargs):
        raise AssertionError("cooldown suppression must not call a Provider")

    def forbidden_lane_call(*_args, **_kwargs):
        raise AssertionError("cooldown suppression must not enter the Provider lane")

    class ForbiddenPiRuntime:
        async def evaluate(self, _payload):
            raise AssertionError("cooldown suppression must not start Pi")

    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(forbidden_provider_config),
    )
    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_provider_call)
    monkeypatch.setattr(provider_lanes, "reserve_realtime", forbidden_lane_call)
    monkeypatch.setattr(provider_lanes, "try_acquire_realtime", forbidden_lane_call)
    app.state.pi_coach_runtime = ForbiddenPiRuntime()

    apply_calls: list[str] = []
    original_apply = persistence.apply_intelligence_response

    def tracked_apply(**kwargs):
        apply_calls.append(str(kwargs["job_id"]))
        return original_apply(**kwargs)

    monkeypatch.setattr(persistence, "apply_intelligence_response", tracked_apply)
    historical_commit_ms = time.time_ns() // 1_000_000 - 1_000

    def commit_and_claim(suffix: str) -> dict:
        committed = persistence.commit_final_and_enqueue(
            meeting_id="local-reflex-cooldown-meeting",
            final_id=f"local-reflex-cooldown-final-{suffix}",
            segment_id=f"local-reflex-cooldown-segment-{suffix}",
            text="今天先到这里。",
            normalized_text="今天先到这里。",
            started_at_ms=0 if suffix == "one" else 1_100,
            ended_at_ms=1_000 if suffix == "one" else 2_000,
            evidence_hash=f"local-reflex-cooldown-hash-{suffix}",
            source_track="microphone",
            now_ms=historical_commit_ms,
        )
        claimed = persistence.claim_next_job(
            worker_id=f"local-reflex-cooldown-worker-{suffix}",
            lane="intelligence",
            now_ms=time.time_ns() // 1_000_000,
            lease_ms=30_000,
        )
        assert claimed is not None
        assert claimed["id"] == committed["job_ids"]["intelligence"]
        return claimed

    first_job = commit_and_claim("one")
    first_output = asyncio.run(app.state.v2_intelligence_job_handler_impl(first_job))
    assert persistence.complete_job(
        job_id=first_job["id"],
        worker_id="local-reflex-cooldown-worker-one",
        now_ms=time.time_ns() // 1_000_000,
        output=first_output,
    ) is not None
    first_events = persistence.list_events(
        "local-reflex-cooldown-meeting",
        limit=1_000,
    )
    first_applied = [
        event
        for event in first_events
        if event["type"] == "meeting.intelligence.applied"
    ]
    assert len(first_applied) == 1
    first_decision_id = first_applied[0]["payload"]["coach_decision"]["decision_id"]

    second_job = commit_and_claim("two")
    second_output = asyncio.run(app.state.v2_intelligence_job_handler_impl(second_job))
    completed = persistence.complete_job(
        job_id=second_job["id"],
        worker_id="local-reflex-cooldown-worker-two",
        now_ms=time.time_ns() // 1_000_000,
        output=second_output,
    )

    assert completed is not None
    assert completed["status"] == "succeeded"
    assert completed["output"] == second_output
    assert second_output["applied"] is False
    assert second_output["transport_mode"] == "local_reflex_cooldown_suppressed"
    assert second_output["provider_attempt_count"] == 0
    assert second_output["coach"]["triggered"] is False
    assert second_output["coach"]["suppression_reason"] == "same_kind_active"
    assert second_output["semantic"]["status"] == "suppressed_by_active_local_reflex"
    assert apply_calls == [first_job["id"]]
    final_events = persistence.list_events(
        "local-reflex-cooldown-meeting",
        limit=1_000,
    )
    final_applied = [
        event
        for event in final_events
        if event["type"] == "meeting.intelligence.applied"
    ]
    assert len(final_applied) == 1
    assert final_applied[0]["payload"]["coach_decision"]["decision_id"] == first_decision_id
    snapshot = persistence.get_snapshot("local-reflex-cooldown-meeting")
    assert snapshot["coach_decision"]["decision_id"] == first_decision_id
    assert not any(
        event["type"].startswith("meeting.realtime_provider.reservation")
        for event in final_events
    )
    assert persistence.recent_coach_episode_priorities(
        "local-reflex-cooldown-meeting",
        since_ms=0,
    ) == {}
    assert provider_lanes.active_realtime_count == 0
    assert provider_lanes.pending_realtime_count == 0


def test_v2_nonlocal_provider_path_waits_for_lane_and_revalidates_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    provider_lanes = app.state.provider_lane_registry
    blocker = provider_lanes.try_acquire_realtime("provider-revalidation-blocker")
    assert blocker is not None
    provider_config_calls = 0

    def forbidden_provider_config(_cls):
        nonlocal provider_config_calls
        provider_config_calls += 1
        raise AssertionError("stale evidence must fail before Provider configuration")

    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(forbidden_provider_config),
    )
    committed_at_ms = time.time_ns() // 1_000_000
    committed = persistence.commit_final_and_enqueue(
        meeting_id="provider-revalidation-meeting",
        final_id="provider-revalidation-final",
        segment_id="provider-revalidation-segment",
        text="我们继续讨论发布方案。",
        normalized_text="我们继续讨论发布方案。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="provider-revalidation-hash",
        source_track="microphone",
        now_ms=committed_at_ms,
    )
    intelligence_job = persistence.get_job(committed["job_ids"]["intelligence"])

    async def scenario() -> None:
        task = asyncio.create_task(
            app.state.v2_intelligence_job_handler_impl(intelligence_job)
        )
        try:
            await asyncio.sleep(0.05)
            assert not task.done()
            revised = persistence.commit_transcript_revision(
                meeting_id="provider-revalidation-meeting",
                segment_id="provider-revalidation-segment",
                expected_evidence_hash="provider-revalidation-hash",
                corrected_text="我们继续讨论经过修订的发布方案。",
                revision_id="provider-revalidation-revision",
                now_ms=time.time_ns() // 1_000_000,
                causation_id="external-correction",
            )
            assert revised is not None
            blocker.release()
            with pytest.raises(
                IntelligenceEvidenceSuperseded,
                match="evidence changed before execution",
            ):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(scenario())
        assert provider_config_calls == 0
        assert provider_lanes.active_realtime_count == 0
        assert provider_lanes.pending_realtime_count == 0
        replacement_jobs = [
            job
            for job in persistence.list_jobs(
                meeting_id="provider-revalidation-meeting"
            )
            if job["kind"] == "intelligence"
            and job["id"] != intelligence_job["id"]
            and job["status"] == "pending"
        ]
        assert len(replacement_jobs) == 1
        assert replacement_jobs[0]["evidence_hash"] != intelligence_job[
            "evidence_hash"
        ]
    finally:
        blocker.release()
        provider_lanes.release_realtime_reservation(
            "provider-revalidation-blocker"
        )


def test_public_coach_agent_metrics_are_bounded_and_secret_free():
    metrics = app_module._public_coach_agent_metrics(
        {
            "job_queue_latency_ms": 120,
            "provider_timeout_ms": 4_250,
            "correction_lane_active_at_coach_start": True,
            "bridge_process_reused": False,
            "prompt_characters": 512,
            "system_prompt_characters": 1_024,
            "tool_schema_characters": 2_048,
            "request_characters": 3_072,
            "provider_connect_ms": 2_138.4,
            "prompt_profile": "candidate_fast",
            "available_tool_names": ["submit_intervention", "keep_silent"],
            "coach_skill_id": "general",
            "coach_skill_version": 1,
            "response_validation_category": "semantic_safety",
            "response_validation_error": "intervention.evidence_quote is not present",
            "tool_names": ["read_realtime_context"],
            "usage": {"prompt_tokens": 90, "completion_tokens": 20, "total_tokens": 110},
            "timings": {
                "clock": "unix_epoch_ms",
                "started_at_ms": 1_000,
                "completed_at_ms": 1_120,
            },
            "prompt": "must never be persisted",
            "provider_response": "must never be persisted",
            "api_key": "must never be persisted",
        }
    )

    assert metrics["job_queue_latency_ms"] == 120
    assert metrics["provider_timeout_ms"] == 4_250
    assert metrics["system_prompt_characters"] == 1_024
    assert metrics["tool_schema_characters"] == 2_048
    assert metrics["request_characters"] == 3_072
    assert metrics["provider_connect_ms"] == 2_138.4
    assert metrics["prompt_profile"] == "candidate_fast"
    assert metrics["available_tool_names"] == ["submit_intervention", "keep_silent"]
    assert metrics["coach_skill_version"] == 1
    assert metrics["response_validation_category"] == "semantic_safety"
    assert metrics["response_validation_error"] == "intervention.evidence_quote is not present"
    assert metrics["correction_lane_active_at_coach_start"] is True
    assert metrics["usage"] == {"prompt_tokens": 90, "completion_tokens": 20, "total_tokens": 110}
    assert metrics["timings"]["clock"] == "unix_epoch_ms"
    assert "prompt" not in metrics
    assert "provider_response" not in metrics
    assert "api_key" not in metrics


def test_v2_intelligence_skips_pi_when_only_projection_reserve_remains(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def fake_intelligence(**_kwargs):
        now = time.perf_counter()
        return {
            "response": RealtimeIntelligenceResponse((), None, (), None),
            "transport_mode": "test",
            "ttft_ms": 1,
            "timings": {
                "started_at": now,
                "connected_at": now,
                "first_token_at": now,
                "completed_at": now,
            },
            "usage": None,
            "model": "test-model",
        }

    monkeypatch.setattr(app_module, "run_realtime_intelligence", fake_intelligence)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()

    class PiRuntime:
        calls = 0

        async def evaluate(self, _payload):
            self.calls += 1
            raise AssertionError("Pi must not run without a viable provider budget")

    pi_runtime = PiRuntime()
    app.state.pi_coach_runtime = pi_runtime
    finalized_at_ms = time.time_ns() // 1_000_000 - 8_500
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="deadline-budget-meeting",
        final_id="deadline-budget-final",
        segment_id="deadline-budget-segment",
        text="我们一定周五上线。",
        normalized_text="我们一定周五上线。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="deadline-budget-hash",
        source_track="microphone",
        now_ms=finalized_at_ms,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert pi_runtime.calls == 0
    assert output["coach"]["status"] == "timed_out"
    assert output["coach"]["status_reason"] == "deadline_budget_exhausted"
    assert output["coach"]["agent_metrics"]["provider_timeout_ms"] < 1_000
    assert output["applied"]["coach_decision"]["pi_provider_attempted"] is False
    assert app.state.v2_persistence.recent_coach_episode_priorities(
        "deadline-budget-meeting", since_ms=0
    ) == {}


def test_v2_pi_soft_budget_skip_releases_half_open_circuit_permit(
    tmp_path,
    monkeypatch,
):
    """Skipping Pi after admission must not strand a half-open circuit trial."""

    class FakeClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def forbidden_intelligence(**_kwargs):
        raise AssertionError("the Pi priority lane must not start semantic Provider work")

    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_intelligence)
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    monkeypatch.setattr(
        app_module,
        "RealtimeProviderCircuit",
        lambda **_kwargs: circuit,
    )
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()

    class PiRuntime:
        calls = 0

        async def evaluate(self, _payload):
            self.calls += 1
            raise AssertionError("Pi must not run after its soft budget is exhausted")

    pi_runtime = PiRuntime()
    app.state.pi_coach_runtime = pi_runtime
    identity = app_module._realtime_provider_identity(
        app_module.llm_service.realtime_config(config)
    )
    for failure_class in ("timeout", "provider_server"):
        admission = circuit.acquire(identity)
        assert admission.permit is not None
        admission.permit.record_failure(failure_class)
    assert circuit.snapshot(identity).state == "open"

    # The handler's next admission becomes the half-open trial. Its final is
    # already outside the 2.5s product window but remains well within the
    # independent 10s hard deadline.
    clock.advance(15.0)
    finalized_at_ms = time.time_ns() // 1_000_000 - 2_000
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="soft-budget-half-open-meeting",
        final_id="soft-budget-half-open-final",
        segment_id="soft-budget-half-open-segment",
        text="我们一定周五上线。",
        normalized_text="我们一定周五上线。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="soft-budget-half-open-hash",
        source_track="microphone",
        now_ms=finalized_at_ms,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert pi_runtime.calls == 0
    assert output["coach"]["status"] == "timed_out"
    assert output["coach"]["status_reason"] == "soft_deadline_exceeded"
    assert output["coach"]["agent_metrics"]["realtime_circuit_admitted"] is True
    # A release returns the half-open slot to the open circuit and restarts
    # its cooldown. Without it, this would remain half_open forever.
    assert circuit.snapshot(identity).state == "open"
    clock.advance(15.0)
    retry = circuit.acquire(identity)
    assert retry.admitted is True
    assert retry.permit is not None
    assert retry.permit.half_open is True
    retry.permit.release()


def test_v2_intelligence_deadline_budget_exhaustion_persists_through_real_worker(
    tmp_path,
    monkeypatch,
):
    """Exercise the durable worker path for a late Pi candidate.

    The handler must emit an explainable silent decision without touching
    either Provider lane.  The executor then persists that decision exactly as
    it would in production, while the independent correction lane completes.
    """

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    semantic_calls = 0

    async def forbidden_intelligence(**_kwargs):
        nonlocal semantic_calls
        semantic_calls += 1
        raise AssertionError("deadline-exhausted Pi jobs must skip semantic Provider work")

    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_intelligence)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")

    class PiRuntime:
        calls = 0

        async def evaluate(self, _payload):
            self.calls += 1
            raise AssertionError("deadline-exhausted Pi jobs must not invoke the Pi runtime")

    pi_runtime = PiRuntime()
    app.state.pi_coach_runtime = pi_runtime
    correction_calls: list[str] = []

    async def correction_handler(job):
        correction_calls.append(str(job["id"]))
        return {"lane": "correction", "job_id": str(job["id"])}

    app.state.v2_correction_job_handler_impl = correction_handler

    meeting_id = "worker-deadline-budget-meeting"
    now_ms = time.time_ns() // 1_000_000
    # Leave a ~1.7s absolute deadline so the worker can claim and project, but
    # the 750ms projection reserve reduces the coach Provider budget below the
    # 1s minimum.  The timestamp is calculated immediately before enqueueing,
    # avoiding a sleep-based race in the test itself.
    finalized_at_ms = now_ms - 8_300
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="worker-deadline-budget-final",
        segment_id="worker-deadline-budget-segment",
        text="我们一定周五上线。",
        normalized_text="我们一定周五上线。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="worker-deadline-budget-hash",
        source_track="microphone",
        now_ms=finalized_at_ms,
    )
    intelligence_job_id = committed["job_ids"]["intelligence"]
    correction_job_id = committed["job_ids"]["correction"]
    executor = app.state.v2_executor
    assert executor is not None

    async def wait_for_terminal_jobs() -> None:
        deadline = time.perf_counter() + 2.0
        while True:
            intelligence = app.state.v2_persistence.get_job(intelligence_job_id)
            correction = app.state.v2_persistence.get_job(correction_job_id)
            if intelligence["status"] == "succeeded" and correction["status"] == "succeeded":
                return
            if time.perf_counter() >= deadline:
                raise AssertionError(
                    f"worker did not finish jobs: intelligence={intelligence['status']!r}, "
                    f"correction={correction['status']!r}"
                )
            await asyncio.sleep(0.005)

    async def run_worker() -> float:
        started = time.perf_counter()
        try:
            await executor.start()
            await wait_for_terminal_jobs()
            return time.perf_counter() - started
        finally:
            await executor.stop()

    elapsed_s = asyncio.run(run_worker())

    intelligence_job = app.state.v2_persistence.get_job(intelligence_job_id)
    correction_job = app.state.v2_persistence.get_job(correction_job_id)
    output = intelligence_job["output"]
    coach = output["coach"]
    assert intelligence_job["status"] == "succeeded"
    assert correction_job["status"] == "succeeded"
    assert correction_job["output"] == {"lane": "correction", "job_id": correction_job_id}
    assert correction_calls == [correction_job_id]
    assert semantic_calls == 0
    assert pi_runtime.calls == 0
    assert output["semantic"] == {
        "status": "suppressed_by_realtime_deadline",
        "error_class": None,
    }
    timing = intelligence_job["timing"]
    assert timing["valid"] is True
    assert timing["excluded"] is False
    assert timing["final_committed_at_ms"] == finalized_at_ms
    assert timing["job_created_at_ms"] == finalized_at_ms
    assert timing["job_started_at_ms"] >= timing["job_created_at_ms"]
    assert timing["decision_completed_at_ms"] >= timing["job_started_at_ms"]
    assert timing["projected_at_ms"] >= timing["decision_completed_at_ms"]
    assert timing["final_to_projection_ms"] == (
        timing["projected_at_ms"] - timing["final_committed_at_ms"]
    )
    assert coach["status"] == "timed_out"
    assert coach["status_reason"] == "deadline_budget_exhausted"
    assert coach["fallback_error_code"] == "deadline_budget_exhausted"
    assert coach["fallback_reason"] == "deadline_budget_exhausted"
    assert coach["runtime_requested"] == "pi"
    assert coach["runtime_used"] is None
    assert coach["intervention"] is None
    assert coach["agent_metrics"]["deadline_budget_exhausted"] is True
    assert coach["agent_metrics"]["semantic_branch_status"] == "suppressed_by_realtime_deadline"
    assert coach["agent_metrics"]["provider_timeout_ms"] < 1_000
    assert coach["completed_at_ms"] >= coach["created_at_ms"]
    assert coach["completed_at_ms"] - coach["created_at_ms"] < 1_000
    assert elapsed_s < 2.0

    snapshot = app.state.v2_persistence.get_snapshot(meeting_id)
    assert snapshot["jobs"]
    assert {job["kind"]: job["status"] for job in snapshot["jobs"]} == {
        "intelligence": "succeeded",
        "correction": "succeeded",
    }
    # The raw persistence snapshot intentionally keeps formal coach decisions
    # in the append-only event stream; the public route rehydrates that view.
    response = TestClient(app).get(f"/v2/meetings/{meeting_id}/snapshot")
    assert response.status_code == 200
    projected_snapshot = response.json()
    assert projected_snapshot["coach_decision"]["status_reason"] == "deadline_budget_exhausted"
    assert projected_snapshot["coach_decision"]["fallback_error_code"] == "deadline_budget_exhausted"
    assert projected_snapshot["coach_decision"]["runtime_requested"] == "pi"
    assert projected_snapshot["coach_decision"]["runtime_used"] is None

    events = app.state.v2_persistence.list_events(meeting_id)
    applied_events = [event for event in events if event["type"] == "meeting.intelligence.applied"]
    assert applied_events
    applied_payload = applied_events[-1]["payload"]
    assert applied_payload["coach_intervention"] is None
    assert applied_payload["timing"] == timing
    assert applied_payload["coach_decision"]["status_reason"] == "deadline_budget_exhausted"
    assert applied_payload["coach_decision"]["fallback_error_code"] == "deadline_budget_exhausted"
    assert applied_payload["coach_decision"]["runtime_requested"] == "pi"


def test_v2_intelligence_provider_deadline_projects_timeout_instead_of_cancelling_job(
    tmp_path,
    monkeypatch,
):
    """A task-level Pi timeout must leave a durable explainable decision."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def slow_coach(**_kwargs):
        # Longer than the job's remaining window; asyncio.timeout will cancel
        # this coroutine and the handler should project a timeout audit.
        await asyncio.sleep(3.0)

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", slow_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.pi_coach_runtime = object()

    async def correction_handler(job):
        return {"lane": "correction", "job_id": str(job["id"])}

    app.state.v2_correction_job_handler_impl = correction_handler
    now_ms = time.time_ns() // 1_000_000
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="provider-timeout-worker-meeting",
        final_id="provider-timeout-worker-final",
        segment_id="provider-timeout-worker-segment",
        text="我们一定周五上线。",
        normalized_text="我们一定周五上线。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="provider-timeout-worker-hash",
        source_track="microphone",
        # Keep the hard deadline healthy while the deliberately slow fake
        # provider crosses the 2.5s product cutoff.
        now_ms=now_ms,
    )
    intelligence_job_id = committed["job_ids"]["intelligence"]
    executor = app.state.v2_executor
    assert executor is not None

    async def wait_for_terminal() -> None:
        deadline = time.perf_counter() + 4.0
        while True:
            job = app.state.v2_persistence.get_job(intelligence_job_id)
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                return
            if time.perf_counter() >= deadline:
                raise AssertionError(f"timeout worker did not settle: {job['status']!r}")
            await asyncio.sleep(0.01)

    async def run_worker() -> None:
        try:
            await executor.start()
            await wait_for_terminal()
        finally:
            await executor.stop()

    asyncio.run(run_worker())
    job = app.state.v2_persistence.get_job(intelligence_job_id)
    assert job["status"] == "succeeded"
    assert job["output"]["coach"]["status"] == "timed_out"
    assert job["output"]["coach"]["status_reason"] == "soft_deadline_exceeded"
    assert job["output"]["coach"]["fallback_error_code"] == "soft_deadline_exceeded"
    assert job["output"]["coach"]["fallback_reason"] == "soft_deadline_exceeded"
    assert job["output"]["coach"]["delivery_status"] == "too_late"
    assert job["output"]["coach"]["soft_cutoff_triggered"] is True
    assert job["output"]["coach"]["late_result_discarded"] is True
    assert job["output"]["coach"]["intervention"] is None

    snapshot = TestClient(app).get(
        "/v2/meetings/provider-timeout-worker-meeting/snapshot"
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["coach_decision"]["status"] == "timed_out"
    assert snapshot.json()["coach_decision"]["fallback_reason"] == "soft_deadline_exceeded"
    assert snapshot.json()["coach_decision"]["delivery_status"] == "too_late"


def test_v2_pi_soft_timeout_preserves_sanitized_bridge_metrics(
    tmp_path,
    monkeypatch,
):
    """A soft-cutoff projection keeps Pi phase metrics without secrets."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    original_time_ns = app_module.time.time_ns

    async def fake_coach(**kwargs):
        kwargs["before_attempt"](1)
        future_ns = original_time_ns() + (
            app_module.REALTIME_COACH_SOFT_CUTOFF_MS + 1_000
        ) * 1_000_000
        monkeypatch.setattr(app_module.time, "time_ns", lambda: future_ns)
        error = RuntimeError("bounded Pi timeout")
        error.code = "agent_deadline_exceeded"
        error.metrics = {
            "elapsed_ms": 2_241,
            "ttft_ms": None,
            "turns": 1,
            "tool_calls": 0,
            "bridge_startup_ms": 0.4,
            "bridge_round_trip_ms": 2_241.2,
            "prompt_profile": "candidate_fast",
            "system_prompt_characters": 2_100,
            "tool_schema_characters": 640,
            "request_characters": 3_020,
            "available_tool_names": ["submit_intervention", "keep_silent"],
            "api_key": "must-not-persist",
        }
        raise error

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.pi_coach_runtime = object()

    now_ms = original_time_ns() // 1_000_000
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="pi-soft-timeout-metrics",
        final_id="pi-soft-timeout-metrics-final",
        segment_id="pi-soft-timeout-metrics-segment",
        text="我们一定周五上线，但是回滚负责人还没有确定。",
        normalized_text="我们一定周五上线，但是回滚负责人还没有确定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="pi-soft-timeout-metrics-hash",
        source_track="microphone",
        now_ms=now_ms,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    coach = output["coach"]
    metrics = coach["agent_metrics"]
    # The app may deliver a narrow deterministic hint after a Pi timeout, but
    # the provenance must remain an explicit local fallback and the Pi timeout
    # error/metrics must survive the projection.
    assert coach["status"] in {"timed_out", "intervention"}
    assert coach["fallback_reason"] in {"soft_deadline_exceeded", "provider_timeout"}
    assert coach["runtime_used"] in {"pi", "local_reflex"}
    if coach["runtime_used"] == "local_reflex":
        assert coach["intervention"]["origin"] == "local_reflex"
        assert coach["intervention"]["pi_provider_attempted"] is True
    assert metrics["elapsed_ms"] == 2_241
    assert metrics["bridge_startup_ms"] == 0.4
    assert metrics["bridge_round_trip_ms"] == 2_241.2
    assert metrics["prompt_profile"] == "candidate_fast"
    assert metrics["available_tool_names"] == ["submit_intervention", "keep_silent"]
    assert "api_key" not in metrics


def test_v2_pi_realtime_circuit_fast_fails_with_durable_explainable_silence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    intelligence_calls = 0

    async def fake_intelligence(**_kwargs):
        nonlocal intelligence_calls
        intelligence_calls += 1
        raise AssertionError("open realtime circuit must skip all Provider work for this Pi job")

    monkeypatch.setattr(app_module, "run_realtime_intelligence", fake_intelligence)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()

    class PiRuntime:
        calls = 0

        async def evaluate(self, _payload):
            self.calls += 1
            raise AssertionError("open realtime circuit must fail before Pi/provider work")

    pi_runtime = PiRuntime()
    app.state.pi_coach_runtime = pi_runtime
    circuit = app.state.realtime_provider_circuit
    identity = app_module._realtime_provider_identity(
        app_module.llm_service.realtime_config(config)
    )
    admission = circuit.acquire(identity)
    assert admission.permit is not None
    admission.permit.record_failure("rate_limit")

    finalized_at_ms = time.time_ns() // 1_000_000
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="circuit-fast-fail-meeting",
        final_id="circuit-fast-fail-final",
        segment_id="circuit-fast-fail-segment",
        text="我们一定周五上线。",
        normalized_text="我们一定周五上线。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="circuit-fast-fail-hash",
        source_track="microphone",
        now_ms=finalized_at_ms,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert pi_runtime.calls == 0
    assert intelligence_calls == 0
    assert output["semantic"]["status"] == "suppressed_by_realtime_provider_circuit"
    coach = output["coach"]
    assert coach["status"] == "protected_silent"
    assert coach["status_reason"] == "realtime_provider_rate_limit_backoff"
    assert coach["fallback_error_code"] == "realtime_provider_rate_limit_backoff"
    assert coach["fallback_reason"] == "realtime_provider_rate_limit_backoff"
    assert coach["decision_reason"] == (
        "实时 Provider 已返回限流，本轮快速静默，等待限流冷却后再试。"
    )
    assert coach["runtime_requested"] == "pi"
    assert coach["runtime_used"] is None
    assert coach["pi_provider_attempted"] is False
    assert coach["agent_metrics"]["realtime_circuit_state"] == "open"
    assert coach["agent_metrics"]["realtime_circuit_failure_count"] == 1
    assert coach["agent_metrics"]["realtime_circuit_last_failure_class"] == "rate_limit"
    assert coach["agent_metrics"]["realtime_circuit_admitted"] is False
    assert output["provider_availability"] == {
        "policy_version": "shared_realtime_provider.v1",
        "scope": "pi_coach",
        "admitted": False,
        "provider_attempted": False,
        "provider_attempt_count": 0,
        "circuit_scope_bypassed": None,
        "admission_state": "open",
        "admission_reason": "realtime_provider_rate_limit_backoff",
        "terminal_status": "denied",
        "terminal_reason": "realtime_provider_rate_limit_backoff",
        "final_circuit_state": "open",
        "final_failure_count": 1,
        "final_failure_class": "rate_limit",
    }
    assert coach["llm_called"] is False
    assert coach["llm_call_status"] == "not_called"
    assert coach["provider_access_scope"] == "pi_coach"
    assert coach["circuit_scope_bypassed"] is None
    assert coach["durable_terminal_status"] == "denied"
    assert coach["durable_terminal_reason"] == "realtime_provider_rate_limit_backoff"
    assert app.state.v2_persistence.recent_coach_episode_priorities(
        "circuit-fast-fail-meeting",
        since_ms=0,
    ) == {}


def test_v2_pi_realtime_circuit_keeps_high_signal_local_reflex_available(
    tmp_path,
    monkeypatch,
):
    """An open Provider circuit may still surface a clearly bounded local hint."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        app_module,
        "run_realtime_intelligence",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("circuit suppression must not start semantic Provider work")
        ),
    )
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()

    class PiRuntime:
        calls = 0

        async def evaluate(self, _payload):
            self.calls += 1
            raise AssertionError("circuit suppression must not start Pi Provider work")

    pi_runtime = PiRuntime()
    app.state.pi_coach_runtime = pi_runtime
    circuit = app.state.realtime_provider_circuit
    identity = app_module._realtime_provider_identity(config)
    for _ in range(2):
        admission = circuit.acquire(identity)
        assert admission.permit is not None
        admission.permit.record_failure("timeout")
    admission = circuit.acquire(identity)
    assert admission.admitted is False

    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="circuit-local-reflex-meeting",
        final_id="circuit-local-reflex-final",
        segment_id="circuit-local-reflex-segment",
        text="我还缺少",
        normalized_text="我还缺少",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="circuit-local-reflex-hash",
        source_track="microphone",
        now_ms=time.time_ns() // 1_000_000,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    coach = output["coach"]
    assert output["semantic"]["status"] == "suppressed_by_realtime_provider_circuit"
    assert coach["status"] == "intervention"
    assert coach["origin"] == "local_reflex"
    assert coach["runtime_used"] == "local_reflex"
    assert coach["intervention"]["origin"] == "local_reflex"
    assert coach["pi_provider_attempted"] is False
    assert coach["llm_called"] is False
    assert coach["fallback_reason"] in {
        "realtime_provider_recovery_probe_required",
        "realtime_provider_circuit_open",
    }
    assert coach["agent_metrics"]["fallback_after_circuit_suppression"] is True
    assert pi_runtime.calls == 0


def test_direct_semantic_uses_pi_shared_circuit_and_cannot_take_expired_half_open_trial(
    tmp_path,
    monkeypatch,
):
    """A non-candidate final must not bypass known Pi gateway failure state."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )
    # Isolate the semantic-only route regardless of product trigger wording.
    monkeypatch.setattr(
        app_module,
        "should_run_realtime_coach",
        lambda *_args, **_kwargs: False,
    )

    clock = [0.0]
    circuit = RealtimeProviderCircuit(
        failure_threshold=1,
        cooldown_seconds=1.0,
        clock=lambda: clock[0],
    )
    monkeypatch.setattr(
        app_module,
        "RealtimeProviderCircuit",
        lambda **_kwargs: circuit,
    )
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")

    provider_calls = 0

    class ProviderTimeout(RuntimeError):
        category = "timeout"

    async def failing_direct_semantic(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        kwargs["before_attempt"](1)
        raise ProviderTimeout("bounded test timeout")

    monkeypatch.setattr(app_module, "run_realtime_intelligence", failing_direct_semantic)
    now_ms = time.time_ns() // 1_000_000
    first = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="shared-circuit-first-direct",
        final_id="shared-circuit-first-final",
        segment_id="shared-circuit-first-segment",
        text="我听到了。",
        normalized_text="我听到了。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="shared-circuit-first-hash",
        source_track="microphone",
        now_ms=now_ms,
    )
    first_job = app.state.v2_persistence.get_job(first["job_ids"]["intelligence"])

    with pytest.raises(ProviderTimeout):
        asyncio.run(app.state.v2_intelligence_job_handler_impl(first_job))

    assert provider_calls == 1
    assert circuit.snapshot(app_module._realtime_provider_identity(config)).state == "open"

    # The normal cooldown elapsed, but no explicit/short-budget recovery probe
    # has succeeded. Direct semantic must fail fast instead of taking a new
    # ten-second half-open request.
    clock[0] = 2.0

    async def forbidden_direct_semantic(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("direct semantic must not bypass shared Provider recovery")

    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_direct_semantic)
    second = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="shared-circuit-second-direct",
        final_id="shared-circuit-second-final",
        segment_id="shared-circuit-second-segment",
        text="我知道了。",
        normalized_text="我知道了。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="shared-circuit-second-hash",
        source_track="microphone",
        now_ms=time.time_ns() // 1_000_000,
    )
    second_job = app.state.v2_persistence.get_job(second["job_ids"]["intelligence"])

    started = time.perf_counter()
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(second_job))
    elapsed = time.perf_counter() - started

    assert provider_calls == 1
    assert elapsed < 0.25
    assert output["semantic"] == {
        "status": "suppressed_by_realtime_provider_circuit",
        "error_class": None,
    }
    availability = output["provider_availability"]
    assert availability == {
        "policy_version": "shared_realtime_provider.v1",
        "scope": "direct_semantic",
        "admitted": False,
        "provider_attempted": False,
        "provider_attempt_count": 0,
        "circuit_scope_bypassed": None,
        "admission_state": "open",
        "admission_reason": "realtime_provider_recovery_probe_required",
        "terminal_status": "denied",
        "terminal_reason": "realtime_provider_recovery_probe_required",
        "final_circuit_state": "open",
        "final_failure_count": 1,
        "final_failure_class": "timeout",
    }
    coach = output["coach"]
    assert coach["origin"] == "direct_intelligence"
    assert coach["status"] == "protected_silent"
    assert coach["status_reason"] == "realtime_provider_recovery_probe_required"
    assert coach["llm_called"] is False
    assert coach["llm_call_status"] == "not_called"
    assert coach["provider_access_scope"] == "direct_semantic"
    assert coach["circuit_scope_bypassed"] is None
    assert coach["provider_availability"] == availability
    assert coach["durable_terminal_status"] == "denied"
    assert coach["durable_terminal_reason"] == "realtime_provider_recovery_probe_required"
    assert output["formal_event_context"]["llm_called"] is False
    assert output["formal_event_context"]["llm_call_status"] == "not_called"

    applied_events = [
        event
        for event in app.state.v2_persistence.list_events("shared-circuit-second-direct")
        if event["type"] == "meeting.intelligence.applied"
    ]
    assert len(applied_events) == 1
    durable_decision = applied_events[0]["payload"]["coach_decision"]
    assert durable_decision["provider_availability"] == availability
    assert durable_decision["llm_called"] is False
    assert durable_decision["durable_terminal_status"] == "denied"
    assert durable_decision["durable_terminal_reason"] == (
        "realtime_provider_recovery_probe_required"
    )


def test_successful_direct_semantic_closes_shared_circuit_permit(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        app_module,
        "should_run_realtime_coach",
        lambda *_args, **_kwargs: False,
    )

    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    provider_calls = 0

    async def successful_direct_semantic(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        kwargs["before_attempt"](1)
        return {
            "response": RealtimeIntelligenceResponse((), None, (), None),
            "idempotency_key": "direct-semantic-success",
            "transport_mode": "streaming",
            "fallback_reason": None,
            "ttft_ms": 50,
            "repair_ttft_ms": None,
            "provider_attempt_count": 1,
            "repair_attempted": False,
            "timings": {},
            "usage": None,
            "response_id": "direct-semantic-response",
            "model": config.model,
            "finish_reason": "stop",
        }

    monkeypatch.setattr(app_module, "run_realtime_intelligence", successful_direct_semantic)
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="shared-circuit-direct-success",
        final_id="shared-circuit-direct-success-final",
        segment_id="shared-circuit-direct-success-segment",
        text="我知道了。",
        normalized_text="我知道了。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="shared-circuit-direct-success-hash",
        source_track="microphone",
        now_ms=time.time_ns() // 1_000_000,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert provider_calls == 1
    assert output["provider_availability"]["scope"] == "direct_semantic"
    assert output["provider_availability"]["admitted"] is True
    assert output["provider_availability"]["provider_attempted"] is True
    assert output["provider_availability"]["provider_attempt_count"] == 1
    assert output["provider_availability"]["terminal_status"] == "completed"
    assert output["provider_availability"]["terminal_reason"] == "provider_completed"
    assert output["coach"]["llm_called"] is True
    assert output["coach"]["llm_call_status"] == "called"
    snapshot = app.state.realtime_provider_circuit.snapshot(
        app_module._realtime_provider_identity(config)
    )
    assert snapshot.state == "closed"
    assert snapshot.failure_count == 0


def test_v2_pi_timeout_keeps_failure_and_projects_owner_gap_local_reflex(
    tmp_path,
    monkeypatch,
):
    """A timed-out Pi attempt may yield a separately labeled local safety hint."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def slow_coach(**kwargs):
        kwargs["before_attempt"](0)
        await asyncio.sleep(3.0)

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", slow_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.pi_coach_runtime = object()
    now_ms = time.time_ns() // 1_000_000
    meeting_id = "provider-timeout-owner-gap-meeting"
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="provider-timeout-owner-gap-final",
        segment_id="provider-timeout-owner-gap-segment",
        text="下周三上线，但是负责人目前还没有明确，监控指标和回滚方案也没有最终确定。",
        normalized_text="下周三上线，但是负责人目前还没有明确，监控指标和回滚方案也没有最终确定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="provider-timeout-owner-gap-hash",
        source_track="microphone",
        now_ms=now_ms,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    coach = output["coach"]
    assert coach["status"] == "intervention"
    assert coach["runtime_requested"] == "pi"
    assert coach["runtime_used"] == "local_reflex"
    assert coach["fallback_reason"] == "provider_timeout"
    assert coach["pi_provider_attempted"] is True
    assert coach["intervention"]["origin"] == "local_reflex"
    assert coach["intervention"]["local_reflex_kind"] == "missing_next_step"
    assert coach["intervention"]["evidence_quote"]
    assert output["execution_status"] == "runtime_fallback"
    assert output["decision"] == "recommendation"
    assert output["applied"]["coach_decision"]["runtime_used"] == "local_reflex"


def test_v2_pi_response_validation_keeps_failure_and_projects_owner_gap_local_reflex(
    tmp_path,
    monkeypatch,
):
    """A rejected Pi response gets a visible local hint without being mislabeled as Pi."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def rejected_coach(**kwargs):
        kwargs["before_attempt"](0)
        return {
            "intervention": None,
            "status": "failed",
            "status_reason": "IntelligenceResponseValidationError",
            "fallback_error_code": "IntelligenceResponseValidationError",
            "fallback_reason": "IntelligenceResponseValidationError",
            "runtime_requested": "pi",
            "runtime_used": "pi",
            "pi_provider_attempted": True,
            "agent_metrics": {
                "response_validation_category": "semantic_safety",
                "response_validation_error": "deadline_scope",
                "fallback_suppressed": True,
            },
        }

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", rejected_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.pi_coach_runtime = object()
    meeting_id = "provider-validation-owner-gap-meeting"
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="provider-validation-owner-gap-final",
        segment_id="provider-validation-owner-gap-segment",
        text="下周三上线，但是负责人目前还没有明确，监控指标和回滚方案也没有最终确定。",
        normalized_text="下周三上线，但是负责人目前还没有明确，监控指标和回滚方案也没有最终确定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="provider-validation-owner-gap-hash",
        source_track="microphone",
        now_ms=time.time_ns() // 1_000_000,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    coach = output["coach"]
    assert coach["status"] == "intervention"
    assert coach["runtime_requested"] == "pi"
    assert coach["runtime_used"] == "local_reflex"
    assert coach["fallback_reason"] == "response_validation"
    assert coach["fallback_error_code"] == "IntelligenceResponseValidationError"
    assert coach["status_reason"] == "response_validation_local_reflex"
    assert coach["pi_provider_attempted"] is True
    assert coach["agent_metrics"]["fallback_after_response_validation"] is True
    assert coach["agent_metrics"]["response_validation_category"] == "semantic_safety"
    assert coach["intervention"]["origin"] == "local_reflex"
    assert coach["intervention"]["local_reflex_kind"] == "missing_next_step"
    assert output["execution_status"] == "runtime_fallback"
    assert output["applied"]["coach_decision"]["runtime_used"] == "local_reflex"


def test_timeout_local_reflex_can_be_resolved_by_a_later_pi_lifecycle_refresh(
    tmp_path,
    monkeypatch,
):
    """A fallback card remains an auditable episode without becoming Pi success."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    calls = 0

    async def fake_coach(**kwargs):
        nonlocal calls
        calls += 1
        kwargs["before_attempt"](0)
        if calls == 1:
            await asyncio.sleep(3.0)
        request = kwargs["request"]
        paragraph = request.new_paragraphs[0]
        intervention = CoachIntervention(
            event_type="commitment_risk",
            title="补齐发布条件",
            recommendation="先确认回滚负责人和验收条件。",
            reason="当前发布承诺缺少可验证条件。",
            evidence_segment_ids=(paragraph.id,),
            evidence_quote=paragraph.text,
            urgency="high",
            confidence=0.99,
        )
        result = build_realtime_coach_provenance_decision(
            request=request,
            origin="pi",
            status="intervention",
            status_reason="intervention_submitted",
            decision_reason="当前证据已补齐此前事项。",
            intervention=intervention,
        )
        result.update(
            {
                "transport_mode": "pi_agent_jsonl",
                "model": "test-realtime-model",
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "fallback_error_code": None,
                "fallback_reason": None,
                "ttft_ms": 20.0,
                "decision_latency_ms": 40.0,
                "timings": {
                    "clock": "unix_epoch_ms",
                    "started_at_ms": 1_000,
                    "first_token_at_ms": 1_020,
                    "completed_at_ms": 1_040,
                },
                "agent_metrics": {"turns": 1, "tool_calls": 1},
            }
        )
        return result

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.pi_coach_runtime = object()
    # The real card remains under the normal local-reflex cooldown.  Advance
    # that clock for this two-phase lifecycle test so the fresh answer reaches
    # the lifecycle matcher instead of being a duplicate hint suppression.
    monkeypatch.setattr(
        app.state.v2_persistence,
        "active_local_reflex_kind",
        lambda *_args, **_kwargs: None,
    )
    meeting_id = "timeout-local-reflex-lifecycle"
    base_ms = time.time_ns() // 1_000_000

    first = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="timeout-local-reflex-final-1",
        segment_id="timeout-local-reflex-segment-1",
        text="下周三上线，但是负责人还没有明确，监控指标和回滚方案也没有最终确定。",
        normalized_text="下周三上线，但是负责人还没有明确，监控指标和回滚方案也没有最终确定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="timeout-local-reflex-hash-1",
        source_track="microphone",
        now_ms=base_ms,
    )
    first_job = app.state.v2_persistence.get_job(first["job_ids"]["intelligence"])
    first_output = asyncio.run(app.state.v2_intelligence_job_handler_impl(first_job))
    first_decision = first_output["applied"]["coach_decision"]
    assert first_decision["origin"] == "local_reflex"
    assert first_decision["runtime_used"] == "local_reflex"
    assert first_decision["pi_provider_attempted"] is True

    # The first handler intentionally consumes the whole realtime window. A
    # later final must be committed at its real arrival time so it receives a
    # fresh bounded window of its own; carrying the synthetic ``base_ms``
    # forward would make the second job already expired before admission.
    second_now_ms = time.time_ns() // 1_000_000
    second = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="timeout-local-reflex-final-2",
        segment_id="timeout-local-reflex-segment-2",
        text="回滚负责人是王工，今天下午六点前确认监控阈值，安全测试和压测已经通过，问题已经解决。",
        normalized_text="回滚负责人是王工，今天下午六点前确认监控阈值，安全测试和压测已经通过，问题已经解决。",
        started_at_ms=1_100,
        ended_at_ms=2_000,
        evidence_hash="timeout-local-reflex-hash-2",
        source_track="system_audio",
        now_ms=second_now_ms,
    )
    second_job = app.state.v2_persistence.get_job(second["job_ids"]["intelligence"])
    second_output = asyncio.run(app.state.v2_intelligence_job_handler_impl(second_job))
    second_decision = second_output["applied"]["coach_decision"]

    assert calls == 2
    assert second_decision["origin"] == "pi"
    assert second_decision["runtime_used"] == "pi"
    assert second_decision["status"] == "protected_silent"
    assert second_decision["status_reason"] == "lifecycle_resolved"
    assert second_decision["lifecycle_refresh"] is True
    assert second_decision["lifecycle_action"] == "deprioritize"
    assert second_decision["supersedes_decision_id"] == first_decision["decision_id"]


def test_v2_pi_realtime_circuit_suppression_persists_through_real_worker(
    tmp_path,
    monkeypatch,
):
    """Verify circuit-protected silence survives the complete worker path."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    semantic_calls = 0

    async def forbidden_intelligence(**_kwargs):
        nonlocal semantic_calls
        semantic_calls += 1
        raise AssertionError("an open realtime circuit must suppress semantic Provider work")

    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_intelligence)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")

    class PiRuntime:
        calls = 0

        async def evaluate(self, _payload):
            self.calls += 1
            raise AssertionError("an open realtime circuit must suppress Pi work")

    pi_runtime = PiRuntime()
    app.state.pi_coach_runtime = pi_runtime
    correction_calls: list[str] = []

    async def correction_handler(job):
        correction_calls.append(str(job["id"]))
        return {"lane": "correction", "job_id": str(job["id"])}

    app.state.v2_correction_job_handler_impl = correction_handler
    circuit = app.state.realtime_provider_circuit
    identity = app_module._realtime_provider_identity(
        app_module.llm_service.realtime_config(config)
    )
    for failure_class in ("timeout", "provider_server"):
        admission = circuit.acquire(identity)
        assert admission.permit is not None
        admission.permit.record_failure(failure_class)
    assert circuit.snapshot(identity).state == "open"

    meeting_id = "worker-circuit-suppressed-meeting"
    now_ms = time.time_ns() // 1_000_000
    # Make the two-second debounce boundary immediately claimable while
    # retaining a healthy absolute deadline; this isolates circuit suppression
    # from the deadline-budget branch without sleeping in the test.
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="worker-circuit-final",
        segment_id="worker-circuit-segment",
        text="我们一定周五上线。",
        normalized_text="我们一定周五上线。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="worker-circuit-hash",
        source_track="microphone",
        now_ms=now_ms - 2_000,
    )
    intelligence_job_id = committed["job_ids"]["intelligence"]
    correction_job_id = committed["job_ids"]["correction"]
    executor = app.state.v2_executor
    assert executor is not None

    async def wait_for_terminal_jobs() -> None:
        deadline = time.perf_counter() + 2.0
        while True:
            intelligence = app.state.v2_persistence.get_job(intelligence_job_id)
            correction = app.state.v2_persistence.get_job(correction_job_id)
            if intelligence["status"] == "succeeded" and correction["status"] == "succeeded":
                return
            if time.perf_counter() >= deadline:
                raise AssertionError(
                    f"worker did not finish jobs: intelligence={intelligence['status']!r}, "
                    f"correction={correction['status']!r}"
                )
            await asyncio.sleep(0.005)

    async def run_worker() -> float:
        started = time.perf_counter()
        try:
            await executor.start()
            await wait_for_terminal_jobs()
            return time.perf_counter() - started
        finally:
            await executor.stop()

    elapsed_s = asyncio.run(run_worker())
    intelligence_job = app.state.v2_persistence.get_job(intelligence_job_id)
    correction_job = app.state.v2_persistence.get_job(correction_job_id)
    coach = intelligence_job["output"]["coach"]
    assert intelligence_job["status"] == "succeeded"
    assert correction_job["status"] == "succeeded"
    assert correction_calls == [correction_job_id]
    assert semantic_calls == 0
    assert pi_runtime.calls == 0
    assert intelligence_job["output"]["semantic"] == {
        "status": "suppressed_by_realtime_provider_circuit",
        "error_class": None,
    }
    assert coach["status"] == "protected_silent"
    assert coach["status_reason"] == "realtime_provider_circuit_open"
    assert coach["fallback_error_code"] == "realtime_provider_circuit_open"
    assert coach["runtime_requested"] == "pi"
    assert coach["runtime_used"] is None
    assert coach["intervention"] is None
    assert coach["agent_metrics"]["semantic_branch_status"] == "suppressed_by_realtime_provider_circuit"
    assert coach["agent_metrics"]["realtime_circuit_state"] == "open"
    assert coach["agent_metrics"]["realtime_circuit_admitted"] is False
    assert coach["completed_at_ms"] >= coach["created_at_ms"]
    assert coach["completed_at_ms"] - coach["created_at_ms"] < 1_000
    timing = intelligence_job["timing"]
    assert timing["valid"] is True
    assert timing["excluded"] is False
    assert timing["final_committed_at_ms"] == now_ms - 2_000
    assert timing["job_created_at_ms"] == now_ms - 2_000
    assert timing["job_started_at_ms"] >= timing["job_created_at_ms"]
    assert timing["decision_completed_at_ms"] >= timing["job_started_at_ms"]
    assert timing["projected_at_ms"] >= timing["decision_completed_at_ms"]
    assert timing["final_to_projection_ms"] == (
        timing["projected_at_ms"] - timing["final_committed_at_ms"]
    )
    assert elapsed_s < 2.0

    response = TestClient(app).get(f"/v2/meetings/{meeting_id}/snapshot")
    assert response.status_code == 200
    projected_snapshot = response.json()
    assert projected_snapshot["coach_decision"]["status_reason"] == "realtime_provider_circuit_open"
    assert projected_snapshot["coach_decision"]["fallback_error_code"] == "realtime_provider_circuit_open"
    events = app.state.v2_persistence.list_events(meeting_id)
    applied_events = [event for event in events if event["type"] == "meeting.intelligence.applied"]
    assert applied_events
    applied_decision = applied_events[-1]["payload"]["coach_decision"]
    assert applied_events[-1]["payload"]["timing"] == timing
    assert applied_decision["status_reason"] == "realtime_provider_circuit_open"
    assert applied_decision["fallback_error_code"] == "realtime_provider_circuit_open"


def test_manual_provider_probe_success_closes_app_realtime_circuit(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "probe-fast-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    app_module.llm_service.clear_runtime_config()
    app = create_app(data_dir=tmp_path)
    config = app_module.llm_service.LlmConfig.from_env()
    assert config is not None
    identity = app_module._realtime_provider_identity(
        app_module.llm_service.realtime_config(config)
    )
    for failure_class in ("timeout", "transport"):
        admission = app.state.realtime_provider_circuit.acquire(identity)
        assert admission.permit is not None
        admission.permit.record_failure(failure_class)
    assert app.state.realtime_provider_circuit.snapshot(identity).state == "open"

    monkeypatch.setattr(
        app_module.llm_service,
        "probe_gateway",
        lambda probe_config: {
            "operational": True,
            "provider": probe_config.provider_label,
            "model": probe_config.model,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )
    response = TestClient(app).post(
        "/providers/llm/probe",
        headers={"X-Meeting-Copilot-Verification": "1"},
    )

    assert response.status_code == 200
    assert app.state.realtime_provider_circuit.snapshot(identity).state == "closed"


def test_provider_probe_cache_is_bypassed_after_realtime_circuit_failures(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "probe-fast-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    app_module.llm_service.clear_runtime_config()
    app = create_app(data_dir=tmp_path)
    config = app_module.llm_service.LlmConfig.from_env()
    assert config is not None
    identity = app_module._realtime_provider_identity(
        app_module.llm_service.realtime_config(config)
    )
    calls = 0

    def probe(probe_config):
        nonlocal calls
        calls += 1
        return {
            "operational": True,
            "provider": probe_config.provider_label,
            "model": probe_config.model,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(app_module.llm_service, "probe_gateway", probe)
    client = TestClient(app)
    headers = {"X-Meeting-Copilot-Verification": "1"}

    first = client.post("/providers/llm/probe", headers=headers)
    cached = client.post("/providers/llm/probe", headers=headers)
    assert first.status_code == 200
    assert cached.status_code == 200
    assert cached.json()["cached"] is True
    assert calls == 1

    # A failure after the cached success makes that result stale. The next
    # explicit probe must perform a paid request and reset the failure streak.
    admission = app.state.realtime_provider_circuit.acquire(identity)
    assert admission.permit is not None
    admission.permit.record_failure("timeout")
    degraded = app.state.realtime_provider_circuit.snapshot(identity)
    assert degraded.state == "closed"
    assert degraded.failure_count == 1

    after_failure = client.post("/providers/llm/probe", headers=headers)
    assert after_failure.status_code == 200
    assert after_failure.json().get("cached") is not True
    assert calls == 2
    reset = app.state.realtime_provider_circuit.snapshot(identity)
    assert reset.state == "closed"
    assert reset.failure_count == 0
    assert reset.identity_generation > degraded.identity_generation

    for failure_class in ("timeout", "transport"):
        admission = app.state.realtime_provider_circuit.acquire(identity)
        assert admission.permit is not None
        admission.permit.record_failure(failure_class)
    assert app.state.realtime_provider_circuit.snapshot(identity).state == "open"

    after_open = client.post("/providers/llm/probe", headers=headers)
    assert after_open.status_code == 200
    assert after_open.json().get("cached") is not True
    assert calls == 3
    reopened = app.state.realtime_provider_circuit.snapshot(identity)
    assert reopened.state == "closed"
    assert reopened.failure_count == 0


def test_v2_pi_priority_does_not_lose_coach_to_slow_semantic_lane(
    tmp_path,
    monkeypatch,
):
    """A valid Pi card is projected even when semantic extraction is deferred."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )
    semantic_calls = 0

    async def semantic_must_not_run(**_kwargs):
        nonlocal semantic_calls
        semantic_calls += 1
        raise AssertionError("Pi priority must not start the competing semantic request")

    async def fake_pi_coach(**kwargs):
        kwargs["before_attempt"](1)
        request = kwargs["request"]
        paragraph = request.new_paragraphs[0]
        intervention = CoachIntervention(
            event_type="commitment_risk",
            title="补齐发布条件",
            recommendation="先确认负责人和回滚条件。",
            reason="当前承诺还缺少执行边界。",
            evidence_segment_ids=(paragraph.id,),
            evidence_quote=paragraph.text,
            urgency="high",
            confidence=0.99,
        )
        result = build_realtime_coach_provenance_decision(
            request=request,
            origin="pi",
            status="intervention",
            status_reason="intervention_submitted",
            decision_reason="证据足够且现在介入仍有价值。",
            intervention=intervention,
        )
        now_ms = int(time.time() * 1_000)
        result.update(
            {
                "transport_mode": "pi_agent_jsonl",
                "model": "test-realtime-model",
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "fallback_error_code": None,
                "fallback_reason": None,
                "ttft_ms": 25.0,
                "decision_latency_ms": 50.0,
                "timings": {
                    "clock": "unix_epoch_ms",
                    "started_at_ms": now_ms,
                    "first_token_at_ms": now_ms + 25,
                    "completed_at_ms": now_ms + 50,
                },
                "agent_metrics": {"turns": 1, "tool_calls": 1},
            }
        )
        return result

    monkeypatch.setattr(app_module, "run_realtime_intelligence", semantic_must_not_run)
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()

    finalized_at_ms = time.time_ns() // 1_000_000
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="pi-priority-meeting",
        final_id="pi-priority-final",
        segment_id="pi-priority-segment",
        text="我们一定周五上线，但回滚负责人还没定。",
        normalized_text="我们一定周五上线，但回滚负责人还没定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="pi-priority-hash",
        source_track="microphone",
        now_ms=finalized_at_ms,
    )
    job = app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert semantic_calls == 0
    assert output["semantic"]["status"] == app_module.REALTIME_COACH_PI_PRIORITY_SEMANTIC_STATUS
    assert output["coach"]["status"] == "intervention"
    assert output["coach"]["runtime_used"] == "pi"
    assert output["ttft_ms"] == 25.0
    assert output["applied"]["coach_intervention"]["recommendation"] == "先确认负责人和回滚条件。"
    assert output["applied"]["coach_intervention"]["say_this"] == "先确认负责人和回滚条件。"
    assert output["applied"]["coach_intervention"]["why_now"] == "当前承诺还缺少执行边界。"
    assert output["applied"]["follow_up"]["say_this"] == "先确认负责人和回滚条件。"
    assert output["applied"]["follow_up"]["why_now"] == "当前承诺还缺少执行边界。"


def test_v2_user_request_enters_pi_lane_without_fresh_candidate(
    tmp_path,
    monkeypatch,
):
    """An explicit Ask-AI request must not silently downgrade to direct LLM."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )
    semantic_calls = 0
    coach_calls = 0

    async def semantic_must_not_run(**_kwargs):
        nonlocal semantic_calls
        semantic_calls += 1
        raise AssertionError("explicit user requests must use the requested Pi lane")

    async def fake_pi_coach(**kwargs):
        nonlocal coach_calls
        coach_calls += 1
        assert kwargs["priority_mode"] == "deep"
        kwargs["before_attempt"](1)
        request = kwargs["request"]
        paragraph = request.context_paragraphs[-1]
        intervention = CoachIntervention(
            event_type="question_to_user",
            title="补齐发布条件",
            recommendation="我先确认负责人和回滚条件，再给出发布日期。",
            reason="用户明确要求检查发布风险，当前原话仍缺少执行边界。",
            evidence_segment_ids=(paragraph.id,),
            evidence_quote=paragraph.text,
            urgency="high",
            confidence=0.95,
        )
        result = build_realtime_coach_provenance_decision(
            request=request,
            origin="pi",
            status="intervention",
            status_reason="intervention_submitted",
            decision_reason="用户主动请求且证据支持立即澄清。",
            intervention=intervention,
        )
        now_ms = int(time.time() * 1_000)
        result.update(
            {
                "transport_mode": "pi_agent_jsonl",
                "provider_lane": "pi_deep",
                "model": "test-realtime-model",
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "fallback_error_code": None,
                "fallback_reason": None,
                "ttft_ms": 20.0,
                "decision_latency_ms": 35.0,
                "timings": {
                    "clock": "unix_epoch_ms",
                    "started_at_ms": now_ms,
                    "first_token_at_ms": now_ms + 20,
                    "completed_at_ms": now_ms + 35,
                },
                "agent_metrics": {"turns": 1, "tool_calls": 1},
            }
        )
        return result

    monkeypatch.setattr(app_module, "run_realtime_intelligence", semantic_must_not_run)
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()

    finalized_at_ms = time.time_ns() // 1_000_000
    app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id="user-request-pi-meeting",
        final_id="user-request-pi-final",
        segment_id="user-request-pi-segment",
        text="发布前还需要确认负责人和回滚时间。",
        normalized_text="发布前还需要确认负责人和回滚时间。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="user-request-pi-hash",
        source_track="microphone",
        now_ms=finalized_at_ms,
    )
    client = TestClient(app)
    response = client.post(
        "/v2/meetings/user-request-pi-meeting/coach/request",
        headers={"Idempotency-Key": "user-request-pi-1"},
        json={"request": "请基于当前会议检查最重要的发布风险。"},
    )
    assert response.status_code == 202
    job = app.state.v2_persistence.get_job(response.json()["job"]["id"])

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert semantic_calls == 0
    assert coach_calls == 1
    assert output["semantic"]["status"] == app_module.REALTIME_COACH_PI_PRIORITY_SEMANTIC_STATUS
    assert output["coach"]["runtime_used"] == "pi"
    assert output["coach"]["status"] == "intervention"
    assert output["applied"]["coach_intervention"]["recommendation"] == (
        "我先确认负责人和回滚条件，再给出发布日期。"
    )
    decision = output["applied"]["coach_decision"]
    assert decision["pi_provider_attempted"] is True
    assert decision["provider_lane"] == "pi_deep"
    assert decision["eligible_candidate_events"][0]["episode_source_track"] == "microphone"
    assert app.state.v2_persistence.recent_coach_episode_priorities(
        "user-request-pi-meeting", since_ms=0
    ) == {
        decision["eligible_candidate_events"][0]["episode_id"]: decision[
            "eligible_candidate_events"
        ][0]["candidate_priority"]
    }
    reservation_events = [
        event
            for event in app.state.v2_persistence.list_events("user-request-pi-meeting")
        if event["type"].startswith("meeting.realtime_provider.reservation")
    ]
    assert [event["payload"]["status"] for event in reservation_events] == [
        "reserved",
        "committed",
    ]
    assert reservation_events[-1]["payload"]["reason"] == "meeting.intelligence.applied"
    assert app.state.provider_priority_arbiter.active_realtime_count == 0


def test_v2_pi_intervention_is_deprioritized_by_resolving_evidence_and_kept_in_history(
    tmp_path,
    monkeypatch,
):
    """Exercise the production handler twice instead of projecting fixture events."""

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def fake_intelligence(**_kwargs):
        now = time.perf_counter()
        return {
            "response": RealtimeIntelligenceResponse((), None, (), None),
            "transport_mode": "test",
            "ttft_ms": 1.0,
            "timings": {
                "started_at": now,
                "connected_at": now,
                "first_token_at": now,
                "completed_at": now,
            },
            "usage": None,
            "model": "test-realtime-model",
        }

    pi_calls = 0

    async def fake_pi_coach(**kwargs):
        nonlocal pi_calls
        pi_calls += 1
        request = kwargs["request"]
        paragraph = request.new_paragraphs[0]
        intervention = CoachIntervention(
            event_type="commitment_risk",
            title="补齐发布条件",
            recommendation="先确认回滚负责人、压测和安全测试条件。",
            reason="当前发布承诺缺少负责人和可验证条件。",
            evidence_segment_ids=(paragraph.id,),
            evidence_quote=paragraph.text,
            urgency="high",
            confidence=0.99,
        )
        result = build_realtime_coach_provenance_decision(
            request=request,
            origin="pi",
            status="intervention",
            status_reason="intervention_submitted",
            decision_reason="负责人和发布条件仍未闭环。",
            intervention=intervention,
        )
        now_ms = int(time.time() * 1_000)
        result.update(
            {
                "transport_mode": "pi_agent_jsonl",
                "model": "test-realtime-model",
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "fallback_error_code": None,
                "fallback_reason": None,
                "ttft_ms": 20.0,
                "decision_latency_ms": 40.0,
                "timings": {
                    "clock": "unix_epoch_ms",
                    "started_at_ms": now_ms,
                    "first_token_at_ms": now_ms + 20,
                    "completed_at_ms": now_ms + 40,
                },
                "agent_metrics": {"turns": 1, "tool_calls": 1},
            }
        )
        return result

    monkeypatch.setattr(app_module, "run_realtime_intelligence", fake_intelligence)
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_coach)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = object()
    persistence = app.state.v2_persistence
    meeting_id = "pi-lifecycle-production-entry"
    base_ms = time.time_ns() // 1_000_000

    first_commit = persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="pi-lifecycle-final-1",
        segment_id="pi-lifecycle-segment-1",
        text="我们一定周五上线，但回滚负责人还没定。",
        normalized_text="我们一定周五上线，但回滚负责人还没定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash="pi-lifecycle-hash-1",
        source_track="microphone",
        now_ms=base_ms,
    )
    first_job = persistence.claim_next_job(
        worker_id="pi-lifecycle-worker-1",
        lane="intelligence",
        now_ms=base_ms + 2_500,
        lease_ms=30_000,
    )
    assert first_job is not None
    assert first_job["id"] == first_commit["job_ids"]["intelligence"]
    first_output = asyncio.run(app.state.v2_intelligence_job_handler_impl(first_job))
    assert persistence.complete_job(
        job_id=first_job["id"],
        worker_id="pi-lifecycle-worker-1",
        now_ms=base_ms + 2_600,
        output=first_output,
    ) is not None
    first_decision = first_output["applied"]["coach_decision"]
    assert first_decision["origin"] == "pi"
    assert first_decision["status"] == "intervention"
    assert first_decision["lifecycle_action"] == "retain"
    assert first_decision["runtime_requested"] == "pi"
    assert first_decision["runtime_used"] == "pi"
    assert first_decision["fallback_error_code"] is None
    assert first_decision["fallback_reason"] is None

    second_commit = persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="pi-lifecycle-final-2",
        segment_id="pi-lifecycle-segment-2",
        text="回滚负责人是王工，安全测试和压测已经通过。",
        normalized_text="回滚负责人是王工，安全测试和压测已经通过。",
        started_at_ms=1_100,
        ended_at_ms=2_000,
        evidence_hash="pi-lifecycle-hash-2",
        source_track="microphone",
        now_ms=base_ms + 3_000,
    )
    second_job = persistence.claim_next_job(
        worker_id="pi-lifecycle-worker-2",
        lane="intelligence",
        now_ms=base_ms + 5_500,
        lease_ms=30_000,
    )
    assert second_job is not None
    assert second_job["id"] == second_commit["job_ids"]["intelligence"]
    second_output = asyncio.run(app.state.v2_intelligence_job_handler_impl(second_job))
    assert persistence.complete_job(
        job_id=second_job["id"],
        worker_id="pi-lifecycle-worker-2",
        now_ms=base_ms + 5_600,
        output=second_output,
    ) is not None

    assert pi_calls == 1
    second_decision = second_output["applied"]["coach_decision"]
    assert second_decision["status"] == "not_triggered"
    assert second_decision["lifecycle_action"] == "deprioritize"
    assert second_decision["runtime_requested"] == "pi"
    assert second_decision["runtime_used"] is None
    assert second_decision["supersedes_decision_id"] == first_decision["decision_id"]

    formal_events = persistence.list_events(meeting_id)
    assert app_module._latest_formal_coach_follow_up(formal_events) is None
    history = app_module._bounded_formal_coach_history(formal_events)
    assert len(history) == 1
    assert history[0]["decision_id"] == first_decision["decision_id"]
    assert history[0]["lifecycle_action"] == "deprioritize"
    assert history[0]["superseded_by"] == second_decision["decision_id"]
    applied_events = [
        event
        for event in formal_events
        if event["type"] == "meeting.intelligence.applied"
    ]
    assert len(applied_events) == 2
    assert applied_events[0]["payload"]["coach_intervention"]["decision_id"] == first_decision["decision_id"]
    assert applied_events[1]["payload"]["coach_intervention"] is None


def _configure_pi_handler_for_reservation_test(monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-key",
        model="test-model",
        realtime_model="test-realtime-model",
        timeout_seconds=20,
        is_mock=True,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )
    return config


def _reservation_test_coach_result(request, *, runtime_used="pi"):
    paragraph = request.new_paragraphs[0]
    intervention = CoachIntervention(
        event_type="commitment_risk",
        title="补齐发布条件",
        recommendation="先确认负责人和回滚条件。",
        reason="当前发布承诺缺少负责人和可验证条件。",
        evidence_segment_ids=(paragraph.id,),
        evidence_quote=paragraph.text,
        urgency="high",
        confidence=0.99,
    )
    origin = "pi" if runtime_used == "pi" else "direct_fallback"
    result = build_realtime_coach_provenance_decision(
        request=request,
        origin=origin,
        status="intervention",
        status_reason="intervention_submitted",
        decision_reason="证据足够且现在介入仍有价值。",
        intervention=intervention,
    )
    now_ms = int(time.time() * 1_000)
    result.update(
        {
            "transport_mode": "test",
            "model": "test-realtime-model",
            "runtime_requested": "pi",
            "runtime_used": runtime_used,
            "fallback_error_code": "pi_unavailable" if runtime_used == "direct" else None,
            "fallback_reason": "runtime_unavailable" if runtime_used == "direct" else None,
            "ttft_ms": 25.0,
            "decision_latency_ms": 50.0,
            "timings": {
                "clock": "unix_epoch_ms",
                "started_at_ms": now_ms,
                "first_token_at_ms": now_ms + 25,
                "completed_at_ms": now_ms + 50,
            },
            "agent_metrics": {
                "turns": 0 if runtime_used == "direct" else 1,
                "tool_calls": 0,
            },
        }
    )
    return result


def _reservation_test_job(app, meeting_id: str):
    now_ms = time.time_ns() // 1_000_000
    committed = app.state.v2_persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id=f"{meeting_id}-final",
        segment_id=f"{meeting_id}-segment",
        text="我们一定周五上线，但回滚负责人还没定。",
        normalized_text="我们一定周五上线，但回滚负责人还没定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash=f"{meeting_id}-hash",
        source_track="microphone",
        now_ms=now_ms,
    )
    return app.state.v2_persistence.get_job(committed["job_ids"]["intelligence"])


def test_pi_reservation_released_for_direct_fallback_before_provider_attempt(
    tmp_path,
    monkeypatch,
):
    _configure_pi_handler_for_reservation_test(monkeypatch)

    async def forbidden_semantic(**_kwargs):
        raise AssertionError("semantic lane must remain deferred for a coach candidate")

    async def direct_fallback(**kwargs):
        return _reservation_test_coach_result(kwargs["request"], runtime_used="direct")

    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_semantic)
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", direct_fallback)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    meeting_id = "pi-reservation-direct-fallback"
    job = _reservation_test_job(app, meeting_id)

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert output["coach"]["runtime_used"] == "direct"
    assert output["coach"]["pi_provider_attempted"] is False
    events = app.state.v2_persistence.list_events(meeting_id)
    reservation_events = [
        event
        for event in events
        if event["type"].startswith("meeting.realtime_provider.reservation")
    ]
    assert [event["payload"]["status"] for event in reservation_events] == [
        "reserved",
        "released",
    ]
    assert reservation_events[-1]["payload"]["reason"] == "direct_fallback"
    assert app.state.v2_persistence.recent_coach_episode_priorities(
        meeting_id, since_ms=0
    ) == {}


def test_pi_reservation_is_retained_when_projection_fails_after_provider_attempt(
    tmp_path,
    monkeypatch,
):
    _configure_pi_handler_for_reservation_test(monkeypatch)

    async def forbidden_semantic(**_kwargs):
        raise AssertionError("semantic lane must remain deferred for a coach candidate")

    async def fake_pi(**kwargs):
        kwargs["before_attempt"](1)
        return _reservation_test_coach_result(kwargs["request"])

    monkeypatch.setattr(app_module, "run_realtime_intelligence", forbidden_semantic)
    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    meeting_id = "pi-reservation-projection-failure"
    job = _reservation_test_job(app, meeting_id)

    def fail_projection(**_kwargs):
        raise RuntimeError("projection write failed")

    monkeypatch.setattr(persistence, "apply_intelligence_response", fail_projection)
    with pytest.raises(RuntimeError, match="projection write failed"):
        asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    events = persistence.list_events(meeting_id)
    reservation_events = [
        event
        for event in events
        if event["type"].startswith("meeting.realtime_provider.reservation")
    ]
    assert [event["payload"]["status"] for event in reservation_events] == ["reserved"]
    now_ms = time.time_ns() // 1_000_000
    priorities = persistence.recent_coach_episode_priorities(
        meeting_id,
        since_ms=max(0, now_ms - app_module.REALTIME_COACH_RESERVATION_TTL_MS),
        now_ms=now_ms,
    )
    assert len(priorities) == 1

    # A failed projection intentionally leaves the bounded crash-protection
    # lease open; clean it after asserting that invariant in this temp DB.
    reservation_id = reservation_events[0]["payload"]["reservation_id"]
    persistence.finish_realtime_provider_attempt(
        meeting_id=meeting_id,
        reservation_id=reservation_id,
        status="released",
        reason="test_cleanup",
        finished_at_ms=now_ms,
    )
    assert persistence.recent_coach_episode_priorities(
        meeting_id, since_ms=0, now_ms=now_ms
    ) == {}


def _formal_projection_event(
    *,
    seq: int,
    event_type: str,
    projection: dict | None,
    occurred_at_ms: int | None = None,
) -> dict:
    projection_keys = {
        "meeting.intelligence.applied": "follow_up",
        "meeting.topic.updated": "topic",
        "meeting.decision.updated": "decision",
        "meeting.open_question.updated": "question",
    }
    payload = {
        "source": "llm_first",
        "job_id": f"job-{seq}",
        "batch_id": f"batch-{seq}",
        "provider": "test-provider",
        "model": "test-model",
        "llm_called": True,
        "evidence": {"segment_ids": [f"segment-{seq}"], "quote": f"quote-{seq}"},
        projection_keys[event_type]: projection,
    }
    if event_type == "meeting.topic.updated" and projection is not None:
        payload["summary"] = projection.get("summary")
    return {
        "seq": seq,
        "event_id": f"event-{seq}",
        "type": event_type,
        "aggregate_id": f"aggregate-{seq}",
        "occurred_at_ms": occurred_at_ms or seq * 1_000,
        "payload": payload,
    }


def test_coach_history_retains_advice_across_silent_rounds_and_deduplicates():
    first = {
        "question": "建议先说清楚验收标准。",
        "reason": "标准还没有被明确。",
        "urgency": "medium",
        "coach_event_type": "communication_clarity",
    }
    repeated = {**first, "reason": "新一轮仍然没有明确标准。"}
    second = {
        "question": "建议确认由谁负责回滚。",
        "reason": "回滚负责人尚未确认。",
        "urgency": "high",
        "coach_event_type": "commitment_risk",
    }
    events = [
        _formal_projection_event(seq=1, event_type="meeting.intelligence.applied", projection=first),
        _formal_projection_event(seq=2, event_type="meeting.intelligence.applied", projection=None),
        _formal_projection_event(seq=3, event_type="meeting.intelligence.applied", projection=repeated),
        _formal_projection_event(seq=4, event_type="meeting.intelligence.applied", projection=second),
        _formal_projection_event(seq=5, event_type="meeting.intelligence.applied", projection=None),
    ]

    history = app_module._bounded_formal_coach_history(events)

    assert [item["question"] for item in history] == [first["question"], second["question"]]
    assert history[0]["reason"] == repeated["reason"]
    assert history[0]["history_id"] == "event-3"
    assert history[-1]["formal_evidence"]["segment_ids"] == ["segment-4"]
    # The newest silent decision clears the current card; history remains
    # available for review without reviving the stale recommendation.
    assert app_module._latest_formal_coach_follow_up(events) is None


def test_coach_runtime_history_preserves_pi_failures_without_reviving_cards():
    timeout = _formal_projection_event(
        seq=1,
        event_type="meeting.intelligence.applied",
        projection=None,
    )
    timeout["payload"]["coach_decision"] = {
        "decision_id": "coach-timeout-1",
        "status": "timed_out",
        "status_reason": "provider_timeout",
        "origin": "pi",
        "runtime_requested": "pi",
        "runtime_used": "pi",
        "pi_provider_attempted": True,
        "llm_called": True,
        "llm_call_status": "called",
        "job_id": "job-timeout-1",
        "created_at_ms": 1_000,
        "completed_at_ms": 3_000,
    }
    fallback = _formal_projection_event(
        seq=2,
        event_type="meeting.intelligence.applied",
        projection=None,
    )
    fallback["payload"]["coach_decision"] = {
        "decision_id": "coach-local-1",
        "status": "intervention",
        "status_reason": "intervention_submitted",
        "origin": "local_reflex",
        "runtime_requested": "pi",
        "runtime_used": "local_reflex",
        "pi_provider_attempted": False,
        "llm_called": False,
        "llm_call_status": "not_called",
        "created_at_ms": 4_000,
    }

    history = app_module._bounded_coach_runtime_history([timeout, fallback])

    assert history[0]["outcome"] == "failure"
    assert history[0]["status_reason"] == "provider_timeout"
    assert history[0]["origin"] == "pi"
    assert history[1]["outcome"] == "local_reflex_fallback"
    assert history[1]["pi_provider_attempted"] is False
    assert app_module._bounded_formal_coach_history([timeout, fallback]) == []


@pytest.mark.parametrize(
    ("replacement_status", "replacement_action", "expected_history_action"),
    [
        ("intervention", "retain", "deprioritize"),
        ("stale", "retract", "retract"),
    ],
)
def test_coach_history_projects_append_only_supersession_links(
    replacement_status,
    replacement_action,
    expected_history_action,
):
    first = _formal_projection_event(
        seq=1,
        event_type="meeting.intelligence.applied",
        projection=None,
    )
    first["payload"].update(
        {
            "coach_intervention": {
                "recommendation": "先确认回滚负责人。",
                "reason": "当前承诺缺少负责人。",
                "evidence_segment_ids": ["segment-1"],
                "evidence_quote": "周五上线",
                "urgency": "high",
                "event_type": "commitment_risk",
                "decision_id": "coach-decision-1",
                "status": "intervention",
                "lifecycle_action": "retain",
            },
            "coach_decision": {
                "decision_id": "coach-decision-1",
                "status": "intervention",
                "lifecycle_action": "retain",
            },
        }
    )
    replacement = _formal_projection_event(
        seq=2,
        event_type="meeting.intelligence.applied",
        projection=None,
    )
    replacement["payload"]["coach_decision"] = {
        "decision_id": "coach-decision-2",
        "status": replacement_status,
        "lifecycle_action": replacement_action,
        "supersedes_decision_id": "coach-decision-1",
    }

    history = app_module._bounded_formal_coach_history([first, replacement])

    assert history[0]["decision_id"] == "coach-decision-1"
    assert history[0]["superseded_by"] == "coach-decision-2"
    assert history[0]["lifecycle_action"] == expected_history_action


def test_coach_history_marks_lifecycle_resolution_separately_from_supersession():
    first = _formal_projection_event(seq=1, event_type="meeting.intelligence.applied", projection=None)
    first["payload"].update({
        "coach_intervention": {
            "recommendation": "先确认回滚负责人。", "reason": "当前承诺缺少负责人。",
            "evidence_segment_ids": ["segment-1"], "evidence_quote": "周五上线",
            "urgency": "high", "event_type": "commitment_risk", "decision_id": "coach-1",
        },
        "coach_decision": {"decision_id": "coach-1", "status": "intervention", "lifecycle_action": "retain"},
    })
    resolved = _formal_projection_event(seq=2, event_type="meeting.intelligence.applied", projection=None)
    resolved["payload"]["coach_decision"] = {
        "decision_id": "coach-2", "status": "protected_silent", "lifecycle_action": "deprioritize",
        "status_reason": "lifecycle_resolved", "supersedes_decision_id": "coach-1",
    }
    history = app_module._bounded_formal_coach_history([first, resolved])
    assert history[0]["lifecycle_action"] == "deprioritize"
    assert history[0]["lifecycle_status"] == "resolved"


def test_coach_due_work_items_are_rebuilt_from_retained_persistent_history():
    event = _formal_projection_event(seq=1, event_type="meeting.intelligence.applied", projection=None)
    event["payload"].update({
        "coach_intervention": {
            "recommendation": "先确认回滚负责人。", "reason": "当前承诺缺少负责人。",
            "evidence_segment_ids": ["segment-1"], "evidence_quote": "周五上线",
            "urgency": "high", "event_type": "commitment_risk", "decision_id": "coach-due",
            "valid_until_ms": 1_000,
        },
        "coach_decision": {
            "decision_id": "coach-due", "status": "intervention", "lifecycle_action": "retain",
        },
    })
    assert app_module._coach_due_work_items([event], now_ms=1_001) == [{
        "item_id": "coach-due", "status": "due", "next_check_at_ms": 1_000,
        "coach_event_type": "commitment_risk", "title": None,
        "evidence_segment_ids": ["segment-1"], "evidence_quote": "周五上线",
        "created_at_ms": 1_000,
    }]
    assert app_module._coach_due_work_items([event], now_ms=999) == []


def test_coach_history_keeps_same_wording_when_decision_ids_differ_for_supersession():
    first = _formal_projection_event(
        seq=1,
        event_type="meeting.intelligence.applied",
        projection=None,
    )
    first["payload"].update(
        {
            "coach_intervention": {
                "recommendation": "先确认回滚负责人。",
                "reason": "当前承诺缺少负责人。",
                "evidence_segment_ids": ["segment-1"],
                "evidence_quote": "周五上线",
                "urgency": "high",
                "event_type": "commitment_risk",
                "decision_id": "coach-decision-1",
                "status": "intervention",
            },
            "coach_decision": {
                "decision_id": "coach-decision-1",
                "status": "intervention",
                "lifecycle_action": "retain",
            },
        }
    )
    replacement = _formal_projection_event(
        seq=2,
        event_type="meeting.intelligence.applied",
        projection=None,
    )
    replacement["payload"].update(
        {
            "coach_intervention": {
                "recommendation": "先确认回滚负责人。",
                "reason": "负责人仍需要再次确认。",
                "evidence_segment_ids": ["segment-2"],
                "evidence_quote": "回滚负责人",
                "urgency": "high",
                "event_type": "commitment_risk",
                "decision_id": "coach-decision-2",
                "status": "intervention",
            },
            "coach_decision": {
                "decision_id": "coach-decision-2",
                "status": "intervention",
                "lifecycle_action": "retain",
                "supersedes_decision_id": "coach-decision-1",
            },
        }
    )

    history = app_module._bounded_formal_coach_history([first, replacement])

    assert [item["decision_id"] for item in history] == [
        "coach-decision-1",
        "coach-decision-2",
    ]
    assert history[0]["superseded_by"] == "coach-decision-2"
    assert history[0]["lifecycle_action"] == "deprioritize"


def test_ended_coach_projection_clears_current_state_and_retains_history():
    history = [{"decision_id": "coach-decision-1", "question": "确认负责人"}]
    projected = app_module._clear_ended_coach_projection(
        {
            "follow_up": {"question": "确认负责人"},
            "coach_intervention": {"question": "确认负责人"},
            "semantic_follow_up": {"question": "负责人是谁？"},
            "coach_decision": {"decision_id": "coach-decision-1", "status": "intervention"},
            "coach_history": history,
            "runtime": {
                "phase": "ended",
                "ai": {
                    "state": "active",
                    "capabilities": {
                        "proactive_suggestions": {
                            "state": "active",
                            "label": "Pi 教练已分析正文",
                        }
                    },
                },
            },
        }
    )

    assert projected["follow_up"] is None
    assert projected["coach_intervention"] is None
    assert projected["semantic_follow_up"] is None
    assert projected["coach_decision"] is None
    assert projected["coach_history"] == history
    assert projected["runtime"]["ai"]["capabilities"]["proactive_suggestions"] == {
        "state": "idle",
        "label": "Pi 教练已结束",
        "detail": "会中建议已停止，历史建议保留在会议记录中",
        "decision": None,
    }


def test_live_coach_projection_is_not_cleared():
    source = {
        "follow_up": {"question": "确认负责人"},
        "coach_decision": {"status": "intervention"},
        "runtime": {"phase": "live"},
    }

    assert app_module._clear_ended_coach_projection(source) == source


def test_partition_coach_candidates_preserves_full_cooldown_audit():
    request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-episode",
        state_revision=1,
        context_paragraphs=[
            {
                "id": "segment-anchor",
                "text": "我们先把背景说完整。",
                "revision": 1,
                "start_ms": 0,
                "end_ms": 1_000,
                "speaker": "Alice",
                "source_track": "microphone",
            }
        ],
        new_paragraphs=[
            {
                "id": "segment-1",
                "text": "不过周五一定上线吗？",
                "revision": 1,
                "start_ms": 1_100,
                "end_ms": 2_000,
                "speaker": "Alice",
                "source_track": "microphone",
            },
            {
                "id": "system-segment",
                "text": "另一个轨道的问题。",
                "revision": 1,
                "start_ms": 1_200,
                "end_ms": 1_900,
                "speaker": "Bob",
                "source_track": "system_audio",
            },
            {
                "id": "other-speaker-segment",
                "text": "同一轨道换人后的问题。",
                "revision": 1,
                "start_ms": 2_100,
                "end_ms": 2_900,
                "speaker": "Bob",
                "source_track": "microphone",
            },
        ],
        rolling_state={},
    )
    detected = (
        app_module.CoachCandidateEvent(
            event_type="question_pending",
            evidence_segment_ids=("segment-1",),
            reason="question",
            candidate_key="candidate-question",
        ),
        app_module.CoachCandidateEvent(
            event_type="commitment_without_condition",
            evidence_segment_ids=("segment-1",),
            reason="commitment",
            candidate_key="candidate-commitment",
        ),
        app_module.CoachCandidateEvent(
            event_type="question_pending",
            evidence_segment_ids=("system-segment",),
            reason="other track question",
            candidate_key="candidate-other-track",
        ),
        app_module.CoachCandidateEvent(
            event_type="question_pending",
            evidence_segment_ids=("other-speaker-segment",),
            reason="other speaker question",
            candidate_key="candidate-other-speaker",
        ),
    )
    payloads = app_module._coach_candidate_payloads(detected, request=request)
    microphone_episode = payloads["candidate-question"]["episode_id"]
    other_track_episode = payloads["candidate-other-track"]["episode_id"]
    other_speaker_episode = payloads["candidate-other-speaker"]["episode_id"]

    assert payloads["candidate-question"]["episode_anchor_id"] == "segment-anchor"
    assert payloads["candidate-question"]["episode_source_track"] == "microphone"
    assert payloads["candidate-question"]["episode_speaker"] == "Alice"
    assert payloads["candidate-question"]["candidate_priority"] == 100
    assert payloads["candidate-commitment"]["episode_id"] == microphone_episode
    assert other_track_episode != microphone_episode
    assert other_speaker_episode not in {microphone_episode, other_track_episode}

    eligible, suppressed = app_module._partition_coach_candidates(
        detected,
        candidate_payloads=payloads,
        cooled_episode_priorities={microphone_episode: 95},
    )

    assert [item.candidate_key for item in detected] == [
        "candidate-question",
        "candidate-commitment",
        "candidate-other-track",
        "candidate-other-speaker",
    ]
    assert [item.candidate_key for item in eligible] == [
        "candidate-question",
        "candidate-other-track",
        "candidate-other-speaker",
    ]
    assert suppressed == ("candidate-commitment",)

    upgraded_eligible, upgraded_suppressed = app_module._partition_coach_candidates(
        detected,
        candidate_payloads=payloads,
        cooled_episode_priorities={microphone_episode: 100},
    )

    assert [item.candidate_key for item in upgraded_eligible] == [
        "candidate-other-track",
        "candidate-other-speaker",
    ]
    assert upgraded_suppressed == (
        "candidate-question",
        "candidate-commitment",
    )


def test_coach_episode_anchor_resets_after_a_real_pause():
    request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-paused-episode",
        state_revision=1,
        context_paragraphs=[
            {
                "id": "earlier-segment",
                "text": "前一个话题已经说完。",
                "revision": 1,
                "start_ms": 0,
                "end_ms": 1_000,
                "speaker": "Alice",
                "source_track": "microphone",
            }
        ],
        new_paragraphs=[
            {
                "id": "later-segment",
                "text": "十秒后开始另一个问题吗？",
                "revision": 1,
                "start_ms": 10_000,
                "end_ms": 11_000,
                "speaker": "Alice",
                "source_track": "microphone",
            }
        ],
        rolling_state={},
    )
    candidate = app_module.CoachCandidateEvent(
        event_type="question_pending",
        evidence_segment_ids=("later-segment",),
        reason="new question",
        candidate_key="candidate-after-pause",
    )

    payload = app_module._coach_candidate_payloads((candidate,), request=request)[
        candidate.candidate_key
    ]

    assert payload["episode_anchor_id"] == "later-segment"


def test_coach_episode_identity_stays_stable_with_cross_batch_retrieval_evidence():
    prior = {
        "id": "retrieval-anchor",
        "text": "我继续补充背景，结论稍后再说。",
        "revision": 1,
        "start_ms": 0,
        "end_ms": 20_000,
        "speaker": "Alice",
        "source_track": "microphone",
    }
    middle = {
        "id": "retrieval-middle",
        "text": "现在仍然没有收束。",
        "revision": 1,
        "start_ms": 20_100,
        "end_ms": 40_000,
        "speaker": "Alice",
        "source_track": "microphone",
    }
    latest = {
        "id": "fresh-latest",
        "text": "我还要补充一些背景，暂时不下结论。",
        "revision": 1,
        "start_ms": 40_100,
        "end_ms": 60_000,
        "speaker": "Alice",
        "source_track": "microphone",
    }
    first_request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-retrieval-episode",
        state_revision=2,
        retrieval_paragraphs=[prior],
        context_paragraphs=[],
        new_paragraphs=[middle],
        rolling_state={},
    )
    second_request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-retrieval-episode",
        state_revision=3,
        retrieval_paragraphs=[prior, middle],
        context_paragraphs=[],
        new_paragraphs=[latest],
        rolling_state={},
    )
    first_candidate = app_module.CoachCandidateEvent(
        event_type="objection_detected",
        evidence_segment_ids=("retrieval-anchor", "retrieval-middle"),
        reason="first clarity check",
        candidate_key="candidate-first-clarity",
    )
    second_candidate = app_module.CoachCandidateEvent(
        event_type="monologue_duration",
        evidence_segment_ids=("retrieval-anchor", "fresh-latest"),
        reason="next clarity check",
        candidate_key="candidate-next-clarity",
    )

    first_payload = app_module._coach_candidate_payloads(
        (first_candidate,),
        request=first_request,
    )[first_candidate.candidate_key]
    second_payload = app_module._coach_candidate_payloads(
        (second_candidate,),
        request=second_request,
    )[second_candidate.candidate_key]

    assert first_payload["episode_anchor_id"] == "retrieval-anchor"
    assert second_payload["episode_anchor_id"] == "retrieval-anchor"
    assert second_payload["episode_id"] == first_payload["episode_id"]


def test_coach_episode_identity_inherits_after_context_window_rolls_out():
    first_request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-rolled-episode",
        state_revision=1,
        context_paragraphs=[],
        new_paragraphs=[
            {
                "id": "rolled-anchor",
                "text": "我先补充背景。",
                "revision": 1,
                "start_ms": 0,
                "end_ms": 4_000,
                "speaker": "Alice",
                "source_track": "microphone",
            }
        ],
        rolling_state={},
    )
    second_request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-rolled-episode",
        state_revision=2,
        context_paragraphs=[],
        retrieval_paragraphs=[],
        new_paragraphs=[
            {
                "id": "rolled-fresh",
                "text": "我继续补充，但暂时还没有结论。",
                "revision": 1,
                "start_ms": 4_100,
                "end_ms": 8_000,
                "speaker": "Alice",
                "source_track": "microphone",
            }
        ],
        rolling_state={},
    )
    first_candidate = app_module.CoachCandidateEvent(
        event_type="monologue_duration",
        evidence_segment_ids=("rolled-anchor",),
        reason="first",
        candidate_key="rolled-first",
    )
    second_candidate = app_module.CoachCandidateEvent(
        event_type="monologue_duration",
        evidence_segment_ids=("rolled-fresh",),
        reason="second",
        candidate_key="rolled-second",
    )
    first_payload = app_module._coach_candidate_payloads(
        (first_candidate,),
        request=first_request,
    )[first_candidate.candidate_key]
    second_payload = app_module._coach_candidate_payloads(
        (second_candidate,),
        request=second_request,
        recent_episode_descriptors=[first_payload],
    )[second_candidate.candidate_key]

    assert second_payload["episode_id"] == first_payload["episode_id"]
    assert second_payload["episode_anchor_id"] == "rolled-anchor"
    eligible, suppressed = app_module._partition_coach_candidates(
        (second_candidate,),
        candidate_payloads={second_candidate.candidate_key: second_payload},
        cooled_episode_priorities={
            first_payload["episode_id"]: first_payload["candidate_priority"]
        },
    )
    assert eligible == ()
    assert suppressed == (second_candidate.candidate_key,)


def test_coach_episode_inheritance_requires_known_same_speaker_track_and_gap():
    recent = {
        "episode_id": "coach-episode:prior",
        "episode_anchor_id": "prior-anchor",
        "episode_source_track": "microphone",
        "episode_speaker": "Alice",
        "episode_speaker_key": app_module.hashlib.sha256(b"alice").hexdigest()[:24],
        "episode_anchor_start_ms": 0,
        "episode_anchor_end_ms": 1_000,
        "episode_latest_start_ms": 0,
        "episode_latest_end_ms": 1_000,
    }

    def payload(*, speaker, track="microphone", start_ms=1_100):
        request = app_module.RealtimeIntelligenceRequest.from_payload(
            meeting_id="meeting-boundary",
            state_revision=2,
            context_paragraphs=[],
            new_paragraphs=[
                {
                    "id": f"fresh-{speaker}-{track}-{start_ms}",
                    "text": "新的候选。",
                    "revision": 1,
                    "start_ms": start_ms,
                    "end_ms": start_ms + 500,
                    "speaker": speaker,
                    "source_track": track,
                }
            ],
            rolling_state={},
        )
        candidate = app_module.CoachCandidateEvent(
            event_type="objection_detected",
            evidence_segment_ids=(request.new_paragraphs[0].id,),
            reason="boundary",
            candidate_key=f"candidate-{speaker}-{track}-{start_ms}",
        )
        return app_module._coach_candidate_payloads(
            (candidate,),
            request=request,
            recent_episode_descriptors=[recent],
        )[candidate.candidate_key]

    assert payload(speaker="Alice")["episode_id"] == recent["episode_id"]
    assert payload(speaker="Bob")["episode_id"] != recent["episode_id"]
    assert payload(speaker="Alice", track="system_audio")["episode_id"] != recent["episode_id"]
    assert payload(speaker="Alice", start_ms=9_001)["episode_id"] != recent["episode_id"]
    assert payload(speaker=None)["episode_id"] != recent["episode_id"]


def test_coach_episode_priority_upgrade_still_crosses_candidate_types():
    candidate = app_module.CoachCandidateEvent(
        event_type="question_pending",
        evidence_segment_ids=("fresh",),
        reason="higher priority",
        candidate_key="higher-priority",
    )
    payload = {
        "episode_id": "coach-episode:shared",
        "candidate_priority": 100,
    }
    eligible, suppressed = app_module._partition_coach_candidates(
        (candidate,),
        candidate_payloads={candidate.candidate_key: payload},
        cooled_episode_priorities={"coach-episode:shared": 90},
    )

    assert eligible == (candidate,)
    assert suppressed == ()


def test_coach_episode_descriptor_does_not_bridge_a_pause_in_one_request():
    request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-descriptor-pause",
        state_revision=1,
        context_paragraphs=[],
        new_paragraphs=[
            {
                "id": "before-pause",
                "text": "第一个表达。",
                "revision": 1,
                "start_ms": 0,
                "end_ms": 1_000,
                "speaker": "Alice",
                "source_track": "microphone",
            },
            {
                "id": "after-pause",
                "text": "停顿后另一个表达。",
                "revision": 1,
                "start_ms": 10_000,
                "end_ms": 11_000,
                "speaker": "Alice",
                "source_track": "microphone",
            },
        ],
        rolling_state={},
    )
    candidate = app_module.CoachCandidateEvent(
        event_type="objection_detected",
        evidence_segment_ids=("before-pause",),
        reason="before pause",
        candidate_key="candidate-before-pause",
    )

    payload = app_module._coach_candidate_payloads(
        (candidate,),
        request=request,
    )[candidate.candidate_key]

    assert payload["episode_anchor_id"] == "before-pause"
    assert payload["episode_latest_end_ms"] == 1_000


def test_missing_episode_timestamps_disable_lineage_not_provider_reservation():
    request = app_module.RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-no-timestamps",
        state_revision=1,
        context_paragraphs=[],
        new_paragraphs=[
            {
                "id": "no-time-segment",
                "text": "我们需要确认负责人吗？",
                "revision": 1,
                "speaker": "Alice",
                "source_track": "microphone",
            }
        ],
        rolling_state={},
    )
    candidate = app_module.CoachCandidateEvent(
        event_type="question_pending",
        evidence_segment_ids=("no-time-segment",),
        reason="question",
        candidate_key="candidate-no-time",
    )
    payload = app_module._coach_candidate_payloads(
        (candidate,),
        request=request,
    )[candidate.candidate_key]

    assert payload["episode_id"].startswith("coach-episode:")
    assert app_module._coach_episode_descriptors_for_reservation([payload]) == []


def test_recent_context_history_is_mixed_deduplicated_and_bounded():
    events = [
        _formal_projection_event(
            seq=1,
            event_type="meeting.topic.updated",
            projection={"text": "发布方案", "summary": "先确认发布窗口。"},
        ),
        _formal_projection_event(
            seq=2,
            event_type="meeting.decision.updated",
            projection={"text": "采用蓝绿发布", "updated_at_ms": 2_000},
        ),
        _formal_projection_event(
            seq=3,
            event_type="meeting.topic.updated",
            projection={"text": "发布方案", "summary": "先确认发布窗口。"},
        ),
        _formal_projection_event(
            seq=4,
            event_type="meeting.open_question.updated",
            projection={"text": "谁负责回滚？", "updated_at_ms": 4_000},
        ),
    ]

    history = app_module._bounded_recent_context_history(events, limit=2)

    assert [(item["kind"], item["title"]) for item in history] == [
        ("decision", "采用蓝绿发布"),
        ("question", "谁负责回滚？"),
    ]
    full_history = app_module._bounded_recent_context_history(events)
    assert len(full_history) == 3
    assert full_history[0]["context_id"] == "event-3"
    assert full_history[0]["evidence_segment_ids"] == ["segment-3"]


def test_coach_runtime_capability_exposes_pi_loop_metrics():
    capability = app_module._coach_runtime_capability(
        enabled=True,
        provider_configured=True,
        active=False,
        requested_runtime="pi",
        latest_job={
            "output": {
                "coach": {
                    "status": "silent",
                    "runtime_used": "pi",
                    "decision_reason": "没有发现需要立刻介入的表达问题",
                    "agent_metrics": {
                        "checklist_item_ids": ["question", "commitment", "goal", "conflict", "clarity", "value"],
                        "history_searches": 2,
                        "session_reused": True,
                        "turns": 2,
                        "tool_calls": 3,
                        "elapsed_ms": 4_320,
                    },
                }
            }
        },
    )

    assert capability == {
        "state": "active",
        "label": "Pi 教练监听中",
        "detail": "本轮完成 6 项检查 · 2 轮 Agent · 3 次工具调用 · 检索历史 2 次 · 响应约 4.3 秒 · 已延续会议上下文",
        "decision": "本轮结论：暂不打断，没有发现需要立刻介入的表达问题",
    }


def test_coach_runtime_capability_waits_for_capture_input():
    capability = app_module._coach_runtime_capability(
        enabled=True,
        provider_configured=True,
        active=False,
        capture_active=False,
        requested_runtime="pi",
        latest_job={"output": {"coach": {"runtime_used": "pi"}}},
    )

    assert capability == {
        "state": "idle",
        "label": "Pi 教练等待录音",
        "detail": "当前没有录音输入；开始录音后才会检查新的会议文字，已有建议仍保留。",
    }


def test_coach_runtime_capability_surfaces_completed_body_analysis_without_capture():
    capability = app_module._coach_runtime_capability(
        enabled=True,
        provider_configured=True,
        active=False,
        capture_active=False,
        requested_runtime="pi",
        latest_job={
            "output": {
                "coach": {
                    "status": "intervention",
                    "runtime_used": "pi",
                    "agent_metrics": {
                        "checklist_item_ids": ["question", "commitment", "goal"],
                        "session_reused": True,
                        "turns": 1,
                        "tool_calls": 1,
                        "elapsed_ms": 4_320,
                    },
                }
            }
        },
    )

    assert capability == {
        "state": "active",
        "label": "Pi 教练已分析正文",
        "detail": "本轮完成 3 项检查 · 1 轮 Agent · 1 次工具调用 · 响应约 4.3 秒 · 已延续会议上下文 · 当前没有录音输入，开始录音后继续检查新内容",
        "decision": None,
    }


def test_coach_runtime_capability_makes_pi_fallback_visible():
    capability = app_module._coach_runtime_capability(
        enabled=True,
        provider_configured=True,
        active=False,
        requested_runtime="pi",
        latest_job={
            "output": {
                "coach": {
                    "runtime_used": "direct",
                    "fallback_error_code": "pi_unavailable",
                    "fallback_reason": "runtime_unavailable",
                }
            }
        },
    )

    assert capability["state"] == "paused"
    assert capability["label"] == "Pi 已回退普通模式"
    assert capability["detail"] == "回退原因：Pi 运行组件不可用"
    assert capability["error_class"] == "pi_unavailable"


def test_coach_decision_timing_envelope_preserves_observed_pi_wall_clock():
    timing = app_module._coach_decision_timing_envelope(
        {
            "ttft_ms": 125.5,
            "decision_latency_ms": 840.25,
            "timings": {
                "clock": "unix_epoch_ms",
                "started_at_ms": 1_000,
                "first_token_at_ms": 1_125,
                "completed_at_ms": 1_840,
            }
        },
        fallback_started_at_ms=900,
        fallback_completed_at_ms=2_000,
    )

    assert timing == {
        "created_at_ms": 1_000,
        "first_token_at_ms": 1_125,
        "completed_at_ms": 1_840,
        "projected_at_ms": None,
        "dropped_at_ms": None,
        "ttft_ms": 125.5,
        "decision_latency_ms": 840.25,
    }


def test_coach_decision_timing_envelope_never_fabricates_first_token():
    timing = app_module._coach_decision_timing_envelope(
        {
            "timings": {
                "clock": "monotonic_ms",
                "started_at_ms": 100,
                "first_token_at_ms": 120,
                "completed_at_ms": 180,
            }
        },
        fallback_started_at_ms=1_000,
        fallback_completed_at_ms=1_840,
    )

    assert timing == {
        "created_at_ms": 1_000,
        "first_token_at_ms": None,
        "completed_at_ms": 1_840,
        "projected_at_ms": None,
        "dropped_at_ms": None,
        "ttft_ms": None,
        "decision_latency_ms": None,
    }


def test_v2_intelligence_batch_reads_only_the_next_bounded_increment(tmp_path):
    persistence = V2Persistence(
        tmp_path / "intelligence-batch.db",
        semantic_projection_mode="llm_first",
    )
    try:
        for index in range(1, 11):
            persistence.commit_final_and_enqueue(
                meeting_id="meeting-1",
                final_id=f"final-{index}",
                segment_id=f"segment-{index}",
                text=f"Paragraph {index}.",
                normalized_text=f"Paragraph {index}.",
                started_at_ms=index * 5_000,
                ended_at_ms=index * 5_000 + 1_000,
                evidence_hash=f"hash-{index}",
                now_ms=1_000 + index,
            )
        jobs = [
            job
            for job in persistence.list_jobs(meeting_id="meeting-1")
            if job["kind"] == "intelligence"
        ]

        first_segments, first_context = app_module._v2_intelligence_batch_segments(
            persistence,
            jobs[0],
        )
        claimed = persistence.claim_next_job(
            worker_id="worker-1",
            lane="intelligence",
            now_ms=10_000,
            lease_ms=5_000,
        )
        assert claimed is not None
        assert persistence.complete_job(
            job_id=claimed["id"],
            worker_id="worker-1",
            now_ms=10_100,
            output={"applied": True},
        ) is not None
        second_segments, second_context = app_module._v2_intelligence_batch_segments(
            persistence,
            jobs[1],
        )

        assert [item["segment_id"] for item in first_segments] == [
            f"segment-{index}" for index in range(1, 9)
        ]
        assert first_context == []
        assert [item["segment_id"] for item in second_segments] == ["segment-9", "segment-10"]
        assert [item["segment_id"] for item in second_context] == [
            "segment-6",
            "segment-7",
            "segment-8",
        ]
    finally:
        persistence.close()


def test_create_app_rejects_multi_worker_llm_runtime(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    with pytest.raises(RuntimeError, match="single worker"):
        create_app()


def test_runtime_app_factory_uses_sqlite_when_data_dir_is_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path))

    runtime_app = app_module.create_runtime_app()

    assert isinstance(runtime_app.state.asr_live_repository, SqliteAsrLiveSessionRepository)
    assert isinstance(runtime_app.state.session_repository, SqliteSessionRepository)
    assert (tmp_path / "meeting_copilot.db").is_file()


def test_runtime_app_factory_uses_sqlite_default_when_env_is_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("MEETING_COPILOT_DATA_DIR", raising=False)
    monkeypatch.setattr(app_module, "DEFAULT_RUNTIME_DATA_DIR", tmp_path)

    runtime_app = app_module.create_runtime_app()

    assert isinstance(runtime_app.state.asr_live_repository, SqliteAsrLiveSessionRepository)
    assert isinstance(runtime_app.state.session_repository, SqliteSessionRepository)
    assert (tmp_path / "meeting_copilot.db").is_file()


def test_runtime_app_prewarms_resident_funasr_during_startup(monkeypatch, tmp_path):
    lifecycle_calls = []
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        app_module.asr_stream,
        "prewarm_funasr_resident_manager",
        lambda: lifecycle_calls.append("prewarm") or True,
    )
    monkeypatch.setattr(
        app_module.asr_stream,
        "shutdown_funasr_resident_manager",
        lambda: lifecycle_calls.append("shutdown"),
    )

    with TestClient(app_module.create_runtime_app()) as client:
        assert client.get("/health").status_code == 200

    assert lifecycle_calls == ["prewarm", "shutdown"]


def test_pi_bridge_prewarm_is_best_effort_and_provider_free(monkeypatch):
    lifecycle_calls = []

    class FakePiSidecar:
        def prewarm(self):
            lifecycle_calls.append("prewarm")
            return {
                "ready": True,
                "bridge_process_reused": False,
                "bridge_startup_ms": 12.5,
            }

        def close(self):
            lifecycle_calls.append("shutdown")

    monkeypatch.setattr(app_module, "PiCoachSidecar", FakePiSidecar)
    monkeypatch.setenv("MEETING_COPILOT_PI_BRIDGE_PREWARM", "1")
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example.test")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "provider-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "coach-model")

    with TestClient(app_module.create_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.pi_coach_prewarm == {
            "attempted": True,
            "enabled": True,
            "ready": True,
            "bridge_process_reused": False,
            "bridge_startup_ms": 12.5,
        }

    assert lifecycle_calls == ["prewarm", "shutdown"]


def test_base_runtime_starts_without_prewarming_optional_funasr(monkeypatch, tmp_path):
    manifest_path = tmp_path / "runtime-bundle-manifest.json"
    manifest_path.write_text(
        json.dumps({"distribution_profile": "base"}),
        encoding="utf-8",
    )
    lifecycle_calls = []
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv("MEETING_COPILOT_RUNTIME_MANIFEST", str(manifest_path))
    monkeypatch.setattr(
        app_module.asr_stream,
        "prewarm_funasr_resident_manager",
        lambda: lifecycle_calls.append("prewarm") or False,
    )

    with TestClient(app_module.create_runtime_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.distribution_profile == "base"

    assert lifecycle_calls == []


def test_base_runtime_does_not_prewarm_available_offline_refiner_by_default(
    monkeypatch,
    tmp_path,
):
    manifest_path = tmp_path / "runtime-bundle-manifest.json"
    manifest_path.write_text(
        json.dumps({"distribution_profile": "base"}),
        encoding="utf-8",
    )
    lifecycle_calls = []
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEETING_COPILOT_RUNTIME_MANIFEST", str(manifest_path))
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_REFINER_POLICY", raising=False)
    monkeypatch.setattr(
        app_module.asr_refiner,
        "refinement_capability",
        lambda: {"status": "ready"},
    )
    monkeypatch.setattr(
        app_module.asr_refiner,
        "prewarm_refiner_worker",
        lambda: (_ for _ in ()).throw(AssertionError("default policy must not prewarm")),
    )
    monkeypatch.setattr(
        app_module.asr_refiner,
        "shutdown_refiner_worker",
        lambda: lifecycle_calls.append("refiner_shutdown"),
    )
    monkeypatch.setattr(
        app_module.asr_stream,
        "prewarm_funasr_resident_manager",
        lambda: lifecycle_calls.append("realtime_prewarm") or True,
    )

    with TestClient(app_module.create_runtime_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.funasr_resident_prewarm_ready is False
        assert client.app.state.funasr_refiner_prewarm_ready is False
        assert client.app.state.funasr_refiner_policy["mode"] == "online_only"
        assert (
            client.app.state.funasr_refiner_policy["degradation_reason"]
            == "offline_refinement_bypassed_by_resource_policy"
        )

    assert "refiner_prewarm" not in lifecycle_calls
    assert "realtime_prewarm" not in lifecycle_calls
    assert "refiner_shutdown" in lifecycle_calls


def test_base_runtime_explicit_prewarm_policy_preserves_offline_refiner_compatibility(
    monkeypatch,
    tmp_path,
):
    manifest_path = tmp_path / "runtime-bundle-manifest.json"
    manifest_path.write_text(
        json.dumps({"distribution_profile": "base"}),
        encoding="utf-8",
    )
    lifecycle_calls = []
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEETING_COPILOT_RUNTIME_MANIFEST", str(manifest_path))
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_POLICY", "prewarm")
    monkeypatch.setattr(
        app_module.asr_refiner,
        "refinement_capability",
        lambda: {"status": "ready"},
    )
    monkeypatch.setattr(
        app_module.asr_refiner,
        "prewarm_refiner_worker",
        lambda: lifecycle_calls.append("refiner_prewarm") or True,
    )
    monkeypatch.setattr(
        app_module.asr_refiner,
        "shutdown_refiner_worker",
        lambda: lifecycle_calls.append("refiner_shutdown"),
    )
    monkeypatch.setattr(
        app_module.asr_stream,
        "prewarm_funasr_resident_manager",
        lambda: lifecycle_calls.append("realtime_prewarm") or True,
    )

    with TestClient(app_module.create_runtime_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.funasr_resident_prewarm_ready is False
        assert client.app.state.funasr_refiner_prewarm_ready is True
        assert client.app.state.funasr_refiner_policy["mode"] == "prewarm"
        assert client.app.state.funasr_refiner_policy["source"] == "environment"

    assert "refiner_prewarm" in lifecycle_calls
    assert "realtime_prewarm" not in lifecycle_calls
    assert "refiner_shutdown" in lifecycle_calls


def test_packaged_runtime_fails_startup_when_resident_funasr_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    monkeypatch.setattr(app_module.asr_stream, "prewarm_funasr_resident_manager", lambda: False)

    with pytest.raises(RuntimeError, match="failed to become ready"):
        with TestClient(app_module.create_runtime_app()):
            pass


def test_asr_runtime_status_reports_real_resident_readiness(monkeypatch):
    monkeypatch.setattr(app_module.asr_stream, "funasr_realtime_available", lambda: True)
    monkeypatch.setattr(app_module.asr_stream, "_funasr_resident_enabled", lambda: True)
    monkeypatch.setattr(
        app_module.asr_stream,
        "funasr_resident_status",
        lambda: {
            "schema_version": "funasr_resident_status.v1",
            "spawned": True,
            "process_running": True,
            "process_ready": True,
            "pid": 123,
            "generation": 1,
            "active_session_id": None,
            "process_start_count": 1,
            "completed_session_count": 0,
            "last_exit_code": None,
            "last_error": None,
        },
    )

    response = TestClient(create_app()).get("/providers/asr/runtime")

    assert response.status_code == 200
    assert response.json()["resident"]["process_ready"] is True
    assert response.json()["resident"]["pid"] == 123


def test_asr_refiner_prewarm_is_local_verification_gated(monkeypatch):
    policy = {
        "schema_version": "realtime_refiner_policy.v1",
        "mode": "prewarm",
        "source": "environment",
        "prewarm_enabled": True,
    }
    monkeypatch.setattr(app_module.asr_refiner, "realtime_refiner_policy", lambda: policy)
    monkeypatch.setattr(
        app_module.asr_refiner,
        "refinement_capability",
        lambda: {"status": "ready", "process_resident": True},
    )
    monkeypatch.setattr(app_module.asr_refiner, "prewarm_refiner_worker", lambda: True)
    monkeypatch.setattr(
        app_module.asr_refiner,
        "refiner_worker_status",
        lambda: {
            "spawned": True,
            "process_running": True,
            "process_ready": True,
            "pid": 456,
        },
    )
    client = TestClient(create_app())

    forbidden = client.post("/providers/asr/prewarm")
    assert forbidden.status_code == 403

    response = client.post(
        "/providers/asr/prewarm",
        headers={
            "origin": "http://127.0.0.1:8981",
            "x-meeting-copilot-verification": "1",
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["worker"]["process_ready"] is True


def test_execution_preview_uses_locally_normalized_final_as_llm_evidence():
    record = {
        "session_id": "normalized_preview",
        "events": [
            {
                "id": "transcript_final:s1",
                "event_type": "transcript_final",
                "sequence": 1,
                "payload": {
                    "segment_id": "s1",
                    "text": "ment gate 和 t九九",
                    "normalized_text": "payment-gateway 和 P99",
                    "evidence_spans": [
                        {
                            "id": "asr_ev_s1",
                            "segment_id": "s1",
                            "quote": "ment gate 和 t九九",
                            "start_ms": 0,
                            "end_ms": 1000,
                            "status": "active",
                        }
                    ],
                },
            },
            {
                "id": "llm_request_draft:c1",
                "event_type": "llm_request_draft_event",
                "sequence": 2,
                "payload": {
                    "request_id": "c1",
                    "target_candidate_id": "candidate_1",
                    "target_type": "Risk",
                    "target_id": "risk_1",
                    "gap_rule_id": "risk.rollback.validation",
                    "evidence_span_ids": ["asr_ev_s1"],
                    "segment_batch": ["s1"],
                },
            },
        ],
    }

    preview = app_module._execution_previews_from_record(record)[0]

    assert preview["evidence_spans"][0]["quote"] == "payment-gateway 和 P99"
    assert "ment gate" not in preview["evidence_context"]


def _expected_suggestion_card_schema_outline_preview():
    return {
        "name": "SuggestionCardV1",
        "strict": True,
        "schema_outline_status": "outline_only",
        "schema_outline_source": "local_contract_preview",
        "schema_outline": {
            "type": "object",
            "required": [
                "id",
                "type",
                "evidence_span_ids",
                "state_refs",
                "state_event_ids",
                "gap_rule_id",
                "trigger_reason",
                "trigger_source",
                "final_segment_at_ms",
                "state_event_at_ms",
                "card_created_at_ms",
                "latency_ms",
                "prompt_version",
                "model",
                "usage",
                "schema_result",
                "show_or_silence_decision",
                "segment_batch",
                "status",
            ],
            "optional": [
                "title",
                "suggested_question",
            ],
            "properties": {
                "id": {"type": "string"},
                "type": {"type": "string"},
                "evidence_span_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "state_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "state_event_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "gap_rule_id": {"type": "string"},
                "trigger_reason": {"type": "string"},
                "trigger_source": {"type": "string"},
                "final_segment_at_ms": {"type": "integer", "minimum": 0},
                "state_event_at_ms": {"type": "integer", "minimum": 0},
                "card_created_at_ms": {"type": "integer", "minimum": 0},
                "latency_ms": {"type": "integer", "minimum": 0},
                "prompt_version": {"type": "string"},
                "model": {"type": "string"},
                "usage": {"type": "object"},
                "schema_result": {"type": "string"},
                "show_or_silence_decision": {"type": "string"},
                "segment_batch": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "status": {"type": "string", "default": "new"},
                "title": {"type": ["string", "null"]},
                "suggested_question": {"type": ["string", "null"]},
            },
            "additional_properties_status": "allowed_by_local_contract_extra",
        },
    }


def _payload():
    return {
        "session_id": "meeting_001",
        "transcript_report": {
            "provider": "funasr",
            "latency_ms": 1800,
            "rtf": 0.42,
            "text": "payment-gateway 先灰度 10%。还没有确认回滚负责人。",
            "normalized_text": "payment-gateway 先灰度 10%。还没有确认回滚负责人。",
            "segments": [
                {
                    "id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "text": "payment-gateway 先灰度 10%。",
                    "confidence": 0.91,
                },
                {
                    "id": "seg_002",
                    "start_ms": 5000,
                    "end_ms": 9000,
                    "text": "还没有确认回滚负责人。",
                    "confidence": 0.88,
                },
            ],
            "evidence_spans": [
                {
                    "id": "ev_001",
                    "segment_id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "quote": "payment-gateway 先灰度 10%。",
                },
                {
                    "id": "ev_002",
                    "segment_id": "seg_002",
                    "start_ms": 5000,
                    "end_ms": 9000,
                    "quote": "还没有确认回滚负责人。",
                },
            ],
        },
        "analysis": {
            "summary": "讨论 payment-gateway 灰度发布。",
            "meeting_context": {
                "is_engineering_meeting": True,
                "reason": "包含灰度、回滚负责人等发布评审内容。",
            },
            "states": {
                "decision_candidates": [
                    {
                        "id": "decision_001",
                        "statement": "payment-gateway 先灰度 10%",
                        "evidence_span_id": "ev_001",
                    }
                ],
                "action_items": [],
                "risks": [],
                "open_questions": [
                    {
                        "id": "question_001",
                        "question": "谁负责回滚？",
                        "evidence_span_ids": ["ev_002"],
                    }
                ],
            },
            "suggestion_cards": [
                {
                    "id": "card_001",
                    "type": "owner_gap",
                    "suggested_question": "是否需要确认回滚负责人？",
                    "evidence_span_id": "ev_002",
                    "state_refs": ["open_question:question_001"],
                    "state_event_ids": ["event_001"],
                    "gap_rule_id": "owner.required",
                    "trigger_reason": "候选灰度决策缺少回滚负责人",
                    "trigger_source": "state_gap_detector",
                    "final_segment_at_ms": 9000,
                    "state_event_at_ms": 9600,
                    "card_created_at_ms": 13800,
                    "latency_ms": 4800,
                    "prompt_version": "suggestion-card.v1",
                    "model": "gpt-5.5",
                    "usage": {"total_tokens": 321},
                    "schema_result": "valid",
                    "show_or_silence_decision": "show",
                    "segment_batch": ["seg_002"],
                }
            ],
        },
        "state_events": [
            {
                "id": "event_001",
                "target_type": "OpenQuestion",
                "target_id": "question_001",
                "event_type": "created",
                "created_at_ms": 9600,
                "evidence_span_ids": ["ev_001"],
            }
        ],
        "llm_usage": {
            "model": "gpt-5.5",
            "call_count": 1,
            "usage": {"total_tokens": 1234},
        },
    }


def test_audio_check_distinguishes_file_asr_and_realtime_asr(monkeypatch, tmp_path):
    fake_funasr_python = tmp_path / "funasr-python"
    fake_funasr_worker = tmp_path / "funasr-stream-worker.py"
    fake_funasr_model = tmp_path / "funasr-online-model"
    fake_funasr_python.write_text("# executable placeholder", encoding="utf-8")
    fake_funasr_worker.write_text("# worker placeholder", encoding="utf-8")
    fake_funasr_model.mkdir()
    (fake_funasr_model / "model.pt").write_bytes(b"model")
    (fake_funasr_model / "config.yaml").write_text("model: local\n", encoding="utf-8")

    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(app_module.asr_stream, "_FUNASR_VENV_PY", fake_funasr_python)
    monkeypatch.setattr(app_module.asr_stream, "_FUNASR_WORKER", fake_funasr_worker)
    monkeypatch.setattr(app_module.asr_stream, "_FUNASR_MODEL_DIR", fake_funasr_model)
    monkeypatch.setattr(app_module.asr_stream, "_SHERPA_VENV_PY", tmp_path / "missing-sherpa-python")
    monkeypatch.setattr(app_module.asr_stream, "_SHERPA_WORKER", tmp_path / "missing-sherpa-worker.py")
    monkeypatch.setattr(app_module.asr_stream, "_SHERPA_MODEL", tmp_path / "missing-sherpa-model")

    body = TestClient(create_app()).get("/audio/check").json()

    assert body["file_asr_available"] is True
    assert body["realtime_asr_available"] is True
    assert body["realtime_asr_providers"] == ["funasr_realtime"]
    assert body["asr_readiness_summary"] == "realtime_ready"
    assert body["funasr_available"] is True


def _asr_live_payload(session_id: str = "local_asr_stream_review"):
    return {
        "session_id": session_id,
        "provider": "local_mock_asr",
        "streaming_events": [
            {
                "event_type": "partial",
                "segment_id": "asr_seg_001",
                "text": "先灰度",
                "start_ms": 0,
                "end_ms": 1200,
                "received_at_ms": 1300,
                "confidence": 0.72,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_001",
                "text": "先灰度 10%。",
                "start_ms": 0,
                "end_ms": 3200,
                "received_at_ms": 3500,
                "confidence": 0.91,
            },
            {
                "event_type": "revision",
                "segment_id": "asr_seg_001_rev1",
                "revision_of": "asr_seg_001",
                "text": "先灰度 5%，不是 10%。",
                "start_ms": 0,
                "end_ms": 3400,
                "received_at_ms": 5200,
                "confidence": 0.94,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_002",
                "text": "谁负责回滚？",
                "start_ms": 3400,
                "end_ms": 6100,
                "received_at_ms": 7000,
                "confidence": 0.9,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_003",
                "text": "如果错误率超过 0.1% 就回滚。",
                "start_ms": 6100,
                "end_ms": 8200,
                "received_at_ms": 8800,
                "confidence": 0.9,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_004",
                "text": "张三下周三补充兼容性测试用例。",
                "start_ms": 8200,
                "end_ms": 10400,
                "received_at_ms": 11200,
                "confidence": 0.9,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 10400,
                "end_ms": 11400,
                "received_at_ms": 11400,
            },
        ],
    }


def _write_asr_events_file(root: Path, relative_path: str, events: list[dict]) -> str:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    return relative_path


def _valid_schema_validation_candidate_response():
    return {
        "id": "card_dry_run_001",
        "type": "owner_gap",
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "state_refs": ["DecisionCandidate:asr_decision_asr_seg_001"],
        "state_event_ids": ["asr_state_event_asr_seg_001"],
        "gap_rule_id": "release.rollback.owner.required",
        "trigger_reason": "dry-run validation sample",
        "trigger_source": "llm_schema_validation_dry_run",
        "final_segment_at_ms": 3500,
        "state_event_at_ms": 3500,
        "card_created_at_ms": 3700,
        "latency_ms": 200,
        "prompt_version": "suggestion-card-execution-preview.v1",
        "model": "not_called",
        "usage": {"total_tokens": 0},
        "schema_result": "valid",
        "show_or_silence_decision": "show",
        "segment_batch": ["asr_seg_001"],
        "status": "new",
        "title": "确认回滚负责人",
        "suggested_question": "这次发布的回滚负责人是谁？",
    }


def _card_lifecycle_append_idempotency_key(
    session_id: str,
    event_type: str,
    card_id: str = "card_dry_run_001",
    request_id: str = ("asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"),
) -> str:
    return f"live_asr_card_lifecycle_append:{session_id}:{request_id}:{event_type}:{card_id}"


def _append_persisted_lifecycle_event(
    record: dict,
    *,
    session_id: str,
    event_type: str,
    sequence: int,
    idempotency_key: str | None = None,
    card_id: str = "card_dry_run_001",
    event_id: str | None = None,
    payload_extra: dict | None = None,
):
    payload = {
        "card_id": card_id,
        "idempotency_key": idempotency_key
        if idempotency_key is not None
        else _card_lifecycle_append_idempotency_key(
            session_id,
            event_type,
            card_id,
        ),
        "request_id": ("asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"),
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
    }
    if event_type == "suggestion_card":
        payload["card"] = {"id": card_id}
    if payload_extra:
        payload.update(payload_extra)
    record["events"].append(
        {
            "id": event_id or f"{event_type}:{card_id}",
            "event_type": event_type,
            "at_ms": 3700 + sequence,
            "sequence": sequence,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": payload,
        }
    )


def _install_no_llm_config_or_secret_read_guards(monkeypatch, tmp_path, label: str):
    config_path = tmp_path / f"{label}.local.json"
    configs_local_dir = tmp_path / "configs" / "local"
    configs_local_dir.mkdir(parents=True)
    configs_local_path = configs_local_dir / f"{label}.json"
    configs_local_path.write_text(
        json.dumps({"api_key": f"TEST_{label.upper()}_CONFIGS_LOCAL_SECRET"}),
        encoding="utf-8",
    )
    config_url = f"https://{label}-read-sentinel.invalid"
    config_secret = f"TEST_{label.upper()}_CONFIG_SECRET"
    config_model = f"{label}-config-model"
    config_bearer = f"{label.upper()}_CONFIG_BEARER"
    env_openai_key = f"TEST_{label.upper()}_ENV_OPENAI_KEY"
    env_meeting_key = f"TEST_{label.upper()}_ENV_MEETING_KEY"
    config_path.write_text(
        json.dumps(
            {
                "base_url": config_url,
                "api_key": config_secret,
                "model": config_model,
                "authorization": f"Bearer {config_bearer}",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", env_openai_key)
    monkeypatch.setenv("MEETING_COPILOT_LLM_API_KEY", env_meeting_key)
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    original_path_open = Path.open
    original_path_exists = Path.exists
    original_path_is_file = Path.is_file
    original_path_stat = Path.stat
    original_builtin_open = builtins.open
    original_os_stat = os.stat
    original_getenv = os.getenv
    original_environ_get = os.environ.get
    original_environ_getitem = os.environ.__class__.__getitem__

    def is_llm_config_path(path) -> bool:
        try:
            candidate = Path(path)
        except TypeError:
            return False
        if candidate == config_path:
            return True
        return "configs" in candidate.parts and "local" in candidate.parts

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not read config files")
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not read config bytes")
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not open config files")
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(f"{label} must not open config files")
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not check config existence")
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not check config file type")
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not stat config files")
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not stat config files")
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(f"{label} must not read env secrets")
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(f"{label} must not read env secrets")
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_secret_environ_getitem(environ, key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(f"{label} must not read env secrets")
        return original_environ_getitem(environ, key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(f"{label} must not load llm gateway config")

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError(f"{label} must not access keychain")

    def reject_outbound_llm_http(*args, **kwargs):
        raise AssertionError(f"{label} must not make outbound llm/http calls")

    monkeypatch.setattr(Path, "read_text", reject_llm_config_read_text)
    monkeypatch.setattr(Path, "read_bytes", reject_llm_config_read_bytes)
    monkeypatch.setattr(Path, "open", reject_llm_config_path_open)
    monkeypatch.setattr(Path, "exists", reject_llm_config_exists)
    monkeypatch.setattr(Path, "is_file", reject_llm_config_is_file)
    monkeypatch.setattr(Path, "stat", reject_llm_config_path_stat)
    monkeypatch.setattr(builtins, "open", reject_llm_config_builtin_open)
    monkeypatch.setattr(os, "stat", reject_llm_config_os_stat)
    monkeypatch.setattr(os, "getenv", reject_llm_secret_getenv)
    monkeypatch.setattr(os.environ, "get", reject_llm_secret_environ_get)
    monkeypatch.setattr(
        os.environ.__class__,
        "__getitem__",
        reject_llm_secret_environ_getitem,
    )
    monkeypatch.setattr(urllib.request, "urlopen", reject_outbound_llm_http)
    monkeypatch.setattr(
        app_module,
        "requests",
        type(
            "NoRequestsAllowed",
            (),
            {
                "get": staticmethod(reject_outbound_llm_http),
                "post": staticmethod(reject_outbound_llm_http),
                "request": staticmethod(reject_outbound_llm_http),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "httpx",
        type(
            "NoHttpxAllowed",
            (),
            {
                "get": staticmethod(reject_outbound_llm_http),
                "post": staticmethod(reject_outbound_llm_http),
                "request": staticmethod(reject_outbound_llm_http),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "load_llm_gateway_config",
        reject_llm_gateway_config_load,
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "load_keychain_secret",
        reject_keychain_access,
        raising=False,
    )
    return [
        str(config_path),
        str(configs_local_path),
        config_url,
        config_secret,
        config_model,
        config_bearer,
        env_openai_key,
        env_meeting_key,
        f"TEST_{label.upper()}_CONFIGS_LOCAL_SECRET",
        "Bearer",
        "sk-",
    ]


def _install_no_native_audio_or_process_guards(monkeypatch, label: str):
    blocked_modules = {
        "AudioToolbox",
        "AVFoundation",
        "CoreAudio",
        "multiprocessing",
        "pyaudio",
        "ScreenCaptureKit",
        "soundcard",
        "sounddevice",
        "subprocess",
        "wasapi",
        "wave",
    }
    original_import = builtins.__import__
    original_import_module = importlib.import_module

    def is_blocked_module(name: str) -> bool:
        root_name = name.split(".", 1)[0]
        return name in blocked_modules or root_name in blocked_modules

    def reject_native_import(name, *args, **kwargs):
        if is_blocked_module(name):
            raise AssertionError(f"{label} must not import native audio/process APIs")
        return original_import(name, *args, **kwargs)

    def reject_native_import_module(name, *args, **kwargs):
        if is_blocked_module(name):
            raise AssertionError(f"{label} must not import native audio/process APIs")
        return original_import_module(name, *args, **kwargs)

    def reject_process_or_native_probe(*args, **kwargs):
        raise AssertionError(f"{label} must not spawn processes or probe native audio")

    monkeypatch.setattr(builtins, "__import__", reject_native_import)
    monkeypatch.setattr(importlib, "import_module", reject_native_import_module)
    monkeypatch.setattr(subprocess, "Popen", reject_process_or_native_probe)
    monkeypatch.setattr(subprocess, "run", reject_process_or_native_probe)
    monkeypatch.setattr(subprocess, "check_call", reject_process_or_native_probe)
    monkeypatch.setattr(subprocess, "check_output", reject_process_or_native_probe)
    monkeypatch.setattr(multiprocessing, "Process", reject_process_or_native_probe)
    monkeypatch.setattr(os, "system", reject_process_or_native_probe)
    monkeypatch.setattr(os, "popen", reject_process_or_native_probe)
    monkeypatch.setattr(
        app_module,
        "subprocess",
        type(
            "NoSubprocessAllowed",
            (),
            {
                "Popen": staticmethod(reject_process_or_native_probe),
                "run": staticmethod(reject_process_or_native_probe),
                "check_call": staticmethod(reject_process_or_native_probe),
                "check_output": staticmethod(reject_process_or_native_probe),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "multiprocessing",
        type(
            "NoMultiprocessingAllowed",
            (),
            {"Process": staticmethod(reject_process_or_native_probe)},
        )(),
        raising=False,
    )


def _asr_live_payload_without_revision(session_id: str):
    payload = _asr_live_payload(session_id=session_id)
    payload["streaming_events"] = [
        event for event in payload["streaming_events"] if event.get("event_type") != "revision"
    ]
    return payload


def _asr_live_payload_with_low_confidence_candidate(session_id: str):
    payload = _asr_live_payload_without_revision(session_id=session_id)
    for event in payload["streaming_events"]:
        if event.get("segment_id") == "asr_seg_001":
            event["confidence"] = 0.5
    return payload


def _valid_llm_provider_config_validation_payload():
    return {
        "provider_protocol": "openai_compatible_chat_completions",
        "base_url": "https://provider-validation.example.invalid/v1",
        "api_key": "TEST_PROVIDER_VALIDATION_SECRET_VALUE",
        "model": "gpt-5.5",
        "timeout_seconds": 30,
        "ca_bundle_path": "certs/root-ca.pem",
    }


def _valid_llm_provider_config_loader_preflight_payload(config_path: str):
    return {
        "loader_mode": "preflight_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_path": config_path,
        "requested_fields": [
            "base_url",
            "api_key",
            "model",
            "timeout_seconds",
            "ca_bundle_path",
        ],
        "authorization": {
            "user_confirmed_local_config_access": True,
            "allow_secret_read": False,
            "allow_llm_call": False,
        },
    }


def _valid_llm_provider_config_reader_dry_run_payload(config_path: str):
    return {
        "reader_mode": "dry_run_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_path": config_path,
        "secret_reference": {
            "reference_type": "keychain_item_reference",
            "reference_id": "meeting-copilot/provider-config-reader-secret",
        },
        "authorization": {
            "user_confirmed_local_config_access": True,
            "acknowledged_secret_storage_policy": True,
            "allow_config_file_read": False,
            "allow_secret_read": False,
            "allow_llm_call": False,
            "allow_event_mutation": False,
        },
    }


def _valid_llm_provider_masked_status_loader_dry_run_payload(config_path: str):
    return {
        "loader_mode": "masked_status_dry_run_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_path": config_path,
        "secret_reference": {
            "reference_type": "keychain_item_reference",
            "reference_id": "meeting-copilot/provider-masked-status-secret",
        },
        "requested_display_fields": [
            "base_url_origin",
            "model",
            "timeout_seconds",
            "ca_bundle_name",
            "api_key",
        ],
        "authorization": {
            "user_confirmed_local_config_access": True,
            "acknowledged_secret_storage_policy": True,
            "allow_config_file_read": False,
            "allow_secret_read": False,
            "allow_llm_call": False,
            "allow_event_mutation": False,
            "allow_status_value_inference": False,
        },
    }


def _assert_config_reader_dry_run_response_redacts_submitted_values(
    response,
    *values: str,
) -> None:
    response_text = response.text
    for value in values:
        assert value not in response_text
    assert "Bearer" not in response_text
    assert "sk-" not in response_text


def _assert_masked_status_loader_dry_run_response_redacts_submitted_values(
    response,
    *values: str,
) -> None:
    response_text = response.text
    for value in values:
        assert value not in response_text
    assert "Bearer" not in response_text
    assert "sk-" not in response_text


def _assert_llm_provider_secret_storage_policy_body(body: dict, session_id: str):
    assert body == {
        "session_id": session_id,
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "policy_kind": "provider_secret_storage",
        "policy_status": "template_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "not_read",
        "secret_storage_status": "not_connected",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "safe_to_read_secret": False,
        "recommended_storage_order": [
            "os_keychain",
            "enterprise_secret_provider",
            "environment_variable_for_development_only",
        ],
        "allowed_secret_references": [
            "keychain_item_reference",
            "enterprise_secret_reference",
            "env_var_name_reference",
        ],
        "forbidden_storage_locations": [
            "repository_files",
            "configs_local_plaintext_api_key",
            "session_json",
            "live_asr_audit_events",
            "logs",
            "reports",
            "browser_local_storage",
        ],
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "masked_api_key",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_length",
            "api_key_fingerprint",
        ],
        "forbidden_status_signals": [
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "required_loader_guards": [
            "explicit_user_authorization",
            "path_privacy_redaction",
            "secret_value_redaction",
            "no_secret_in_error_response",
            "no_secret_in_audit_event",
            "no_secret_in_logs",
            "no_secret_in_browser_storage",
        ],
        "block_reasons": [
            "template_only_policy",
            "secret_storage_adapter_not_connected",
            "provider_config_not_loaded",
            "credentials_not_read",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "os_keychain_adapter",
            "enterprise_secret_provider_adapter",
            "authorized_config_file_reader",
            "authorized_masked_status_loader",
            "enabled_executor_mode_contract",
        ],
    }


def test_health_endpoint_reports_ok():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "meeting-copilot-web-mvp"}


def test_backend_allows_tauri_packaged_origin_for_local_api_probe():
    client = TestClient(create_app())

    response = client.options(
        "/health",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"


def test_provider_health_endpoint_masks_llm_secret_and_disables_remote_asr_by_default(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-provider-health-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-provider-health")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(app_module, "_realtime_asr_providers", lambda: ["sherpa_onnx_realtime"])
    app_module.llm_service.clear_runtime_config()

    response = TestClient(create_app(data_dir=tmp_path)).get("/providers/health")

    assert response.status_code == 200
    body = response.json()
    assert body["llm"] == {
        "configured": True,
        "provider": "openai_compatible_gateway",
        "model": "gpt-provider-health",
        "realtime_model": "gpt-provider-health",
        "realtime_model_source": "general_model_fallback",
        "realtime_model_explicit": False,
        "realtime_model_warning": "realtime_model_inherits_general_model",
        "correction_model": "gpt-provider-health",
        "correction_model_source": "general_model_fallback",
        "correction_model_explicit": False,
        "correction_model_warning": "correction_model_inherits_general_model",
        "is_mock": False,
        "api_style": "chat_completions",
        "credential_configured": True,
        "runtime_synced": True,
        "probe_status": "not_run",
        "operational": None,
        "realtime_ready": None,
        "realtime_probe_ready": None,
        "realtime_readiness_reason": "probe_not_ready",
        "probe_latency_ms": None,
        "probe_usage": None,
        "realtime_cutoff_ms": 2_500,
    }
    assert body["asr"]["file_provider"] == "local_funasr_batch"
    assert body["asr"]["file_asr_available"] is True
    assert body["asr"]["realtime_providers"] == ["sherpa_onnx_realtime"]
    assert body["remote_asr"] == {
        "default_enabled": False,
        "enabled": False,
        "providers": [],
        "adapter_contract": "optional_openai_compatible_or_vendor_adapter_disabled_by_default",
    }
    serialized = json.dumps(body, ensure_ascii=False)
    assert "sk-provider-health-secret" not in serialized
    assert "api_key" not in serialized


def test_provider_health_reports_resident_file_asr_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: False)
    monkeypatch.setattr(
        app_module.asr_refiner,
        "refinement_capability",
        lambda: {"status": "ready"},
    )

    response = TestClient(create_app(data_dir=tmp_path)).get("/providers/health")

    assert response.status_code == 200
    assert response.json()["asr"]["file_provider"] == "local_funasr_resident_file"
    assert response.json()["asr"]["file_asr_available"] is True


def test_provider_status_and_health_surface_open_realtime_circuit(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-provider-health-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-provider-health")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    app_module.llm_service.clear_runtime_config()
    app = create_app(data_dir=tmp_path)
    config = app_module.llm_service.LlmConfig.from_env()
    identity = app_module._realtime_provider_identity(config)
    app_module.provider_config_runtime.mark_probe_succeeded(
        config,
        latency_ms=1_700,
        usage={"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14},
        realtime_ready=True,
    )
    app.state.realtime_provider_circuit.record_probe_failure(identity, "timeout")

    with TestClient(app) as client:
        status = client.get("/providers/status")
        health = client.get("/providers/health")

    assert status.status_code == 200
    circuit = status.json()["realtime_circuit"]
    assert circuit["state"] == "open"
    assert circuit["reason"] == "realtime_provider_circuit_open"
    assert circuit["failure_count"] >= 1
    assert circuit["last_failure_class"] == "timeout"
    assert health.status_code == 200
    assert health.json()["realtime_circuit"]["state"] == "open"
    assert health.json()["llm"]["realtime_ready"] is False
    assert health.json()["llm"]["realtime_readiness_reason"] == "realtime_provider_circuit_open"
    assert health.json()["degradation"]["can_generate_suggestions"] is False
    assert health.json()["degradation"]["can_call_llm"] is False


def test_realtime_provider_identity_is_stable_before_and_after_realtime_normalization():
    config = app_module.llm_service.LlmConfig(
        base_url="https://gateway.example/v1",
        api_key="test-provider-key",
        model="general-model",
        realtime_model="realtime-model",
        api_style="responses",
    )

    assert app_module._realtime_provider_identity(config) == app_module._realtime_provider_identity(
        app_module.llm_service.realtime_config(config)
    )


def test_asr_live_sessions_list_endpoint_hides_mock_sessions_by_default(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    first = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="history_review_a"),
    )
    second = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id="history_review_b"),
    )

    response = client.get("/live/asr/sessions")
    demo_response = client.get("/live/asr/sessions?include_demo=true")

    assert first.status_code == 201
    assert second.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_count"] == 0
    assert body["sessions"] == []
    assert demo_response.status_code == 200
    body = demo_response.json()
    assert body["session_count"] == 2
    sessions = {item["session_id"]: item for item in body["sessions"]}
    assert set(sessions) == {"history_review_a", "history_review_b"}
    assert sessions["history_review_a"]["provider"] == "local_mock_asr"
    assert sessions["history_review_a"]["event_count"] >= 1
    assert sessions["history_review_a"]["final_count"] >= 1
    assert sessions["history_review_a"]["suggestion_candidate_count"] >= 1
    assert sessions["history_review_a"]["suggestion_card_count"] == 0
    assert sessions["history_review_a"]["approach_card_count"] == 0
    assert sessions["history_review_a"]["has_minutes"] is False


def test_asr_live_session_summary_exposes_recovery_authority_fields():
    summary = app_module._asr_live_session_summary(
        {
            "session_id": "recoverable_real_session",
            "provider": "funasr_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "created_at_epoch_ms": 1_700_000_000_100,
            "last_activity_at_epoch_ms": 1_700_000_000_900,
            "audio": {"saved": True},
            "events": [
                {
                    "event_type": "transcript_final",
                    "at_ms": 1000,
                    "payload": {"segment_id": "seg_1", "normalized_text": "已经确认的会议文字"},
                }
            ],
        }
    )

    assert summary["created_at_ms"] == 1_700_000_000_100
    assert summary["last_activity_at_ms"] == 1_700_000_000_900
    assert summary["has_transcript"] is True
    assert summary["has_audio"] is True
    assert summary["recoverable"] is True


def test_asr_live_session_summary_never_marks_mock_or_empty_session_recoverable():
    mock_summary = app_module._asr_live_session_summary(
        {
            "session_id": "mock_session",
            "provider": "local_mock_asr",
            "provider_mode": "mock",
            "is_mock": True,
            "last_activity_at_epoch_ms": 1_700_000_000_900,
            "events": [{"event_type": "transcript_final", "payload": {"text": "演示文字"}}],
        }
    )
    empty_summary = app_module._asr_live_session_summary(
        {
            "session_id": "empty_real_session",
            "provider": "funasr_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "last_activity_at_epoch_ms": 1_700_000_001_000,
            "events": [],
        }
    )

    assert mock_summary["recoverable"] is False
    assert empty_summary["has_transcript"] is False
    assert empty_summary["has_audio"] is False
    assert empty_summary["recoverable"] is False


def test_asr_live_sessions_list_is_sorted_by_wall_clock_activity_not_session_id(tmp_path):
    repository = JsonFileAsrLiveSessionRepository(tmp_path)
    base_record = {
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "source": "asr_live_event_source",
        "trace_kind": "asr_live_trace",
        "events": [
            {
                "event_type": "transcript_final",
                "payload": {"segment_id": "seg_1", "normalized_text": "真实会议文字"},
            }
        ],
    }
    repository.create(
        {
            **base_record,
            "session_id": "aaa_older",
            "created_at_epoch_ms": 1_700_000_000_000,
            "last_activity_at_epoch_ms": 1_700_000_001_000,
        }
    )
    repository.create(
        {
            **base_record,
            "session_id": "zzz_newer",
            "created_at_epoch_ms": 1_700_000_002_000,
            "last_activity_at_epoch_ms": 1_700_000_003_000,
        }
    )

    response = TestClient(create_app(data_dir=tmp_path)).get("/live/asr/sessions")

    assert response.status_code == 200
    assert [item["session_id"] for item in response.json()["sessions"]] == [
        "zzz_newer",
        "aaa_older",
    ]


def test_mock_asr_live_session_persists_mock_boundary_with_custom_provider(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            **_asr_live_payload(session_id="custom_mock_provider_review"),
            "provider": "custom_provider_label",
        },
    )
    events_response = client.get("/live/asr/sessions/custom_mock_provider_review/events")
    list_response = client.get("/live/asr/sessions?include_demo=true")

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["event_source"]["is_mock"] is True
    assert created["event_source"]["provider_mode"] == "mock"
    assert created["event_source"]["ingest_mode"] == "mock_asr_session"
    assert created["event_source"]["input_source"] == "mock"
    assert created["event_source"]["acceptance_eligible"] is False
    assert "mock_or_demo_session" in created["event_source"]["acceptance_blockers"]
    assert events_response.status_code == 200
    body = events_response.json()
    assert body["is_mock"] is True
    assert body["provider_mode"] == "mock"
    assert body["event_source"]["is_mock"] is True
    assert body["event_source"]["provider_mode"] == "mock"
    assert body["event_source"]["ingest_mode"] == "mock_asr_session"
    assert body["event_source"]["input_source"] == "mock"
    assert body["event_source"]["acceptance_eligible"] is False
    sessions = {item["session_id"]: item for item in list_response.json()["sessions"]}
    assert sessions["custom_mock_provider_review"]["is_mock"] is True
    assert sessions["custom_mock_provider_review"]["provider_mode"] == "mock"
    assert sessions["custom_mock_provider_review"]["event_source"]["ingest_mode"] == "mock_asr_session"
    assert sessions["custom_mock_provider_review"]["event_source"]["acceptance_eligible"] is False


def test_asr_live_event_metadata_rechecks_persisted_transcript_quality(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    session_id = "persisted_quality_policy_review"
    bad_text = (
        "下能脱稿画出a卷的全链路能说出每一个组件的位置和作用被属黑准的主循环"
        "request到contest xt moden downtwo calling to methoc ine ofdel背熟midiwell"
        "le的六值和位置背书三三状态一个短期机一个常见机一外一个任务状态"
    )
    events = app_module.build_asr_live_events(
        session_id=session_id,
        provider="sherpa_onnx_realtime",
        streaming_events=[
            {
                "event_type": "final",
                "segment_id": "quality_seg_1",
                "text": bad_text,
                "start_ms": 0,
                "end_ms": 3_000,
                "received_at_ms": 3_000,
                "confidence": 0.9,
            }
        ],
        is_mock=False,
    )
    app.state.asr_live_repository.create(
        {
            "session_id": session_id,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "sherpa_onnx_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "real_mic",
            "degradation_reasons": [],
            # Simulate a session persisted before the v3 quality policy existed.
            "asr_semantic_quality": {
                "schema_version": "asr_semantic_quality.v1",
                "policy_version": "general_chinese_technical_meeting.v2",
                "status": "passed",
                "blocker": None,
            },
            "suggestion_cards": [{"card_id": "stale_card"}],
            "approach_cards": [{"card_id": "stale_approach"}],
            "minutes": {"minutes_md": "旧纪要"},
            "events": events,
        }
    )

    response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert response.status_code == 200
    body = response.json()
    quality = body["event_source"]["asr_semantic_quality"]
    assert quality["policy_version"] == "general_chinese_technical_meeting.v3"
    assert quality["status"] == "blocked"
    assert "mixed_language_fragmentation" in quality["quality_failure_reasons"]
    assert "asr_semantic_quality_blocked" in body["event_source"]["acceptance_blockers"]
    assert body["event_source"]["acceptance_eligible"] is False
    assert body["formal_derivation_status"] == "suppressed_by_asr_semantic_quality"
    assert body["suggestion_cards"] == []
    assert body["approach_cards"] == []
    assert body["minutes"] == {}
    assert body["stored_formal_derivation_counts"] == {
        "suggestion_cards": 1,
        "approach_cards": 1,
        "minutes": 1,
    }


def test_asr_live_quality_migration_clears_stale_semantic_degradation(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    session_id = "stale_semantic_degradation_migration"
    events = app_module.build_asr_live_events(
        session_id=session_id,
        provider="funasr_realtime",
        streaming_events=[
            {
                "event_type": "final",
                "segment_id": "general_seg_1",
                "text": "今天聊聊天气，下午一起散步。",
                "start_ms": 0,
                "end_ms": 3_000,
                "received_at_ms": 3_000,
                "confidence": 0.9,
            }
        ],
        is_mock=False,
    )
    app.state.asr_live_repository.create(
        {
            "session_id": session_id,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "funasr_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "browser_live_mic",
            "degradation_reasons": ["asr_semantic_quality_blocked", "degraded_asr_session"],
            "asr_semantic_quality": {
                "policy_version": "general_chinese_technical_meeting.v2",
                "status": "blocked",
                "blocker": "asr_semantic_quality_blocked",
            },
            "events": events,
            "suggestion_cards": [],
            "approach_cards": [],
            "minutes": {},
        }
    )

    response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert response.status_code == 200
    body = response.json()
    assert body["degradation_reasons"] == []
    assert body["event_source"]["degradation_reasons"] == []
    assert body["event_source"]["asr_semantic_quality"]["status"] == "warning"
    assert body["event_source"]["acceptance_blockers"] == []


def test_asr_live_events_response_includes_canonical_transcript_snapshot(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "canonical_snapshot_review"

    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    assert create_response.status_code == 201

    response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert response.status_code == 200
    body = response.json()
    snapshot = body["canonical_transcript"]
    assert snapshot["schema_version"] == "canonical-transcript.v1"
    assert snapshot["session_id"] == session_id
    assert snapshot["segments"]
    assert snapshot["committed_char_count"] > 0
    assert snapshot["full_text"] == snapshot["committed_text"] + (
        snapshot["active_tail"]["display_text"] if snapshot["active_tail"] else ""
    )


def test_asr_live_events_exposes_non_secret_llm_evidence_from_runtime_ledger(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-runtime-evidence-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "team_gateway")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    session_id = "runtime_llm_evidence_review"

    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    assert create_response.status_code == 201
    app.state.settings_usage_repository.record_usage(
        session_id=session_id,
        purpose="formal_suggestion",
        provider="team_gateway",
        model="gpt-5.5",
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
        timestamp_ms=1_000,
    )

    response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert response.status_code == 200
    body = response.json()
    assert body["llm_evidence"] == {
        "schema_version": "llm-session-evidence.v1",
        "source": "runtime_config_and_usage_ledger",
        "configured": True,
        "provider": "team_gateway",
        "model": "gpt-5.5",
        "is_mock": False,
        "gateway_base_url_kind": "remote",
        "llm_called": True,
        "llm_call_count": 1,
        "llm_usage_total_tokens": 150,
    }
    assert "sk-runtime-evidence-secret" not in response.text
    assert "gateway.example" not in response.text


def test_create_asr_live_session_events_json_and_sse_use_asr_boundary():
    client = TestClient(create_app())

    create_response = client.post("/live/asr/mock/sessions", json=_asr_live_payload())

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["session_id"] == "local_asr_stream_review"
    assert {
        key: created["event_source"][key] for key in ["source", "trace_kind", "transport", "provider", "is_mock"]
    } == {
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "provider": "local_mock_asr",
        "is_mock": True,
    }
    assert created["event_source"]["provider_mode"] == "mock"
    assert created["event_source"]["ingest_mode"] == "mock_asr_session"
    assert [event["event_type"] for event in created["live_events"]] == [
        "transcript_partial",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_revision",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]

    json_response = client.get("/live/asr/sessions/local_asr_stream_review/events")
    sse_response = client.get("/live/asr/sessions/local_asr_stream_review/events.sse")

    assert json_response.status_code == 200
    body = json_response.json()
    assert body["session_id"] == "local_asr_stream_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    events = body["events"]
    assert {event["source"] for event in events} == {"live_asr_stream"}
    assert {event["trace_kind"] for event in events} == {"live_event"}
    assert events[-1]["event_type"] == "evaluation_summary"
    assert events[-1]["payload"]["provider"] == "local_mock_asr"
    assert events[-1]["payload"]["final_event_count"] == 4
    assert events[-1]["payload"]["revision_event_count"] == 1
    assert "suggestion_card" not in [event["event_type"] for event in events]
    suggestion_candidates = [event for event in events if event["event_type"] == "suggestion_candidate_event"]
    assert [event["payload"]["gap_rule_id"] for event in suggestion_candidates] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {event["payload"]["llm_call_status"] for event in suggestion_candidates} == {"not_called"}
    assert {event["payload"]["card_status"] for event in suggestion_candidates} == {"not_created"}
    request_drafts = [event for event in events if event["event_type"] == "llm_request_draft_event"]
    assert [event["payload"]["gap_rule_id"] for event in request_drafts] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {event["payload"]["request_status"] for event in request_drafts} == {"draft_only"}
    assert {event["payload"]["llm_call_status"] for event in request_drafts} == {"not_called"}
    assert {event["payload"]["schema_status"] for event in request_drafts} == {"not_generated"}
    assert {event["payload"]["card_status"] for event in request_drafts} == {"not_created"}
    assert request_drafts[0]["payload"]["target_candidate_id"] == suggestion_candidates[0]["payload"]["candidate_id"]
    state_events = [event for event in events if event["event_type"] == "state_event"]
    assert [event["payload"]["target_type"] for event in state_events] == [
        "DecisionCandidate",
        "DecisionCandidate",
        "OpenQuestion",
        "Risk",
        "ActionItem",
    ]
    assert state_events[0]["payload"]["state_item"]["source"] == "live_asr_stream"
    assert state_events[2]["payload"]["target_id"] == "asr_question_asr_seg_002"
    assert state_events[2]["payload"]["state_item"] == {
        "id": "asr_question_asr_seg_002",
        "question": "谁负责回滚？",
        "evidence_span_ids": ["asr_ev_asr_seg_002"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    assert state_events[3]["payload"]["state_item"] == {
        "id": "asr_risk_asr_seg_003",
        "description": "如果错误率超过 0.1% 就回滚。",
        "impact": "condition_exceeded",
        "mitigation": "回滚",
        "status": "open",
        "evidence_span_ids": ["asr_ev_asr_seg_003"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    assert state_events[4]["payload"]["state_item"] == {
        "id": "asr_action_asr_seg_004",
        "description": "张三下周三补充兼容性测试用例。",
        "owner": "张三",
        "deadline": "下周三",
        "status": "candidate",
        "evidence_span_ids": ["asr_ev_asr_seg_004"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    scheduler_event = next(event for event in events if event["event_type"] == "scheduler_event")
    assert scheduler_event["payload"]["scheduler_event_type"] == "llm_candidate_queued"
    assert scheduler_event["payload"]["decision_reason"] == "state_change"
    assert scheduler_event["payload"]["would_call_llm"] is True
    assert scheduler_event["payload"]["llm_call_status"] == "not_called"
    assert scheduler_event["payload"]["budget_remaining"] == 79
    assert scheduler_event["payload"]["model"] == "not-called"
    skipped_scheduler_event = [event for event in events if event["event_type"] == "scheduler_event"][1]
    assert skipped_scheduler_event["payload"]["scheduler_event_type"] == "llm_candidate_skipped"
    assert skipped_scheduler_event["payload"]["decision_reason"] == "cooldown"
    assert skipped_scheduler_event["payload"]["would_call_llm"] is False
    assert skipped_scheduler_event["payload"]["cooldown_remaining_ms"] == 8300

    assert sse_response.status_code == 200
    assert sse_response.headers["content-type"].startswith("text/event-stream")
    assert "event: transcript_partial" in sse_response.text
    assert "event: transcript_final" in sse_response.text
    assert "event: state_event" in sse_response.text
    assert "event: scheduler_event" in sse_response.text
    assert "event: suggestion_candidate_event" in sse_response.text
    assert "event: llm_request_draft_event" in sse_response.text
    assert "event: transcript_revision" in sse_response.text
    assert "event: evaluation_summary" in sse_response.text
    assert "谁负责回滚？" in sse_response.text
    assert "not-called" in sse_response.text
    assert "llm_candidate_queued" in sse_response.text
    assert "llm_candidate_skipped" in sse_response.text
    assert "not_called" in sse_response.text
    assert "action.owner.deadline.confirmation" in sse_response.text
    assert "asr-candidate-policy.v1" in sse_response.text
    assert "local_deterministic_heuristic" in sse_response.text
    assert '"confidence_level":"high"' in sse_response.text
    assert "not_created" in sse_response.text
    assert "draft_only" in sse_response.text
    assert "not_generated" in sse_response.text
    sse_events = [
        json.loads(line.removeprefix("data: ")) for line in sse_response.text.splitlines() if line.startswith("data: ")
    ]
    assert sse_events == events


def test_create_asr_live_session_from_local_event_file_uses_worker_handoff_boundary(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.sherpa.events.json",
        _asr_live_payload()["streaming_events"],
    )
    client = TestClient(create_app())

    create_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "local_asr_file_handoff_review",
            "provider": "sherpa_onnx_streaming",
            "events_path": events_path,
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["session_id"] == "local_asr_file_handoff_review"
    assert created["ingest_mode"] == "local_asr_event_file"
    assert created["events_path"] == "artifacts/tmp/asr_events/api-review-001.sherpa.events.json"
    assert {
        key: created["event_source"][key] for key in ("source", "trace_kind", "transport", "provider", "is_mock")
    } == {
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "provider": "sherpa_onnx_streaming",
        "is_mock": False,
    }
    assert created["event_source"]["provider_mode"] == "real"
    assert created["event_source"]["ingest_mode"] == "local_asr_event_file"
    assert created["event_source"]["asr_fallback_used"] is False
    assert created["event_source"]["degradation_reasons"] == []
    assert created["event_source"]["input_source"] == "local_event_file"
    assert created["event_source"]["acceptance_eligible"] is False
    assert "local_event_file_not_real_input" in created["event_source"]["acceptance_blockers"]
    assert created["safe_to_call_llm_now"] is False
    assert created["safe_to_call_remote_asr_now"] is False
    assert created["safe_to_read_user_audio_now"] is False
    assert created["safe_to_read_configs_local_now"] is False
    assert created["safe_to_capture_microphone_now"] is False
    assert created["live_event_counts"]["transcript_final"] == 4
    assert created["live_event_counts"]["transcript_revision"] == 1
    assert created["live_event_counts"]["suggestion_card"] == 0
    assert created["all_llm_statuses"] == ["not_called"]

    json_response = client.get("/live/asr/sessions/local_asr_file_handoff_review/events")
    sse_response = client.get("/live/asr/sessions/local_asr_file_handoff_review/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    assert events == created["live_events"]
    assert {event["source"] for event in events} == {"live_asr_stream"}
    assert "event: transcript_final" in sse_response.text
    assert "event: suggestion_candidate_event" in sse_response.text
    assert "not_called" in sse_response.text


def test_create_asr_live_session_from_local_event_file_rejects_forbidden_paths_before_reading(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    forbidden_path = _write_asr_events_file(
        tmp_path,
        "configs/local/private-asr-events.json",
        _asr_live_payload()["streaming_events"],
    )
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "blocked_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": forbidden_path,
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["ingest_status"] == "blocked_by_path_validation"
    assert body["detail"]["events_path"] == "<redacted_invalid_path>"
    assert body["detail"]["validation_errors"] == [
        "events path is blocked: configs/local",
    ]
    assert body["detail"]["safe_to_call_llm_now"] is False
    assert body["detail"]["safe_to_read_configs_local_now"] is False


def test_create_asr_live_session_from_local_event_file_rejects_symlink_to_forbidden_root(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    visible_root = tmp_path / "artifacts" / "tmp" / "asr_events"
    forbidden_root = tmp_path / "outside" / "configs" / "local"
    visible_root.mkdir(parents=True)
    forbidden_root.mkdir(parents=True)
    target = forbidden_root / "events.json"
    target.write_text(
        json.dumps(_asr_live_payload()["streaming_events"], ensure_ascii=False),
        encoding="utf-8",
    )
    link = visible_root / "linked.events.json"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "blocked_symlink_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": "artifacts/tmp/asr_events/linked.events.json",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["validation_errors"] == [
        "events path is blocked: configs/local",
    ]


def test_create_asr_live_session_from_local_event_file_rejects_invalid_json_shapes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    cases = [
        (
            "bad-json.events.json",
            "{not-json",
            "blocked_by_invalid_events_file",
            "ASR events file must contain valid JSON",
        ),
        (
            "non-list.events.json",
            json.dumps({"event_type": "final"}),
            "blocked_by_invalid_events_file",
            "ASR events JSON must be a list",
        ),
        (
            "non-object-item.events.json",
            json.dumps([{"event_type": "partial"}, "not-an-object"]),
            "blocked_by_invalid_events_file",
            "ASR events JSON items must be objects",
        ),
    ]
    client = TestClient(create_app())

    for filename, file_text, expected_status, expected_error in cases:
        events_file = tmp_path / "artifacts" / "tmp" / "asr_events" / filename
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text(file_text, encoding="utf-8")

        response = client.post(
            "/live/asr/local-event-files/sessions",
            json={
                "session_id": f"blocked_{filename.replace('.', '_')}",
                "provider": "sherpa_onnx_streaming",
                "events_path": f"artifacts/tmp/asr_events/{filename}",
            },
        )

        assert response.status_code == 422, filename
        detail = response.json()["detail"]
        assert detail["ingest_status"] == expected_status
        assert detail["validation_errors"] == [expected_error]
        assert detail["events_path"] == f"artifacts/tmp/asr_events/{filename}"
        assert detail["safe_to_call_llm_now"] is False
        assert detail["safe_to_capture_microphone_now"] is False
        assert detail["safe_to_download_models_now"] is False


def test_create_asr_live_session_from_local_event_file_rejects_event_contract_errors(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    base_event = {
        "event_type": "final",
        "segment_id": "asr_seg_contract",
        "text": "API 回滚负责人还没确认。",
        "start_ms": 0,
        "end_ms": 2400,
        "received_at_ms": 2500,
        "confidence": 0.9,
    }
    cases = [
        (
            "unknown-type.events.json",
            [{**base_event, "event_type": "draft"}],
            "unsupported ASR streaming event_type: draft",
        ),
        (
            "missing-segment.events.json",
            [{key: value for key, value in base_event.items() if key != "segment_id"}],
            "ASR final event missing segment_id",
        ),
        (
            "empty-final.events.json",
            [{**base_event, "text": " "}],
            "ASR final event text must be non-empty",
        ),
        (
            "empty-revision.events.json",
            [
                {
                    **base_event,
                    "event_type": "revision",
                    "segment_id": "asr_seg_rev",
                    "revision_of": "asr_seg_contract",
                    "text": " ",
                }
            ],
            "ASR revision event text must be non-empty",
        ),
        (
            "negative-timestamp.events.json",
            [{**base_event, "start_ms": -1}],
            "ASR final event start_ms must be a non-negative number",
        ),
        (
            "bad-time-order.events.json",
            [{**base_event, "start_ms": 3000, "end_ms": 2000}],
            "ASR final event end_ms must be greater than or equal to start_ms",
        ),
        (
            "bad-confidence.events.json",
            [{**base_event, "confidence": 1.5}],
            "ASR final event confidence must be between 0 and 1",
        ),
        (
            "revision-missing-base.events.json",
            [{**base_event, "event_type": "revision", "segment_id": "asr_seg_rev"}],
            "ASR revision event missing revision_of",
        ),
    ]
    client = TestClient(create_app())

    for filename, events, expected_error in cases:
        events_path = _write_asr_events_file(
            tmp_path,
            f"artifacts/tmp/asr_events/{filename}",
            events,
        )

        response = client.post(
            "/live/asr/local-event-files/sessions",
            json={
                "session_id": f"contract_{filename.replace('.', '_')}",
                "provider": "sherpa_onnx_streaming",
                "events_path": events_path,
            },
        )

        assert response.status_code == 422, filename
        detail = response.json()["detail"]
        assert detail["ingest_status"] == "blocked_by_event_contract"
        assert detail["validation_errors"] == [expected_error]
        assert detail["events_path"] == f"artifacts/tmp/asr_events/{filename}"
        assert detail["safe_to_call_remote_asr_now"] is False
        assert detail["safe_to_read_user_audio_now"] is False
        assert detail["safe_to_download_models_now"] is False


def test_create_asr_live_session_from_local_event_file_handles_absolute_and_missing_paths(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    inside_absolute_path = tmp_path / "artifacts" / "tmp" / "asr_events" / "inside-absolute.events.json"
    inside_absolute_path.parent.mkdir(parents=True, exist_ok=True)
    inside_absolute_path.write_text(
        json.dumps(_asr_live_payload()["streaming_events"], ensure_ascii=False),
        encoding="utf-8",
    )
    outside_path = tmp_path.parent / "outside-asr-events.json"
    outside_path.write_text(
        json.dumps(_asr_live_payload()["streaming_events"], ensure_ascii=False),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    inside_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "inside_absolute_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": str(inside_absolute_path),
        },
    )
    outside_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "outside_absolute_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": str(outside_path),
        },
    )
    missing_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "missing_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": "artifacts/tmp/asr_events/missing.events.json",
        },
    )

    assert inside_response.status_code == 201
    assert inside_response.json()["events_path"] == ("artifacts/tmp/asr_events/inside-absolute.events.json")
    assert outside_response.status_code == 422
    outside_detail = outside_response.json()["detail"]
    assert outside_detail["ingest_status"] == "blocked_by_path_validation"
    assert outside_detail["events_path"] == "<redacted_invalid_path>"
    assert outside_detail["validation_errors"] == [
        "events path is not under approved ASR events root",
    ]
    assert str(outside_path) not in outside_response.text
    assert missing_response.status_code == 422
    missing_detail = missing_response.json()["detail"]
    assert missing_detail["ingest_status"] == "blocked_by_invalid_events_file"
    assert missing_detail["events_path"] == "artifacts/tmp/asr_events/missing.events.json"
    assert missing_detail["validation_errors"] == [
        "ASR events file could not be read",
    ]
    assert str(tmp_path) not in missing_response.text


def test_create_asr_live_session_from_local_event_file_rejects_duplicate_session_without_mutation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    first_events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/duplicate-first.events.json",
        _asr_live_payload()["streaming_events"],
    )
    replacement_events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/duplicate-replacement.events.json",
        [
            {
                "event_type": "final",
                "segment_id": "asr_seg_replacement",
                "text": "API 替换内容不应该污染已有 session。",
                "start_ms": 0,
                "end_ms": 2000,
                "received_at_ms": 2100,
                "confidence": 0.9,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 2000,
                "end_ms": 2100,
                "received_at_ms": 2100,
            },
        ],
    )
    client = TestClient(create_app())

    first_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "duplicate_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": first_events_path,
        },
    )
    duplicate_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "duplicate_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": replacement_events_path,
        },
    )
    read_response = client.get("/live/asr/sessions/duplicate_local_asr_file_handoff/events")

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 422
    detail = duplicate_response.json()["detail"]
    assert detail["ingest_status"] == "blocked_by_duplicate_session"
    assert detail["events_path"] == "artifacts/tmp/asr_events/duplicate-replacement.events.json"
    assert detail["validation_errors"] == [
        "ASR live session already exists: duplicate_local_asr_file_handoff",
    ]
    assert detail["safe_to_call_llm_now"] is False
    assert read_response.status_code == 200
    read_text = read_response.text
    assert "替换内容不应该污染" not in read_text
    assert read_response.json()["events"] == first_response.json()["live_events"]


def test_create_asr_live_session_from_local_event_file_persists_across_app_instances(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/persisted-handoff.events.json",
        _asr_live_payload()["streaming_events"],
    )
    first_client = TestClient(create_app(data_dir=tmp_path / "repo-data"))

    create_response = first_client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "persisted_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": events_path,
        },
    )

    second_client = TestClient(create_app(data_dir=tmp_path / "repo-data"))
    json_response = second_client.get("/live/asr/sessions/persisted_local_asr_file_handoff/events")
    sse_response = second_client.get("/live/asr/sessions/persisted_local_asr_file_handoff/events.sse")

    assert create_response.status_code == 201
    assert json_response.status_code == 200
    assert json_response.json()["events"] == create_response.json()["live_events"]
    assert json_response.json()["source"] == "live_asr_stream"
    assert json_response.json()["trace_kind"] == "live_event"
    assert sse_response.status_code == 200
    assert "event: transcript_final" in sse_response.text
    assert "not_called" in sse_response.text


def test_create_asr_live_session_keeps_multi_state_scheduler_pairs_at_api_boundary():
    client = TestClient(create_app())
    payload = {
        "session_id": "local_asr_multi_state_review",
        "provider": "local_mock_asr",
        "streaming_events": [
            {
                "event_type": "final",
                "segment_id": "asr_seg_multi_001",
                "text": "先灰度 10%，谁负责回滚？",
                "start_ms": 0,
                "end_ms": 3200,
                "received_at_ms": 3500,
                "confidence": 0.9,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 3600,
                "end_ms": 3600,
                "received_at_ms": 3600,
            },
        ],
    }

    create_response = client.post("/live/asr/mock/sessions", json=payload)
    json_response = client.get("/live/asr/sessions/local_asr_multi_state_review/events")
    sse_response = client.get("/live/asr/sessions/local_asr_multi_state_review/events.sse")

    assert create_response.status_code == 201
    events = json_response.json()["events"]
    assert [event["event_type"] for event in events] == [
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]
    assert events[1]["payload"]["target_type"] == "DecisionCandidate"
    assert events[2]["payload"]["source_event_ids"] == ["asr_state_event_asr_seg_multi_001"]
    assert events[3]["payload"]["target_type"] == "DecisionCandidate"
    assert events[3]["payload"]["gap_rule_id"] == "release.rollback.owner.required"
    assert events[4]["payload"]["target_candidate_id"] == events[3]["payload"]["candidate_id"]
    assert events[5]["payload"]["target_type"] == "OpenQuestion"
    assert events[5]["payload"]["state_item"]["question"] == "先灰度 10%，谁负责回滚？"
    assert events[6]["payload"]["source_event_ids"] == ["asr_question_event_asr_seg_multi_001"]
    assert events[7]["payload"]["target_type"] == "OpenQuestion"
    assert events[7]["payload"]["gap_rule_id"] == "open.question.followup"
    assert events[8]["payload"]["target_candidate_id"] == events[7]["payload"]["candidate_id"]

    sse_events = [
        json.loads(line.removeprefix("data: ")) for line in sse_response.text.splitlines() if line.startswith("data: ")
    ]
    assert sse_events == events


def test_asr_live_suggestion_candidates_endpoint_returns_only_candidate_queue():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_candidate_query_review"),
    )

    response = client.get("/live/asr/sessions/local_asr_candidate_query_review/suggestion-candidates")

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_candidate_query_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["candidate_count"] == 5
    candidates = body["candidates"]
    assert [candidate["event_type"] for candidate in candidates] == [
        "suggestion_candidate_event",
        "suggestion_candidate_event",
        "suggestion_candidate_event",
        "suggestion_candidate_event",
        "suggestion_candidate_event",
    ]
    assert [candidate["sequence"] for candidate in candidates] == [
        5,
        10,
        15,
        20,
        25,
    ]
    assert "llm_request_draft_event" not in [candidate["event_type"] for candidate in candidates]
    assert [candidate["payload"]["gap_rule_id"] for candidate in candidates] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {candidate["payload"]["candidate_policy_version"] for candidate in candidates} == {"asr-candidate-policy.v1"}
    assert {candidate["payload"]["confidence_source"] for candidate in candidates} == {"local_deterministic_heuristic"}
    assert {candidate["payload"]["llm_call_status"] for candidate in candidates} == {"not_called"}
    assert {candidate["payload"]["card_status"] for candidate in candidates} == {"not_created"}
    assert candidates[0]["event_id"] == "suggestion_candidate:asr_state_event_asr_seg_001"
    assert candidates[0]["at_ms"] == 3500
    assert "payload" in candidates[0]


def test_asr_live_suggestion_candidates_endpoint_returns_empty_queue_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_candidate_empty_review",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_transcript_only_001",
                    "text": "今天我们同步一下背景信息。",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "received_at_ms": 2500,
                    "confidence": 0.9,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "asr_eos",
                    "text": "",
                    "start_ms": 2600,
                    "end_ms": 2600,
                    "received_at_ms": 2600,
                },
            ],
        },
    )

    response = client.get("/live/asr/sessions/local_asr_candidate_empty_review/suggestion-candidates")

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "local_asr_candidate_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "candidate_count": 0,
        "candidates": [],
    }


def test_asr_live_suggestion_candidates_endpoint_reads_persisted_record_across_app_instances(tmp_path):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_candidate_query_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get("/live/asr/sessions/persisted_asr_candidate_query_review/suggestion-candidates")

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_count"] == 5
    assert body["candidates"] == [
        {
            "sequence": event["sequence"],
            "event_id": event["id"],
            "event_type": event["event_type"],
            "at_ms": event["at_ms"],
            "payload": event["payload"],
        }
        for event in create_response.json()["live_events"]
        if event["event_type"] == "suggestion_candidate_event"
    ]


def test_asr_live_suggestion_candidates_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get("/live/asr/sessions/missing_asr_review/suggestion-candidates")

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_request_drafts_endpoint_returns_only_request_draft_queue():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_request_draft_query_review"),
    )

    response = client.get("/live/asr/sessions/local_asr_request_draft_query_review/llm-request-drafts")

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_request_draft_query_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["request_draft_count"] == 5
    drafts = body["request_drafts"]
    assert [draft["event_type"] for draft in drafts] == [
        "llm_request_draft_event",
        "llm_request_draft_event",
        "llm_request_draft_event",
        "llm_request_draft_event",
        "llm_request_draft_event",
    ]
    assert [draft["sequence"] for draft in drafts] == [6, 11, 16, 21, 26]
    assert "suggestion_candidate_event" not in [draft["event_type"] for draft in drafts]
    assert {draft["payload"]["request_status"] for draft in drafts} == {"draft_only"}
    assert {draft["payload"]["llm_call_status"] for draft in drafts} == {"not_called"}
    assert {draft["payload"]["schema_status"] for draft in drafts} == {"not_generated"}
    assert {draft["payload"]["card_status"] for draft in drafts} == {"not_created"}
    assert drafts[0]["event_id"] == "llm_request_draft:asr_state_event_asr_seg_001"
    assert drafts[0]["at_ms"] == 3500
    assert "payload" in drafts[0]


def test_asr_live_llm_request_drafts_endpoint_returns_empty_queue_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_request_draft_empty_review",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_transcript_only_001",
                    "text": "今天我们同步一下背景信息。",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "received_at_ms": 2500,
                    "confidence": 0.9,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "asr_eos",
                    "text": "",
                    "start_ms": 2600,
                    "end_ms": 2600,
                    "received_at_ms": 2600,
                },
            ],
        },
    )

    response = client.get("/live/asr/sessions/local_asr_request_draft_empty_review/llm-request-drafts")

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "local_asr_request_draft_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "request_draft_count": 0,
        "request_drafts": [],
    }


def test_asr_live_llm_request_drafts_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_request_draft_query_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get("/live/asr/sessions/persisted_asr_request_draft_query_review/llm-request-drafts")

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["request_draft_count"] == 5
    assert body["request_drafts"] == [
        {
            "sequence": event["sequence"],
            "event_id": event["id"],
            "event_type": event["event_type"],
            "at_ms": event["at_ms"],
            "payload": event["payload"],
        }
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]


def test_asr_live_llm_request_drafts_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get("/live/asr/sessions/missing_asr_review/llm-request-drafts")

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_execution_previews_endpoint_returns_preview_queue_without_calling_llm():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_preview_review"),
    )

    response = client.get("/live/asr/sessions/local_asr_execution_preview_review/llm-execution-previews")
    events_response = client.get("/live/asr/sessions/local_asr_execution_preview_review/events")

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_execution_preview_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["execution_preview_count"] == 5
    previews = body["execution_previews"]
    assert [preview["request_draft_event_id"] for preview in previews] == [
        event["id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert [preview["request_draft_sequence"] for preview in previews] == [
        6,
        11,
        16,
        21,
        26,
    ]
    assert previews[0] == {
        "execution_id": (
            "asr_llm_execution_preview_asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "execution_status": "preview_only",
        "request_id": ("asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"),
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
        "request_draft_sequence": 6,
        "request_type": "llm_suggestion_card_draft",
        "target_candidate_id": ("asr_suggestion_candidate_asr_state_event_asr_seg_001"),
        "target_type": "DecisionCandidate",
        "target_id": "asr_decision_asr_seg_001",
        "gap_rule_id": "release.rollback.owner.required",
        "prompt_version": "suggestion-card-execution-preview.v1",
        "provider": "not_configured",
        "model": "not_called",
        "llm_call_status": "not_called",
        "schema_name": "SuggestionCardV1",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "idempotency_key": (
            "live_asr_execution_preview:local_asr_execution_preview_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "source_event_ids": ["asr_state_event_asr_seg_001"],
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "evidence_spans": [
            {
                "id": "asr_ev_asr_seg_001",
                "segment_id": "asr_seg_001",
                "start_ms": 0,
                "end_ms": 3200,
                "quote": "先灰度 10%。",
                "status": "active",
            }
        ],
        "evidence_context": "[00:00-00:03] 先灰度 10%。",
        "segment_batch": ["asr_seg_001"],
        "candidate_confidence": 0.9,
        "candidate_confidence_level": "high",
        "candidate_degradation_reasons": [],
        "input_summary": "DecisionCandidate asr_decision_asr_seg_001 from asr_seg_001 using asr_ev_asr_seg_001",
        "suggested_prompt": "确认决策是否包含 owner、回滚条件和监控口径。",
    }
    assert {preview["execution_status"] for preview in previews} == {"preview_only"}
    assert {preview["llm_call_status"] for preview in previews} == {"not_called"}
    assert {preview["schema_status"] for preview in previews} == {"not_generated"}
    assert {preview["card_status"] for preview in previews} == {"not_created"}
    assert {preview["cost_status"] for preview in previews} == {"not_estimated"}
    assert {preview["provider"] for preview in previews} == {"not_configured"}
    assert {preview["model"] for preview in previews} == {"not_called"}
    assert all(preview["source_event_ids"] for preview in previews)
    assert all(preview["evidence_span_ids"] for preview in previews)
    assert all(preview["segment_batch"] for preview in previews)
    assert events_response.status_code == 200
    assert events_response.json()["events"] == create_response.json()["live_events"]
    assert "llm_schema_result" not in [event["event_type"] for event in events_response.json()["events"]]
    assert "suggestion_card" not in [event["event_type"] for event in events_response.json()["events"]]
    assert "suggestion_silenced" not in [event["event_type"] for event in events_response.json()["events"]]


def test_asr_live_llm_execution_previews_endpoint_returns_empty_queue_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_execution_preview_empty_review",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_transcript_only_001",
                    "text": "今天我们同步一下背景信息。",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "received_at_ms": 2500,
                    "confidence": 0.9,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "asr_eos",
                    "text": "",
                    "start_ms": 2600,
                    "end_ms": 2600,
                    "received_at_ms": 2600,
                },
            ],
        },
    )

    response = client.get("/live/asr/sessions/local_asr_execution_preview_empty_review/llm-execution-previews")

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "local_asr_execution_preview_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "execution_preview_count": 0,
        "execution_previews": [],
    }


def test_asr_live_llm_execution_previews_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_execution_preview_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get("/live/asr/sessions/persisted_asr_execution_preview_review/llm-execution-previews")

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["execution_preview_count"] == 5
    assert [preview["request_id"] for preview in body["execution_previews"]] == [
        event["payload"]["request_id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert body["execution_previews"][0]["idempotency_key"] == (
        "live_asr_execution_preview:persisted_asr_execution_preview_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )


def test_asr_live_llm_execution_previews_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get("/live/asr/sessions/missing_asr_review/llm-execution-previews")

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_execution_runs_disabled_endpoint_returns_skipped_runs_without_calling_llm():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_disabled_run_review"),
    )
    events_before_response = client.get("/live/asr/sessions/local_asr_execution_disabled_run_review/events")

    response = client.post(
        "/live/asr/sessions/local_asr_execution_disabled_run_review/llm-execution-runs",
        json={"mode": "disabled"},
    )
    events_after_response = client.get("/live/asr/sessions/local_asr_execution_disabled_run_review/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_execution_disabled_run_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["executor_mode"] == "disabled"
    assert body["run_count"] == 5
    runs = body["runs"]
    assert [run["request_draft_event_id"] for run in runs] == [
        event["id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert [run["request_draft_sequence"] for run in runs] == [
        6,
        11,
        16,
        21,
        26,
    ]
    assert runs[0] == {
        "run_id": (
            "asr_llm_execution_run_disabled_"
            "asr_llm_execution_preview_"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "run_status": "skipped",
        "skip_reason": "llm_executor_disabled",
        "execution_id": (
            "asr_llm_execution_preview_asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "execution_status": "preview_only",
        "request_id": ("asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"),
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
        "request_draft_sequence": 6,
        "request_type": "llm_suggestion_card_draft",
        "target_candidate_id": ("asr_suggestion_candidate_asr_state_event_asr_seg_001"),
        "target_type": "DecisionCandidate",
        "target_id": "asr_decision_asr_seg_001",
        "gap_rule_id": "release.rollback.owner.required",
        "prompt_version": "suggestion-card-execution-preview.v1",
        "provider": "not_configured",
        "model": "not_called",
        "llm_call_status": "not_called",
        "schema_name": "SuggestionCardV1",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "idempotency_key": (
            "live_asr_execution_run:disabled:"
            "local_asr_execution_disabled_run_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "source_event_ids": ["asr_state_event_asr_seg_001"],
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "evidence_spans": [
            {
                "id": "asr_ev_asr_seg_001",
                "segment_id": "asr_seg_001",
                "start_ms": 0,
                "end_ms": 3200,
                "quote": "先灰度 10%。",
                "status": "active",
            }
        ],
        "evidence_context": "[00:00-00:03] 先灰度 10%。",
        "segment_batch": ["asr_seg_001"],
        "candidate_confidence": 0.9,
        "candidate_confidence_level": "high",
        "candidate_degradation_reasons": [],
        "input_summary": "DecisionCandidate asr_decision_asr_seg_001 from asr_seg_001 using asr_ev_asr_seg_001",
        "suggested_prompt": "确认决策是否包含 owner、回滚条件和监控口径。",
    }
    assert {run["run_status"] for run in runs} == {"skipped"}
    assert {run["skip_reason"] for run in runs} == {"llm_executor_disabled"}
    assert {run["llm_call_status"] for run in runs} == {"not_called"}
    assert {run["schema_status"] for run in runs} == {"not_generated"}
    assert {run["card_status"] for run in runs} == {"not_created"}
    assert {run["cost_status"] for run in runs} == {"not_estimated"}
    assert {run["provider"] for run in runs} == {"not_configured"}
    assert {run["model"] for run in runs} == {"not_called"}
    assert all(run["source_event_ids"] for run in runs)
    assert all(run["evidence_span_ids"] for run in runs)
    assert all(run["segment_batch"] for run in runs)
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    assert "llm_schema_result" not in [event["event_type"] for event in events_after_response.json()["events"]]
    assert "suggestion_card" not in [event["event_type"] for event in events_after_response.json()["events"]]
    assert "suggestion_silenced" not in [event["event_type"] for event in events_after_response.json()["events"]]


def test_asr_live_llm_execution_runs_disabled_endpoint_returns_empty_runs_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_execution_disabled_empty_review",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_transcript_only_001",
                    "text": "今天我们同步一下背景信息。",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "received_at_ms": 2500,
                    "confidence": 0.9,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "asr_eos",
                    "text": "",
                    "start_ms": 2600,
                    "end_ms": 2600,
                    "received_at_ms": 2600,
                },
            ],
        },
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_disabled_empty_review/llm-execution-runs",
        json={"mode": "disabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in ("session_id", "source", "trace_kind", "executor_mode", "run_count", "runs")} == {
        "session_id": "local_asr_execution_disabled_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "executor_mode": "disabled",
        "run_count": 0,
        "runs": [],
    }
    assert body["llm_provider"] == {
        "provider": "not_configured",
        "model": "not_called",
        "realtime_model": "not_called",
        "realtime_model_source": "not_configured",
        "realtime_model_explicit": False,
        "realtime_model_warning": None,
        "correction_model": "not_called",
        "correction_model_source": "not_configured",
        "correction_model_explicit": False,
        "correction_model_warning": None,
        "configured_from_env": False,
        "is_mock": False,
        "api_style": "not_configured",
    }


def test_asr_live_llm_execution_runs_disabled_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_execution_disabled_run_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_execution_disabled_run_review/llm-execution-runs",
        json={"mode": "disabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["executor_mode"] == "disabled"
    assert body["run_count"] == 5
    assert [run["request_id"] for run in body["runs"]] == [
        event["payload"]["request_id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert body["runs"][0]["idempotency_key"] == (
        "live_asr_execution_run:disabled:persisted_asr_execution_disabled_run_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )


def _create_acceptance_eligible_asr_live_session(tmp_path, session_id: str) -> TestClient:
    client = TestClient(create_app(data_dir=tmp_path))
    repo = app_module.SqliteAsrLiveSessionRepository(tmp_path)
    events = app_module.build_asr_live_events(
        session_id=session_id,
        provider="sherpa_onnx_realtime",
        streaming_events=[
            {
                "event_type": "final",
                "segment_id": "asr_seg_001",
                "text": "先灰度 10%。谁负责回滚？",
                "start_ms": 0,
                "end_ms": 3200,
                "received_at_ms": 3500,
                "confidence": 0.91,
            }
        ],
        is_mock=False,
    )
    repo.create(
        {
            "session_id": session_id,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "sherpa_onnx_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "real_mic",
            "asr_fallback_used": False,
            "degradation_reasons": [],
            "events": events,
        }
    )
    return client


def test_asr_live_production_derivation_endpoints_reject_mock_llm_provider(monkeypatch, tmp_path):
    session_id = "production_mock_llm_provider_blocked"
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "true")
    client = _create_acceptance_eligible_asr_live_session(tmp_path, session_id)

    endpoints = [
        f"/live/asr/sessions/{session_id}/llm-execution-runs",
        f"/live/asr/sessions/{session_id}/approach-cards",
        f"/live/asr/sessions/{session_id}/minutes",
        f"/live/asr/sessions/{session_id}/minutes.json",
    ]
    responses = [client.post(endpoint, json={"mode": "enabled"}) for endpoint in endpoints]

    for response in responses:
        assert response.status_code == 409
        assert "mock LLM provider cannot create production derivations" in response.text


def test_asr_live_llm_execution_runs_disabled_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/sessions/missing_asr_review/llm-execution-runs",
        json={"mode": "disabled"},
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_execution_runs_endpoint_rejects_unsupported_mode():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_mode_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_mode_review/llm-execution-runs",
        json={"mode": "foo"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "unsupported llm execution mode: foo" in response.text


def test_asr_live_llm_execution_runs_enabled_without_config_returns_422(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", "missing.env")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_enabled_no_cfg"),
    )
    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_enabled_no_cfg/llm-execution-runs",
        json={"mode": "enabled"},
    )
    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "not configured" in response.text


def test_asr_live_llm_execution_runs_enabled_rejects_mock_session_without_explicit_demo_allowance(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_enabled_mock_blocked"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_enabled_mock_blocked/llm-execution-runs",
        json={"mode": "enabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 409
    assert "not eligible for enabled LLM execution" in response.text
    assert "mock_or_demo_session" in response.text


def test_enabled_llm_allows_persisted_finals_with_recoverable_refinement_interruption(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    record = {
        "session_id": "recoverable_refinement_interruption",
        "source": "live_asr_stream",
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "input_source": "browser_live_mic",
        "degradation_reasons": [
            "stream_interrupted",
            "offline_refinement_unavailable",
            "offline_refinement_text_too_short",
        ],
        "events": [
            {
                "event_type": "transcript_final",
                "payload": {"segment_id": "segment-1", "text": "已有可用的会议正文。"},
            }
        ],
    }

    app_module._ensure_enabled_llm_allowed(record, allow_non_acceptance_execution=False)
    assert app_module._realtime_correction_blockers(record) == []


def test_enabled_llm_allows_online_only_policy_with_recoverable_boundary_tail(monkeypatch):
    """A real final stays usable when resource policy and boundary tail coexist."""

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    record = {
        "session_id": "online-only-boundary-tail",
        "source": "live_asr_stream",
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "input_source": "browser_live_mic",
        "degradation_reasons": [
            "offline_refinement_bypassed_by_resource_policy",
            "funasr_boundary_ack_timeout",
        ],
        "events": [
            {
                "event_type": "transcript_final",
                "payload": {
                    "segment_id": "segment-1",
                    "text": "发布由李四负责，周五完成，错误率超过百分之二就回滚。",
                },
            }
        ],
    }

    app_module._ensure_enabled_llm_allowed(record, allow_non_acceptance_execution=False)
    assert app_module._realtime_correction_blockers(record) == []


def test_asr_live_enabled_approach_and_minutes_reject_mock_session_without_explicit_demo_allowance(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_enabled_derivatives_mock_blocked"),
    )

    approach = client.post(
        "/live/asr/sessions/local_asr_enabled_derivatives_mock_blocked/approach-cards",
        json={"mode": "enabled"},
    )
    minutes = client.post(
        "/live/asr/sessions/local_asr_enabled_derivatives_mock_blocked/minutes",
        json={"mode": "enabled"},
    )
    minutes_json = client.post(
        "/live/asr/sessions/local_asr_enabled_derivatives_mock_blocked/minutes.json",
        json={"mode": "enabled"},
    )

    assert create_response.status_code == 201
    for response in (approach, minutes, minutes_json):
        assert response.status_code == 409
        assert "not eligible for enabled LLM execution" in response.text
        assert "mock_or_demo_session" in response.text


def test_asr_live_production_derivation_endpoints_reject_non_acceptance_bypass_field(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="public_bypass_field_blocked"),
    )

    endpoints = [
        "/live/asr/sessions/public_bypass_field_blocked/llm-execution-runs",
        "/live/asr/sessions/public_bypass_field_blocked/approach-cards",
        "/live/asr/sessions/public_bypass_field_blocked/minutes",
        "/live/asr/sessions/public_bypass_field_blocked/minutes.json",
    ]
    responses = [
        client.post(endpoint, json={"mode": "enabled", "allow_non_acceptance_execution": True})
        for endpoint in endpoints
    ]

    assert create_response.status_code == 201
    for response in responses:
        assert response.status_code == 422
        assert "allow_non_acceptance_execution" in response.text


def test_demo_derivation_endpoint_can_execute_mock_session_without_public_bypass_field(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="demo_derivation_run"),
    )

    response = client.post(
        "/live/asr/demo/sessions/demo_derivation_run/llm-execution-runs",
        json={"mode": "enabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executor_mode"] == "enabled"
    assert body["execution_boundary"] == "demo_non_acceptance_execution"
    assert body["run_count"] >= 1
    assert body["runs"][0]["card"]["suggestion_text"] == "建议确认 owner"


def test_asr_live_llm_execution_runs_enabled_calls_llm_and_creates_real_cards(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, headers, body, timeout):
            self.calls += 1
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    fake = FakeClient()
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    raw_base_url = "https://private-gateway.example/internal"
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", raw_base_url)
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "team_gateway")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_enabled_run"),
    )
    assert create_response.status_code == 201
    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_enabled_run/llm-execution-runs",
        json={"mode": "enabled"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executor_mode"] == "enabled"
    assert body["run_count"] >= 1
    run = body["runs"][0]
    assert run["run_status"] == "completed"
    assert run["llm_call_status"] == "called"
    assert run["card_status"] == "new"
    assert run["card"]["card_status"] == "new"
    assert run["card"]["suggestion_text"]
    assert run["card"]["llm_trace"]["model"] == "test-model"
    assert run["provider"] == "team_gateway"
    assert run["card"]["llm_trace"]["provider"] == "team_gateway"
    assert run["llm_usage"]["total_tokens"] == 130
    assert fake.calls == body["run_count"]
    assert raw_base_url not in response.text
    persisted = client.get("/live/asr/sessions/local_asr_execution_enabled_run/events")
    assert persisted.status_code == 200
    assert raw_base_url not in persisted.text


def test_asr_live_llm_execution_runs_enabled_caps_long_meeting_candidates(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, headers, body, timeout):
            self.calls += 1
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"suggestion_text":"建议先处理最高价值的现场提醒",'
                                '"confidence":0.82,'
                                '"trigger_reason":"长会议候选较多，需要限流"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    fake = FakeClient()
    streaming_events = []
    for index in range(8):
        start_ms = index * 10_000
        streaming_events.append(
            {
                "event_type": "final",
                "segment_id": f"asr_long_seg_{index:03d}",
                "text": (f"第 {index} 个接口发布先灰度 5%，如果错误率超过 0.1% 就回滚，谁负责回滚？"),
                "start_ms": start_ms,
                "end_ms": start_ms + 7_000,
                "received_at_ms": start_ms + 7_500,
                "confidence": 0.91,
            }
        )
    streaming_events.append(
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_long_eos",
            "text": "",
            "start_ms": 90_000,
            "end_ms": 91_000,
            "received_at_ms": 91_000,
        }
    )
    payload = {
        "session_id": "local_asr_execution_long_candidate_cap",
        "provider": "local_mock_asr",
        "streaming_events": streaming_events,
    }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    monkeypatch.setenv("LLM_EXECUTION_MAX_CANDIDATES_PER_RUN", "3")
    client = TestClient(create_app())
    create_response = client.post("/live/asr/mock/sessions", json=payload)
    assert create_response.status_code == 201
    candidate_count = sum(
        1 for event in create_response.json()["live_events"] if event["event_type"] == "llm_request_draft_event"
    )
    assert candidate_count > 3

    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_long_candidate_cap/llm-execution-runs",
        json={"mode": "enabled"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    selection = body["candidate_selection"]
    assert body["run_count"] == 3
    assert fake.calls == 3
    assert selection["policy_version"] == "llm-execution-candidate-selection.v1"
    assert selection["total_candidates"] == candidate_count
    assert selection["max_candidates"] == 3
    assert selection["selected_count"] == 3
    assert selection["skipped_count"] == candidate_count - 3
    assert selection["selection_applied"] is True
    assert len(selection["selected_candidate_ids"]) == 3
    assert len(selection["skipped_candidate_ids"]) == candidate_count - 3


def test_asr_live_llm_execution_runs_enabled_honors_request_candidate_budget(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, headers, body, timeout):
            self.calls += 1
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"suggestion_text":"建议先生成一条最高价值建议",'
                                '"confidence":0.84,'
                                '"trigger_reason":"整理会议快路径"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    fake = FakeClient()
    payload = _asr_live_payload(session_id="local_asr_execution_request_budget")
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    monkeypatch.setenv("LLM_EXECUTION_MAX_CANDIDATES_PER_RUN", "5")
    client = TestClient(create_app())
    create_response = client.post("/live/asr/mock/sessions", json=payload)
    assert create_response.status_code == 201

    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_request_budget/llm-execution-runs",
        json={"mode": "enabled", "max_candidates": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    selection = body["candidate_selection"]
    assert body["run_count"] == 1
    assert fake.calls == 1
    assert selection["max_candidates"] == 1
    assert selection["requested_max_candidates"] == 1
    assert selection["selection_reason"] == "request_max_candidates"
    assert selection["selected_count"] == 1
    assert selection["skipped_count"] >= 1


def test_asr_live_llm_execution_runs_enabled_persists_cards_for_history(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_persist_cards"),
    )

    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_persist_cards/llm-execution-runs",
        json={"mode": "enabled"},
    )
    fetched = client.get("/live/asr/sessions/local_asr_execution_persist_cards/events")
    history = client.get("/live/asr/sessions?include_demo=true")

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert fetched.status_code == 200
    record = fetched.json()
    assert record["suggestion_cards"]
    assert record["suggestion_cards"][0]["suggestion_text"] == "建议确认 owner"
    assert record["suggestion_cards"][0]["evidence_span_ids"]
    indexed = {item["session_id"]: item for item in history.json()["sessions"]}
    assert indexed["local_asr_execution_persist_cards"]["suggestion_card_count"] >= 1


def test_asr_live_llm_execution_runs_disabled_endpoint_requires_explicit_mode():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_missing_mode_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_missing_mode_review/llm-execution-runs",
        json={},
    )

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "mode" in response.text


def test_asr_live_llm_execution_runs_disabled_endpoint_rejects_empty_body():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_empty_body_review"),
    )

    response = client.post("/live/asr/sessions/local_asr_execution_empty_body_review/llm-execution-runs")

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "Field required" in response.text


def test_asr_live_llm_execution_runs_disabled_endpoint_rejects_extra_fields():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_extra_field_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_extra_field_review/llm-execution-runs",
        json={"mode": "disabled", "api_key": "ignored-test-value"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "Extra inputs are not permitted" in response.text


def test_asr_live_session_persists_json_events_across_app_instances(tmp_path):
    first_client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="persisted_asr_live_review")

    create_response = first_client.post("/live/asr/mock/sessions", json=payload)

    assert create_response.status_code == 201
    second_client = TestClient(create_app(data_dir=tmp_path))
    json_response = second_client.get("/live/asr/sessions/persisted_asr_live_review/events")
    sse_response = second_client.get("/live/asr/sessions/persisted_asr_live_review/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    assert events == create_response.json()["live_events"]
    assert json_response.json()["source"] == "live_asr_stream"
    assert json_response.json()["trace_kind"] == "live_event"
    assert sse_response.status_code == 200
    assert "event: state_event" in sse_response.text
    assert "谁负责回滚？" in sse_response.text


def test_delete_session_removes_persisted_asr_live_audit_record(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="delete_asr_live_review")
    create_response = client.post("/live/asr/mock/sessions", json=payload)

    delete_response = client.delete("/sessions/delete_asr_live_review")
    json_response = client.get("/live/asr/sessions/delete_asr_live_review/events")

    assert create_response.status_code == 201
    assert delete_response.status_code == 204
    assert json_response.status_code == 404
    assert "ASR live session not found: delete_asr_live_review" in json_response.text


def test_delete_asr_live_session_reports_exact_delete_scope_without_overclaiming(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="delete_scope_review")
    create_response = client.post("/live/asr/mock/sessions", json=payload)

    delete_response = client.delete("/live/asr/sessions/delete_scope_review")
    json_response = client.get("/live/asr/sessions/delete_scope_review/events")

    assert create_response.status_code == 201
    assert delete_response.status_code == 200
    body = delete_response.json()
    assert body["deleted"] is True
    assert body["session_record_deleted"] is True
    assert body["delete_scope"] == {
        "session_record": "deleted",
        "transcript_events": "deleted_with_session_record",
        "suggestion_cards": "deleted_with_session_record",
        "approach_cards": "deleted_with_session_record",
        "minutes": "deleted_with_session_record",
        "audio": "not_present",
        "exports": "not_tracked_by_live_session_repo",
        "evidence_bundle": "not_tracked_by_live_session_repo",
    }
    assert "cascade" not in body
    assert json_response.status_code == 404


@pytest.mark.parametrize(
    "read_error_type",
    [sqlite3.OperationalError, OSError],
    ids=["sqlite_error", "os_error"],
)
def test_delete_asr_live_session_initial_read_failure_is_structured_and_fail_closed(
    monkeypatch,
    tmp_path,
    read_error_type,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_live_initial_read_failure"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    original_get = app_module.SqliteAsrLiveSessionRepository.get
    audio_delete_called = False

    def fail_initial_read(self, candidate_session_id):
        if candidate_session_id == session_id:
            raise read_error_type("DO_NOT_LEAK_INITIAL_READ_DETAIL")
        return original_get(self, candidate_session_id)

    def track_audio_delete(*args, **kwargs):
        nonlocal audio_delete_called
        audio_delete_called = True
        return "deleted"

    monkeypatch.setattr(
        app_module.SqliteAsrLiveSessionRepository,
        "get",
        fail_initial_read,
    )
    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", track_audio_delete)

    delete_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert create_response.status_code == 201
    assert delete_response.status_code == 500
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is False
    assert body["delete_scope"]["session_record"] == "read_failed"
    assert body["delete_scope"]["audio"] == "retained_not_attempted"
    assert body["errors"] == [
        {
            "scope": "session_record",
            "code": "read_failed",
            "error_type": read_error_type.__name__,
        }
    ]
    assert "DO_NOT_LEAK_INITIAL_READ_DETAIL" not in delete_response.text
    assert audio_delete_called is False
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "read_error_type",
    [sqlite3.OperationalError, OSError],
    ids=["sqlite_error", "os_error"],
)
def test_delete_session_initial_live_read_failure_is_structured_and_fail_closed(
    monkeypatch,
    tmp_path,
    read_error_type,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_combined_initial_read_failure"
    session_payload = _payload()
    session_payload["session_id"] = session_id
    session_create_response = client.post("/sessions", json=session_payload)
    live_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    original_get = app_module.SqliteAsrLiveSessionRepository.get
    audio_delete_called = False

    def fail_initial_read(self, candidate_session_id):
        if candidate_session_id == session_id:
            raise read_error_type("DO_NOT_LEAK_INITIAL_READ_DETAIL")
        return original_get(self, candidate_session_id)

    def track_audio_delete(*args, **kwargs):
        nonlocal audio_delete_called
        audio_delete_called = True
        return "deleted"

    monkeypatch.setattr(
        app_module.SqliteAsrLiveSessionRepository,
        "get",
        fail_initial_read,
    )
    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", track_audio_delete)

    delete_response = client.delete(f"/sessions/{session_id}")

    assert session_create_response.status_code == 201
    assert live_create_response.status_code == 201
    assert delete_response.status_code == 500
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is False
    assert body["live_session_record_deleted"] is False
    assert body["delete_scope"] == {
        "session_record": "retained_not_attempted",
        "live_session_record": "read_failed",
        "audio": "retained_not_attempted",
    }
    assert body["errors"] == [
        {
            "scope": "live_session_record",
            "code": "read_failed",
            "error_type": read_error_type.__name__,
        }
    ]
    assert "DO_NOT_LEAK_INITIAL_READ_DETAIL" not in delete_response.text
    assert audio_delete_called is False
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 1
        )


def test_delete_asr_live_session_keeps_audio_when_record_delete_fails(
    monkeypatch,
    tmp_path,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_record_failure_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
            },
        },
    )
    audio_delete_called = False

    def fail_record_delete(self, candidate_session_id):
        if candidate_session_id == session_id:
            raise OSError("synthetic database delete failure")
        return {}

    def track_audio_delete(*args, **kwargs):
        nonlocal audio_delete_called
        audio_delete_called = True
        return "deleted"

    monkeypatch.setattr(
        app_module.SqlitePersistenceCoordinator,
        "delete_live_session",
        fail_record_delete,
    )
    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", track_audio_delete)

    delete_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert create_response.status_code == 201
    assert delete_response.status_code == 500
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is False
    assert body["delete_scope"]["session_record"] == "retained_after_rollback"
    assert body["delete_scope"]["audio"] == "retained_not_attempted"
    assert audio_delete_called is False
    assert audio_path.is_file()
    assert client.get(f"/live/asr/sessions/{session_id}/events").status_code == 200


def test_delete_asr_live_session_reports_partial_failure_after_audio_delete_error(
    monkeypatch,
    tmp_path,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_audio_failure_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
            },
        },
    )

    def fail_audio_delete(*args, **kwargs):
        raise OSError("synthetic audio delete failure")

    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", fail_audio_delete)

    delete_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert create_response.status_code == 201
    assert delete_response.status_code == 207
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is True
    assert body["delete_scope"]["session_record"] == "deleted"
    assert body["delete_scope"]["audio"] == "cleanup_pending"
    assert body["audio_cleanup_pending"] is True
    assert audio_path.is_file()
    assert client.get(f"/live/asr/sessions/{session_id}/events").status_code == 404


def test_delete_session_reports_partial_failure_without_retaining_database_records(
    monkeypatch,
    tmp_path,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_combined_audio_failure_review"
    session_payload = _payload()
    session_payload["session_id"] = session_id
    session_create_response = client.post("/sessions", json=session_payload)
    live_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
            },
        },
    )

    def fail_audio_delete(*args, **kwargs):
        raise OSError("synthetic audio delete failure")

    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", fail_audio_delete)

    delete_response = client.delete(f"/sessions/{session_id}")

    assert session_create_response.status_code == 201
    assert live_create_response.status_code == 201
    assert delete_response.status_code == 207
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is True
    assert body["live_session_record_deleted"] is True
    assert body["delete_scope"] == {
        "session_record": "deleted",
        "live_session_record": "deleted",
        "audio": "cleanup_pending",
    }
    assert body["audio_cleanup_pending"] is True
    assert audio_path.is_file()
    assert client.get(f"/sessions/{session_id}").status_code == 404
    assert client.get(f"/live/asr/sessions/{session_id}/events").status_code == 404


def test_delete_asr_live_session_persists_cleanup_job_and_retries_idempotently(
    monkeypatch,
    tmp_path,
):
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "delete_live_cleanup_retry"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
                "original_filename": "DO_NOT_PERSIST_SECRET_NAME.wav",
                "sha256": "DO_NOT_PERSIST_SECRET_HASH",
            },
        },
    )
    original_delete = app_module.audio_assets.delete_audio_asset
    attempts = 0

    def fail_once(data_dir, audio):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic cleanup failure")
        return original_delete(data_dir, audio)

    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", fail_once)

    first_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert create_response.status_code == 201
    assert first_response.status_code == 207
    assert first_response.json()["delete_scope"]["audio"] == "cleanup_pending"
    assert first_response.json()["audio_cleanup_pending"] is True
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        pending_json = connection.execute(
            "SELECT audio_json FROM pending_audio_cleanup WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )
    assert json.loads(pending_json) == {"relative_path": f"audio_assets/{session_id}/audio.wav"}
    assert "DO_NOT_PERSIST_SECRET" not in pending_json
    assert audio_path.is_file()

    retry_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert retry_response.status_code == 200
    assert retry_response.json()["deleted"] is True
    assert retry_response.json()["delete_scope"]["audio"] == "deleted"
    assert attempts == 2
    assert not audio_path.exists()
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM pending_audio_cleanup WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )


def test_delete_session_persists_cleanup_job_and_retries_idempotently(
    monkeypatch,
    tmp_path,
):
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "delete_bundle_cleanup_retry"
    session_payload = _payload()
    session_payload["session_id"] = session_id
    session_create_response = client.post("/sessions", json=session_payload)
    live_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
            },
        },
    )
    original_delete = app_module.audio_assets.delete_audio_asset
    attempts = 0

    def fail_once(data_dir, audio):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic cleanup failure")
        return original_delete(data_dir, audio)

    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", fail_once)

    first_response = client.delete(f"/sessions/{session_id}")

    assert session_create_response.status_code == 201
    assert live_create_response.status_code == 201
    assert first_response.status_code == 207
    assert first_response.json()["delete_scope"]["audio"] == "cleanup_pending"
    assert first_response.json()["audio_cleanup_pending"] is True
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM pending_audio_cleanup WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 1
        )

    retry_response = client.delete(f"/sessions/{session_id}")

    assert retry_response.status_code == 204
    assert attempts == 2
    assert not audio_path.exists()
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM pending_audio_cleanup WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )


def test_delete_session_rolls_back_both_rows_and_cleanup_job_when_live_delete_fails(
    tmp_path,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_bundle_atomic_rollback"
    session_payload = _payload()
    session_payload["session_id"] = session_id
    session_create_response = client.post("/sessions", json=session_payload)
    live_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        connection.execute(
            "CREATE TRIGGER fail_live_delete BEFORE DELETE ON asr_live_sessions "
            "WHEN OLD.session_id = 'delete_bundle_atomic_rollback' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic live delete failure'); END"
        )

    delete_response = client.delete(f"/sessions/{session_id}")

    assert session_create_response.status_code == 201
    assert live_create_response.status_code == 201
    assert delete_response.status_code == 500
    assert delete_response.json()["delete_scope"] == {
        "session_record": "retained_after_rollback",
        "live_session_record": "retained_after_rollback",
        "audio": "retained_not_attempted",
    }
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM pending_audio_cleanup WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )


def test_create_asr_live_session_rejects_unsafe_session_id_with_json_persistence(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="../bad")

    response = client.post("/live/asr/mock/sessions", json=payload)

    assert response.status_code == 422
    assert "unsafe session_id" in response.text


def test_asr_live_draft_review_json_summarizes_audit_record_without_llm(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="live_asr_draft_review"),
    )

    response = client.get("/live/asr/sessions/live_asr_draft_review/draft")

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "live_asr_draft_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["review_type"] == "asr_live_draft"
    assert body["is_formal_report"] is False
    assert body["llm_call_status"] == "not_called"
    assert (
        body["transcript_text"]
        == "先灰度 10%。先灰度 5%，不是 10%。谁负责回滚？如果错误率超过 0.1% 就回滚。张三下周三补充兼容性测试用例。"
    )
    assert [segment["id"] for segment in body["transcript_segments"]] == [
        "asr_seg_001",
        "asr_seg_001_rev1",
        "asr_seg_002",
        "asr_seg_003",
        "asr_seg_004",
    ]
    assert [item["target_type"] for item in body["state_candidates"]] == [
        "DecisionCandidate",
        "DecisionCandidate",
        "OpenQuestion",
        "Risk",
        "ActionItem",
    ]
    assert body["state_candidates"][2]["state_item"]["question"] == "谁负责回滚？"
    assert body["state_candidates"][3]["state_item"]["description"] == "如果错误率超过 0.1% 就回滚。"
    assert body["state_candidates"][4]["state_item"]["description"] == "张三下周三补充兼容性测试用例。"
    assert [item["scheduler_event_type"] for item in body["scheduler_decisions"]] == [
        "llm_candidate_queued",
        "llm_candidate_skipped",
        "llm_candidate_skipped",
        "llm_candidate_skipped",
        "llm_candidate_skipped",
    ]
    assert {item["llm_call_status"] for item in body["scheduler_decisions"]} == {"not_called"}
    assert [item["gap_rule_id"] for item in body["suggestion_candidates"]] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {item["candidate_policy_version"] for item in body["suggestion_candidates"]} == {"asr-candidate-policy.v1"}
    assert {item["confidence_source"] for item in body["suggestion_candidates"]} == {"local_deterministic_heuristic"}
    assert [item["confidence"] for item in body["suggestion_candidates"]] == [
        0.9,
        0.9,
        0.9,
        0.9,
        0.9,
    ]
    assert [item["confidence_level"] for item in body["suggestion_candidates"]] == [
        "high",
        "high",
        "high",
        "high",
        "high",
    ]
    assert [item["degradation_reasons"] for item in body["suggestion_candidates"]] == [
        [],
        [],
        [],
        [],
        [],
    ]
    assert {item["llm_call_status"] for item in body["suggestion_candidates"]} == {"not_called"}
    assert {item["card_status"] for item in body["suggestion_candidates"]} == {"not_created"}
    assert [item["gap_rule_id"] for item in body["llm_request_drafts"]] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {item["request_status"] for item in body["llm_request_drafts"]} == {"draft_only"}
    assert {item["schema_status"] for item in body["llm_request_drafts"]} == {"not_generated"}
    assert {item["llm_call_status"] for item in body["llm_request_drafts"]} == {"not_called"}
    assert {item["card_status"] for item in body["llm_request_drafts"]} == {"not_created"}
    assert body["llm_request_drafts"][0]["candidate_confidence_level"] == "high"
    assert body["llm_request_drafts"][0]["candidate_degradation_reasons"] == []
    assert body["evaluation_summary"]["final_event_count"] == 4
    assert body["evaluation_summary"]["revision_event_count"] == 1
    assert body["suggestion_cards"] == []
    assert body["llm_schema_results"] == []
    assert "Draft only" in body["warnings"][0]


def test_asr_live_draft_review_markdown_is_marked_as_non_formal(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="live_asr_draft_review"),
    )

    response = client.get("/live/asr/sessions/live_asr_draft_review/draft.md")

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "# Live ASR Draft Review: live_asr_draft_review" in response.text
    assert "Draft only; not a formal gated meeting report." in response.text
    assert "## Transcript Draft" in response.text
    assert "先灰度 5%，不是 10%。" in response.text
    assert "## State Candidates" in response.text
    assert "OpenQuestion" in response.text
    assert "谁负责回滚？" in response.text
    assert "Risk" in response.text
    assert "如果错误率超过 0.1% 就回滚。" in response.text
    assert "ActionItem" in response.text
    assert "张三下周三补充兼容性测试用例。" in response.text
    assert "## Scheduler Decisions" in response.text
    assert "llm_candidate_queued" in response.text
    assert "llm_candidate_skipped" in response.text
    assert "not_called" in response.text
    assert "## Suggestion Candidates" in response.text
    assert "confidence high/0.9" in response.text
    assert "asr-candidate-policy.v1" in response.text
    assert "local_deterministic_heuristic" in response.text
    assert "risk.rollback.validation" in response.text
    assert "action.owner.deadline.confirmation" in response.text
    assert "not_created" in response.text
    assert "## LLM Request Drafts" in response.text
    assert "draft_only" in response.text
    assert "not_generated" in response.text
    assert "llm_suggestion_card_draft" in response.text
    assert "asr_suggestion_candidate_asr_action_event_asr_seg_004" in response.text
    assert "asr_action_event_asr_seg_004" in response.text
    assert "asr_ev_asr_seg_004" in response.text
    assert "asr_seg_004" in response.text
    assert "ActionItem asr_action_asr_seg_004 from asr_seg_004 using asr_ev_asr_seg_004" in response.text
    assert "## Stream Summary" in response.text


def test_create_asr_live_session_rejects_unknown_streaming_event_type():
    client = TestClient(create_app())
    payload = _asr_live_payload()
    payload["streaming_events"][1]["event_type"] = "draft"

    response = client.post("/live/asr/mock/sessions", json=payload)

    assert response.status_code == 422
    assert "unsupported ASR streaming event_type: draft" in response.text


def test_list_demo_fixtures_exposes_engineering_and_boundary_metadata():
    client = TestClient(create_app())

    response = client.get("/demo/fixtures")

    assert response.status_code == 200
    fixture_ids = {fixture["id"] for fixture in response.json()["fixtures"]}
    assert {
        "api-review",
        "release-review",
        "business-sync",
        "product-priority",
        "mixed-terms-sync",
        "schema-degradation-review",
    }.issubset(fixture_ids)
    release = next(fixture for fixture in response.json()["fixtures"] if fixture["id"] == "release-review")
    assert release["source"] == "fixture"
    assert release["scenario_type"] == "release_review"
    assert release["is_engineering_meeting"] is True
    assert release["expected_gap_rule_count"] == 2

    mixed = next(fixture for fixture in response.json()["fixtures"] if fixture["id"] == "mixed-terms-sync")
    assert mixed["is_engineering_meeting"] is False
    assert mixed["expected_gap_rule_count"] == 0

    degradation = next(
        fixture for fixture in response.json()["fixtures"] if fixture["id"] == "schema-degradation-review"
    )
    assert degradation["is_engineering_meeting"] is True
    assert degradation["expected_gap_rule_count"] == 1


def test_create_session_from_demo_fixture_returns_evaluation_summary():
    client = TestClient(create_app())

    response = client.post(
        "/demo/fixtures/release-review/sessions",
        json={"session_id": "demo_release_review_custom"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["metadata"] == {
        "fixture_id": "release-review",
        "source": "fixture",
        "replay_mode": "demo_fixture",
    }
    snapshot = body["snapshot"]
    assert snapshot["session_id"] == "demo_release_review_custom"
    assert snapshot["quality"]["suggestion_card_count"] == 2
    assert snapshot["suggestion_cards"][0]["state_event_ids"] == ["event_003", "event_004"]
    assert snapshot["suggestion_cards"][0]["latency_ms"] <= 30000
    evaluation = body["evaluation_summary"]
    assert evaluation["source"] == "fixture"
    assert evaluation["gate_version"] == "web_mvp_fixture.v1"
    assert evaluation["is_engineering_meeting"] is True
    assert evaluation["passes_minimum_gate"] is True
    assert evaluation["failures"] == []
    assert evaluation["state_counts"]["action_items"] == 1
    assert evaluation["effective_card_count"] == 2
    assert evaluation["gap_rule_count"] == 2
    assert set(evaluation["gap_rule_ids"]) == {
        "release.rollback.owner.required",
        "release.rollback.drill.required",
    }


def test_demo_fixture_session_exposes_replay_event_timeline():
    client = TestClient(create_app())
    created = client.post(
        "/demo/fixtures/release-review/sessions",
        json={"session_id": "demo_release_review_events"},
    )

    response = client.get("/sessions/demo_release_review_events/events")

    assert created.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "demo_release_review_events"
    assert body["source"] == "replay_snapshot"
    events = body["events"]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["at_ms"] for event in events] == sorted(event["at_ms"] for event in events)
    assert {event["event_type"] for event in events} >= {
        "transcript_final",
        "state_event",
        "suggestion_card",
        "evaluation_summary",
    }
    transcript_event = next(event for event in events if event["event_type"] == "transcript_final")
    assert transcript_event["payload"]["segment_id"] == "seg_001"
    card_event = next(
        event
        for event in events
        if event["event_type"] == "suggestion_card" and event["payload"]["card_id"] == "card_001"
    )
    assert card_event["at_ms"] == 23100
    assert card_event["payload"]["gap_rule_id"] == "release.rollback.owner.required"
    evaluation_event = events[-1]
    assert evaluation_event["event_type"] == "evaluation_summary"
    assert evaluation_event["payload"]["passes_minimum_gate"] is True


def test_demo_fixture_session_exposes_llm_scheduler_trace_events():
    client = TestClient(create_app())
    created = client.post(
        "/demo/fixtures/release-review/sessions",
        json={"session_id": "demo_release_review_llm_trace"},
    )

    response = client.get("/sessions/demo_release_review_llm_trace/events")

    snapshot = created.json()["snapshot"]
    assert response.status_code == 200
    events = response.json()["events"]
    event_types = [event["event_type"] for event in events]
    cards = snapshot["suggestion_cards"]
    assert event_types.count("llm_scheduled") == len(cards)
    assert event_types.count("llm_schema_result") == len(cards)

    events_by_type_and_card = {
        (event["event_type"], event["payload"].get("card_id")): event
        for event in events
        if event["event_type"] in {"llm_scheduled", "llm_schema_result", "suggestion_card"}
    }
    state_events_by_id = {
        event["payload"]["event_id"]: event for event in events if event["event_type"] == "state_event"
    }
    for card in cards:
        card_id = card["id"]
        scheduled = events_by_type_and_card[("llm_scheduled", card_id)]
        schema_result = events_by_type_and_card[("llm_schema_result", card_id)]
        suggestion_event = events_by_type_and_card[("suggestion_card", card_id)]
        assert scheduled["at_ms"] == card["state_event_at_ms"]
        assert scheduled["payload"] == {
            "card_id": card_id,
            "gap_rule_id": card["gap_rule_id"],
            "trigger_source": card["trigger_source"],
            "trigger_reason": card["trigger_reason"],
            "segment_batch": card["segment_batch"],
            "state_event_ids": card["state_event_ids"],
            "prompt_version": card["prompt_version"],
            "model": card["model"],
        }
        assert schema_result["at_ms"] == card["card_created_at_ms"]
        assert schema_result["payload"] == {
            "card_id": card_id,
            "schema_result": card["schema_result"],
            "show_or_silence_decision": card["show_or_silence_decision"],
            "usage": card["usage"],
            "latency_ms": card["latency_ms"],
        }
        latest_state_event = max(
            state_events_by_id[event_id]
            for event_id in card["state_event_ids"]
            if state_events_by_id[event_id]["at_ms"] == scheduled["at_ms"]
        )
        assert latest_state_event["sequence"] < scheduled["sequence"]
        if schema_result["at_ms"] == suggestion_event["at_ms"]:
            assert schema_result["sequence"] < suggestion_event["sequence"]


def test_replay_events_carry_source_boundary_in_json_and_sse():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/api-review/sessions",
        json={"session_id": "demo_api_review_event_boundary"},
    )

    json_response = client.get("/sessions/demo_api_review_event_boundary/events")
    sse_response = client.get("/sessions/demo_api_review_event_boundary/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    assert {event["source"] for event in events} == {"replay_snapshot"}
    assert {event["trace_kind"] for event in events} == {"replay_derived"}

    sse_events = [
        __import__("json").loads(line.removeprefix("data: "))
        for line in sse_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert sse_events == events


def test_create_mock_live_session_from_fixture_returns_live_event_source_boundary():
    client = TestClient(create_app())

    response = client.post(
        "/live/mock/fixtures/release-review/sessions",
        json={"session_id": "live_release_review_custom"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["metadata"] == {
        "fixture_id": "release-review",
        "source": "fixture",
        "replay_mode": "demo_fixture",
        "live_mode": "mock_fixture_stream",
    }
    assert body["snapshot"]["session_id"] == "live_release_review_custom"
    assert body["event_source"] == {
        "source": "live_mock_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "is_mock": True,
    }
    events = body["live_events"]
    assert {event["source"] for event in events} == {"live_mock_stream"}
    assert {event["trace_kind"] for event in events} == {"live_event"}
    assert {event["event_type"] for event in events} >= {
        "transcript_partial",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "llm_schema_result",
        "suggestion_card",
        "evaluation_summary",
    }
    assert all(event["source"] != "replay_snapshot" for event in events)
    partial = next(event for event in events if event["event_type"] == "transcript_partial")
    assert partial["payload"]["is_final"] is False
    scheduler = next(event for event in events if event["event_type"] == "scheduler_event")
    assert scheduler["payload"]["scheduler_event_type"] == "llm_scheduled"


def test_mock_live_session_events_json_and_sse_use_live_boundary():
    client = TestClient(create_app())
    client.post(
        "/live/mock/fixtures/api-review/sessions",
        json={"session_id": "live_api_review_events"},
    )

    json_response = client.get("/live/sessions/live_api_review_events/events")
    sse_response = client.get("/live/sessions/live_api_review_events/events.sse")

    assert json_response.status_code == 200
    body = json_response.json()
    assert body["session_id"] == "live_api_review_events"
    assert body["source"] == "live_mock_stream"
    assert body["trace_kind"] == "live_event"
    events = body["events"]
    assert {event["source"] for event in events} == {"live_mock_stream"}
    assert "transcript_partial" in [event["event_type"] for event in events]
    assert "transcript_revision" in [event["event_type"] for event in events]
    assert "suggestion_invalidated" in [event["event_type"] for event in events]
    assert "scheduler_event" in [event["event_type"] for event in events]
    revision = next(event for event in events if event["event_type"] == "transcript_revision")
    assert revision["payload"]["segment_id"] == "seg_002_rev1"
    assert revision["payload"]["supersedes_segment_id"] == "seg_002"
    assert revision["payload"]["evidence_spans"] == [
        {
            "id": "ev_002_rev1",
            "segment_id": "seg_002_rev1",
            "start_ms": 6200,
            "end_ms": 10500,
            "quote": "老版本调用方只需要兼容 v2，不再兼容两个版本。",
            "status": "active",
            "revision_of": "ev_002",
        }
    ]
    assert revision["payload"]["superseded_evidence_spans"] == [
        {
            "id": "ev_002",
            "segment_id": "seg_002",
            "start_ms": 6200,
            "end_ms": 10500,
            "quote": "老版本调用方要兼容两个版本。",
            "status": "superseded",
            "replaced_by": "ev_002_rev1",
        }
    ]
    invalidated = next(event for event in events if event["event_type"] == "suggestion_invalidated")
    assert invalidated["payload"]["card_id"] == "card_002"
    assert invalidated["payload"]["reason"] == "stale_evidence"
    assert invalidated["payload"]["invalidated_by_event_id"] == "transcript_revision:seg_002_rev1"
    assert invalidated["payload"]["stale_evidence_span_ids"] == ["ev_002"]
    assert invalidated["payload"]["replacement_evidence_span_ids"] == ["ev_002_rev1"]
    assert invalidated["payload"]["card"]["show_or_silence_decision"] == "silence"
    assert invalidated["payload"]["card"]["invalidation_reason"] == "stale_evidence"

    assert sse_response.status_code == 200
    assert sse_response.headers["content-type"].startswith("text/event-stream")
    sse_events = [
        json.loads(line.removeprefix("data: ")) for line in sse_response.text.splitlines() if line.startswith("data: ")
    ]
    assert sse_events == events
    assert "event: transcript_partial" in sse_response.text
    assert "event: transcript_revision" in sse_response.text
    assert "event: suggestion_invalidated" in sse_response.text
    assert "event: scheduler_event" in sse_response.text


def test_api_review_fixture_preserves_revision_segment_after_core_gate():
    client = TestClient(create_app())

    response = client.post(
        "/live/mock/fixtures/api-review/sessions",
        json={"session_id": "live_api_review_revision_snapshot"},
    )

    assert response.status_code == 201
    snapshot = response.json()["snapshot"]
    revision_segment = next(
        segment for segment in snapshot["transcript"]["segments"] if segment["id"] == "seg_002_rev1"
    )
    revision_evidence = next(
        evidence for evidence in snapshot["transcript"]["evidence_spans"] if evidence["id"] == "ev_002_rev1"
    )
    assert revision_segment["revision_of"] == "seg_002"
    assert revision_evidence["revision_of"] == "ev_002"
    assert "replaced_by" not in revision_evidence


def test_mock_live_session_unknown_fixture_returns_404():
    client = TestClient(create_app())

    response = client.post("/live/mock/fixtures/missing/sessions", json={})

    assert response.status_code == 404
    assert "fixture not found" in response.text


def test_demo_fixture_session_exposes_sse_replay_stream():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/api-review/sessions",
        json={"session_id": "demo_api_review_sse"},
    )

    response = client.get("/sessions/demo_api_review_sse/events.sse")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: transcript_final" in response.text
    assert "event: llm_scheduled" in response.text
    assert "event: llm_schema_result" in response.text
    assert "event: suggestion_card" in response.text
    assert "event: evaluation_summary" in response.text
    assert '"gap_rule_id":"api.change.monitoring.required"' in response.text
    assert '"schema_result":"valid"' in response.text
    assert '"total_tokens":336' in response.text


def test_schema_degradation_fixture_records_failures_without_strong_suggestions():
    client = TestClient(create_app())

    response = client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation"},
    )

    assert response.status_code == 201
    body = response.json()
    snapshot = body["snapshot"]
    evaluation = body["evaluation_summary"]
    cards = snapshot["suggestion_cards"]
    blocking_cards = [card for card in cards if card["schema_result"] in {"failed", "timeout", "invalid"}]
    assert len(blocking_cards) == 3
    assert all(card["show_or_silence_decision"] != "show" for card in blocking_cards)
    assert evaluation["passes_minimum_gate"] is True
    assert evaluation["effective_card_count"] == 1
    assert evaluation["schema_blocked_count"] == 3
    assert evaluation["silenced_card_count"] == 3
    assert evaluation["schema_result_counts"] == {
        "failed": 1,
        "invalid": 1,
        "timeout": 1,
        "valid": 1,
    }

    events = body["replay_events"]
    silenced_events = [event for event in events if event["event_type"] == "suggestion_silenced"]
    shown_events = [event for event in events if event["event_type"] == "suggestion_card"]
    assert len(silenced_events) == 3
    assert len(shown_events) == 1
    assert {event["payload"]["schema_result"] for event in silenced_events} == {"failed", "timeout", "invalid"}
    assert all(event["payload"]["show_or_silence_decision"] != "show" for event in silenced_events)


def test_schema_degradation_replay_stream_exposes_silenced_events_in_json_and_sse():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation_events"},
    )

    json_response = client.get("/sessions/demo_schema_degradation_events/events")
    sse_response = client.get("/sessions/demo_schema_degradation_events/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    event_types = [event["event_type"] for event in events]
    assert event_types.count("llm_schema_result") == 4
    assert event_types.count("suggestion_silenced") == 3
    assert event_types.count("suggestion_card") == 1
    for event in events:
        if event["event_type"] == "suggestion_silenced":
            card_id = event["payload"]["card_id"]
            schema_event = next(
                item
                for item in events
                if item["event_type"] == "llm_schema_result" and item["payload"]["card_id"] == card_id
            )
            assert schema_event["sequence"] < event["sequence"]

    sse_events = [
        json.loads(line.removeprefix("data: ")) for line in sse_response.text.splitlines() if line.startswith("data: ")
    ]
    assert sse_events == events
    assert "event: suggestion_silenced" in sse_response.text
    assert '"schema_result":"timeout"' in sse_response.text


def test_schema_degradation_replay_evaluation_preserves_fixture_gate_metadata():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation_gate_metadata"},
    )

    response = client.get("/sessions/demo_schema_degradation_gate_metadata/events")

    assert response.status_code == 200
    evaluation_event = next(event for event in response.json()["events"] if event["event_type"] == "evaluation_summary")
    assert evaluation_event["payload"]["source"] == "replay_snapshot"
    assert evaluation_event["payload"]["expected_gap_rule_count"] == 1
    assert evaluation_event["payload"]["gap_rule_count"] == 1
    assert evaluation_event["payload"]["passes_minimum_gate"] is True
    assert evaluation_event["payload"]["failures"] == []


def test_update_card_status_rejects_silenced_schema_card_without_mutating_record():
    client = TestClient(create_app())
    created = client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation_status_guard"},
    )
    silenced_card = next(
        card for card in created.json()["snapshot"]["suggestion_cards"] if card["schema_result"] == "failed"
    )

    rejected = client.patch(
        f"/sessions/demo_schema_degradation_status_guard/cards/{silenced_card['id']}/status",
        json={"status": "kept"},
    )
    fetched = client.get("/sessions/demo_schema_degradation_status_guard")

    assert rejected.status_code == 422
    assert "silenced suggestion card cannot be updated" in rejected.text
    assert fetched.status_code == 200
    still_silenced = next(card for card in fetched.json()["suggestion_cards"] if card["id"] == silenced_card["id"])
    assert still_silenced["status"] == "new"


def test_engineering_demo_fixtures_cover_multiple_gap_rules():
    client = TestClient(create_app())

    for fixture_id in ("api-review", "release-review"):
        response = client.post(f"/demo/fixtures/{fixture_id}/sessions", json={})

        assert response.status_code == 201
        body = response.json()
        evaluation = body["evaluation_summary"]
        assert evaluation["is_engineering_meeting"] is True
        assert evaluation["passes_minimum_gate"] is True
        assert evaluation["effective_card_count"] >= 2
        assert evaluation["gap_rule_count"] >= 2
        assert body["snapshot"]["quality"]["suggestion_card_count"] >= 2


def test_non_engineering_demo_fixtures_do_not_emit_engineering_cards():
    client = TestClient(create_app())

    for fixture_id in ("business-sync", "product-priority", "mixed-terms-sync"):
        response = client.post(f"/demo/fixtures/{fixture_id}/sessions", json={})

        assert response.status_code == 201
        body = response.json()
        evaluation = body["evaluation_summary"]
        assert evaluation["is_engineering_meeting"] is False
        assert evaluation["passes_minimum_gate"] is True
        assert evaluation["suggestion_card_count"] == 0
        assert evaluation["effective_card_count"] == 0
        assert evaluation["gap_rule_count"] == 0
        assert body["snapshot"]["suggestion_cards"] == []
        assert body["snapshot"]["quality"]["is_engineering_meeting"] is False


def test_create_session_from_unknown_demo_fixture_returns_404():
    client = TestClient(create_app())

    response = client.post("/demo/fixtures/missing/sessions", json={})

    assert response.status_code == 404
    assert "fixture not found" in response.text


def test_create_and_read_session_snapshot():
    client = TestClient(create_app())

    created = client.post("/sessions", json=_payload())

    assert created.status_code == 201
    snapshot = created.json()
    assert snapshot["session_id"] == "meeting_001"
    assert snapshot["suggestion_cards"][0]["status"] == "new"
    assert snapshot["quality"]["suggestion_card_count"] == 1

    fetched = client.get("/sessions/meeting_001")

    assert fetched.status_code == 200
    assert fetched.json() == snapshot


def test_create_rejects_invalid_session_without_persisting_bad_record():
    client = TestClient(create_app())
    payload = _payload()
    payload["analysis"]["suggestion_cards"][0].pop("state_refs")

    rejected = client.post("/sessions", json=payload)
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 422
    assert "card_001 missing state_refs" in rejected.text
    assert fetched.status_code == 404


def test_create_rejects_strong_card_when_degraded_without_persisting_record():
    client = TestClient(create_app())
    payload = _payload()
    payload["degradation_reasons"] = ["asr_low_confidence"]

    rejected = client.post("/sessions", json=payload)
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 422
    assert "degradation blocks strong suggestion card" in rejected.text
    assert fetched.status_code == 404


def test_create_rejects_strong_card_using_stale_evidence_without_persisting_record():
    client = TestClient(create_app())
    payload = _payload()
    payload["transcript_report"]["evidence_spans"][1]["status"] = "stale"
    payload["transcript_report"]["evidence_spans"][1]["replaced_by"] = "ev_002_rev"

    rejected = client.post("/sessions", json=payload)
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 422
    assert "card_001 references stale evidence_span_id: ev_002" in rejected.text
    assert fetched.status_code == 404


def test_create_allows_non_strong_audit_card_using_stale_evidence():
    client = TestClient(create_app())
    payload = _payload()
    payload["transcript_report"]["evidence_spans"][1]["status"] = "stale"
    payload["transcript_report"]["evidence_spans"][1]["replaced_by"] = "ev_002_rev"
    card = payload["analysis"]["suggestion_cards"][0]
    card["show_or_silence_decision"] = "draft"
    card["status"] = "dismissed"

    created = client.post("/sessions", json=payload)

    assert created.status_code == 201
    snapshot = created.json()
    evidence = next(item for item in snapshot["transcript"]["evidence_spans"] if item["id"] == "ev_002")
    assert evidence["status"] == "stale"
    assert evidence["replaced_by"] == "ev_002_rev"
    assert snapshot["suggestion_cards"][0]["show_or_silence_decision"] == "draft"


def test_update_card_status_updates_snapshot():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    response = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "kept"},
    )

    assert response.status_code == 200
    assert response.json()["suggestion_cards"][0]["status"] == "kept"


def test_update_card_status_blocks_overwriting_negative_feedback_without_mutating_record():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    marked_late = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "too_late"},
    )
    rejected = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "kept"},
    )
    fetched = client.get("/sessions/meeting_001")

    assert marked_late.status_code == 200
    assert rejected.status_code == 409
    assert "card status transition not allowed" in rejected.text
    assert fetched.status_code == 200
    assert fetched.json()["suggestion_cards"][0]["status"] == "too_late"


def test_update_card_status_rejects_unknown_status():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    response = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "snoozed"},
    )

    assert response.status_code == 422
    assert "unsupported suggestion card status" in response.text


def test_update_card_status_rejects_unknown_status_without_mutating_record():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    rejected = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "snoozed"},
    )
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 422
    assert fetched.status_code == 200
    assert fetched.json()["suggestion_cards"][0]["status"] == "new"


def test_update_card_status_rejects_unknown_card_id_without_mutating_record():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    rejected = client.patch(
        "/sessions/meeting_001/cards/card_missing/status",
        json={"status": "dismissed"},
    )
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 404
    assert "card not found" in rejected.text
    assert fetched.status_code == 200
    assert fetched.json()["suggestion_cards"][0]["status"] == "new"


def test_export_markdown_report():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    response = client.get("/sessions/meeting_001/report.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "Meeting meeting_001" in response.text
    assert "evidence: ev_001" in response.text


def test_export_markdown_report_separates_silenced_schema_records():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation_report"},
    )

    response = client.get("/sessions/demo_schema_degradation_report/report.md")

    assert response.status_code == 200
    assert "## Suggestion Cards" in response.text
    assert "补齐回滚演练安排" in response.text
    assert "## Silenced Suggestion Records" in response.text
    assert "[silence; schema: failed]" in response.text
    assert "[silence; schema: timeout]" in response.text
    assert "[silence; schema: invalid]" in response.text


class _GateFailingRepository:
    def snapshot(self, session_id):
        raise ValueError(f"stored session failed gate: {session_id}")


def test_read_session_converts_stored_gate_failure_to_422():
    client = TestClient(create_app(repository=_GateFailingRepository()))

    response = client.get("/sessions/bad_session")

    assert response.status_code == 422
    assert "stored session failed gate" in response.text


def test_export_report_converts_stored_gate_failure_to_422():
    client = TestClient(create_app(repository=_GateFailingRepository()))

    response = client.get("/sessions/bad_session/report.md")

    assert response.status_code == 422
    assert "stored session failed gate" in response.text


def test_delete_session_removes_it():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    deleted = client.delete("/sessions/meeting_001")
    missing = client.get("/sessions/meeting_001")

    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_json_repository_persists_session_and_card_status_across_instances(tmp_path):
    repository = JsonFileSessionRepository(tmp_path)
    client = TestClient(create_app(repository=repository))
    client.post("/sessions", json=_payload())

    updated = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "kept"},
    )
    reloaded_client = TestClient(create_app(repository=JsonFileSessionRepository(tmp_path)))
    fetched = reloaded_client.get("/sessions/meeting_001")

    assert updated.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["suggestion_cards"][0]["status"] == "kept"
    assert (tmp_path / "sessions" / "meeting_001.json").is_file()


def test_json_repository_delete_removes_session_file(tmp_path):
    client = TestClient(create_app(repository=JsonFileSessionRepository(tmp_path)))
    client.post("/sessions", json=_payload())
    session_file = tmp_path / "sessions" / "meeting_001.json"

    deleted = client.delete("/sessions/meeting_001")
    missing = client.get("/sessions/meeting_001")

    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert not session_file.exists()


def test_json_repository_delete_rejects_unsafe_session_id(tmp_path):
    client = TestClient(create_app(repository=JsonFileSessionRepository(tmp_path)))

    rejected = client.delete("/sessions/..escape")

    assert rejected.status_code == 422
    assert "unsafe session_id" in rejected.text


def test_json_repository_rejects_unsafe_session_id_without_writing_outside_data_dir(
    tmp_path,
):
    payload = _payload()
    payload["session_id"] = "../escape"
    client = TestClient(create_app(repository=JsonFileSessionRepository(tmp_path)))

    rejected = client.post("/sessions", json=payload)

    assert rejected.status_code == 422
    assert "unsafe session_id" in rejected.text
    assert list((tmp_path / "sessions").glob("*.json")) == []
    assert not (tmp_path.parent / "escape.json").exists()


def test_create_app_uses_single_sqlite_database_when_data_dir_is_provided(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/sessions", json=_payload())

    assert response.status_code == 201
    assert (tmp_path / "meeting_copilot.db").is_file()
    assert not (tmp_path / "meeting_copilot.db").is_dir()

    reloaded = TestClient(create_app(data_dir=tmp_path)).get("/sessions/meeting_001")
    assert reloaded.status_code == 200


def test_create_app_bootstraps_formal_schema_before_compatibility_steps_and_repositories(
    monkeypatch,
    tmp_path,
):
    calls = []
    real_bootstrap = app_module.bootstrap_application_schema
    real_json_import = app_module.migrate_json_to_sqlite
    real_session_repository = app_module.SqliteSessionRepository

    def bootstrap(*args, **kwargs):
        calls.append("formal_schema")
        return real_bootstrap(*args, **kwargs)

    def import_legacy_json(*args, **kwargs):
        calls.append("legacy_json_import")
        return real_json_import(*args, **kwargs)

    def migrate_shadow(*args, **kwargs):
        calls.append("v1_to_v2_data_shadow")
        return {"status": "no_source_records"}

    def open_session_repository(*args, **kwargs):
        calls.append("first_repository")
        return real_session_repository(*args, **kwargs)

    monkeypatch.setattr(app_module, "bootstrap_application_schema", bootstrap)
    monkeypatch.setattr(app_module, "migrate_json_to_sqlite", import_legacy_json)
    monkeypatch.setattr(app_module, "migrate_v1_to_v2", migrate_shadow)
    monkeypatch.setattr(app_module, "SqliteSessionRepository", open_session_repository)

    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert calls[:4] == [
        "formal_schema",
        "legacy_json_import",
        "v1_to_v2_data_shadow",
        "first_repository",
    ]


def test_create_app_fails_closed_on_formal_schema_error_before_compatibility_or_repository_open(
    monkeypatch,
    tmp_path,
):
    forbidden_path = tmp_path / "private-customer-name" / "meeting_copilot.db"
    later_calls = []

    def fail_schema(*args, **kwargs):
        raise OSError(f"could not migrate {forbidden_path}")

    def forbidden_step(*args, **kwargs):
        later_calls.append("called")
        raise AssertionError("startup continued after formal schema failure")

    monkeypatch.setattr(app_module, "bootstrap_application_schema", fail_schema)
    monkeypatch.setattr(app_module, "migrate_json_to_sqlite", forbidden_step)
    monkeypatch.setattr(app_module, "migrate_v1_to_v2", forbidden_step)
    monkeypatch.setattr(app_module, "SqliteSessionRepository", forbidden_step)

    with pytest.raises(
        RuntimeError,
        match="Application SQLite schema bootstrap failed: OSError",
    ) as captured:
        create_app(data_dir=tmp_path)

    assert later_calls == []
    assert str(forbidden_path) not in str(captured.value)


def test_application_schema_diagnostic_is_safe_and_startup_bootstrap_is_idempotent(tmp_path):
    first_app = create_app(data_dir=tmp_path)
    with TestClient(first_app) as client:
        first = client.get("/v2/diagnostics/application-schema")

    second_app = create_app(data_dir=tmp_path)
    with TestClient(second_app) as client:
        second = client.get("/v2/diagnostics/application-schema")

    assert first.status_code == 200
    assert first.json() == {
        "schema_version": "application-schema-migration-report.v1",
        "status": "ready",
        "storage": "sqlite",
        "source_version": 0,
        "final_version": APPLICATION_SCHEMA_VERSION,
        "applied_versions": list(range(1, APPLICATION_SCHEMA_VERSION + 1)),
        "migrated": True,
        "backup_created": False,
    }
    assert second.status_code == 200
    assert second.json() == {
        "schema_version": "application-schema-migration-report.v1",
        "status": "ready",
        "storage": "sqlite",
        "source_version": APPLICATION_SCHEMA_VERSION,
        "final_version": APPLICATION_SCHEMA_VERSION,
        "applied_versions": [],
        "migrated": False,
        "backup_created": False,
    }
    serialized = json.dumps(first.json()) + json.dumps(second.json())
    assert str(tmp_path) not in serialized
    assert "migration_backups" not in serialized
    assert first_app.state.application_schema_migration_report == first.json()
    assert second_app.state.application_schema_migration_report == second.json()


def test_in_memory_application_schema_diagnostic_is_explicitly_not_applicable():
    app = create_app()

    response = TestClient(app).get("/v2/diagnostics/application-schema")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "application-schema-migration-report.v1",
        "status": "not_applicable",
        "storage": "memory",
    }


def test_create_app_closes_all_created_sqlite_repositories_on_shutdown(tmp_path):
    app = create_app(data_dir=tmp_path)
    repositories = app.state.sqlite_repositories

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert all(repository.closed is False for repository in repositories)

    assert repositories
    assert all(repository.closed is True for repository in repositories)
    db_path = tmp_path / "meeting_copilot.db"
    moved_path = tmp_path / "meeting_copilot.after_shutdown.db"
    db_path.replace(moved_path)
    moved_path.unlink()
    assert not moved_path.exists()


def test_create_app_shuts_down_process_resident_funasr_worker(monkeypatch):
    shutdown_calls = []
    monkeypatch.setattr(
        app_module.asr_stream,
        "shutdown_funasr_resident_manager",
        lambda: shutdown_calls.append("shutdown"),
    )

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200

    assert shutdown_calls == ["shutdown"]


def test_shutdown_cancels_active_capture_tasks_and_clears_registry():
    async def scenario():
        active_tasks = {}
        started = asyncio.Event()

        async def active_capture():
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(active_capture())
        active_tasks["meeting-shutdown"] = {task}
        await started.wait()

        cancelled_count = await app_module._cancel_active_capture_tasks(active_tasks)

        assert cancelled_count == 1
        assert task.done()
        assert task.cancelled()
        assert active_tasks == {}

    asyncio.run(scenario())


def test_new_app_does_not_inherit_previous_runtime_degradation():
    controller = get_degradation_controller()
    controller.set_level(3, "asr_sidecar_crashed: synthetic previous app")

    with TestClient(create_app()) as client:
        response = client.get("/degradation/status")

    assert response.status_code == 200
    assert response.json()["level"] == 0


def test_packaged_startup_recovers_recording_owned_by_crashed_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    app = create_app(data_dir=tmp_path)
    persistence = app.state.v2_persistence
    persistence.create_meeting(
        meeting_id="crashed-runtime-recording",
        title="崩溃恢复会议",
        now_ms=1_000,
    )
    persistence.begin_recording(
        meeting_id="crashed-runtime-recording",
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=16_000,
        lease_owner="capture-from-dead-runtime",
        lease_ms=30_000,
        now_ms=1_000,
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        recording = persistence.get_recording_session(
            "crashed-runtime-recording",
            track="microphone",
            epoch=0,
        )
        assert recording["status"] == "interrupted"
        assert recording["error_class"] == "runtime_restarted"
        resumed = persistence.begin_recording(
            meeting_id="crashed-runtime-recording",
            track="microphone",
            epoch=0,
            source_type="browser_live_mic",
            sample_rate_hz=16_000,
            lease_owner="capture-from-new-runtime",
            lease_ms=30_000,
            now_ms=2_000,
        )
        assert resumed["status"] == "active"
        assert resumed["capture_generation"] == 2


def test_create_app_fails_closed_when_sqlite_migration_fails(monkeypatch, tmp_path):
    def fail_migration(*args, **kwargs):
        raise OSError("migration exploded")

    monkeypatch.setattr(app_module, "migrate_json_to_sqlite", fail_migration)

    with pytest.raises(RuntimeError, match="SQLite migration failed: migration exploded"):
        create_app(data_dir=tmp_path)
