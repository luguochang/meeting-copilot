from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import time

import pytest
from fastapi.testclient import TestClient

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.realtime_intelligence import (
    CoachIntervention,
    build_realtime_coach_provenance_decision,
)
import meeting_copilot_web_mvp.v2_persistence as persistence_module
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def _create_meeting(database_path, meeting_id: str = "reservation-meeting") -> None:
    persistence = V2Persistence(database_path)
    try:
        persistence.create_meeting(meeting_id=meeting_id, title="reservation", now_ms=1_000)
    finally:
        persistence.close()


def _reserve(
    database_path,
    *,
    reservation_id: str,
    job_id: str,
    priority: int = 90,
    now_ms: int = 2_000,
    expires_at_ms: int | None = None,
):
    persistence = V2Persistence(database_path)
    try:
        return persistence.reserve_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            job_id=job_id,
            reservation_id=reservation_id,
            episode_ids=["coach-episode:shared"],
            candidate_keys=[f"coach-candidate:{reservation_id}"],
            priority=priority,
            reserved_at_ms=now_ms,
            expires_at_ms=expires_at_ms or now_ms + 30_000,
        )
    finally:
        persistence.close()


def test_reservation_is_idempotent_and_same_priority_is_suppressed(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    _create_meeting(database_path)
    persistence = V2Persistence(database_path)
    try:
        first = persistence.reserve_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            job_id="job-1",
            reservation_id="reservation-1",
            episode_ids=["coach-episode:shared"],
            candidate_keys=["candidate-1"],
            priority=90,
            reserved_at_ms=2_000,
            expires_at_ms=32_000,
        )
        repeated = persistence.reserve_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            job_id="job-1",
            reservation_id="reservation-1",
            episode_ids=["coach-episode:shared"],
            candidate_keys=["candidate-1"],
            priority=90,
            reserved_at_ms=2_100,
            expires_at_ms=32_100,
        )
        blocked = persistence.reserve_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            job_id="job-2",
            reservation_id="reservation-2",
            episode_ids=["coach-episode:shared"],
            candidate_keys=["candidate-2"],
            priority=90,
            reserved_at_ms=2_200,
            expires_at_ms=32_200,
        )
        assert first["reserved"] is True
        assert repeated["reserved"] is True
        assert repeated["idempotent"] is True
        assert blocked["reserved"] is False
        assert blocked["reason"] == "realtime_provider_episode_reserved"
        assert persistence.recent_coach_episode_priorities(
            "reservation-meeting", since_ms=0, now_ms=2_300
        ) == {"coach-episode:shared": 90}
    finally:
        persistence.close()


def test_higher_priority_supersedes_and_release_allows_retry(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    _create_meeting(database_path)
    low = _reserve(database_path, reservation_id="low", job_id="job-low", priority=80)
    assert low["reserved"] is True
    high = _reserve(database_path, reservation_id="high", job_id="job-high", priority=100, now_ms=2_100)
    assert high["reserved"] is True

    persistence = V2Persistence(database_path)
    try:
        assert persistence.recent_coach_episode_priorities(
            "reservation-meeting", since_ms=0, now_ms=2_200
        ) == {"coach-episode:shared": 100}
        finished = persistence.finish_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            reservation_id="high",
            status="released",
            reason="direct_fallback",
            finished_at_ms=2_300,
        )
        assert finished["status"] == "released"
        assert persistence.recent_coach_episode_priorities(
            "reservation-meeting", since_ms=0, now_ms=2_400
        ) == {}
    finally:
        persistence.close()


