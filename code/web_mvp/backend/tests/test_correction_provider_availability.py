from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from threading import Barrier, Thread

import pytest

from meeting_copilot_web_mvp.correction_provider_availability import (
    CorrectionProviderAvailability,
    CorrectionProviderAvailabilityDeferred,
    classify_correction_availability_failure,
    correction_provider_identity,
)
from meeting_copilot_web_mvp.llm_service import (
    LlmConfig,
    LlmProviderHttpError,
    LlmProviderTransportError,
    correction_config,
)
from meeting_copilot_web_mvp.realtime_provider_circuit import RealtimeProviderCircuit
from meeting_copilot_web_mvp.v2_persistence import V2Persistence
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor


class Clock:
    def __init__(self) -> None:
        self.seconds = 10.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def config() -> LlmConfig:
    return LlmConfig(
        base_url="https://availability.example.test/v1",
        api_key="offline-test-credential-only",
        model="general-model",
        realtime_model="working-pi-model",
        correction_model="correction-model",
        api_style="responses",
    )


def open_availability(guard: CorrectionProviderAvailability, cfg: LlmConfig) -> None:
    for outcome in (LlmProviderTransportError("timeout"), LlmProviderTransportError("transport")):
        attempt = guard.acquire(cfg)
        assert attempt.admitted and attempt.permit is not None
        attempt.permit.record_failure(outcome)
    assert guard.snapshot(cfg).state == "open"


def test_failed_requests_pause_for_sixty_seconds_and_skip_does_not_consume_calls() -> None:
    clock = Clock()
    guard = CorrectionProviderAvailability(clock=clock)
    cfg = config()
    calls = 0
    for _ in range(58):
        attempt = guard.acquire(cfg)
        if attempt.admitted:
            calls += 1
            assert attempt.permit is not None
            attempt.permit.record_failure(LlmProviderTransportError("timeout"))
    assert calls == 2
    assert guard.snapshot(cfg).failure_count == 2
    assert guard.snapshot(cfg).retry_after_ms == 60_000
    clock.advance(59.0)
    assert not guard.acquire(cfg).admitted
    clock.advance(1.0)
    trial = guard.acquire(cfg)
    assert trial.admitted and trial.permit is not None and trial.permit.half_open
    assert not guard.acquire(cfg).admitted
    trial.permit.record_failure(LlmProviderTransportError("timeout"))
    assert guard.snapshot(cfg).failure_count == 3
    assert guard.snapshot(cfg).retry_after_ms == 60_000


def test_deferred_admission_preserves_job_attempt_without_creating_provider_work() -> None:
    guard = CorrectionProviderAvailability(clock=Clock())
    cfg = config()
    open_availability(guard, cfg)
    for _ in range(8):
        with pytest.raises(CorrectionProviderAvailabilityDeferred) as caught:
            guard.admit_or_defer(cfg)
        assert caught.value.preserve_attempt is True
        assert caught.value.retry_after_ms == 60_000
        assert caught.value.snapshot.state == "open"
    assert guard.snapshot(cfg).failure_count == 2


@pytest.mark.parametrize("outcome", [
    asyncio.CancelledError(), ValueError("malformed schema"),
    {"error_class": "evidence_superseded"}, {"category": "structural"},
    {"category": "correction_speech_mode_changed"}, {"status_code": 401},
    {"status_code": 400}, RuntimeError("provider_timeout embedded in untrusted text"),
])
def test_cancel_content_schema_and_unknown_errors_do_not_increment_or_clear_streak(outcome) -> None:
    guard = CorrectionProviderAvailability(clock=Clock())
    cfg = config()
    first = guard.admit_or_defer(cfg)
    first.record_failure(LlmProviderTransportError("timeout"))
    for _ in range(3):
        permit = guard.admit_or_defer(cfg)
        permit.record_failure(outcome)
    assert guard.snapshot(cfg).state == "closed"
    assert guard.snapshot(cfg).failure_count == 1
    assert classify_correction_availability_failure(outcome) is None


