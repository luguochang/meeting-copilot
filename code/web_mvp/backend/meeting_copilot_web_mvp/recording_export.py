from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
import logging
import time
from typing import Any, TypeAlias
from uuid import uuid4

from .v2_persistence import V2Persistence


ExportOutput: TypeAlias = Mapping[str, Any]
ExportHandler: TypeAlias = Callable[[dict[str, Any]], ExportOutput | Awaitable[ExportOutput]]
CaptureRecoveryHandler: TypeAlias = Callable[[int], list[str]]


class RecordingExportExecutor:
    """Lease-protected background assembly for sealed recording journals."""

    def __init__(
        self,
        persistence: V2Persistence,
        *,
        export_handler: ExportHandler,
        worker_id: str | None = None,
        lease_ms: int = 30_000,
        heartbeat_interval_ms: int | None = None,
        poll_interval_ms: int = 100,
        retry_initial_ms: int = 500,
        retry_max_ms: int = 5_000,
        shutdown_timeout_s: float = 30.0,
        now_ms: Callable[[], int] | None = None,
        capture_recovery_interval_ms: int = 5_000,
        capture_recovery_handler: CaptureRecoveryHandler | None = None,
    ) -> None:
        if not callable(export_handler):
            raise TypeError("export_handler must be callable")
        if lease_ms < 2:
            raise ValueError("lease_ms must be at least 2")
        resolved_heartbeat_ms = heartbeat_interval_ms or max(1, lease_ms // 3)
        if resolved_heartbeat_ms <= 0 or resolved_heartbeat_ms >= lease_ms:
            raise ValueError("heartbeat_interval_ms must be positive and less than lease_ms")
        if poll_interval_ms <= 0:
            raise ValueError("poll_interval_ms must be positive")
        if retry_initial_ms < 0 or retry_max_ms < retry_initial_ms:
            raise ValueError("recording export retry bounds are invalid")
        if shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be positive")
        if capture_recovery_interval_ms <= 0:
            raise ValueError("capture_recovery_interval_ms must be positive")

        self._persistence = persistence
        self._export_handler = export_handler
        self._worker_id = str(worker_id or f"recording-export-{uuid4().hex}").strip()
        self._lease_ms = int(lease_ms)
        self._heartbeat_interval_s = resolved_heartbeat_ms / 1_000
        self._poll_interval_s = poll_interval_ms / 1_000
        self._retry_initial_ms = int(retry_initial_ms)
        self._retry_max_ms = int(retry_max_ms)
        self._shutdown_timeout_s = float(shutdown_timeout_s)
        self._now_ms = now_ms or (lambda: int(time.time() * 1_000))
        self._capture_recovery_interval_ms = int(capture_recovery_interval_ms)
        self._capture_recovery_handler = capture_recovery_handler or (
            lambda now: self._persistence.recover_expired_recording_leases(now_ms=now)
        )
        self._next_capture_recovery_ms = 0
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._logger = logging.getLogger(__name__)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._task is not None:
                if self.running:
                    return
                raise RuntimeError("recording export executor failed; stop it before restart")
            self._stop_event.clear()
            self._wake_event.clear()
            self._next_capture_recovery_ms = 0
            self._loop = asyncio.get_running_loop()
            recovered = await asyncio.to_thread(
                self._persistence.recover_expired_recording_export_leases,
                now_ms=self._clock_ms(),
            )
            if recovered:
                self._logger.info("Recovered %s expired recording export lease(s)", recovered)
            self._task = asyncio.create_task(self._run(), name="recording-export-worker")

    async def stop(self, *, timeout_s: float | None = None) -> None:
        async with self._lifecycle_lock:
            if self._task is None:
                return
            self._stop_event.set()
            self.wake()
            timeout = self._shutdown_timeout_s if timeout_s is None else float(timeout_s)
            if timeout <= 0:
                raise ValueError("timeout_s must be positive")
            try:
                async with asyncio.timeout(timeout):
                    await self._task
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._logger.warning("Forced recording export shutdown after %.3fs", timeout)
            finally:
                self._task = None
                self._loop = None

    def wake(self) -> None:
        # Seal callbacks can run on the per-session blocking worker. Mutating
        # asyncio.Event from that thread is unsafe; marshal the wake-up onto
        # the executor's event loop instead.
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._wake_event.set)

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                now_ms = self._clock_ms()
                if now_ms >= self._next_capture_recovery_ms:
                    recovered_recordings = await asyncio.to_thread(
                        self._capture_recovery_handler,
                        now_ms,
                    )
                    self._next_capture_recovery_ms = now_ms + self._capture_recovery_interval_ms
                    if recovered_recordings:
                        self._logger.warning(
                            "Recovered %s expired recording capture lease(s)",
                            len(recovered_recordings),
                        )
                job = await asyncio.to_thread(
                    self._persistence.claim_next_recording_export,
                    worker_id=self._worker_id,
                    now_ms=now_ms,
                    lease_ms=self._lease_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.error(
                    "Failed to claim a recording export; error_class=%s",
                    type(exc).__name__,
                )
                await self._wait_for_work()
                continue
            if job is None:
                await self._wait_for_work()
                continue
            try:
                await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.error(
                    "Failed while executing recording export; error_class=%s",
                    type(exc).__name__,
                )

    async def _execute(self, job: dict[str, Any]) -> None:
        handler_task = asyncio.create_task(
            self._invoke_handler(job),
            name=f"recording-export-handler-{job['id']}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(job["id"]),
            name=f"recording-export-heartbeat-{job['id']}",
        )
        try:
            done, _ = await asyncio.wait(
                (handler_task, heartbeat_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
                return
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            try:
                output = handler_task.result()
            except Exception as exc:
                await self._retry(job, exc)
                return
            completed = await asyncio.to_thread(
                self._persistence.complete_recording_export,
                export_id=job["id"],
                worker_id=self._worker_id,
                output=output,
                now_ms=self._clock_ms(),
            )
            if completed is None:
                self._logger.warning("Lost lease before completing recording export %s", job["id"])
        except asyncio.CancelledError:
            handler_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(handler_task, heartbeat_task, return_exceptions=True)
            raise

    async def _heartbeat(self, export_id: str) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval_s)
            renewed = await asyncio.to_thread(
                self._persistence.heartbeat_recording_export,
                export_id=export_id,
                worker_id=self._worker_id,
                now_ms=self._clock_ms(),
                lease_ms=self._lease_ms,
            )
            if not renewed:
                raise RuntimeError("recording export lease was lost")

    async def _retry(self, job: dict[str, Any], error: Exception) -> None:
        now_ms = self._clock_ms()
        attempt = max(1, int(job.get("attempts") or 1))
        delay_ms = min(self._retry_max_ms, self._retry_initial_ms * (2 ** (attempt - 1)))
        retried = await asyncio.to_thread(
            self._persistence.retry_recording_export,
            export_id=job["id"],
            worker_id=self._worker_id,
            error_class=type(error).__name__ or "recording_export_error",
            next_attempt_at_ms=now_ms + delay_ms,
            now_ms=now_ms,
        )
        if retried is None:
            self._logger.warning("Lost lease before retrying recording export %s", job["id"])
        else:
            self._logger.warning(
                "Recording export %s failed on attempt %s; status=%s",
                job["id"],
                attempt,
                retried["status"],
            )

    async def _invoke_handler(self, job: dict[str, Any]) -> ExportOutput:
        if inspect.iscoroutinefunction(self._export_handler):
            return await self._export_handler(dict(job))
        result = await asyncio.to_thread(self._export_handler, dict(job))
        if inspect.isawaitable(result):
            return await result
        return result

    async def _wait_for_work(self) -> None:
        self._wake_event.clear()
        if self._stop_event.is_set():
            return
        try:
            async with asyncio.timeout(self._poll_interval_s):
                await self._wake_event.wait()
        except TimeoutError:
            pass

    def _clock_ms(self) -> int:
        return max(0, int(self._now_ms()))
