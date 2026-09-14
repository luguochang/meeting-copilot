from __future__ import annotations

from threading import Barrier, Thread

from meeting_copilot_web_mvp.realtime_provider_circuit import (
    RealtimeProviderCircuit,
    classify_realtime_provider_failure,
    normalize_failure_class,
)
from meeting_copilot_web_mvp.streaming_llm_provider import (
    ProviderErrorCategory,
    StreamingProviderError,
)
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _open_circuit(circuit: RealtimeProviderCircuit, identity: str = "provider-a") -> None:
    first = circuit.acquire(identity)
    assert first.admitted and first.permit is not None
    first.permit.record_failure("timeout")
    second = circuit.acquire(identity)
    assert second.admitted and second.permit is not None
    second.permit.record_failure("provider_server")
    assert circuit.snapshot(identity).state == "open"


def test_two_retryable_failures_open_for_fifteen_seconds_and_success_resets() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)

    first = circuit.acquire("provider-a")
    assert first.permit is not None
    first.permit.record_failure("timeout")
    healthy = circuit.acquire("provider-a")
    assert healthy.permit is not None
    healthy.permit.record_success()

    # The successful attempt clears the streak; one later failure is not open.
    after_reset = circuit.acquire("provider-a")
    assert after_reset.admitted and after_reset.permit is not None
    after_reset.permit.record_failure("transport")
    assert circuit.snapshot("provider-a").state == "closed"

    circuit.record_probe_success("provider-a")
    _open_circuit(circuit)
    blocked = circuit.acquire("provider-a")
    assert blocked.admitted is False
    assert blocked.snapshot.reason == "realtime_provider_circuit_open"
    assert 14_000 <= blocked.snapshot.retry_after_ms <= 15_000

    clock.advance(15.0)
    trial = circuit.acquire("provider-a")
    assert trial.admitted and trial.permit is not None
    assert trial.snapshot.state == "half_open"
    trial.permit.record_success()
    assert circuit.snapshot("provider-a").state == "closed"


def test_failed_explicit_probe_opens_immediately_and_successful_probe_recovers() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)

    failed = circuit.record_probe_failure("provider-a", "timeout")

    assert failed.state == "open"
    assert failed.reason == "manual_probe_failed"
    assert failed.failure_count == circuit.failure_threshold
    assert failed.last_failure_class == "timeout"
    assert failed.retry_after_ms == 15_000
    assert circuit.acquire("provider-a").admitted is False

    recovered = circuit.record_probe_success("provider-a")
    assert recovered.state == "closed"
    assert recovered.failure_count == 0
    assert circuit.acquire("provider-a").admitted is True


def test_non_retryable_explicit_probe_failure_does_not_poison_circuit() -> None:
    circuit = RealtimeProviderCircuit()

    snapshot = circuit.record_probe_failure("provider-a", "authentication")

    assert snapshot.state == "closed"
    assert snapshot.failure_count == 0


def test_non_retryable_failure_does_not_open_the_availability_circuit() -> None:
    circuit = RealtimeProviderCircuit()
    for _ in range(4):
        attempt = circuit.acquire("provider-a")
        assert attempt.admitted and attempt.permit is not None
        attempt.permit.record_failure("authentication")
    snapshot = circuit.snapshot("provider-a")
    assert snapshot.state == "closed"
    assert snapshot.failure_count == 0

    assert classify_realtime_provider_failure({"status_code": 401}) is None
    assert classify_realtime_provider_failure({"status_code": 503}) == "provider_server"
    assert classify_realtime_provider_failure({"fallback_reason": "provider_timeout"}) == "timeout"


