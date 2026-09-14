from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from threading import Event, Lock
import time

import pytest

from meeting_copilot_web_mvp import v2_pipeline
from meeting_copilot_web_mvp.app import CorrectionProviderPriorityDeferred
from meeting_copilot_web_mvp.llm_lane_locks import ProviderPriorityArbiter
from meeting_copilot_web_mvp.v2_persistence import V2Persistence
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor, DueCoachRefreshScheduler


@pytest.mark.parametrize(
    "category",
    ["structural", "truncated", "evidence", "stale", "semantic_safety"],
)
def test_intelligence_validation_error_class_keeps_only_safe_category(category):
    error_type = type("IntelligenceResponseValidationError", (ValueError,), {})
    error = error_type("must not be persisted")
    error.category = category

    assert v2_pipeline._handler_error_class(error) == f"intelligence_validation_{category}"


def test_intelligence_validation_error_class_rejects_unknown_category():
    error_type = type("IntelligenceResponseValidationError", (ValueError,), {})
    error = error_type("must not be persisted")
    error.category = "provider_response_text"

    assert v2_pipeline._handler_error_class(error) == "IntelligenceResponseValidationError"


def test_due_coach_refresh_scheduler_ticks_wakes_and_stops_cleanly():
    async def scenario() -> None:
        calls: list[int] = []
        scheduler = DueCoachRefreshScheduler(
            lambda: calls.append(len(calls) + 1),
            interval_ms=10_000,
        )
        await scheduler.start()
        await _wait_until(lambda: len(calls) == 1)
        scheduler.wake()
        await _wait_until(lambda: len(calls) == 2)
        assert scheduler.running
        await scheduler.stop()
        assert not scheduler.running
        count_after_stop = len(calls)
        scheduler.wake()
        await asyncio.sleep(0.02)
        assert len(calls) == count_after_stop

    asyncio.run(scenario())


def test_due_coach_refresh_scheduler_survives_callback_failure():
    async def scenario() -> None:
        calls = 0

        def callback() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient")

        scheduler = DueCoachRefreshScheduler(callback, interval_ms=10_000)
        await scheduler.start()
        await _wait_until(lambda: calls == 1)
        scheduler.wake()
        await _wait_until(lambda: calls == 2)
        await scheduler.stop()

    asyncio.run(scenario())


class MutableClock:
    def __init__(self, now_ms: int) -> None:
        self._now_ms = now_ms
        self._lock = Lock()

    def __call__(self) -> int:
        with self._lock:
            return self._now_ms

    def advance(self, milliseconds: int) -> None:
        with self._lock:
            self._now_ms += milliseconds


def _commit_final(
    persistence: V2Persistence,
    *,
    final_number: int,
    now_ms: int,
    max_attempts: int = 3,
) -> dict:
    text = f"第 {final_number} 段会议文本"
    return persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id=f"final-{final_number}",
        segment_id=f"segment-{final_number}",
        text=text,
        normalized_text=text,
        started_at_ms=now_ms - 100,
        ended_at_ms=now_ms,
        evidence_hash=f"hash-{final_number}",
        now_ms=now_ms,
        max_attempts=max_attempts,
    )


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 2.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not predicate():
            await asyncio.sleep(0.005)


