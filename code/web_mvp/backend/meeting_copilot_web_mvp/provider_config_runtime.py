from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Literal

from meeting_copilot_web_mvp import llm_service


ProviderProbeStatus = Literal["not_run", "probing", "succeeded", "failed"]
_ProviderIdentity = tuple[int, str, str, str, str, str]


@dataclass(frozen=True)
class ProviderRuntimeStatus:
    configured: bool
    runtime_synced: bool
    probe_status: ProviderProbeStatus
    model: str | None
    realtime_model: str | None

    def to_dict(self) -> dict[str, bool | str | None]:
        return asdict(self)


_lock = RLock()
_observed_identity: _ProviderIdentity | None = None
_probe_status: ProviderProbeStatus = "not_run"


def _identity(config: llm_service.LlmConfig) -> _ProviderIdentity:
    return (
        llm_service.runtime_config_generation(),
        config.base_url,
        config.model,
        str(config.realtime_model or config.model),
        config.api_style,
        llm_service.provider_identifier(config),
    )


def _observe(config: llm_service.LlmConfig | None) -> ProviderProbeStatus:
    global _observed_identity, _probe_status
    identity = _identity(config) if config is not None else None
    if identity != _observed_identity:
        _observed_identity = identity
        _probe_status = "not_run"
    return _probe_status


def get_status(
    config: llm_service.LlmConfig | None = None,
) -> ProviderRuntimeStatus:
    resolved = config if config is not None else llm_service.LlmConfig.from_env()
    with _lock:
        probe_status = _observe(resolved)
        configured = resolved is not None
        return ProviderRuntimeStatus(
            configured=configured,
            runtime_synced=configured,
            probe_status=probe_status,
            model=resolved.model if resolved is not None else None,
            realtime_model=(resolved.realtime_model if resolved is not None else None),
        )


def mark_probe_started(config: llm_service.LlmConfig) -> ProviderRuntimeStatus:
    return _mark_probe(config, "probing")


def mark_probe_succeeded(config: llm_service.LlmConfig) -> ProviderRuntimeStatus:
    return _mark_probe(config, "succeeded")


def mark_probe_failed(config: llm_service.LlmConfig) -> ProviderRuntimeStatus:
    return _mark_probe(config, "failed")


def _mark_probe(
    config: llm_service.LlmConfig,
    probe_status: ProviderProbeStatus,
) -> ProviderRuntimeStatus:
    global _observed_identity, _probe_status
    with _lock:
        _observed_identity = _identity(config)
        _probe_status = probe_status
    return get_status(config)