@pytest.mark.parametrize("outcome, expected", [
    (LlmProviderTransportError("timeout"), "timeout"),
    (LlmProviderTransportError("transport"), "transport"),
    (LlmProviderHttpError(502), "provider_server"),
    (LlmProviderHttpError(429), "rate_limit"),
    ({"error_class": "provider_timeout"}, "timeout"),
    ({"category": "rate_limited"}, "rate_limit"),
])
def test_only_allowlisted_failures_are_classified(outcome, expected: str) -> None:
    assert classify_correction_availability_failure(outcome) == expected


def test_rate_limit_uses_existing_immediate_backoff_and_bounds_untrusted_hint() -> None:
    guard = CorrectionProviderAvailability(clock=Clock())
    cfg = config()
    guard.admit_or_defer(cfg).record_failure(LlmProviderHttpError(429), retry_after_ms=10**12)
    snapshot = guard.snapshot(cfg)
    assert snapshot.state == "open" and snapshot.failure_count == 1
    assert snapshot.retry_after_ms == 300_000


def test_successful_trial_and_explicit_resolution_clear_failure_streak() -> None:
    clock = Clock()
    guard = CorrectionProviderAvailability(clock=clock)
    cfg = config()
    open_availability(guard, cfg)
    clock.advance(60)
    trial = guard.admit_or_defer(cfg)
    trial.record_success()
    assert guard.snapshot(cfg).state == "closed"
    assert guard.snapshot(cfg).failure_count == 0
    open_availability(guard, cfg)
    guard.record_resolution_success(cfg)
    assert guard.snapshot(cfg).state == "closed"
    assert guard.snapshot(cfg).failure_count == 0


def test_effective_identity_normalization_and_actual_config_changes() -> None:
    cfg = config()
    original = correction_provider_identity(cfg)
    assert original == correction_provider_identity(correction_config(cfg))
    assert original == correction_provider_identity(replace(cfg, realtime_model="another-pi-model"))
    for changed in (
        replace(cfg, api_key="different-credential"),
        replace(cfg, correction_model="new-correction-model"),
        replace(cfg, base_url="https://new.example.test/v1"),
        replace(cfg, api_style="chat_completions"),
    ):
        assert original != correction_provider_identity(changed)
    assert original != correction_provider_identity(cfg, config_revision=5)


def test_durable_restart_and_config_changes_keep_outages_isolated(tmp_path) -> None:
    path = tmp_path / "availability.db"
    clock = Clock()
    cfg = config()
    persistence = V2Persistence(path)
    guard = CorrectionProviderAvailability(persistence=persistence, clock=clock)
    open_availability(guard, cfg)
    persistence.close()
    reopened = V2Persistence(path)
    try:
        fresh = CorrectionProviderAvailability(persistence=reopened, clock=clock)
        assert not fresh.acquire(cfg).admitted
        changed = replace(cfg, api_key="replacement-credential")
        permit = fresh.admit_or_defer(changed)
        permit.release()
        assert fresh.snapshot(changed).state == "closed"
        assert fresh.snapshot(cfg).state == "open"
        stored = reopened._conn.execute("SELECT * FROM realtime_provider_circuits").fetchall()
        public = json.dumps([dict(row) for row in stored]) + json.dumps(fresh.snapshot(cfg).to_dict())
        assert cfg.api_key not in public and cfg.base_url not in public
        assert "replacement-credential" not in public
        assert "AI校对暂不可用，识别原文已保存" in fresh.snapshot(cfg).to_dict()["message"]
    finally:
        reopened.close()