def test_half_open_allows_only_one_concurrent_trial() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    _open_circuit(circuit)
    clock.advance(15.0)

    barrier = Barrier(3)
    results = []

    def worker() -> None:
        barrier.wait()
        results.append(circuit.acquire("provider-a"))

    threads = [Thread(target=worker), Thread(target=worker)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sum(result.admitted for result in results) == 1
    admitted = next(result for result in results if result.admitted)
    blocked = next(result for result in results if not result.admitted)
    assert admitted.permit is not None and admitted.permit.half_open is True
    assert blocked.snapshot.reason == "realtime_provider_circuit_half_open"
    admitted.permit.record_success()


def test_configuration_identity_change_resets_and_invalidates_old_result() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    first = circuit.acquire("provider-a")
    assert first.permit is not None
    first.permit.record_failure("timeout")
    second = circuit.acquire("provider-a")
    assert second.permit is not None

    changed = circuit.acquire("provider-b")
    assert changed.admitted and changed.snapshot.state == "closed"
    assert changed.permit is not None
    changed.permit.record_failure("timeout")

    # The old provider's late failure must not poison the new identity.
    second.permit.record_failure("timeout")
    snapshot = circuit.snapshot("provider-b")
    assert snapshot.state == "closed"
    assert snapshot.failure_count == 1
    assert snapshot.identity_generation == 2


def test_late_closed_state_success_cannot_close_newly_opened_circuit() -> None:
    circuit = RealtimeProviderCircuit()
    first = circuit.acquire("provider-a")
    second = circuit.acquire("provider-a")
    late_success = circuit.acquire("provider-a")
    assert first.permit is not None
    assert second.permit is not None
    assert late_success.permit is not None

    first.permit.record_failure("timeout")
    second.permit.record_failure("transport")
    assert circuit.snapshot("provider-a").state == "open"

    late_success.permit.record_success()
    snapshot = circuit.snapshot("provider-a")
    assert snapshot.state == "open"
    assert snapshot.failure_count == 2


def test_pre_open_permit_cannot_steal_half_open_trial_transition() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    first = circuit.acquire("provider-a")
    second = circuit.acquire("provider-a")
    stale = circuit.acquire("provider-a")
    assert first.permit is not None
    assert second.permit is not None
    assert stale.permit is not None
    first.permit.record_failure("timeout")
    second.permit.record_failure("provider_server")

    clock.advance(15.0)
    trial = circuit.acquire("provider-a")
    assert trial.admitted and trial.permit is not None
    stale.permit.record_success()
    assert circuit.snapshot("provider-a").state == "half_open"

    trial.permit.record_failure("transport")
    assert circuit.snapshot("provider-a").state == "open"


def test_nested_pi_provider_status_classifies_only_server_failures() -> None:
    assert (
        classify_realtime_provider_failure(
            {"agent_metrics": {"provider_status_code": 503}}
        )
        == "provider_server"
    )
    assert (
        classify_realtime_provider_failure(
            {"agent_metrics": {"provider_status_code": 429}}
        )
        == "rate_limit"
    )


def test_streaming_provider_error_enums_and_rate_limited_alias_are_classified() -> None:
    expected = {
        ProviderErrorCategory.RATE_LIMIT: "rate_limit",
        ProviderErrorCategory.TIMEOUT: "timeout",
        ProviderErrorCategory.TRANSPORT: "transport",
        ProviderErrorCategory.PROVIDER_SERVER: "provider_server",
    }
    for category, failure_class in expected.items():
        error = StreamingProviderError(category, retryable=True)
        assert classify_realtime_provider_failure(error) == failure_class
        assert classify_realtime_provider_failure({"category": category}) == failure_class

    assert normalize_failure_class("rate_limited") == "rate_limit"


def test_streaming_rate_limit_opens_immediately_after_an_existing_failure() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    first = circuit.acquire("provider-a")
    assert first.permit is not None
    first.permit.record_failure("provider_server")

    limited = circuit.acquire("provider-a")
    assert limited.permit is not None
    error = StreamingProviderError(
        ProviderErrorCategory.RATE_LIMIT,
        retryable=True,
    )
    limited.permit.record_failure(classify_realtime_provider_failure(error))

    snapshot = circuit.snapshot("provider-a")
    assert snapshot.state == "open"
    assert snapshot.reason == "realtime_provider_rate_limit_backoff"
    assert snapshot.failure_count == 2
    assert snapshot.last_failure_class == "rate_limit"
    assert snapshot.retry_after_ms == 60_000


def test_rate_limit_opens_immediately_for_sixty_seconds() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    first = circuit.acquire("provider-a")
    assert first.admitted and first.permit is not None
    first.permit.record_failure("rate_limit")

    snapshot = circuit.snapshot("provider-a")
    assert snapshot.state == "open"
    assert snapshot.failure_count == 1
    assert snapshot.last_failure_class == "rate_limit"
    assert 59_000 <= snapshot.retry_after_ms <= 60_000

    clock.advance(59.0)
    assert circuit.acquire("provider-a").admitted is False
    clock.advance(1.0)
    trial = circuit.acquire("provider-a")
    assert trial.admitted and trial.permit is not None
    assert trial.snapshot.state == "half_open"


def test_releasing_an_unused_rate_limit_trial_does_not_restart_backoff() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    first = circuit.acquire("provider-a")
    assert first.permit is not None
    first.permit.record_failure("rate_limit")

    clock.advance(60.0)
    unused_trial = circuit.acquire("provider-a")
    assert unused_trial.permit is not None and unused_trial.permit.half_open
    unused_trial.permit.release()

    released = circuit.snapshot("provider-a")
    assert released.state == "open"
    assert released.retry_after_ms == 0
    next_trial = circuit.acquire("provider-a")
    assert next_trial.admitted and next_trial.permit is not None
    assert next_trial.permit.half_open
    next_trial.permit.record_success()
    assert circuit.snapshot("provider-a").state == "closed"


def test_success_after_rate_limit_restores_the_standard_failure_cooldown() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    limited = circuit.acquire("provider-a")
    assert limited.permit is not None
    limited.permit.record_failure("rate_limit")
    clock.advance(60.0)
    recovered = circuit.acquire("provider-a")
    assert recovered.permit is not None
    recovered.permit.record_success()

    first_failure = circuit.acquire("provider-a")
    second_failure = circuit.acquire("provider-a")
    assert first_failure.permit is not None
    assert second_failure.permit is not None
    first_failure.permit.record_failure("timeout")
    second_failure.permit.record_failure("provider_server")

    snapshot = circuit.snapshot("provider-a")
    assert snapshot.state == "open"
    assert 14_000 <= snapshot.retry_after_ms <= 15_000


def test_successful_manual_probe_closes_open_circuit_and_cancels_half_open_trial() -> None:
    clock = FakeClock()
    circuit = RealtimeProviderCircuit(clock=clock)
    _open_circuit(circuit)
    clock.advance(15.0)
    trial = circuit.acquire("provider-a")
    assert trial.admitted and trial.permit is not None
    assert circuit.record_probe_success("provider-a").reason == "manual_probe_succeeded"
    assert circuit.snapshot("provider-a").state == "closed"

    # A late failed trial is ignored after the manual probe reset.
    trial.permit.record_failure("timeout")
    snapshot = circuit.snapshot("provider-a")
    assert snapshot.state == "closed"
    assert snapshot.failure_count == 0


def test_durable_circuit_survives_repository_restart_and_allows_one_half_open_trial(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    persistence_a = V2Persistence(database_path)
    persistence_b = V2Persistence(database_path)
    try:
        persistence_a.create_meeting(meeting_id="circuit-meeting", title="circuit", now_ms=0)
        clock = FakeClock()
        first = RealtimeProviderCircuit(persistence=persistence_a, clock=clock)
        second = RealtimeProviderCircuit(persistence=persistence_b, clock=clock)
        identity = ("https://provider.example.test/v1", "model")

        a = first.acquire(identity)
        assert a.permit is not None
        a.permit.record_failure("timeout")
        b = second.acquire(identity)
        assert b.permit is not None
        b.permit.record_failure("provider_server")
        assert second.snapshot(identity).state == "open"

        # A newly constructed circuit reads the same open row.
        restarted = RealtimeProviderCircuit(persistence=persistence_b, clock=clock)
        assert restarted.snapshot(identity).state == "open"
        clock.advance(15.0)
        trial_a = first.acquire(identity)
        trial_b = restarted.acquire(identity)
        assert sorted((trial_a.admitted, trial_b.admitted)) == [False, True]
        admitted = trial_a if trial_a.admitted else trial_b
        assert admitted.permit is not None and admitted.permit.half_open
        admitted.permit.record_success()
        assert first.snapshot(identity).state == "closed"
    finally:
        persistence_a.close()
        persistence_b.close()


def test_durable_retry_after_is_bounded_and_never_shortens_default(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    persistence = V2Persistence(database_path)
    try:
        persistence.create_meeting(meeting_id="circuit-meeting", title="circuit", now_ms=0)
        clock = FakeClock()
        circuit = RealtimeProviderCircuit(persistence=persistence, clock=clock)
        identity = "provider-a"
        attempt = circuit.acquire(identity)
        assert attempt.permit is not None
        attempt.permit.record_failure("rate_limit", retry_after_ms=999_999)
        snapshot = circuit.snapshot(identity)
        assert snapshot.retry_after_ms == 300_000
        clock.advance(15.0)
        assert circuit.acquire(identity).admitted is False
    finally:
        persistence.close()


def test_durable_failed_probe_blocks_other_process_until_probe_recovers(tmp_path) -> None:
    database_path = tmp_path / "meeting.db"
    persistence_a = V2Persistence(database_path)
    persistence_b = V2Persistence(database_path)
    try:
        persistence_a.create_meeting(meeting_id="circuit-meeting", title="circuit", now_ms=0)
        clock = FakeClock()
        first = RealtimeProviderCircuit(persistence=persistence_a, clock=clock)
        second = RealtimeProviderCircuit(persistence=persistence_b, clock=clock)
        identity = ("https://provider.example.test/v1", "model")

        failed = first.record_probe_failure(identity, "transport")

        assert failed.state == "open"
        assert failed.reason == "manual_probe_failed"
        observed = second.snapshot(identity)
        assert observed.state == "open"
        assert observed.failure_count == 2
        assert observed.last_failure_class == "transport"
        assert second.acquire(identity).admitted is False

        recovered = second.record_probe_success(identity)
        assert recovered.state == "closed"
        assert first.snapshot(identity).state == "closed"
    finally:
        persistence_a.close()
        persistence_b.close()
