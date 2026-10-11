"""Two lazy Pi channels with bounded foreground admission and cancellation."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping

from .pi_coach_runtime import PiCoachRuntimeError, PiCoachSidecar


class PiCoachRuntimePool:
    """Keep realtime free while serializing one bounded analysis request.

    Provider and durable-job admission remain the host's responsibility. This
    pool prevents shared process locks and supplies a second preemption barrier
    for callers already admitted to the analysis lane.
    """

    def __init__(self, *, sidecar_factory: Any = PiCoachSidecar, **sidecar_options: Any) -> None:
        self._factory = sidecar_factory
        self._options = dict(sidecar_options)
        self._delegate = self._new_sidecar()
        self._analysis = self._new_sidecar(analysis=True)
        self._analysis_lock = asyncio.Lock()
        self._analysis_operation: asyncio.Task | None = None
        self._analysis_manual = False
        self._manual_waiters = 0

    def _new_sidecar(self, *, analysis: bool = False):
        options = dict(self._options)
        if analysis:
            # The existing sidecar default (12s) only covers realtime. Deep
            # requests already have a 25s provider / 30s durable job ceiling.
            options.setdefault("timeout_seconds", 30.0)
        try:
            return self._factory(**options)
        except TypeError:
            if self._factory is PiCoachSidecar:
                raise
            return self._factory()

    @property
    def available(self) -> bool:
        return bool(self._delegate.available and self._analysis.available)

    def prewarm(self) -> dict[str, Any]:
        # Only the interactive bridge is prewarmed; neither contacts a model.
        return {**self._delegate.prewarm(), "sidecar_lane": "realtime"}

    async def evaluate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        context = payload.get("context") or {}
        analysis = (context.get("priority_mode") == "deep"
                    or context.get("coach_profile") == "participation"
                    or context.get("task_mode", "realtime_coach") != "realtime_coach")
        if not analysis:
            result = await self._delegate.evaluate(payload)
            return {**result, "metrics": {**(result.get("metrics") or {}), "sidecar_lane": "realtime"}}
        manual = context.get("trigger_type") == "user_request"
        queued_at = time.perf_counter()
        if manual:
            self._manual_waiters += 1
            operation = self._analysis_operation
            if operation is not None and not self._analysis_manual and not operation.done():
                operation.cancel()
        elif self._manual_waiters:
            raise PiCoachRuntimeError("Automatic analysis deferred for a user request", code="pi_background_deferred")
        try:
            # Never spend the entire 30s analysis deadline waiting for a channel.
            await asyncio.wait_for(self._analysis_lock.acquire(), timeout=1.0)
        except TimeoutError as exc:
            raise PiCoachRuntimeError("Analysis channel is busy; retry the contribution", code="pi_busy") from exc
        finally:
            if manual:
                self._manual_waiters -= 1
        sidecar = self._analysis
        admission_ms = max(0.0, (time.perf_counter() - queued_at) * 1_000)
        operation = asyncio.create_task(sidecar.evaluate(payload))
        self._analysis_operation = operation
        self._analysis_manual = manual
        try:
            result = await operation
            return {**result, "metrics": {**(result.get("metrics") or {}),
                "sidecar_lane": "analysis", "analysis_admission_ms": round(admission_ms, 3)}}
        except asyncio.CancelledError:
            if isinstance(sidecar, PiCoachSidecar):
                # to_thread cancellation must finish releasing its process lock
                # before the next request may clear that instance's signal.
                idle = await asyncio.to_thread(sidecar.wait_until_idle, 0.75)
                if not idle:
                    self._analysis = self._new_sidecar(analysis=True)
                    # The replaced instance has already received cancellation.
                    # Its cleanup is bounded by the existing process-stop logic.
                    asyncio.create_task(asyncio.to_thread(sidecar.close))
            if asyncio.current_task().cancelling():
                raise
            raise PiCoachRuntimeError("Automatic analysis gave way to a user request", code="pi_preempted") from None
        finally:
            self._analysis_operation = None
            self._analysis_manual = False
            self._analysis_lock.release()

    def close(self) -> None:
        self._delegate.close()
        self._analysis.close()
