from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
import logging
import time
from typing import Any, TypeAlias
from uuid import uuid4

from .pipeline_trace import classify_pipeline_failure
from .v2_persistence import IntelligenceDeadlineExceeded, V2Persistence


JobOutput: TypeAlias = Mapping[str, Any] | list[Any] | str | int | float | bool | None
JobHandler: TypeAlias = Callable[[dict[str, Any]], JobOutput | Awaitable[JobOutput]]
JobTelemetryObserver: TypeAlias = Callable[[str], Any]
JobLifecycleObserver: TypeAlias = Callable[[Mapping[str, Any]], Any]
_PUBLIC_INTELLIGENCE_VALIDATION_CATEGORIES = frozenset(
    {"structural", "truncated", "evidence", "stale", "semantic_safety"}
)
# The intelligence handler needs a small post-deadline grace window to persist
# an explicit timeout audit.  Its persistence layer still rejects every late
# content-bearing response; this grace is only for the empty timed-out
# envelope, so a stalled Provider cannot turn into a late card.
INTELLIGENCE_TIMEOUT_PROJECTION_GRACE_MS = 2_000


def _handler_error_class(error: Exception) -> str:
    durable_error_class = str(
        getattr(error, "durable_error_class", "") or ""
    ).strip()
    if durable_error_class:
        return durable_error_class
    error_class = type(error).__name__ or "handler_error"
    category = str(getattr(error, "category", "") or "").strip()
    if (
        error_class == "IntelligenceResponseValidationError"
        and category in _PUBLIC_INTELLIGENCE_VALIDATION_CATEGORIES
    ):
        return f"intelligence_validation_{category}"
    return error_class