def test_expired_reservation_is_reusable_and_survives_restart(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    _create_meeting(database_path)
    first = _reserve(
        database_path,
        reservation_id="expired",
        job_id="job-expired",
        now_ms=2_000,
        expires_at_ms=3_000,
    )
    assert first["reserved"] is True

    # A fresh repository instance must reconstruct the reservation from the
    # event log, then allow a new attempt once its TTL has elapsed.
    restarted = V2Persistence(database_path)
    try:
        assert restarted.recent_coach_episode_priorities(
            "reservation-meeting", since_ms=0, now_ms=2_500
        ) == {"coach-episode:shared": 90}
        assert restarted.recent_coach_episode_priorities(
            "reservation-meeting", since_ms=0, now_ms=3_001
        ) == {}
        expired_snapshot = restarted.realtime_provider_reservation_snapshot(
            "reservation-meeting",
            now_ms=3_001,
        )["expired"]
        assert expired_snapshot["status"] == "expired"
        assert expired_snapshot["active"] is False
        assert expired_snapshot["expired"] is True
    finally:
        restarted.close()
    replacement = _reserve(
        database_path,
        reservation_id="replacement",
        job_id="job-replacement",
        now_ms=3_001,
    )
    assert replacement["reserved"] is True


def test_episode_descriptor_is_durable_only_during_reservation_ttl(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    _create_meeting(database_path)
    descriptor = {
        "episode_id": "coach-episode:shared",
        "episode_anchor_id": "segment-anchor",
        "episode_source_track": "microphone",
        "episode_speaker": "Alice",
        "episode_speaker_key": hashlib.sha256(b"alice").hexdigest()[:24],
        "episode_anchor_start_ms": 100,
        "episode_anchor_end_ms": 900,
        "episode_latest_start_ms": 100,
        "episode_latest_end_ms": 900,
    }
    persistence = V2Persistence(database_path)
    try:
        reserved = persistence.reserve_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            job_id="descriptor-job",
            reservation_id="descriptor-reservation",
            episode_ids=[descriptor["episode_id"]],
            episode_descriptors=[descriptor],
            candidate_keys=["descriptor-candidate"],
            priority=90,
            reserved_at_ms=2_000,
            expires_at_ms=3_000,
        )
        assert reserved["reserved"] is True
    finally:
        persistence.close()

    restarted = V2Persistence(database_path)
    try:
        inherited = restarted.recent_coach_episode_descriptors(
            "reservation-meeting",
            since_ms=0,
            now_ms=2_999,
        )
        assert inherited == [{**descriptor, "attempted_at_ms": 2_000}]
        assert restarted.recent_coach_episode_descriptors(
            "reservation-meeting",
            since_ms=0,
            now_ms=3_000,
        ) == []
    finally:
        restarted.close()


def test_contiguous_descriptor_serializes_different_episode_ids_and_allows_upgrade(
    tmp_path,
) -> None:
    database_path = tmp_path / "meeting.db"
    _create_meeting(database_path)
    speaker_key = hashlib.sha256(b"alice").hexdigest()[:24]

    def descriptor(episode_id: str, start_ms: int, end_ms: int) -> dict:
        return {
            "episode_id": episode_id,
            "episode_anchor_id": f"anchor-{episode_id}",
            "episode_source_track": "microphone",
            "episode_speaker": "Alice",
            "episode_speaker_key": speaker_key,
            "episode_anchor_start_ms": start_ms,
            "episode_anchor_end_ms": end_ms,
            "episode_latest_start_ms": start_ms,
            "episode_latest_end_ms": end_ms,
        }

    persistence = V2Persistence(database_path)
    try:
        first_descriptor = descriptor("coach-episode:first", 0, 1_000)
        first = persistence.reserve_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            job_id="job-first",
            reservation_id="reservation-first",
            episode_ids=[first_descriptor["episode_id"]],
            episode_descriptors=[first_descriptor],
            candidate_keys=["candidate-first"],
            priority=90,
            reserved_at_ms=2_000,
            expires_at_ms=32_000,
        )
        second_descriptor = descriptor("coach-episode:rolled", 1_100, 2_000)
        blocked = persistence.reserve_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            job_id="job-rolled",
            reservation_id="reservation-rolled",
            episode_ids=[second_descriptor["episode_id"]],
            episode_descriptors=[second_descriptor],
            candidate_keys=["candidate-rolled"],
            priority=90,
            reserved_at_ms=2_100,
            expires_at_ms=32_100,
        )
        high_descriptor = descriptor("coach-episode:upgrade", 2_100, 3_000)
        upgraded = persistence.reserve_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            job_id="job-upgrade",
            reservation_id="reservation-upgrade",
            episode_ids=[high_descriptor["episode_id"]],
            episode_descriptors=[high_descriptor],
            candidate_keys=["candidate-upgrade"],
            priority=100,
            reserved_at_ms=2_200,
            expires_at_ms=32_200,
        )

        assert first["reserved"] is True
        assert blocked["reserved"] is False
        assert blocked["reason"] == "realtime_provider_episode_reserved"
        assert upgraded["reserved"] is True
        assert persistence.recent_coach_episode_priorities(
            "reservation-meeting",
            since_ms=0,
            now_ms=2_300,
        ) == {"coach-episode:upgrade": 100}
    finally:
        persistence.close()


