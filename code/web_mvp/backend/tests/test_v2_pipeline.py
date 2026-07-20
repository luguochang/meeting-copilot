from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from threading import Lock

import pytest

from meeting_copilot_web_mvp import v2_pipeline
from meeting_copilot_web_mvp.v2_persistence import V2Persistence
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor


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
