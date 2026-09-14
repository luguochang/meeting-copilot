from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from threading import RLock
from typing import Any, Literal, Mapping

from meeting_copilot_web_mvp import llm_service


ProviderProbeStatus = Literal["not_run", "probing", "succeeded", "failed"]
_ProviderIdentity = tuple[int, str, str, str, str, str, str, str]


def _realtime_ready_cutoff_ms() -> int:
    """Resolve the optional managed acceptance cutoff, defaulting to 2.5s."""

    raw = str(os.environ.get("MEETING_COPILOT_REALTIME_READY_CUTOFF_MS") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 2_500
    return value if 1_000 <= value <= 10_000 else 2_500


REALTIME_READY_CUTOFF_MS = _realtime_ready_cutoff_ms()


@dataclass(frozen=True)
class ProviderRuntimeStatus:
    configured: bool
    runtime_synced: bool
    probe_status: ProviderProbeStatus
    model: str | None
    realtime_model: str | None
    realtime_model_source: str
    realtime_model_explicit: bool
    realtime_model_warning: str | None
    correction_model: str | None
    correction_model_source: str
    correction_model_explicit: bool
    correction_model_warning: str | None
    operational: bool | None
    realtime_ready: bool | None
    probe_latency_ms: int | None
    probe_usage: dict[str, int] | None
    realtime_cutoff_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_lock = RLock()
_observed_identity: _ProviderIdentity | None = None
_probe_status: ProviderProbeStatus = "not_run"
_probe_operational: bool | None = None
_probe_realtime_ready: bool | None = None
_probe_latency_ms: int | None = None
_probe_usage: dict[str, int] | None = None


def _clear_probe_outcome() -> None:
    global _probe_operational, _probe_realtime_ready, _probe_latency_ms, _probe_usage
    _probe_operational = None
    _probe_realtime_ready = None
    _probe_latency_ms = None
    _probe_usage = None


def _identity(config: llm_service.LlmConfig) -> _ProviderIdentity:
    return (
        llm_service.runtime_config_generation(),
        config.base_url,
        config.model,
        str(config.realtime_model or config.model),
        str(config.correction_model or config.model),
        str(config.correction_model_source or ""),
        config.api_style,
        llm_service.provider_identifier(config),
    )


def _observe(config: llm_service.LlmConfig | None) -> ProviderProbeStatus:
    global _observed_identity, _probe_status
    identity = _identity(config) if config is not None else None
    if identity != _observed_identity:
        _observed_identity = identity
        _probe_status = "not_run"
        _clear_probe_outcome()
    return _probe_status


def get_status(
    config: llm_service.LlmConfig | None = None,
) -> ProviderRuntimeStatus:
    resolved = config if config is not None else llm_service.LlmConfig.from_env()
    with _lock:
        probe_status = _observe(resolved)
        configured = resolved is not None
        metadata = llm_service.provider_metadata(resolved)
        return ProviderRuntimeStatus(
            configured=configured,
            runtime_synced=configured,
            probe_status=probe_status,
            model=resolved.model if resolved is not None else None,
            realtime_model=(resolved.realtime_model if resolved is not None else None),
            realtime_model_source=str(
                metadata.get("realtime_model_source") or "not_configured"
            ),
            realtime_model_explicit=bool(
                metadata.get("realtime_model_explicit", False)
            ),
            realtime_model_warning=(
                str(metadata["realtime_model_warning"])
                if metadata.get("realtime_model_warning") is not None
                else None
            ),
            correction_model=(resolved.correction_model if resolved is not None else None),
            correction_model_source=str(
                metadata.get("correction_model_source") or "not_configured"
            ),
            correction_model_explicit=bool(
                metadata.get("correction_model_explicit", False)
            ),
            correction_model_warning=(
                str(metadata["correction_model_warning"])
                if metadata.get("correction_model_warning") is not None
                else None
            ),
            operational=_probe_operational,
            realtime_ready=_probe_realtime_ready,
            probe_latency_ms=_probe_latency_ms,
            probe_usage=dict(_probe_usage) if _probe_usage is not None else None,
            realtime_cutoff_ms=REALTIME_READY_CUTOFF_MS,
        )


def mark_probe_started(config: llm_service.LlmConfig) -> ProviderRuntimeStatus:
    return _mark_probe(config, "probing")


def mark_probe_succeeded(
    config: llm_service.LlmConfig,
    *,
    latency_ms: int | None = None,
    usage: Mapping[str, Any] | None = None,
    realtime_ready: bool | None = None,
) -> ProviderRuntimeStatus:
    normalized_latency = _non_negative_int(latency_ms, field="latency_ms")
    normalized_usage = _validated_usage(usage)
    if realtime_ready is True and (
        normalized_latency is None
        or normalized_latency > REALTIME_READY_CUTOFF_MS
        or normalized_usage is None
    ):
        raise ValueError("realtime_ready requires valid usage within the realtime cutoff")
    return _mark_probe(
        config,
        "succeeded",
        operational=True,
        realtime_ready=realtime_ready,
        latency_ms=normalized_latency,
        usage=normalized_usage,
    )


def mark_probe_failed(config: llm_service.LlmConfig) -> ProviderRuntimeStatus:
    return _mark_probe(
        config,
        "failed",
        operational=False,
        realtime_ready=False,
    )


def _non_negative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _validated_usage(usage: Mapping[str, Any] | None) -> dict[str, int] | None:
    if usage is None:
        return None
    normalized = {
        field: _non_negative_int(usage.get(field), field=field)
        for field in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    if any(value is None for value in normalized.values()):
        raise ValueError("probe usage is incomplete")
    prompt_tokens = int(normalized["prompt_tokens"])
    completion_tokens = int(normalized["completion_tokens"])
    total_tokens = int(normalized["total_tokens"])
    if total_tokens <= 0 or total_tokens != prompt_tokens + completion_tokens:
        raise ValueError("probe usage is inconsistent")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _mark_probe(
    config: llm_service.LlmConfig,
    probe_status: ProviderProbeStatus,
    *,
    operational: bool | None = None,
    realtime_ready: bool | None = None,
    latency_ms: int | None = None,
    usage: dict[str, int] | None = None,
) -> ProviderRuntimeStatus:
    global _observed_identity, _probe_status
    global _probe_operational, _probe_realtime_ready, _probe_latency_ms, _probe_usage
    with _lock:
        _observed_identity = _identity(config)
        _probe_status = probe_status
        _probe_operational = operational
        _probe_realtime_ready = realtime_ready
        _probe_latency_ms = latency_ms
        _probe_usage = dict(usage) if usage is not None else None
    return get_status(config)