def test_late_finish_from_expired_generation_cannot_close_reused_id(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    _create_meeting(database_path)
    first = _reserve(
        database_path,
        reservation_id="reused-id",
        job_id="job-old",
        now_ms=2_000,
        expires_at_ms=3_000,
    )
    second = _reserve(
        database_path,
        reservation_id="reused-id",
        job_id="job-new",
        now_ms=3_001,
        expires_at_ms=4_000,
    )
    assert first["reserved"] is True
    assert second["reserved"] is True
    assert first["attempt_generation"] == 1
    assert second["attempt_generation"] == 2
    assert first["attempt_token"] != second["attempt_token"]

    persistence = V2Persistence(database_path)
    try:
        stale = persistence.finish_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            reservation_id="reused-id",
            status="committed",
            reason="late_old_provider_result",
            attempt_generation=first["attempt_generation"],
            attempt_token=first["attempt_token"],
            finished_at_ms=3_002,
        )
        assert stale["status"] == "reserved"
        assert stale["finished"] is False
        assert stale["stale"] is True
        assert stale["ignored"] is True
        assert stale["reason"] == "reservation_attempt_mismatch"

        current = persistence.realtime_provider_reservation_snapshot(
            "reservation-meeting",
            now_ms=3_002,
        )["reused-id"]
        assert current["status"] == "reserved"
        assert current["attempt_generation"] == second["attempt_generation"]
        assert current["attempt_token"] == second["attempt_token"]

        legacy_stale = persistence.finish_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            reservation_id="reused-id",
            status="released",
            reason="late_tokenless_old_worker",
            finished_at_ms=3_002,
        )
        assert legacy_stale["status"] == "reserved"
        assert legacy_stale["finished"] is False
        assert legacy_stale["reason"] == "reservation_attempt_token_required"

        finished = persistence.finish_realtime_provider_attempt(
            meeting_id="reservation-meeting",
            reservation_id="reused-id",
            status="committed",
            reason="new_provider_result",
            attempt_generation=second["attempt_generation"],
            attempt_token=second["attempt_token"],
            finished_at_ms=3_003,
        )
        assert finished["status"] == "committed"
        assert finished["finished"] is True
        assert persistence.realtime_provider_reservation_snapshot(
            "reservation-meeting",
            now_ms=3_004,
        )["reused-id"]["status"] == "committed"

        events = _reservation_events(persistence, "reservation-meeting")
        assert [event["payload"]["status"] for event in events] == [
            "reserved",
            "reserved",
            "committed",
        ]
    finally:
        persistence.close()


def test_two_independent_repositories_only_one_reservation_wins(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    _create_meeting(database_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: _reserve(
                    database_path,
                    reservation_id=item,
                    job_id=f"job-{item}",
                ),
                ("concurrent-a", "concurrent-b"),
            )
        )
    assert sorted(result["reserved"] for result in results) == [False, True]


