"""Correction-only Provider availability, independent of working coach lanes.

This helper performs no Provider requests and creates no retries. Its admission
permit wraps the existing durable circuit so the correction handler can defer
before a request, preserving the durable job's attempt count. Only the effective
configuration digest is persisted; snapshots contain neither endpoint nor key.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.realtime_provider_circuit import (
    CircuitPermit,
    CircuitSnapshot,
    RealtimeProviderCircuit,
    normalize_failure_class,
)


CORRECTION_AVAILABILITY_COOLDOWN_SECONDS = 60.0
CORRECTION_RECOVERY_LEASE_MS = 45_000
_CORRECTION_ERROR_ALIASES = {
    "provider_timeout": "timeout",
    "provider_transport": "transport",
    "provider_502": "provider_server",
}


def correction_provider_identity(
    config: llm_service.LlmConfig | None,
    *,
    config_revision: str | int | None = None,
) -> str:
    """Stable effective correction identity, including credential replacement.

    Configuration and credential material exist only while forming this digest.
    Realtime model choices do not change the correction identity. An optional
    revision must come from durable configuration, never process startup counts.
    """

    if config is None:
        identity: dict[str, Any] = {"scope": "correction", "configured": False}
    else:
        effective = llm_service.correction_config(config)
        identity = {
            "scope": "correction",
            "endpoint": effective.base_url,
            "provider": llm_service.provider_identifier(effective),
            "model": effective.model,
            "protocol": effective.api_style,
            "is_mock": effective.is_mock,
            "credential_revision": hashlib.sha256(effective.api_key.encode("utf-8")).hexdigest(),
        }
    if config_revision is not None:
        identity["config_revision"] = str(config_revision)
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def classify_correction_availability_failure(outcome: Any) -> str | None:
    """Recognize safe availability categories, never arbitrary exception text.

    Correction validation, changed evidence and cancellation do not indicate an
    upstream outage. The caller may record success separately if a rejected
    model result nevertheless proves a completed Provider response.
    """

    if isinstance(outcome, str):
        return normalize_failure_class(_CORRECTION_ERROR_ALIASES.get(outcome, outcome))
    if isinstance(outcome, Mapping):
        category = outcome.get("category")
        error_class = outcome.get("error_class")
        status_code = outcome.get("status_code")
    else:
        category = getattr(outcome, "category", None)
        error_class = getattr(outcome, "error_class", None)
        status_code = getattr(outcome, "status_code", None)
    classified = normalize_failure_class(category)
    if classified is not None:
        return classified
    classified = normalize_failure_class(_CORRECTION_ERROR_ALIASES.get(str(error_class)))
    if classified is not None:
        return classified
    if type(status_code) is int:
        if status_code == 429:
            return "rate_limit"
        if 500 <= status_code <= 599:
            return "provider_server"
    return None


@dataclass(frozen=True)
class CorrectionAvailabilitySnapshot:
    state: str
    reason: str
    failure_count: int
    retry_after_ms: int
    half_open: bool
    identity_generation: int
    last_failure_class: str | None

    @classmethod
    def from_circuit(cls, value: CircuitSnapshot) -> "CorrectionAvailabilitySnapshot":
        reason = (
            "correction_provider_recovery_trial" if value.state == "half_open"
            else "correction_provider_rate_limit_backoff" if value.state == "open" and value.last_failure_class == "rate_limit"
            else "correction_provider_paused" if value.state == "open"
            else "correction_provider_available"
        )
        return cls(
            state=value.state,
            reason=reason,
            failure_count=value.failure_count,
            retry_after_ms=value.retry_after_ms,
            half_open=value.half_open,
            identity_generation=value.identity_generation,
            last_failure_class=value.last_failure_class,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": "correction",
            "state": self.state,
            "reason": self.reason,
            "failure_count": self.failure_count,
            "retry_after_ms": self.retry_after_ms,
            "half_open": self.half_open,
            "identity_generation": self.identity_generation,
            "last_failure_class": self.last_failure_class,
            "status": "checking" if self.half_open else "paused" if self.state == "open" else "ready",
            "message": (
                "AI校对正在检查恢复，识别原文已保存" if self.half_open
                else "AI校对暂不可用，识别原文已保存" if self.state == "open"
                else "AI校对可用"
            ),
        }


class CorrectionAvailabilityPermit:
    """Single-use actual request outcome, coordinated by the durable circuit."""

    def __init__(self, permit: CircuitPermit) -> None:
        self._permit = permit

    @property
    def half_open(self) -> bool:
        return self._permit.half_open

    def record_success(self) -> None:
        self._permit.record_success()

    def record_failure(self, outcome: Any, *, retry_after_ms: int | None = None) -> None:
        category = classify_correction_availability_failure(outcome)
        if category is None:
            self._permit.release()
        else:
            self._permit.record_failure(category, retry_after_ms=retry_after_ms)

    def release(self) -> None:
        """Release cancelled, superseded or pre-call deferred work without counting."""

        self._permit.release()


@dataclass(frozen=True)
class CorrectionAvailabilityAdmission:
    admitted: bool
    snapshot: CorrectionAvailabilitySnapshot
    permit: CorrectionAvailabilityPermit | None


class CorrectionProviderAvailabilityDeferred(RuntimeError):
    """Defer admission through V2Executor.defer_job, without an actual attempt."""

    preserve_attempt = True

    def __init__(self, snapshot: CorrectionAvailabilitySnapshot) -> None:
        self.snapshot = snapshot
        self.retry_after_ms = max(1_000, snapshot.retry_after_ms)
        super().__init__("correction Provider admission is paused; original transcript is preserved")


class CorrectionProviderAvailability:
    def __init__(self, *, persistence: Any | None = None, clock: Any | None = None) -> None:
        self._circuit = RealtimeProviderCircuit(
            failure_threshold=2,
            cooldown_seconds=CORRECTION_AVAILABILITY_COOLDOWN_SECONDS,
            rate_limit_cooldown_seconds=CORRECTION_AVAILABILITY_COOLDOWN_SECONDS,
            half_open_lease_ms=CORRECTION_RECOVERY_LEASE_MS,
            persistence=persistence,
            clock=clock,
        )

    def acquire(self, config: llm_service.LlmConfig | None) -> CorrectionAvailabilityAdmission:
        admission = self._circuit.acquire(correction_provider_identity(config))
        return CorrectionAvailabilityAdmission(
            admitted=admission.admitted,
            snapshot=CorrectionAvailabilitySnapshot.from_circuit(admission.snapshot),
            permit=CorrectionAvailabilityPermit(admission.permit) if admission.permit is not None else None,
        )

    def admit_or_defer(self, config: llm_service.LlmConfig | None) -> CorrectionAvailabilityPermit:
        admission = self.acquire(config)
        if not admission.admitted or admission.permit is None:
            raise CorrectionProviderAvailabilityDeferred(admission.snapshot)
        return admission.permit

    def snapshot(self, config: llm_service.LlmConfig | None) -> CorrectionAvailabilitySnapshot:
        return CorrectionAvailabilitySnapshot.from_circuit(self._circuit.snapshot(correction_provider_identity(config)))

    def record_resolution_success(self, config: llm_service.LlmConfig | None) -> CorrectionAvailabilitySnapshot:
        """Reset only correction after a separately authorized successful probe."""

        return CorrectionAvailabilitySnapshot.from_circuit(
            self._circuit.record_probe_success(correction_provider_identity(config))
        )
