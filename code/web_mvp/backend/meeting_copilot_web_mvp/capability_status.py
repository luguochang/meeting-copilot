"""Read-only, allowlist-only runtime and Provider observations for settings UI.

Package installation, configured files and a running worker are separate facts.
These helpers never warm a model, hash model files or make Provider requests.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

from meeting_copilot_web_mvp import asr_refiner, asr_stream, batch_transcribe, llm_service


_ASR_ERROR_CODES = frozenset({
    "asr_capacity_unavailable", "asr_resource_probe_failed",
    "asr_resource_guard_triggered", "asr_cold_start_busy",
    "worker_out_of_memory", "worker_dependency_load_failed",
    "worker_dependency_unavailable", "worker_start_timeout", "worker_spawn_failed",
    "worker_exited_before_ready", "worker_exited", "worker_write_failed", "worker_timeout",
})


def _read(reader: Callable[[], Any]) -> Any:
    try:
        return reader()
    except Exception:
        # A diagnostic failure must not reflect exception text or secrets.
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _observed_failure(worker: Mapping[str, Any]) -> tuple[str, str] | None:
    resource = _mapping(worker.get("resource"))
    codes = (worker.get("last_error_code"), resource.get("last_error_code"))
    code = next((value for value in codes if isinstance(value, str) and value in _ASR_ERROR_CODES), None)
    if code == "asr_resource_probe_failed":
        return "unknown", code
    if code is not None:
        return "failed", code
    if worker.get("recovery_required") is True or any(codes):
        # Unknown diagnostic values may contain private exception text or paths.
        return "failed", "worker_reported_failure"
    return None


def _worker_state(available: bool | None, worker: Any, *, resident: bool = True) -> tuple[str, str | None]:
    if available is None or not isinstance(worker, Mapping):
        return "unknown", "runtime_status_check_failed"
    if not available:
        return "unavailable", "runtime_components_unavailable"
    # Pool status can retain a failed old slot while another worker is ready.
    if worker.get("process_ready") is True and worker.get("recovery_required") is not True:
        return "ready", None
    failure = _observed_failure(worker)
    if failure is not None:
        return failure
    if worker.get("process_running") is True:
        return "starting", "worker_loading"
    exit_code = worker.get("last_exit_code")
    if worker.get("last_error") or (type(exit_code) is int and exit_code != 0) or worker.get("last_stop_reason") in {
        "worker_start_timeout", "worker_write_failed", "worker_timeout", "worker_exit",
    }:
        return "failed", "worker_reported_failure"
    return ("idle", "worker_starts_on_demand") if resident else ("available", None)


def local_runtime_status(package: Mapping[str, Any], environ: Mapping[str, str]) -> dict[str, Any]:
    deployment = (
        "desktop" if environ.get("MEETING_COPILOT_DESKTOP_RUNTIME") == "1"
        else "packaged" if environ.get("MEETING_COPILOT_RUNTIME_MANIFEST")
        else "source"
    )
    source = "runtime_manifest" if environ.get("MEETING_COPILOT_RUNTIME_MANIFEST") else "source_configuration"
    available = _read(asr_stream.funasr_realtime_available)
    available = available if type(available) is bool else None
    resident = _read(asr_stream._funasr_resident_enabled)
    realtime_worker = _read(asr_stream.funasr_resident_status)
    realtime_state, realtime_reason = _worker_state(available, realtime_worker, resident=resident is not False)
    file_capability = _read(batch_transcribe.capability_status)
    file_data = _mapping(file_capability)
    file_available = file_data.get("available") if type(file_data.get("available")) is bool else None
    file_state = "unknown" if file_available is None else "available" if file_available else "unavailable"
    file_reason = None if file_available else "runtime_components_unavailable" if file_available is False else "runtime_status_check_failed"
    file_runtime_reader = getattr(batch_transcribe, "runtime_status", None)
    if callable(file_runtime_reader):
        # This reader returns the last attempt's cached observation; GET never
        # performs capacity admission or starts a file transcription task.
        file_runtime = _read(file_runtime_reader)
        if file_available is True:
            if not isinstance(file_runtime, Mapping):
                file_state, file_reason = "unknown", "runtime_status_check_failed"
            elif failure := _observed_failure(file_runtime):
                file_state, file_reason = failure
    refinement = _read(asr_refiner.refinement_capability)
    refinement_data = _mapping(refinement)
    refinement_available = None if refinement is None else refinement_data.get("status") == "ready"
    refinement_worker = _read(asr_refiner.refiner_worker_status)
    refinement_state, refinement_reason = _worker_state(refinement_available, refinement_worker)
    policy = _mapping(refinement_data.get("realtime_policy"))
    if refinement_available and policy.get("realtime_refinement_enabled") is False and refinement_state not in {"failed", "unknown"}:
        refinement_state, refinement_reason = "disabled", "disabled_by_resource_policy"
    formats = {
        name: details.get("available") is True
        for name, details in _mapping(file_data.get("formats", file_data.get("format_capabilities"))).items()
        if name in {extension.lstrip(".") for extension in batch_transcribe._SUPPORTED_IMPORT_EXTENSIONS}
        and isinstance(details, Mapping)
    }

    def capability(value: bool | None, state: str, reason: str | None, worker: Any = None) -> dict[str, Any]:
        observed_worker = _mapping(worker)
        return {
            "available": value,
            "state": state,
            "source": source,
            "reason": reason,
            "process_ready": observed_worker.get("process_ready") if type(observed_worker.get("process_ready")) is bool else None,
            "process_running": observed_worker.get("process_running") if type(observed_worker.get("process_running")) is bool else None,
        }

    return {
        **package,
        "deployment_mode": deployment,
        "runtime_capabilities": {
            "realtime_asr": capability(available, realtime_state, realtime_reason, realtime_worker),
            "file_asr": {
                **capability(file_available, file_state, file_reason),
                "formats": formats,
            },
            "offline_refinement": capability(refinement_available, refinement_state, refinement_reason, refinement_worker),
        },
    }


def _recent_results(snapshots: Sequence[Mapping[str, Any]], now_ns: int) -> dict[str, dict[str, Any]]:
    recent: dict[str, tuple[int, dict[str, Any]]] = {}
    outcomes = {"success", "timeout", "rate_limit", "provider_5xx", "transport_error", "cancelled", "validation_error", "failed"}
    for snapshot in snapshots:
        lane = snapshot.get("lane")
        execution = _mapping(snapshot.get("execution"))
        for attempt in execution.get("provider_attempts") or []:
            if not isinstance(attempt, Mapping):
                continue
            branch = attempt.get("branch")
            target = (
                "answer" if lane == "answer"
                else "correction" if lane == "correction"
                else "pi_deep" if lane == "pi_deep" and attempt.get("runtime") == "pi"
                else "pi_realtime" if attempt.get("runtime") == "pi"
                else "direct_semantic" if branch in {"semantic", "direct_semantic"}
                else None
            )
            stamp = attempt.get("completed_at_monotonic_ns")
            outcome = attempt.get("outcome")
            if target is None or type(stamp) is not int or not 0 <= stamp <= now_ns or outcome not in outcomes:
                continue
            status = attempt.get("http_status")
            observed = {
                "outcome": outcome,
                "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                "age_ms": (now_ns - stamp) // 1_000_000,
                "scope": "process_history",
                # Traces do not bind the effective Provider configuration.
                "configuration_match": "unknown",
            }
            if target not in recent or stamp > recent[target][0]:
                recent[target] = (stamp, observed)
    return {lane: value[1] for lane, value in recent.items()}


def provider_lane_status(
    config: llm_service.LlmConfig | None,
    runtime_status: Mapping[str, Any],
    circuits: Mapping[str, Mapping[str, Any]],
    registry: Any,
    *,
    pi_enabled: bool,
    correction_enabled: bool,
    snapshots: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    metadata = llm_service.provider_metadata(config)
    recent = _recent_results(snapshots, time.monotonic_ns())
    lanes: dict[str, Any] = {}
    for lane in ("answer", "pi_realtime", "pi_deep", "direct_semantic", "correction"):
        is_correction = lane == "correction"
        circuit = circuits.get(lane)
        group = "correction" if is_correction else "deep" if lane == "pi_deep" else "realtime" if lane in {"pi_realtime", "direct_semantic"} else None
        enabled = correction_enabled if is_correction else pi_enabled if lane.startswith("pi_") else True
        lanes[lane] = {
            "configured": config is not None,
            "enabled": enabled,
            "model": config.correction_model if config is not None and is_correction else config.realtime_model if config is not None else None,
            "model_source": metadata.get("correction_model_source" if is_correction else "realtime_model_source"),
            "transport": "pi_tool_call" if lane.startswith("pi_") else "json" if is_correction else "streaming_json",
            "circuit_status": "not_applicable" if circuit is None else str(circuit.get("state") or "unknown"),
            "circuit": dict(circuit) if circuit is not None else None,
            "admission_group": group,
            "active_count": getattr(registry, f"active_{group}_count", None) if group else None,
            "pending_count": getattr(registry, f"pending_{group}_count", None) if group else None,
            "max_active": getattr(registry, f"{group}_max_active", None) if group else None,
            # A tiny Pi tool probe is evidence only for that protocol/model.
            "probe_scope": "realtime_model_tool_call" if lane == "pi_realtime" else "not_probed",
            "probe_status": runtime_status.get("probe_status", "not_run") if lane == "pi_realtime" else "not_run",
            "last_result": recent.get(lane),
        }
    return lanes