def test_lanes_run_in_parallel_and_each_lane_remains_sequential(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        _commit_final(persistence, final_number=1, now_ms=1_000)
        _commit_final(persistence, final_number=2, now_ms=2_000)

        release = asyncio.Event()
        both_lanes_started = asyncio.Event()
        started_lanes: set[str] = set()
        active = {"correction": 0, "suggestion": 0}
        max_active = {"correction": 0, "suggestion": 0}
        order = {"correction": [], "suggestion": []}

        def handler_for(lane: str):
            async def handler(job: dict):
                active[lane] += 1
                max_active[lane] = max(max_active[lane], active[lane])
                order[lane].append(job["input_transcript_seq"])
                started_lanes.add(lane)
                if len(started_lanes) == 2:
                    both_lanes_started.set()
                try:
                    await release.wait()
                    return {"lane": lane, "seq": job["input_transcript_seq"]}
                finally:
                    active[lane] -= 1

            return handler

        executor = DurableJobExecutor(
            persistence,
            correction_handler=handler_for("correction"),
            suggestion_handler=handler_for("suggestion"),
            worker_id="parallel-test",
            poll_interval_ms=5,
        )
        try:
            await executor.start()
            await asyncio.wait_for(both_lanes_started.wait(), timeout=1)
            assert active == {"correction": 1, "suggestion": 1}
            release.set()
            await _wait_until(lambda: all(job["status"] == "succeeded" for job in persistence.list_jobs()))
        finally:
            await executor.stop()
            persistence.close()

        assert max_active == {"correction": 1, "suggestion": 1}
        assert order == {"correction": [1, 2], "suggestion": [1, 2]}

    asyncio.run(scenario())


def test_successful_handler_output_is_committed(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)

        async def correction_handler(job: dict):
            return {"corrected_text": f"已修正:{job['evidence_segment_id']}"}

        async def suggestion_handler(job: dict):
            return {"suggestion_text": f"建议:{job['evidence_segment_id']}"}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="success-test",
            poll_interval_ms=5,
        )
        try:
            await executor.start()
            await _wait_until(lambda: all(job["status"] == "succeeded" for job in persistence.list_jobs()))
            correction = persistence.get_job(committed["job_ids"]["correction"])
            suggestion = persistence.get_job(committed["job_ids"]["suggestion"])
            assert correction["output"] == {"corrected_text": "已修正:segment-1"}
            assert suggestion["output"] == {"suggestion_text": "建议:segment-1"}
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_pi_deep_consumer_executes_explicit_request_and_wakes_from_intelligence(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(
            tmp_path / "pi-deep-consumer.db",
            semantic_projection_mode="llm_first",
        )
        try:
            base_ms = time.time_ns() // 1_000_000
            _commit_final(persistence, final_number=1, now_ms=base_ms)
            observed: list[dict] = []

            async def deep_handler(job: dict):
                observed.append(dict(job))
                return {
                    "coach": {
                        "status": "protected_silent",
                        "provider_lane": "pi_deep",
                    }
                }

            executor = DurableJobExecutor(
                persistence,
                correction_handler=lambda job: {"job_id": job["id"]},
                suggestion_handler=lambda job: {"job_id": job["id"]},
                additional_handlers={
                    # Production registers both intelligence lanes. The
                    # shared wake API is intentionally addressed as
                    # ``intelligence`` and fans out to ``pi_deep``.
                    "intelligence": lambda job: {"job_id": job["id"]},
                    "pi_deep": deep_handler,
                },
                worker_id="pi-deep-consumer-test",
                poll_interval_ms=50,
            )
            try:
                await executor.start()
                await asyncio.sleep(0.05)
                deep_job = persistence.enqueue_user_coach_request(
                    meeting_id="meeting-1",
                    user_request="请复查当前会议还有哪些未闭环事项",
                    idempotency_key="api.coach.request:meeting-1:deep-consumer",
                    now_ms=time.time_ns() // 1_000_000,
                )
                # Producers publish all intelligence work through the shared
                # lane name; the executor must fan that wake to pi_deep.
                executor.wake("intelligence")
                await _wait_until(
                    lambda: persistence.get_job(deep_job["id"])["status"] == "succeeded"
                )
            finally:
                await executor.stop()

            assert len(observed) == 1
            assert observed[0]["id"] == deep_job["id"]
            assert observed[0]["trigger_type"] == "user_request"
            assert persistence.get_job(deep_job["id"])["output"] == {
                "coach": {
                    "provider_lane": "pi_deep",
                    "status": "protected_silent",
                }
            }
        finally:
            persistence.close()

    asyncio.run(scenario())


def test_failed_handler_retries_after_backoff_and_then_succeeds(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        clock = MutableClock(10_000)
        suggestion_calls = 0
        retried_job_ids: list[str] = []

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict):
            nonlocal suggestion_calls
            suggestion_calls += 1
            if suggestion_calls == 1:
                raise ConnectionError("temporary provider failure")
            return {"suggestion_text": "重试成功"}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="retry-test",
            poll_interval_ms=5,
            retry_initial_ms=250,
            retry_max_ms=1_000,
            now_ms=clock,
            retry_observer=retried_job_ids.append,
        )
        suggestion_job_id = committed["job_ids"]["suggestion"]
        try:
            await executor.start()
            await _wait_until(lambda: persistence.get_job(suggestion_job_id)["status"] == "retry_wait")
            waiting = persistence.get_job(suggestion_job_id)
            assert waiting["attempts"] == 1
            assert waiting["next_attempt_at_ms"] == 10_250
            assert waiting["error_class"] == "ConnectionError"

            clock.advance(250)
            executor.wake("suggestion")
            await _wait_until(lambda: persistence.get_job(suggestion_job_id)["status"] == "succeeded")
            succeeded = persistence.get_job(suggestion_job_id)
            assert succeeded["attempts"] == 2
            assert succeeded["output"] == {"suggestion_text": "重试成功"}
            assert suggestion_calls == 2
            assert retried_job_ids == [suggestion_job_id]
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_handler_can_request_a_bounded_retry_delay(tmp_path):
    class DeferredCorrection(RuntimeError):
        retry_after_ms = 15_000

    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        clock = MutableClock(10_000)
        correction_calls = 0

        async def correction_handler(job: dict):
            nonlocal correction_calls
            correction_calls += 1
            if correction_calls == 1:
                raise DeferredCorrection("wait for a useful correction batch")
            return {"corrected": job["id"]}

        async def suggestion_handler(job: dict):
            return {"suggestion": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="deferred-correction-test",
            poll_interval_ms=5,
            retry_initial_ms=250,
            retry_max_ms=30_000,
            now_ms=clock,
        )
        correction_job_id = committed["job_ids"]["correction"]
        try:
            await executor.start()
            await _wait_until(lambda: persistence.get_job(correction_job_id)["status"] == "retry_wait")
            waiting = persistence.get_job(correction_job_id)
            assert waiting["next_attempt_at_ms"] == 25_000
            assert waiting["error_class"] == "DeferredCorrection"

            clock.advance(15_000)
            executor.wake("correction")
            await _wait_until(lambda: persistence.get_job(correction_job_id)["status"] == "succeeded")
            assert correction_calls == 2
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_deferred_provider_wait_does_not_consume_job_attempts(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="meeting_copilot_web_mvp.v2_pipeline")

    class ProviderRuntimeNotConfiguredDeferred(RuntimeError):
        preserve_attempt = True
        retry_after_ms = 10_000

    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        clock = MutableClock(10_000)
        suggestion_calls = 0

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict):
            nonlocal suggestion_calls
            suggestion_calls += 1
            if suggestion_calls <= 4:
                raise ProviderRuntimeNotConfiguredDeferred("connect the configured provider")
            return {"suggestion_text": "连接后生成成功", "job_id": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="provider-wait-test",
            poll_interval_ms=5,
            retry_initial_ms=250,
            retry_max_ms=30_000,
            now_ms=clock,
        )
        suggestion_job_id = committed["job_ids"]["suggestion"]
        try:
            await executor.start()
            for expected_calls in range(1, 5):
                await _wait_until(
                    lambda: suggestion_calls == expected_calls
                    and persistence.get_job(suggestion_job_id)["status"] == "retry_wait"
                )
                waiting = persistence.get_job(suggestion_job_id)
                assert waiting["attempts"] == 0
                assert waiting["error_class"] == "ProviderRuntimeNotConfiguredDeferred"
                clock.advance(10_000)
                executor.wake("suggestion")

            await _wait_until(lambda: persistence.get_job(suggestion_job_id)["status"] == "succeeded")
            succeeded = persistence.get_job(suggestion_job_id)
            assert succeeded["attempts"] == 1
            assert suggestion_calls == 5
            deferred_records = [
                record
                for record in caplog.records
                if "ProviderRuntimeNotConfiguredDeferred" in record.getMessage()
            ]
            assert deferred_records
            assert all(record.levelno < 30 for record in deferred_records)
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_provider_dependency_pause_requires_explicit_resume(tmp_path):
    class ProviderRuntimeNotConfiguredDeferred(RuntimeError):
        preserve_attempt = True
        retry_after_ms = 10_000
        pause_until_explicit_resume = True

    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "provider-pause.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        clock = MutableClock(10_000)
        calls = 0
        provider_configured = False

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict):
            nonlocal calls
            calls += 1
            if not provider_configured:
                raise ProviderRuntimeNotConfiguredDeferred("connect provider")
            return {"suggestion_text": "连接后恢复", "job_id": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="provider-pause-test",
            poll_interval_ms=5,
            retry_initial_ms=250,
            retry_max_ms=30_000,
            now_ms=clock,
        )
        suggestion_job_id = committed["job_ids"]["suggestion"]
        try:
            await executor.start()
            await _wait_until(
                lambda: calls == 1
                and persistence.get_job(suggestion_job_id)["status"] == "retry_wait"
            )
            waiting = persistence.get_job(suggestion_job_id)
            assert waiting["attempts"] == 0
            assert waiting["error_class"] == "ProviderRuntimeNotConfiguredDeferred"

            clock.advance(60_000)
            executor.wake("suggestion")
            await asyncio.sleep(0.03)
            assert calls == 1
            assert persistence.get_job(suggestion_job_id)["status"] == "retry_wait"

            provider_configured = True
            executor.resume("suggestion")
            await _wait_until(
                lambda: persistence.get_job(suggestion_job_id)["status"] == "succeeded"
            )
            assert calls == 2
            assert persistence.get_job(suggestion_job_id)["attempts"] == 1
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_resume_during_provider_failure_does_not_leave_lane_paused(tmp_path):
    class ProviderRuntimeNotConfiguredDeferred(RuntimeError):
        preserve_attempt = True
        retry_after_ms = 10_000
        pause_until_explicit_resume = True

    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "provider-resume-race.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        clock = MutableClock(10_000)
        first_call_started = asyncio.Event()
        release_first_call = asyncio.Event()
        calls = 0

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_call_started.set()
                await release_first_call.wait()
                raise ProviderRuntimeNotConfiguredDeferred("stale provider state")
            return {"suggestion_text": "配置变更已生效", "job_id": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="provider-resume-race-test",
            poll_interval_ms=5,
            retry_initial_ms=250,
            retry_max_ms=30_000,
            now_ms=clock,
        )
        suggestion_job_id = committed["job_ids"]["suggestion"]
        try:
            await executor.start()
            await asyncio.wait_for(first_call_started.wait(), timeout=1)
            executor.resume("suggestion")
            release_first_call.set()
            await _wait_until(
                lambda: persistence.get_job(suggestion_job_id)["status"] == "retry_wait"
            )

            clock.advance(10_000)
            executor.wake("suggestion")
            await _wait_until(
                lambda: persistence.get_job(suggestion_job_id)["status"] == "succeeded"
            )
            assert calls == 2
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_pi_priority_defer_preserves_correction_attempt_and_retries(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(
            tmp_path / "pi-priority-defer.db",
            semantic_projection_mode="llm_first",
        )
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        correction_id = committed["job_ids"]["correction"]
        intelligence_id = committed["job_ids"]["intelligence"]
        clock = MutableClock(10_000)
        correction_calls = 0

        async def correction_handler(job: dict):
            nonlocal correction_calls
            correction_calls += 1
            if correction_calls == 1:
                raise CorrectionProviderPriorityDeferred(
                    blocking_job_ids=[intelligence_id]
                )
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict):
            return {"ok": job["id"]}

        async def intelligence_handler(job: dict):
            return {"ok": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            additional_handlers={"intelligence": intelligence_handler},
            worker_id="pi-priority-defer",
            poll_interval_ms=5,
            retry_initial_ms=1_000,
            retry_max_ms=1_000,
            now_ms=clock,
        )
        try:
            await executor.start()
            await _wait_until(
                lambda: persistence.get_job(correction_id)["status"] == "retry_wait"
            )
            waiting = persistence.get_job(correction_id)
            assert waiting["attempts"] == 0
            assert waiting["error_class"] == "CorrectionProviderPriorityDeferred"
            assert waiting["next_attempt_at_ms"] == 10_250

            clock.advance(250)
            executor.wake("correction")
            await _wait_until(
                lambda: persistence.get_job(correction_id)["status"] == "succeeded"
            )
            assert persistence.get_job(correction_id)["attempts"] == 1
            assert correction_calls == 2
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_non_retryable_handler_failure_reaches_terminal_state_once(tmp_path):
    class NonRetryableProviderError(RuntimeError):
        retryable = False

    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        suggestion_calls = 0

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(_job: dict):
            nonlocal suggestion_calls
            suggestion_calls += 1
            raise NonRetryableProviderError("invalid provider request")

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="non-retryable-test",
            poll_interval_ms=5,
        )
        suggestion_job_id = committed["job_ids"]["suggestion"]
        try:
            await executor.start()
            await _wait_until(lambda: persistence.get_job(suggestion_job_id)["status"] == "failed")
            failed = persistence.get_job(suggestion_job_id)
            assert failed["attempts"] == 1
            assert failed["error_class"] == "NonRetryableProviderError"
            await asyncio.sleep(0.03)
            assert suggestion_calls == 1
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_superseded_handler_is_cancelled_instead_of_reported_failed(tmp_path):
    class EvidenceSuperseded(RuntimeError):
        superseded = True
        retryable = False

    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        cancelled_job_ids: list[str] = []

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(_job: dict):
            raise EvidenceSuperseded("new transcript evidence replaced this request")

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="superseded-test",
            poll_interval_ms=5,
            cancellation_observer=cancelled_job_ids.append,
        )
        suggestion_job_id = committed["job_ids"]["suggestion"]
        try:
            await executor.start()
            await _wait_until(lambda: persistence.get_job(suggestion_job_id)["status"] == "cancelled")
            cancelled = persistence.get_job(suggestion_job_id)
            assert cancelled["attempts"] == 1
            assert cancelled["error_class"] == "evidence_superseded"
            assert cancelled_job_ids == [suggestion_job_id]
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_fresher_intelligence_cancels_inflight_job_and_runs_without_waiting_for_deadline(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(
            tmp_path / "meeting_copilot.db",
            semantic_projection_mode="llm_first",
        )
        clock = MutableClock(3_000)
        first = _commit_final(persistence, final_number=1, now_ms=1_000)
        first_started = asyncio.Event()
        first_cancelled = asyncio.Event()
        calls: list[int] = []
        cancelled_job_ids: list[str] = []

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def intelligence_handler(job: dict):
            sequence = int(job["input_transcript_seq"])
            calls.append(sequence)
            if sequence == 1:
                first_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    first_cancelled.set()
                    raise
            return {"latest_sequence": sequence}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=lambda job: {"ok": job["id"]},
            additional_handlers={"intelligence": intelligence_handler},
            worker_id="latest-intelligence-test",
            poll_interval_ms=5,
            now_ms=clock,
            cancellation_observer=cancelled_job_ids.append,
        )
        try:
            await executor.start()
            await asyncio.wait_for(first_started.wait(), timeout=1)
            second = _commit_final(persistence, final_number=2, now_ms=3_100)
            assert executor.supersede_running(
                "intelligence",
                meeting_id="meeting-1",
                replacement_job_id=second["job_ids"]["intelligence"],
            )
            clock.advance(2_100)
            executor.wake("intelligence")

            await asyncio.wait_for(first_cancelled.wait(), timeout=1)
            await _wait_until(
                lambda: persistence.get_job(second["job_ids"]["intelligence"])["status"]
                == "succeeded"
            )
            cancelled = persistence.get_job(first["job_ids"]["intelligence"])
            succeeded = persistence.get_job(second["job_ids"]["intelligence"])
            assert cancelled["status"] == "cancelled"
            assert cancelled["error_class"] == "evidence_superseded"
            assert succeeded["output"] == {"latest_sequence": 2}
            assert calls == [1, 2]
            assert cancelled_job_ids == [first["job_ids"]["intelligence"]]
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_running_pi_does_not_block_independent_correction_lane(tmp_path):
    """Correction may run beside Pi; each lane owns its lifecycle."""

    async def scenario() -> None:
        persistence = V2Persistence(
            tmp_path / "meeting-end-concurrency.db",
            semantic_projection_mode="llm_first",
        )
        clock = MutableClock(10_000)
        committed = _commit_final(
            persistence,
            final_number=1,
            now_ms=1_000,
        )
        arbiter = ProviderPriorityArbiter()
        intelligence_id = committed["job_ids"]["intelligence"]
        # Mirror the pre-claim reservation made by the app's final commit.
        arbiter.reserve_realtime(intelligence_id)
        pi_started = asyncio.Event()
        pi_release = asyncio.Event()
        active_provider_count = 0
        overlap_detected = False
        intervals: dict[str, tuple[float, float]] = {}

        def provider_start(lane: str) -> None:
            nonlocal active_provider_count, overlap_detected
            if active_provider_count:
                overlap_detected = True
            active_provider_count += 1
            intervals[lane] = (asyncio.get_running_loop().time(), 0.0)

        def provider_end(lane: str) -> None:
            nonlocal active_provider_count
            started_at, _ = intervals[lane]
            intervals[lane] = (started_at, asyncio.get_running_loop().time())
            active_provider_count -= 1

        async def correction_handler(job: dict):
            lease = arbiter.try_acquire_background()
            if lease is None:
                raise RuntimeError("correction lane capacity")
            try:
                await pi_started.wait()
                provider_start("correction")
                provider_end("correction")
                return {"job_id": job["id"], "provider": "correction"}
            finally:
                lease.release()

        async def intelligence_handler(job: dict):
            lease = None
            arbiter.reserve_realtime(job["id"])
            try:
                while lease is None:
                    lease = arbiter.try_acquire_realtime(job["id"])
                    if lease is None:
                        await asyncio.sleep(0.001)
                provider_start("pi")
                pi_started.set()
                await pi_release.wait()
                provider_end("pi")
                return {"job_id": job["id"], "provider": "pi"}
            finally:
                if lease is not None:
                    lease.release()
                arbiter.release_realtime_reservation(job["id"])

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=lambda job: {"job_id": job["id"]},
            additional_handlers={"intelligence": intelligence_handler},
            worker_id="meeting-end-concurrency",
            poll_interval_ms=5,
            retry_initial_ms=1_000,
            retry_max_ms=1_000,
            now_ms=clock,
        )
        try:
            await executor.start()
            await asyncio.wait_for(pi_started.wait(), timeout=1)
            await _wait_until(
                lambda: persistence.get_job(committed["job_ids"]["correction"])["status"]
                == "succeeded"
            )

            # Ending the meeting does not cancel the in-flight Pi call.
            persistence.end_meeting(meeting_id="meeting-1", now_ms=10_100)
            assert persistence.get_job(intelligence_id)["status"] == "running"
            pi_release.set()
            await _wait_until(lambda: persistence.get_job(intelligence_id)["status"] == "succeeded")

            assert persistence.get_job(committed["job_ids"]["correction"])["id"] == committed["job_ids"]["correction"]
            assert overlap_detected
            assert intervals["correction"][0] <= intervals["pi"][1]
        finally:
            pi_release.set()
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_sync_background_provider_keeps_lease_until_request_returns():
    """A thread-backed sync request cannot be safely cancelled mid-flight."""

    async def scenario() -> None:
        arbiter = ProviderPriorityArbiter()
        background = arbiter.try_acquire_background()
        assert background is not None
        request_started = Event()
        request_release = Event()

        def slow_sync_provider() -> str:
            request_started.set()
            assert request_release.wait(timeout=2)
            return "ok"

        request = asyncio.create_task(asyncio.to_thread(slow_sync_provider))
        try:
            await asyncio.to_thread(request_started.wait, 1)
            arbiter.reserve_realtime("pi-after-sync")
            realtime = arbiter.try_acquire_realtime("pi-after-sync")
            assert realtime is not None
            realtime.release()

            # Releasing the durable/background lease before the thread exits
            # would permit an overlapping realtime request. Wait for the
            # actual sync request completion first.
            request_release.set()
            assert await request == "ok"
            background.release()
        finally:
            request_release.set()
            if not request.done():
                await request
            background.release()
            arbiter.release_realtime_reservation("pi-after-sync")

    asyncio.run(scenario())


