"""Low-priority host admission. Realtime always proceeds; background awaits local cancel ACK."""
from __future__ import annotations

import asyncio
import math
from typing import Any, Callable
from fastapi import HTTPException

from . import llm_service
from .logging_config import get_logger
from .pi_coach_runtime import PiCoachRuntimeError

_log = get_logger(__name__)
_FAILURE_CODES = frozenset({
    "agent_deadline_exceeded", "pi_timeout", "pi_cancelled", "pi_unavailable",
    "pi_transport_error", "pi_protocol_error", "pi_spawn_failed", "pi_startup_timeout",
    "agent_provider_error", "missing_terminal_action", "invalid_agent_action",
    "invalid_provider", "pi_runtime_error", "pi_stop_unconfirmed",
})
_TOOL_NAMES = frozenset({"propose_memory_update", "submit_preparation_brief",
    "read_project_state", "search_project_documents", "propose_project_changes"})


def safe_background_failure(error: PiCoachRuntimeError) -> tuple[str, dict[str, Any]]:
    """Persist static diagnostics only, never provider text or arbitrary metadata."""
    code = error.code if isinstance(error.code, str) and error.code in _FAILURE_CODES else "pi_runtime_error"
    raw, metrics = error.metrics, {}
    for key in ("elapsed_ms", "decision_latency_ms", "ttft_ms", "turns", "tool_calls",
                "decision_timeout_ms", "provider_status_code", "provider_retry_after_ms",
                "sidecar_queue_ms", "bridge_startup_ms", "bridge_round_trip_ms",
                "response_validation_ms"):
        value = raw.get(key)
        if value is None and key == "ttft_ms":
            metrics[key] = None
        elif type(value) in {int, float} and 0 <= value <= 1_000_000_000 and math.isfinite(value):
            metrics[key] = value
    if type(raw.get("bridge_process_reused")) is bool:
        metrics["bridge_process_reused"] = raw["bridge_process_reused"]
    if isinstance(raw.get("prompt_profile"), str) and raw["prompt_profile"] in {"memory_reconcile", "meeting_preparation", "project_reconcile"}:
        metrics["prompt_profile"] = raw["prompt_profile"]
    if isinstance(raw.get("tool_names"), list):
        metrics["tool_names"] = [name for name in raw["tool_names"][:6]
                                  if isinstance(name, str) and name in _TOOL_NAMES]
    usage = raw.get("usage")
    if isinstance(usage, dict):
        reported = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if type(usage.get(key)) is int and 0 <= usage[key] <= 1_000_000_000}
        # SDK's empty-message aggregate also contains all-zero fields. It is
        # not a provider's assertion that an aborted request was free.
        if reported and any(reported.values()):
            metrics["usage"] = reported
    metrics["usage_known"] = "usage" in metrics
    timings = raw.get("timings")
    if isinstance(timings, dict) and timings.get("clock") == "unix_epoch_ms":
        metrics["timings"] = {"clock": "unix_epoch_ms", **{
            key: timings[key] for key in ("started_at_ms", "first_token_at_ms", "completed_at_ms")
            if type(timings.get(key)) is int and 0 <= timings[key] <= 10_000_000_000_000}}
    return code, metrics


def log_background_failure(error: PiCoachRuntimeError, *, job: dict[str, Any], purpose: str) -> str:
    code, metrics = safe_background_failure(error)
    _log.warning("background.pi.failed", job_id=job["id"], project_id=job["project_id"],
                 purpose=purpose, error_code=code, metrics=metrics)
    return code


async def execute_reconcile(callback: Callable, payload: dict[str, Any], job: dict[str, Any], *,
                            lanes, provider_lanes, enforce_budget: Callable, record_usage: Callable, validate_config: Callable,
                            purpose: str = "project_reconcile") -> dict[str, Any]:
    from .project_reconcile_jobs import BackgroundReconcileDeferred

    def busy() -> bool:
        return provider_lanes.has_priority_work or lanes.any_active({"answer", "ask", "correction", "suggestion", "minutes", "approach"})

    if busy():
        raise BackgroundReconcileDeferred(retry_after_ms=2000)
    config = llm_service.LlmConfig.from_env()
    if config is None:
        raise BackgroundReconcileDeferred(retry_after_ms=5000, code="provider_not_configured")
    validate_config(config)
    try:
        enforce_budget(job["meeting_id"], purpose=purpose, config=config)
    except HTTPException as exc:
        if exc.status_code in {402, 429, 503}:
            raise BackgroundReconcileDeferred(retry_after_ms=5000, code="budget_or_runtime_deferred") from None
        raise
    lease = provider_lanes.try_acquire_reconcile()
    if lease is None:
        raise BackgroundReconcileDeferred(retry_after_ms=2000)
    task = None
    try:
        if busy():
            raise BackgroundReconcileDeferred(retry_after_ms=2000)
        task = asyncio.create_task(callback(payload, job, config))
        while not task.done():
            await asyncio.wait({task}, timeout=0.05)
            if not task.done() and busy():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise BackgroundReconcileDeferred(retry_after_ms=2000, code="preempted_by_realtime")
        result = await task
        metrics = result.get("metrics") or {}
        usage = result.get("usage") or metrics.get("usage")
        if isinstance(usage, dict):
            record_usage(job["meeting_id"], purpose=purpose, config=config, usage=usage)
        return result
    except PiCoachRuntimeError as exc:
        _, metrics = safe_background_failure(exc)
        usage = metrics.get("usage")
        if usage and any(usage.values()):
            try:
                record_usage(job["meeting_id"], purpose=purpose, config=config, usage=usage)
            except Exception:
                # Preserve the original runtime classification even if local accounting fails.
                _log.error("background.usage_record.failed", purpose=purpose,
                           account_id=job["meeting_id"], error_code="usage_record_failed")
        raise
    finally:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        lease.release()