def test_cross_repository_threads_admit_exactly_one_recovery_and_expire_lost_lease(tmp_path) -> None:
    path = tmp_path / "shared.db"
    a, b = V2Persistence(path), V2Persistence(path)
    clock = Clock()
    cfg = config()
    guards = [CorrectionProviderAvailability(persistence=repo, clock=clock) for repo in (a, b)]
    try:
        open_availability(guards[0], cfg)
        clock.advance(60)
        barrier = Barrier(3)
        admissions = []
        def acquire(guard) -> None:
            barrier.wait()
            admissions.append(guard.acquire(cfg))
        threads = [Thread(target=acquire, args=(guard,)) for guard in guards]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert sum(admission.admitted for admission in admissions) == 1
        accepted = next(admission for admission in admissions if admission.admitted)
        assert accepted.permit is not None
        assert not guards[0].acquire(cfg).admitted
        clock.advance(44.0)
        assert not guards[1].acquire(cfg).admitted
        clock.advance(1.0)
        replacement = guards[1].admit_or_defer(cfg)
        accepted.permit.record_success()  # lost lease cannot close replacement's state
        assert guards[0].snapshot(cfg).state == "half_open"
        replacement.record_success()
        assert guards[0].snapshot(cfg).state == "closed"
    finally:
        a.close()
        b.close()


def test_working_pi_circuit_remains_independent_and_completion_is_single_use(tmp_path) -> None:
    persistence = V2Persistence(tmp_path / "lanes.db")
    clock = Clock()
    cfg = config()
    try:
        correction = CorrectionProviderAvailability(persistence=persistence, clock=clock)
        pi = RealtimeProviderCircuit(persistence=persistence, clock=clock)
        pi_identity = (cfg.base_url, cfg.model, "scope:pi_realtime")
        open_availability(correction, cfg)
        pi_permit = pi.acquire(pi_identity).permit
        assert pi_permit is not None
        pi_permit.record_success()
        assert pi.snapshot(pi_identity).state == "closed"
        assert correction.snapshot(cfg).state == "open"
        clock.advance(60)
        permit = correction.admit_or_defer(cfg)
        permit.record_success()
        permit.record_failure(LlmProviderTransportError("timeout"))
        assert correction.snapshot(cfg).failure_count == 0
    finally:
        persistence.close()


def test_actual_executor_defers_without_attempts_then_uses_one_recovery_request(tmp_path) -> None:
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "executor.db")
        clock = Clock()
        cfg = config()
        guard = CorrectionProviderAvailability(persistence=persistence, clock=clock)
        open_availability(guard, cfg)
        committed = persistence.commit_final_and_enqueue(
            meeting_id="meeting-guard",
            final_id="final-guard",
            segment_id="segment-guard",
            text="这里是保存的识别原文",
            normalized_text="这里是保存的识别原文",
            started_at_ms=100,
            ended_at_ms=1_000,
            evidence_hash="offline-evidence-guard",
            now_ms=1_000,
            max_attempts=1,
        )
        correction_id = committed["job_ids"]["correction"]
        claimed_handler_calls = 0
        actual_provider_calls = 0

        async def correction_handler(job: dict) -> dict:
            nonlocal claimed_handler_calls, actual_provider_calls
            claimed_handler_calls += 1
            permit = guard.admit_or_defer(cfg)
            actual_provider_calls += 1
            permit.record_success()
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict) -> dict:
            return {"ok": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="availability-executor-test",
            poll_interval_ms=5,
            retry_initial_ms=250,
            retry_max_ms=10_000,
            now_ms=lambda: int(clock() * 1_000),
        )

        async def wait_for(predicate) -> None:
            async with asyncio.timeout(3):
                while not predicate():
                    await asyncio.sleep(0.005)

        try:
            await executor.start()
            for count in range(1, 5):
                await wait_for(lambda: claimed_handler_calls == count and persistence.get_job(correction_id)["status"] == "retry_wait")
                waiting = persistence.get_job(correction_id)
                assert waiting["attempts"] == 0
                assert actual_provider_calls == 0
                assert guard.snapshot(cfg).failure_count == 2
                clock.advance(10)
                executor.wake("correction")
            clock.advance(20)
            executor.wake("correction")
            await wait_for(lambda: persistence.get_job(correction_id)["status"] == "succeeded")
            assert persistence.get_job(correction_id)["attempts"] == 1
            assert actual_provider_calls == 1
            assert guard.snapshot(cfg).failure_count == 0
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())