def test_correction_timeout_preserves_original_and_next_segment_continues(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        first = _commit_final(
            persistence,
            final_number=1,
            now_ms=1_000,
            max_attempts=1,
        )
        second = _commit_final(
            persistence,
            final_number=2,
            now_ms=2_000,
            max_attempts=1,
        )
        correction_calls: list[int] = []

        async def correction_handler(job: dict):
            correction_calls.append(int(job["input_transcript_seq"]))
            if job["id"] == first["job_ids"]["correction"]:
                raise TimeoutError("provider timed out")
            return {"no_revision_needed": True}

        async def suggestion_handler(job: dict):
            return {"ok": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="correction-timeout-test",
            poll_interval_ms=5,
        )
        try:
            await executor.start()
            await _wait_until(
                lambda: persistence.get_job(first["job_ids"]["correction"])["status"] == "failed"
                and persistence.get_job(second["job_ids"]["correction"])["status"] == "succeeded"
            )

            segments = persistence.list_transcript_segments("meeting-1", limit=10)["segments"]
            assert correction_calls == [1, 2]
            assert segments[0]["normalized_text"] == "第 1 段会议文本"
            assert segments[0]["correction_status"] == "failed_preserved_original"
            assert segments[0]["correction_error_class"] == "TimeoutError"
            assert segments[1]["normalized_text"] == "第 2 段会议文本"
            assert segments[1]["correction_status"] == "no_change"
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_heartbeat_renews_lease_for_long_running_handler(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict):
            await asyncio.sleep(0.35)
            return {"long_running": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="heartbeat-test",
            lease_ms=200,
            heartbeat_interval_ms=20,
            poll_interval_ms=5,
        )
        suggestion_job_id = committed["job_ids"]["suggestion"]
        try:
            await executor.start()
            await _wait_until(lambda: persistence.get_job(suggestion_job_id)["status"] == "succeeded")
            succeeded = persistence.get_job(suggestion_job_id)
            assert succeeded["attempts"] == 1
            assert succeeded["output"] == {"long_running": suggestion_job_id}
        finally:
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_heartbeat_lease_loss_records_structured_lifecycle(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "heartbeat-loss.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        started = asyncio.Event()
        events: list[dict] = []

        async def correction_handler(job: dict):
            started.set()
            await asyncio.Event().wait()
            return {"unreachable": job["id"]}

        async def suggestion_handler(job: dict):
            return {"ok": job["id"]}

        def lost_heartbeat(**_kwargs):
            return False

        persistence.heartbeat_job = lost_heartbeat  # type: ignore[method-assign]
        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="heartbeat-loss-test",
            lease_ms=30,
            heartbeat_interval_ms=5,
            poll_interval_ms=5,
            lifecycle_observer=lambda event: events.append(dict(event)),
        )
        try:
            await executor.start()
            await asyncio.wait_for(started.wait(), timeout=1)
            await _wait_until(
                lambda: persistence.get_job(committed["job_ids"]["correction"])["status"]
                == "cancelled"
            )
        finally:
            await executor.stop()
            persistence.close()

        assert any(
            event["event"] == "terminal"
            and event["durable_status"] == "cancelled"
            and event["terminal_outcome"] == "failed"
            and event["result_outcome"] == "transport_error"
            and event["error_class"] == "RuntimeError"
            for event in events
        )

    asyncio.run(scenario())


def test_forced_shutdown_records_active_job_cancellation_lifecycle(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "shutdown-cancellation.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        started = asyncio.Event()
        events: list[dict] = []

        async def correction_handler(job: dict):
            started.set()
            await asyncio.Event().wait()
            return {"unreachable": job["id"]}

        async def suggestion_handler(job: dict):
            return {"ok": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="shutdown-cancellation-test",
            poll_interval_ms=5,
            shutdown_timeout_s=0.01,
            lifecycle_observer=lambda event: events.append(dict(event)),
        )
        try:
            await executor.start()
            await asyncio.wait_for(started.wait(), timeout=1)
            await executor.stop()
        finally:
            await executor.stop()
        job = persistence.get_job(committed["job_ids"]["correction"])
        persistence.close()
        assert job["status"] == "cancelled"
        assert any(
            event["event"] == "terminal"
            and event["durable_status"] == "cancelled"
            and event["terminal_outcome"] == "cancelled"
            and event["result_outcome"] == "cancelled"
            and event["error_class"] == "executor_shutdown"
            for event in events
        )

    asyncio.run(scenario())


def test_review_handler_deadline_cancels_stalled_provider_work(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "review-deadline.db")
        _commit_final(persistence, final_number=1, now_ms=1_000)
        review = persistence.enqueue_review_job(
            meeting_id="meeting-1",
            kind="approach",
            now_ms=2_000,
        )
        job_id = str(review["job"]["id"])
        # Keep the test fast while exercising the same durable deadline path.
        persistence._conn.execute(
            "UPDATE jobs SET deadline_at_ms = ? WHERE id = ?",
            (int(time.time() * 1_000) + 25, job_id),
        )
        started = asyncio.Event()
        events: list[dict] = []

        async def stalled_handler(_job: dict):
            started.set()
            await asyncio.Event().wait()

        executor = DurableJobExecutor(
            persistence,
            correction_handler=lambda _job: {"ok": True},
            suggestion_handler=lambda _job: {"ok": True},
            additional_handlers={"approach": stalled_handler},
            worker_id="review-deadline-test",
            poll_interval_ms=2,
            lifecycle_observer=lambda event: events.append(dict(event)),
        )
        try:
            await executor.start()
            await asyncio.wait_for(started.wait(), timeout=1)
            await _wait_until(
                lambda: persistence.get_job(job_id)["status"] == "cancelled",
                timeout_s=1,
            )
        finally:
            await executor.stop()
        cancelled = persistence.get_job(job_id)
        persistence.close()
        assert cancelled["status"] == "cancelled"
        assert cancelled["error_class"] == "deadline_exceeded"
        assert any(
            event["event"] == "terminal"
            and event["job_id"] == job_id
            and event["durable_status"] == "cancelled"
            and event["terminal_outcome"] == "timeout"
            and event["error_class"] == "IntelligenceDeadlineExceeded"
            for event in events
        )

    asyncio.run(scenario())


def test_start_recovers_expired_lease_and_graceful_stop_waits_for_handler(tmp_path):
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting_copilot.db")
        committed = _commit_final(persistence, final_number=1, now_ms=1_000)
        suggestion_job_id = committed["job_ids"]["suggestion"]
        claimed = persistence.claim_next_job(
            worker_id="crashed-worker",
            lane="suggestion",
            now_ms=1_100,
            lease_ms=100,
        )
        assert claimed is not None

        entered = asyncio.Event()
        release = asyncio.Event()

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict):
            entered.set()
            await release.wait()
            return {"recovered": job["id"]}

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="recovery-test",
            poll_interval_ms=5,
            now_ms=lambda: 2_000,
        )
        await executor.start()
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            stop_task = asyncio.create_task(executor.stop())
            await asyncio.sleep(0.02)
            assert not stop_task.done()
            release.set()
            await asyncio.wait_for(stop_task, timeout=1)
            recovered = persistence.get_job(suggestion_job_id)
            assert recovered["status"] == "succeeded"
            assert recovered["attempts"] == 2
            assert recovered["output"] == {"recovered": suggestion_job_id}
        finally:
            release.set()
            await executor.stop()
            persistence.close()

    asyncio.run(scenario())