def _configure_pi_app(monkeypatch, tmp_path):
    """Build an llm-first app with deterministic provider configuration."""

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
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    # The Pi-only branch never invokes the semantic Provider, but injecting a
    # client keeps the app setup equivalent to the production worker startup.
    app.state.streaming_llm_client = object()
    return app


def _coach_result(request, *, runtime_used: str = "pi") -> dict:
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
        origin="pi" if runtime_used == "pi" else "direct_fallback",
        status="intervention",
        status_reason="intervention_submitted",
        decision_reason="证据足够且现在介入仍有价值。",
        intervention=intervention,
    )
    now_ms = time.time_ns() // 1_000_000
    result.update(
        {
            "transport_mode": "pi_agent_jsonl",
            "model": "test-realtime-model",
            "runtime_requested": "pi",
            "runtime_used": runtime_used,
            "fallback_error_code": None,
            "fallback_reason": None if runtime_used == "pi" else "runtime_unavailable",
            "ttft_ms": 5.0,
            "decision_latency_ms": 10.0,
            "timings": {
                "clock": "unix_epoch_ms",
                "started_at_ms": now_ms,
                "first_token_at_ms": now_ms + 5,
                "completed_at_ms": now_ms + 10,
            },
            "agent_metrics": {"turns": 1, "tool_calls": 1},
        }
    )
    return result


def _commit_and_claim_pi_job(app, *, meeting_id: str, suffix: str, now_ms: int) -> dict:
    persistence = app.state.v2_persistence
    committed = persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id=f"{suffix}-final",
        segment_id=f"{suffix}-segment",
        text="我们一定周五上线，但回滚负责人还没定。",
        normalized_text="我们一定周五上线，但回滚负责人还没定。",
        started_at_ms=0,
        ended_at_ms=1_000,
        evidence_hash=f"{suffix}-hash",
        source_track="microphone",
        now_ms=now_ms,
    )
    job = persistence.claim_next_job(
        worker_id=f"{suffix}-worker",
        lane="intelligence",
        # The intelligence debounce is two seconds; claim at the boundary so
        # the test does not depend on an actual sleep.
        now_ms=now_ms + 2_500,
        lease_ms=30_000,
    )
    assert job is not None
    assert job["id"] == committed["job_ids"]["intelligence"]
    return job


def _reservation_events(persistence, meeting_id: str) -> list[dict]:
    return [
        event
        for event in persistence.list_events(meeting_id)
        if event["type"]
        in {
            persistence_module.REALTIME_PROVIDER_RESERVATION_EVENT_TYPE,
            persistence_module.REALTIME_PROVIDER_RESERVATION_FINISHED_EVENT_TYPE,
        }
    ]


def test_app_reservation_is_committed_after_successful_projection(tmp_path, monkeypatch) -> None:
    app = _configure_pi_app(monkeypatch, tmp_path)

    async def fake_pi_coach(**kwargs):
        # Mirror the real Pi route's pre-attempt callback. This is the point at
        # which the app begins treating the reservation as a paid Provider try.
        kwargs["before_attempt"](1)
        return _coach_result(kwargs["request"])

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_coach)
    meeting_id = "app-reservation-committed"
    job = _commit_and_claim_pi_job(
        app,
        meeting_id=meeting_id,
        suffix="committed",
        now_ms=time.time_ns() // 1_000_000,
    )

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    assert output["coach"]["status"] == "intervention"
    assert output["coach"]["runtime_used"] == "pi"
    assert output["coach"]["pi_provider_attempted"] is True

    events = _reservation_events(app.state.v2_persistence, meeting_id)
    assert [event["payload"]["status"] for event in events] == ["reserved", "committed"]
    assert events[-1]["payload"]["reason"] == "meeting.intelligence.applied"
    # A committed reservation no longer contributes to the episode cooldown
    # through the in-flight reservation replay; the applied decision remains
    # the durable cooldown source instead.
    decision = output["applied"]["coach_decision"]
    episode_id = decision["eligible_candidate_events"][0]["episode_id"]
    assert app.state.v2_persistence.recent_coach_episode_priorities(
        meeting_id,
        since_ms=0,
        now_ms=time.time_ns() // 1_000_000,
    ) == {episode_id: decision["eligible_candidate_events"][0]["candidate_priority"]}


