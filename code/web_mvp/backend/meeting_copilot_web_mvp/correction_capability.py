"""Passive, meeting-scoped correction preparation; never starts an ASR worker."""

from __future__ import annotations

from typing import Any

from . import asr_refiner, asr_resource_guard, llm_service
from .meeting_preparation import normalize_correction_mode


def correction_capability(*, correction_mode: str, llm_enabled: bool) -> dict[str, Any]:
    requested = normalize_correction_mode(correction_mode)
    worker = asr_refiner.refiner_worker_status()
    recovery_required = bool(worker.get("recovery_required"))
    worker_ready = not recovery_required and all(bool(worker.get(key)) for key in ("process_running", "process_ready"))
    profile = (asr_resource_guard.WARM_INFERENCE_PROFILE if worker_ready
               else asr_resource_guard.COLD_LOAD_PROFILE)
    snapshot = None
    memory_reason = None
    try:
        snapshot = asr_resource_guard.read_memory_snapshot()
        capacity = asr_resource_guard.check_capacity(profile, reader=lambda: snapshot).as_dict()
    except asr_resource_guard.AsrResourceError as error:
        memory_reason = error.code
        capacity = getattr(error, "capacity_check", None) or {
            "checked": False, "profile": profile.name,
            "commit_available_bytes": None, "physical_available_bytes": None,
            "required_commit_bytes": profile.commit_budget_bytes + profile.reserve_commit_bytes,
            "required_physical_bytes": profile.physical_budget_bytes + profile.reserve_physical_bytes,
            "sampled_at_ms": None,
        }
    memory = {key: capacity.get(key) for key in (
        "checked", "profile", "commit_available_bytes", "physical_available_bytes",
        "required_commit_bytes", "required_physical_bytes", "sampled_at_ms",
    )}
    memory["total_physical_bytes"] = getattr(snapshot, "total_physical_bytes", None)
    # Unsupported platforms have no native snapshot. That is unknown, not a
    # claim that the optional offline model passed this capability check.
    if not memory["checked"] and memory_reason is None:
        memory_reason = "asr_resource_probe_failed"
    capability = asr_refiner.refinement_capability()
    policy = asr_refiner.realtime_refiner_policy()
    reason = (
        "offline_refinement_components_missing" if capability.get("status") != "ready"
        else asr_refiner.ONLINE_ONLY_REFINEMENT_REASON if not policy["realtime_refinement_enabled"]
        else memory_reason
    )
    local_available = reason is None
    # Only a positively identified capacity refusal/policy bypass causes an
    # automatic fallback. Probe/dependency failures must remain explicit.
    fallback = reason if requested == "local_and_llm" and reason in {
        "asr_capacity_unavailable", asr_refiner.ONLINE_ONLY_REFINEMENT_REASON,
    } else None
    effective = "llm_only" if requested == "llm_only" or fallback else requested
    config = llm_service.LlmConfig.from_env()
    correction_config = llm_service.correction_config(config) if config is not None else None
    return {
        "schema_version": "meeting_correction_capability.v1",
        "requested_mode": requested,
        "effective_mode": effective,
        "recommended_mode": "local_and_llm" if local_available else "llm_only",
        "fallback_reason": fallback,
        "local_refinement": {
            # available is preparation admission, not a worker-ready claim.
            "available": local_available, "worker_ready": worker_ready, "reason": reason,
            "recovery_required": recovery_required, "last_error_code": worker.get("last_error_code"),
        },
        "memory": memory,
        "llm_correction": {"enabled": bool(llm_enabled), "configured": correction_config is not None},
    }