def test_structured_lifecycle_observer_reports_success_retry_failure_and_deadline(tmp_path):
    class RateLimited(RuntimeError):
        status_code = 429
        retryable = True

    class ProviderServerFailure(RuntimeError):
        status_code = 503
        retryable = False

    async def scenario() -> None:
        persistence = V2Persistence(
            tmp_path / "lifecycle-telemetry.db",
            semantic_projection_mode="legacy",
        )
        first = _commit_final(persistence, final_number=1, now_ms=1_000, max_attempts=2)
        intelligence_persistence = V2Persistence(
            tmp_path / "lifecycle-intelligence.db",
            semantic_projection_mode="llm_first",
        )
        intelligence = _commit_final(
            intelligence_persistence,
            final_number=1,
            now_ms=1_000,
            max_attempts=2,
        )
        clock = MutableClock(2_000)
        events: list[dict] = []
        suggestion_calls = 0

        async def correction_handler(job: dict):
            return {"ok": job["id"]}

        async def suggestion_handler(job: dict):
            nonlocal suggestion_calls
            suggestion_calls += 1
            if suggestion_calls == 1:
                raise RateLimited("private provider detail")
            raise ProviderServerFailure("private provider detail")

        executor = DurableJobExecutor(
            persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            worker_id="lifecycle-observer-test",
            poll_interval_ms=5,
            retry_initial_ms=10,
            retry_max_ms=10,
            now_ms=clock,
            lifecycle_observer=lambda event: events.append(dict(event)),
        )
        try:
            await executor.start()
            await _wait_until(
                lambda: persistence.get_job(first["job_ids"]["suggestion"])["status"]
                == "retry_wait"
            )
            clock.advance(10)
            executor.wake("suggestion")
            await _wait_until(
                lambda: persistence.get_job(first["job_ids"]["suggestion"])["status"]
                == "failed"
                and persistence.get_job(first["job_ids"]["correction"])["status"]
                == "succeeded"
            )
        finally:
            await executor.stop()
            persistence.close()

        async def intelligence_handler(_job: dict):
            raise v2_pipeline.IntelligenceDeadlineExceeded("private deadline detail")

        intelligence_executor = DurableJobExecutor(
            intelligence_persistence,
            correction_handler=correction_handler,
            suggestion_handler=suggestion_handler,
            additional_handlers={"intelligence": intelligence_handler},
            worker_id="lifecycle-intelligence-observer-test",
            poll_interval_ms=5,
            now_ms=clock,
            lifecycle_observer=lambda event: events.append(dict(event)),
        )
        try:
            await intelligence_executor.start()
            await _wait_until(
                lambda: intelligence_persistence.get_job(
                    intelligence["job_ids"]["intelligence"]
                )["status"]
                == "cancelled"
            )
        finally:
            await intelligence_executor.stop()
            intelligence_persistence.close()

        assert any(
            event["event"] == "attempt_failed"
            and event["result_outcome"] == "rate_limit"
            and event["error_class"] == "RateLimited"
            for event in events
        )
        assert any(
            event["event"] == "terminal"
            and event["durable_status"] == "failed"
            and event["result_outcome"] == "provider_5xx"
            for event in events
        )
        timeout_events = [
            event
            for event in events
            if event["event"] == "terminal"
            and event["durable_status"] == "cancelled"
        ]
        assert timeout_events == [
            {
                "event": "terminal",
                "job_id": intelligence["job_ids"]["intelligence"],
                "meeting_id": "meeting-1",
                "lane": "intelligence",
                "attempt_index": 1,
                "durable_status": "cancelled",
                "terminal_outcome": "timeout",
                "result_outcome": "timeout",
                "error_class": "IntelligenceDeadlineExceeded",
            }
        ]
        assert any(
            event["event"] == "terminal"
            and event["durable_status"] == "succeeded"
            and event["result_outcome"] == "success"
            for event in events
        )
        assert "private provider detail" not in repr(events)

    asyncio.run(scenario())