def test_app_provider_timeout_releases_reservation_after_late_result_guard(
    tmp_path,
    monkeypatch,
) -> None:
    app = _configure_pi_app(monkeypatch, tmp_path)

    async def fake_pi_timeout(**kwargs):
        kwargs["before_attempt"](1)
        error = app_module.IntelligenceDeadlineExceeded("synthetic provider timeout")
        error.code = "soft_deadline_exceeded"
        raise error

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_timeout)
    meeting_id = "app-reservation-provider-timeout"
    job = _commit_and_claim_pi_job(
        app,
        meeting_id=meeting_id,
        suffix="provider-timeout",
        now_ms=time.time_ns() // 1_000_000,
    )

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    events = _reservation_events(app.state.v2_persistence, meeting_id)
    assert [event["payload"]["status"] for event in events] == ["reserved", "released"]
    assert events[-1]["payload"]["reason"] == "provider_timeout"
    reservation = output["coach"]["provider_reservation"]
    assert reservation["status"] == "released"
    assert reservation["finish_reason"] == "provider_timeout"
    assert output["coach"]["pi_provider_attempted"] is True
    assert output["coach"]["late_result_discarded"] is False
    assert output["coach"]["fallback_error_code"] == "soft_deadline_exceeded"


def test_app_provider_timeout_mapping_releases_reservation(
    tmp_path,
    monkeypatch,
) -> None:
    app = _configure_pi_app(monkeypatch, tmp_path)

    async def fake_pi_timeout_mapping(**kwargs):
        kwargs["before_attempt"](1)
        result = _coach_result(kwargs["request"])
        result.update(
            {
                "origin": "local_reflex",
                "runtime_used": "local_reflex",
                "fallback_error_code": "soft_deadline_exceeded",
                "fallback_reason": "provider_timeout",
                "agent_metrics": {
                    "provider_availability_terminal_reason": "provider_timeout",
                },
            }
        )
        return result

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_timeout_mapping)
    meeting_id = "app-reservation-provider-timeout-mapping"
    job = _commit_and_claim_pi_job(
        app,
        meeting_id=meeting_id,
        suffix="provider-timeout-mapping",
        now_ms=time.time_ns() // 1_000_000,
    )

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    events = _reservation_events(app.state.v2_persistence, meeting_id)
    assert [event["payload"]["status"] for event in events] == ["reserved", "released"]
    assert events[-1]["payload"]["reason"] == "provider_timeout"
    assert output["coach"]["provider_reservation"]["status"] == "released"


def test_handler_and_snapshot_project_authoritative_reservation_finish(
    tmp_path,
    monkeypatch,
) -> None:
    app = _configure_pi_app(monkeypatch, tmp_path)

    async def fake_pi_coach(**kwargs):
        kwargs["before_attempt"](1)
        return _coach_result(kwargs["request"])

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_coach)
    meeting_id = "app-reservation-authoritative-read"
    job = _commit_and_claim_pi_job(
        app,
        meeting_id=meeting_id,
        suffix="authoritative-read",
        now_ms=time.time_ns() // 1_000_000,
    )

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    reservation_id = str(
        output["coach"]["provider_reservation"]["reservation_id"]
    )
    assert output["coach"]["provider_reservation"]["status"] == "committed"
    assert output["applied"]["coach_decision"]["provider_reservation"]["status"] == "committed"

    snapshot = app.state.v2_persistence.get_snapshot(meeting_id)
    assert snapshot["realtime_provider_reservations"][reservation_id]["status"] == "committed"
    assert snapshot["coach_decision"]["provider_reservation"]["status"] == "committed"

    response = TestClient(app).get(f"/v2/meetings/{meeting_id}/snapshot")
    assert response.status_code == 200
    assert response.json()["coach_decision"]["provider_reservation"]["status"] == "committed"