class DueCoachRefreshScheduler:
    """Periodically ask the host to enqueue due coach refresh work.

    This component deliberately owns no queue and never calls a Provider. The
    callback is responsible for inspecting durable evidence and enqueueing via
    ``V2Persistence``; the existing intelligence lane remains the only worker.
    """

    def __init__(
        self,
        refresh_callback: Callable[[], Awaitable[Any] | Any],
        *,
        interval_ms: int = 1_000,
        logger: logging.Logger | None = None,
    ) -> None:
        interval_ms = int(interval_ms)
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        if not callable(refresh_callback):
            raise TypeError("refresh_callback must be callable")
        self._refresh_callback = refresh_callback
        self._interval_s = interval_ms / 1_000
        self._logger = logger or logging.getLogger(__name__)
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._wake_event.set()
        self._task = asyncio.create_task(self._run(), name="v2-coach-due-refresh")

    def wake(self) -> None:
        self._wake_event.set()

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        self._stop_event.set()
        self._wake_event.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            # Consume a pending wake before the scan. A wake that arrives
            # during the callback must remain set for the next iteration.
            self._wake_event.clear()
            try:
                result = self._refresh_callback()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # scheduler must not kill the executor
                self._logger.error(
                    "v2.coach_due_refresh.failed",
                    extra={"error_class": type(exc).__name__},
                )
            try:
                async with asyncio.timeout(self._interval_s):
                    await self._wake_event.wait()
            except TimeoutError:
                pass


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
        retry_observer: JobTelemetryObserver | None = None,
        cancellation_observer: JobTelemetryObserver | None = None,
        lifecycle_observer: JobLifecycleObserver | None = None,
        include_lifecycle_metadata: bool = False,
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
        if not isinstance(include_lifecycle_metadata, bool):
            raise TypeError("include_lifecycle_metadata must be a boolean")

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
        self._retry_observer = retry_observer
        self._cancellation_observer = cancellation_observer
        self._lifecycle_observer = lifecycle_observer
        self._include_lifecycle_metadata = include_lifecycle_metadata

        self._stop_event = asyncio.Event()
        self._wake_events = {lane: asyncio.Event() for lane in self._lanes}
        self._supersede_events = {lane: asyncio.Event() for lane in self._lanes}
        self._paused_lanes: set[str] = set()
        self._resume_generations = {lane: 0 for lane in self._lanes}
        self._active_jobs: dict[str, dict[str, Any]] = {}
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
            self._paused_lanes.clear()
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

        A forced cancellation records a bounded cancellation terminal while the
        lease is still owned. If the lease has already been lost, the durable
        row is left for normal recovery and the lifecycle observer receives an
        attempt failure instead of a fabricated terminal acknowledgement.
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
            active_jobs_on_stop = tuple(
                (lane, dict(job)) for lane, job in self._active_jobs.items()
            )

            try:
                async with asyncio.timeout(resolved_timeout):
                    await asyncio.gather(*tasks)
            except TimeoutError:
                for lane, job in active_jobs_on_stop:
                    cancelled = await asyncio.to_thread(
                        self._persistence.cancel_job,
                        job_id=job["id"],
                        worker_id=f"{self._worker_id}:{lane}",
                        now_ms=self._clock_ms(),
                        error_class="executor_shutdown",
                    )
                    if cancelled is None:
                        try:
                            current = await asyncio.to_thread(
                                self._persistence.get_job,
                                str(job["id"]),
                            )
                        except (KeyError, OSError, TypeError, ValueError):
                            current = None
                        if current is None or current.get("status") not in {
                            "succeeded",
                            "failed",
                            "cancelled",
                        }:
                            self._notify_lifecycle(
                                job,
                                event="attempt_failed",
                                durable_status="lease_lost",
                                result_outcome="cancelled",
                                error_class="executor_shutdown",
                            )
                    else:
                        self._notify_telemetry(
                            self._cancellation_observer,
                            str(job["id"]),
                            "cancelled",
                        )
                        self._notify_lifecycle(
                            job,
                            event="terminal",
                            durable_status="cancelled",
                            terminal_outcome="cancelled",
                            result_outcome="cancelled",
                            error_class="executor_shutdown",
                        )
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
            # Explicit user_request/task_due jobs share the durable table but
            # are claimed by the independent deep consumer.
            if lane == "intelligence" and "pi_deep" in self._wake_events:
                self._wake_events["pi_deep"].set()
            return
        for event in self._wake_events.values():
            event.set()

    def resume(self, lane: str | None = None) -> None:
        """Resume lanes paused on an external dependency change."""

        if lane is not None:
            if lane not in self._wake_events:
                raise ValueError(f"unsupported lane: {lane}")
            self._resume_generations[lane] += 1
            self._paused_lanes.discard(lane)
            self._wake_events[lane].set()
            return
        self._paused_lanes.clear()
        for lane, event in self._wake_events.items():
            self._resume_generations[lane] += 1
            event.set()

    def supersede_running(
        self,
        lane: str,
        *,
        meeting_id: str,
        replacement_job_id: str,
    ) -> bool:
        """Cancel obsolete in-flight work when the same meeting has fresher evidence."""

        if lane not in self._supersede_events:
            raise ValueError(f"unsupported lane: {lane}")
        active = self._active_jobs.get(lane)
        if (
            active is None
            or str(active.get("meeting_id") or "") != str(meeting_id)
            or str(active.get("id") or "") == str(replacement_job_id)
        ):
            return False
        self._supersede_events[lane].set()
        self._wake_events[lane].set()
        return True

    async def __aenter__(self) -> DurableJobExecutor:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.stop()

    async def _run_lane(self, lane: str) -> None:
        worker_id = f"{self._worker_id}:{lane}"
        while not self._stop_event.is_set():
            if lane in self._paused_lanes:
                await self._wait_for_work(lane, poll=False)
                continue
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
                self._supersede_events[lane].clear()
                self._active_jobs[lane] = dict(job)
                self._notify_lifecycle(
                    job,
                    event="job_claimed",
                    durable_status="running",
                )
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
            finally:
                self._active_jobs.pop(lane, None)
                self._supersede_events[lane].clear()

    async def _execute_claimed_job(
        self,
        lane: str,
        worker_id: str,
        job: dict[str, Any],
    ) -> None:
        resume_generation = self._resume_generations[lane]
        handler_task = asyncio.create_task(
            self._invoke_handler(self._handlers[lane], job),
            name=f"v2-handler-{lane}-{job['id']}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_job(job["id"], worker_id),
            name=f"v2-heartbeat-{lane}-{job['id']}",
        )
        supersede_task = asyncio.create_task(
            self._supersede_events[lane].wait(),
            name=f"v2-supersede-{lane}-{job['id']}",
        )

        try:
            done, _ = await asyncio.wait(
                (handler_task, heartbeat_task, supersede_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if supersede_task in done and handler_task not in done:
                handler_task.cancel()
                heartbeat_task.cancel()
                await asyncio.gather(handler_task, heartbeat_task, return_exceptions=True)
                cancelled = await asyncio.to_thread(
                    self._persistence.cancel_job,
                    job_id=job["id"],
                    worker_id=worker_id,
                    now_ms=self._clock_ms(),
                    error_class="evidence_superseded",
                )
                if cancelled is None:
                    self._logger.warning(
                        "Lost lease while superseding V2 %s job %s",
                        lane,
                        job["id"],
                    )
                else:
                    self._notify_telemetry(
                        self._cancellation_observer,
                        str(job["id"]),
                        "cancelled",
                    )
                    self._notify_lifecycle(
                        job,
                        event="terminal",
                        durable_status="cancelled",
                        terminal_outcome="superseded",
                        result_outcome="cancelled",
                        error_class="evidence_superseded",
                    )
                    self._logger.info(
                        "Superseded in-flight V2 %s job %s for fresher evidence",
                        lane,
                        job["id"],
                    )
                return
            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                handler_task.cancel()
                supersede_task.cancel()
                await asyncio.gather(handler_task, supersede_task, return_exceptions=True)
                if heartbeat_error is not None:
                    heartbeat_error_class = _handler_error_class(heartbeat_error)
                    cancelled = await asyncio.to_thread(
                        self._persistence.cancel_job,
                        job_id=job["id"],
                        worker_id=worker_id,
                        now_ms=self._clock_ms(),
                        error_class="lease_lost",
                    )
                    if cancelled is None:
                        self._notify_lifecycle(
                            job,
                            event="attempt_failed",
                            durable_status="lease_lost",
                            result_outcome="transport_error",
                            error_class=heartbeat_error_class,
                        )
                    else:
                        self._notify_telemetry(
                            self._cancellation_observer,
                            str(job["id"]),
                            "cancelled",
                        )
                        self._notify_lifecycle(
                            job,
                            event="terminal",
                            durable_status="cancelled",
                            terminal_outcome="failed",
                            result_outcome="transport_error",
                            error_class=heartbeat_error_class,
                        )
                    self._logger.error(
                        "V2 %s job %s stopped after heartbeat failure: %s",
                        lane,
                        job["id"],
                        heartbeat_error,
                    )
                return

            heartbeat_task.cancel()
            supersede_task.cancel()
            await asyncio.gather(heartbeat_task, supersede_task, return_exceptions=True)
            try:
                output = handler_task.result()
            except Exception as exc:
                await self._retry_after_handler_failure(
                    job,
                    worker_id,
                    exc,
                    resume_generation=resume_generation,
                )
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
            else:
                self._notify_lifecycle(
                    job,
                    event="terminal",
                    durable_status="succeeded",
                    terminal_outcome="success",
                    result_outcome="success",
                )
        except asyncio.CancelledError:
            handler_task.cancel()
            heartbeat_task.cancel()
            supersede_task.cancel()
            await asyncio.gather(
                handler_task,
                heartbeat_task,
                supersede_task,
                return_exceptions=True,
            )
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
        *,
        resume_generation: int,
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
        error_class = _handler_error_class(error)
        result_outcome = classify_pipeline_failure(
            error,
            deadline_reached=isinstance(error, IntelligenceDeadlineExceeded),
        )
        if isinstance(error, IntelligenceDeadlineExceeded):
            retried = await asyncio.to_thread(
                self._persistence.cancel_job,
                job_id=job["id"],
                worker_id=worker_id,
                now_ms=now_ms,
                error_class="deadline_exceeded",
            )
        elif getattr(error, "superseded", False):
            retried = await asyncio.to_thread(
                self._persistence.cancel_job,
                job_id=job["id"],
                worker_id=worker_id,
                now_ms=now_ms,
                error_class="evidence_superseded",
            )
        elif getattr(error, "preserve_attempt", False):
            retried = await asyncio.to_thread(
                self._persistence.defer_job,
                job_id=job["id"],
                worker_id=worker_id,
                now_ms=now_ms,
                next_attempt_at_ms=now_ms + delay_ms,
                error_class=error_class,
            )
        elif getattr(error, "retryable", True) is False:
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
        if (
            retried["status"] == "retry_wait"
            and getattr(error, "pause_until_explicit_resume", False)
            and self._resume_generations[str(job["kind"])] == resume_generation
        ):
            self._paused_lanes.add(str(job["kind"]))
        if retried["status"] == "cancelled":
            self._notify_telemetry(self._cancellation_observer, str(job["id"]), "cancelled")
            self._notify_lifecycle(
                job,
                event="terminal",
                durable_status="cancelled",
                terminal_outcome=(
                    "timeout"
                    if isinstance(error, IntelligenceDeadlineExceeded)
                    else "superseded"
                    if getattr(error, "superseded", False)
                    else "cancelled"
                ),
                result_outcome=result_outcome,
                error_class=error_class,
                http_status=(
                    getattr(error, "status_code", None)
                    if type(getattr(error, "status_code", None)) is int
                    else None
                ),
            )
        elif retried["status"] == "retry_wait":
            self._notify_telemetry(self._retry_observer, str(job["id"]), "retry")
            self._notify_lifecycle(
                job,
                event="attempt_failed",
                durable_status="retry_wait",
                result_outcome=result_outcome,
                error_class=error_class,
                http_status=(
                    getattr(error, "status_code", None)
                    if type(getattr(error, "status_code", None)) is int
                    else None
                ),
            )
        elif retried["status"] == "failed":
            self._notify_lifecycle(
                job,
                event="terminal",
                durable_status="failed",
                terminal_outcome="failed",
                result_outcome=result_outcome,
                error_class=error_class,
                http_status=(
                    getattr(error, "status_code", None)
                    if type(getattr(error, "status_code", None)) is int
                    else None
                ),
            )
        log = self._logger.info if (
            getattr(error, "preserve_attempt", False) or getattr(error, "superseded", False)
        ) else self._logger.warning
        if getattr(error, "superseded", False):
            message = "V2 %s job %s superseded on attempt %s with %s; status=%s"
        elif getattr(error, "preserve_attempt", False):
            message = "V2 %s job %s deferred on attempt %s with %s; status=%s"
        else:
            message = "V2 %s job %s failed on attempt %s with %s; status=%s"
        log(
            message,
            job["kind"],
            job["id"],
            attempt,
            error_class,
            retried["status"],
        )

    def _notify_telemetry(
        self,
        observer: JobTelemetryObserver | None,
        job_id: str,
        event: str,
    ) -> None:
        if observer is None:
            return
        try:
            observer(job_id)
        except (KeyError, TypeError, ValueError):
            self._logger.debug("Skipped missing V2 %s telemetry for job %s", event, job_id)

    def _notify_lifecycle(
        self,
        job: Mapping[str, Any],
        *,
        event: str,
        durable_status: str,
        terminal_outcome: str | None = None,
        result_outcome: str | None = None,
        error_class: str | None = None,
        http_status: int | None = None,
        branch: str | None = None,
        runtime: str | None = None,
    ) -> None:
        if self._lifecycle_observer is None:
            return
        payload = {
            "event": str(event),
            "job_id": str(job.get("id") or ""),
            "meeting_id": str(job.get("meeting_id") or ""),
            "lane": str(job.get("kind") or "unknown"),
            "attempt_index": max(1, int(job.get("attempts") or 1)),
            "durable_status": str(durable_status),
            "terminal_outcome": (
                str(terminal_outcome) if terminal_outcome is not None else None
            ),
            "result_outcome": (
                str(result_outcome) if result_outcome is not None else None
            ),
            "error_class": str(error_class) if error_class is not None else None,
        }
        if self._include_lifecycle_metadata:
            # Keep the historical payload stable by opting into the expanded
            # fields only for observers that understand the versioned metadata
            # contract.  Values are bounded scalars; no handler output or
            # Provider response body is copied into lifecycle telemetry.
            payload.update(
                {
                    "lifecycle_at_monotonic_ns": time.monotonic_ns(),
                    "http_status": (
                        int(http_status)
                        if type(http_status) is int and 100 <= http_status <= 599
                        else None
                    ),
                    "branch": str(branch) if branch is not None else None,
                    "runtime": str(runtime) if runtime is not None else None,
                    "job_created_at_ms": job.get("created_at_ms"),
                    "final_committed_at_ms": job.get("final_committed_at_ms"),
                    "deadline_at_ms": job.get("deadline_at_ms"),
                }
            )
        try:
            self._lifecycle_observer(payload)
        except (KeyError, TypeError, ValueError):
            self._logger.debug(
                "Skipped invalid V2 lifecycle telemetry for job %s",
                payload["job_id"],
            )

    async def _invoke_handler(self, handler: JobHandler, job: dict[str, Any]) -> JobOutput:
        async def invoke() -> JobOutput:
            if inspect.iscoroutinefunction(handler):
                return await handler(dict(job))
            result = await asyncio.to_thread(handler, dict(job))
            if inspect.isawaitable(result):
                return await result
            return result

        deadline_at_ms = job.get("deadline_at_ms")
        if deadline_at_ms is None:
            return await invoke()
        remaining_seconds = (int(deadline_at_ms) - self._clock_ms()) / 1_000
        if remaining_seconds <= 0:
            raise IntelligenceDeadlineExceeded("durable job reached its realtime deadline before execution")
        grace_seconds = (
            INTELLIGENCE_TIMEOUT_PROJECTION_GRACE_MS / 1_000
            if str(job.get("kind") or "") == "intelligence"
            else 0.0
        )
        deadline = asyncio.timeout(remaining_seconds + grace_seconds)
        try:
            async with deadline:
                return await invoke()
        except TimeoutError as exc:
            if deadline.expired():
                raise IntelligenceDeadlineExceeded(
                    "durable job handler exceeded its realtime deadline"
                ) from exc
            raise

    async def _wait_for_work(self, lane: str, *, poll: bool = True) -> None:
        event = self._wake_events[lane]
        event.clear()
        if self._stop_event.is_set():
            return
        if not poll:
            await event.wait()
            return
        try:
            async with asyncio.timeout(self._poll_interval_s):
                await event.wait()
        except TimeoutError:
            pass

    def _clock_ms(self) -> int:
        return max(0, int(self._now_ms()))
