from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
import logging
import time
from typing import Any, TypeAlias
from uuid import uuid4

from .v2_persistence import V2Persistence


JobOutput: TypeAlias = Mapping[str, Any] | list[Any] | str | int | float | bool | None
JobHandler: TypeAlias = Callable[[dict[str, Any]], JobOutput | Awaitable[JobOutput]]


class DurableJobExecutor:
    """Run the durable correction and suggestion queues in independent lanes.

    Each lane has exactly one consumer, preserving lane order while allowing a
    correction and a suggestion to execute concurrently. Repository calls run
    in worker threads so SQLite access and synchronous handlers do not block the
    asyncio loop.
    """

    def __init__(
        self,
        persistence: V2Persistence,
        *,
        correction_handler: JobHandler,
        suggestion_handler: JobHandler,
        worker_id: str | None = None,
        lease_ms: int = 30_000,
        heartbeat_interval_ms: int | None = None,
        poll_interval_ms: int = 100,
        retry_initial_ms: int = 1_000,
        retry_max_ms: int = 30_000,
        shutdown_timeout_s: float = 30.0,
        now_ms: Callable[[], int] | None = None,
        additional_handlers: Mapping[str, JobHandler] | None = None,
    ) -> None:
        if lease_ms < 2:
            raise ValueError("lease_ms must be at least 2")
        resolved_heartbeat_ms = heartbeat_interval_ms or max(1, lease_ms // 3)
        if resolved_heartbeat_ms <= 0 or resolved_heartbeat_ms >= lease_ms:
            raise ValueError("heartbeat_interval_ms must be positive and less than lease_ms")
        if poll_interval_ms <= 0:
            raise ValueError("poll_interval_ms must be positive")
        if retry_initial_ms < 0:
            raise ValueError("retry_initial_ms must not be negative")
        if retry_max_ms < retry_initial_ms:
            raise ValueError("retry_max_ms must be at least retry_initial_ms")
        if shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be positive")

        self._persistence = persistence
        self._handlers = {
            "correction": correction_handler,
            "suggestion": suggestion_handler,
        }
        for lane, handler in dict(additional_handlers or {}).items():
            normalized_lane = str(lane or "").strip()
            if not normalized_lane:
                raise ValueError("additional handler lane must not be empty")
            if normalized_lane in self._handlers:
                raise ValueError(f"duplicate durable job lane: {normalized_lane}")
            if not callable(handler):
                raise TypeError(f"handler for lane {normalized_lane!r} must be callable")
            self._handlers[normalized_lane] = handler
        self._lanes = tuple(self._handlers)
        self._worker_id = str(worker_id or f"v2-{uuid4().hex}").strip()
        if not self._worker_id:
            raise ValueError("worker_id must not be empty")
        self._lease_ms = int(lease_ms)
        self._heartbeat_interval_s = resolved_heartbeat_ms / 1_000
        self._poll_interval_s = poll_interval_ms / 1_000
        self._retry_initial_ms = int(retry_initial_ms)
        self._retry_max_ms = int(retry_max_ms)
        self._shutdown_timeout_s = float(shutdown_timeout_s)
        self._now_ms = now_ms or (lambda: int(time.time() * 1_000))

        self._stop_event = asyncio.Event()
        self._wake_events = {lane: asyncio.Event() for lane in self._lanes}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._logger = logging.getLogger(__name__)

    @property
    def running(self) -> bool:
        return bool(self._tasks) and all(not task.done() for task in self._tasks.values())

    async def start(self) -> None:
        """Recover expired work and start one consumer task per lane."""

        async with self._lifecycle_lock:
            if self._tasks:
                if self.running:
                    return
                raise RuntimeError("durable job executor has a failed lane; stop it before restart")

            self._stop_event.clear()
            for event in self._wake_events.values():
                event.clear()
            recovered = await asyncio.to_thread(
                self._persistence.recover_expired_leases,
                now_ms=self._clock_ms(),
            )
            if recovered:
                self._logger.info("Recovered %s expired V2 job lease(s)", recovered)

            self._tasks = {
                lane: asyncio.create_task(
                    self._run_lane(lane),
                    name=f"v2-job-{lane}",
                )
                for lane in self._lanes
            }

    async def stop(self, *, timeout_s: float | None = None) -> None:
        """Stop claiming work and let in-flight handlers finish within the timeout.

        A forced cancellation deliberately leaves an in-flight job leased. The
        next executor recovers it after lease expiry, preventing an immediate
        duplicate while a cancelled synchronous handler thread winds down.
        """

        async with self._lifecycle_lock:
            if not self._tasks:
                return
            self._stop_event.set()
            self.wake()
            tasks = tuple(self._tasks.values())
            resolved_timeout = self._shutdown_timeout_s if timeout_s is None else float(timeout_s)
            if resolved_timeout <= 0:
                raise ValueError("timeout_s must be positive")

            try:
                async with asyncio.timeout(resolved_timeout):
                    await asyncio.gather(*tasks)
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self._logger.warning("Forced V2 job executor shutdown after %.3fs", resolved_timeout)
            finally:
                self._tasks = {}

    def wake(self, lane: str | None = None) -> None:
        """Wake consumers after producers commit new jobs."""

        if lane is not None:
            if lane not in self._wake_events:
                raise ValueError(f"unsupported lane: {lane}")
            self._wake_events[lane].set()
            return
        for event in self._wake_events.values():
            event.set()

    async def __aenter__(self) -> DurableJobExecutor:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.stop()

    async def _run_lane(self, lane: str) -> None:
        worker_id = f"{self._worker_id}:{lane}"
        while not self._stop_event.is_set():
            try:
                job = await asyncio.to_thread(
                    self._persistence.claim_next_job,
                    worker_id=worker_id,
                    lane=lane,
                    now_ms=self._clock_ms(),
                    lease_ms=self._lease_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.error(
                    "Failed to claim a V2 %s job; error_class=%s",
                    lane,
                    type(exc).__name__,
                )
                await self._wait_for_work(lane)
                continue

            if job is None:
                await self._wait_for_work(lane)
                continue
            try:
                await self._execute_claimed_job(lane, worker_id, job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The lease remains durable and will be recovered if a terminal
                # repository write failed. One transient failure must not kill a lane.
                self._logger.error(
                    "Failed while executing V2 %s job; error_class=%s",
                    lane,
                    type(exc).__name__,
                )

    async def _execute_claimed_job(
        self,
        lane: str,
        worker_id: str,
        job: dict[str, Any],
    ) -> None:
        handler_task = asyncio.create_task(
            self._invoke_handler(self._handlers[lane], job),
            name=f"v2-handler-{lane}-{job['id']}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_job(job["id"], worker_id),
            name=f"v2-heartbeat-{lane}-{job['id']}",
        )

        try:
            done, _ = await asyncio.wait(
                (handler_task, heartbeat_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
                if heartbeat_error is not None:
                    self._logger.error(
                        "V2 %s job %s stopped after heartbeat failure: %s",
                        lane,
                        job["id"],
                        heartbeat_error,
                    )
                return

            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            try:
                output = handler_task.result()
            except Exception as exc:
                await self._retry_after_handler_failure(job, worker_id, exc)
                return

            completed = await asyncio.to_thread(
                self._persistence.complete_job,
                job_id=job["id"],
                worker_id=worker_id,
                now_ms=self._clock_ms(),
                output=output,
            )
            if completed is None:
                self._logger.warning("Lost lease before completing V2 %s job %s", lane, job["id"])
        except asyncio.CancelledError:
            handler_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(handler_task, heartbeat_task, return_exceptions=True)
            raise

    async def _heartbeat_job(self, job_id: str, worker_id: str) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval_s)
            renewed = await asyncio.to_thread(
                self._persistence.heartbeat_job,
                job_id=job_id,
                worker_id=worker_id,
                now_ms=self._clock_ms(),
                lease_ms=self._lease_ms,
            )
            if not renewed:
                raise RuntimeError("job lease was lost")

    async def _retry_after_handler_failure(
        self,
        job: dict[str, Any],
        worker_id: str,
        error: Exception,
    ) -> None:
        now_ms = self._clock_ms()
        attempt = max(1, int(job.get("attempts") or 1))
        delay_ms = min(
            self._retry_max_ms,
            self._retry_initial_ms * (2 ** (attempt - 1)),
        )
        requested_delay_ms = getattr(error, "retry_after_ms", None)
        if requested_delay_ms is not None:
            delay_ms = min(
                self._retry_max_ms,
                max(0, int(requested_delay_ms)),
            )
        error_class = type(error).__name__ or "handler_error"
        if getattr(error, "retryable", True) is False:
            retried = await asyncio.to_thread(
                self._persistence.fail_job,
                job_id=job["id"],
                worker_id=worker_id,
                now_ms=now_ms,
                error_class=error_class,
            )
        else:
            retried = await asyncio.to_thread(
                self._persistence.retry_job,
                job_id=job["id"],
                worker_id=worker_id,
                now_ms=now_ms,
                next_attempt_at_ms=now_ms + delay_ms,
                error_class=error_class,
            )
        if retried is None:
            self._logger.warning("Lost lease before retrying V2 job %s", job["id"])
            return
        self._logger.warning(
            "V2 %s job %s failed on attempt %s with %s; status=%s",
            job["kind"],
            job["id"],
            attempt,
            error_class,
            retried["status"],
        )

    async def _invoke_handler(self, handler: JobHandler, job: dict[str, Any]) -> JobOutput:
        if inspect.iscoroutinefunction(handler):
            return await handler(dict(job))
        result = await asyncio.to_thread(handler, dict(job))
        if inspect.isawaitable(result):
            return await result
        return result

    async def _wait_for_work(self, lane: str) -> None:
        event = self._wake_events[lane]
        event.clear()
        if self._stop_event.is_set():
            return
        try:
            async with asyncio.timeout(self._poll_interval_s):
                await event.wait()
        except TimeoutError:
            pass

    def _clock_ms(self) -> int:
        return max(0, int(self._now_ms()))