def test_app_direct_fallback_releases_reservation_without_pi_attempt(tmp_path, monkeypatch) -> None:
    app = _configure_pi_app(monkeypatch, tmp_path)

    async def fake_direct_fallback(**kwargs):
        # Deliberately do not call before_attempt: Pi was unavailable before it
        # handed work to the runtime, so this is a direct fallback only.
        return _coach_result(kwargs["request"], runtime_used="direct")

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_direct_fallback)
    meeting_id = "app-reservation-direct-fallback"
    job = _commit_and_claim_pi_job(
        app,
        meeting_id=meeting_id,
        suffix="direct-fallback",
        now_ms=time.time_ns() // 1_000_000,
    )

    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    coach = output["coach"]
    assert coach["status"] == "intervention"
    assert coach["runtime_requested"] == "pi"
    assert coach["runtime_used"] == "direct"
    assert coach["pi_provider_attempted"] is False
    reservation_audit = coach["provider_reservation"]
    assert reservation_audit["status"] == "released"
    assert reservation_audit["finish_reason"] == "direct_fallback"

    events = _reservation_events(app.state.v2_persistence, meeting_id)
    assert [event["payload"]["status"] for event in events] == ["reserved", "released"]
    assert events[-1]["payload"]["reason"] == "direct_fallback"
    assert app.state.v2_persistence.recent_coach_episode_priorities(
        meeting_id,
        since_ms=0,
        now_ms=time.time_ns() // 1_000_000,
    ) == {}


def test_app_projection_failure_after_pi_attempt_keeps_reservation_until_ttl(
    tmp_path,
    monkeypatch,
) -> None:
    app = _configure_pi_app(monkeypatch, tmp_path)

    async def fake_pi_coach(**kwargs):
        kwargs["before_attempt"](1)
        return _coach_result(kwargs["request"])

    monkeypatch.setattr(app_module, "run_realtime_coach_routed", fake_pi_coach)
    persistence = app.state.v2_persistence
    original_apply = persistence.apply_intelligence_response

    def fail_projection(**_kwargs):
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr(persistence, "apply_intelligence_response", fail_projection)
    meeting_id = "app-reservation-projection-failure"
    job = _commit_and_claim_pi_job(
        app,
        meeting_id=meeting_id,
        suffix="projection-failure",
        now_ms=time.time_ns() // 1_000_000,
    )

    with pytest.raises(RuntimeError, match="synthetic projection failure"):
        asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

    events = _reservation_events(persistence, meeting_id)
    assert [event["payload"]["status"] for event in events] == ["reserved"]
    reservation = events[0]["payload"]
    assert reservation["status"] == "reserved"
    expires_at_ms = int(reservation["expires_at_ms"])
    episode_id = reservation["episode_ids"][0]
    priority = int(reservation["priority"])

    # Reconstruct through an independent repository to exercise the crash
    # window that the durable event log is meant to protect.
    restarted = V2Persistence(tmp_path / "meeting_copilot.db")
    try:
        assert restarted.recent_coach_episode_priorities(
            meeting_id,
            since_ms=0,
            now_ms=expires_at_ms - 1,
        ) == {episode_id: priority}
        assert restarted.recent_coach_episode_priorities(
            meeting_id,
            since_ms=0,
            now_ms=expires_at_ms,
        ) == {}
        replacement = restarted.reserve_realtime_provider_attempt(
            meeting_id=meeting_id,
            job_id="replacement-after-ttl",
            reservation_id="replacement-after-ttl",
            episode_ids=[episode_id],
            candidate_keys=reservation["candidate_keys"],
            priority=priority,
            reserved_at_ms=expires_at_ms,
            expires_at_ms=expires_at_ms + 1_000,
        )
        assert replacement["reserved"] is True
    finally:
        restarted.close()

    # Keep the original method referenced so a future refactor cannot silently
    # turn this into a test that never exercised the real projection boundary.
    assert callable(original_apply)
