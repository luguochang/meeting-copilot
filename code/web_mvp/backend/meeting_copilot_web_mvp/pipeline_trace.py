from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
import math
import time
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Mapping


PIPELINE_STAGES = (
    "audio_active",
    "final_committed",
    "job_queued",
    "job_claimed",
    "provider_connected",
    "first_token",
    "provider_completed",
    "validated",
    "event_emitted",
    "ui_rendered",
)

# Keep the historical latency stage tuple stable for API compatibility.  The
# provenance contract below is deliberately separate: a failed request must be
# able to say that a stage was not observed or was unavailable without
# fabricating a latency timestamp for it.
REQUIRED_PROVENANCE_STAGES = (
    # The ASR endpoint is the product-visible start of a speech-triggered
    # intelligence trace. Keep it separate from ``final_committed`` so a
    # failed path can prove whether delay happened before or after the
    # authoritative final was persisted.
    "speech_endpoint",
    "candidate_gate",
    "route_decision",
    "lifecycle_lookup",
    "circuit_admission",
    "reservation",
    "provider_attempt_start",
    "provider_connected",
    "first_token",
    "agent_tool_loop",
    "provider_completed",
    "response_validation",
    "persistence_commit",
    "projection_commit",
    "late_result_guard",
    "cancel_requested",
    "abort_ack",
    "durable_terminal",
    "ui_render_ack",
)
PROVENANCE_STAGES = REQUIRED_PROVENANCE_STAGES
PROVENANCE_STATUSES = frozenset(
    {"observed", "not_required", "not_observed", "failed", "unavailable"}
)
PROVENANCE_ACCOUNTED_STATUSES = frozenset(
    {"observed", "not_required", "failed", "unavailable"}
)
_PROVENANCE_STAGE_SET = frozenset(REQUIRED_PROVENANCE_STAGES)

_STAGE_INDEX = {stage: index for index, stage in enumerate(PIPELINE_STAGES)}
DEFAULT_MAX_TRACES = 2_048

PROVIDER_TIMING_STAGES = {
    "provider_connected": "connected_at",
    "first_token": "first_token_at",
    "provider_completed": "completed_at",
}
PI_EVALUATION_TIMING_FIELDS = {
    "evaluation_started": "started_at_ms",
    "first_token": "first_token_at_ms",
    "evaluation_completed": "completed_at_ms",
}
TIMING_CONTRACT_STATUSES = frozenset({"complete", "partial", "missing", "invalid"})
RESULT_OUTCOMES = frozenset(
    {
        "success",
        "timeout",
        "rate_limit",
        "provider_5xx",
        "transport_error",
        "cancelled",
        "circuit_denied",
        "validation_error",
        "failed",
    }
)
PROVIDER_ATTEMPT_OUTCOMES = RESULT_OUTCOMES - {"circuit_denied"}
TERMINAL_OUTCOMES = frozenset({"success", "timeout", "cancelled", "superseded", "failed"})

_log = logging.getLogger(__name__)


def _required(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _optional_id(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return _required(value, field)


def _milliseconds(duration_ns: int) -> float:
    return round(duration_ns / 1_000_000, 6)


def _bounded_token(value: Any, field: str, *, max_length: int = 120) -> str:
    normalized = _required(str(value or ""), field)
    if len(normalized) > max_length:
        raise ValueError(f"{field} must not exceed {max_length} characters")
    if not all(character.isalnum() or character in "._:-" for character in normalized):
        raise ValueError(f"{field} must be a content-free identifier")
    return normalized


def _optional_bounded_token(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _bounded_token(value, field)


def _content_free_attributes(
    attributes: Mapping[str, Any] | None,
    *,
    field: str = "provenance attributes",
) -> dict[str, Any]:
    """Validate the tiny scalar allowlist used by provenance records.

    Provenance is exported to diagnostics and may be persisted for a long
    time.  Rejecting arbitrary nested values here prevents accidental capture
    of transcript text, URLs, response bodies, or credentials.
    """

    if attributes is None:
        return {}
    if not isinstance(attributes, Mapping):
        raise TypeError(f"{field} must be a mapping")
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in attributes.items():
        key = _bounded_token(raw_key, f"{field} key")
        if raw_value is None or isinstance(raw_value, bool):
            normalized[key] = raw_value
            continue
        if isinstance(raw_value, int) and not isinstance(raw_value, bool):
            if raw_value < 0:
                raise ValueError(f"{field} integer values must be non-negative")
            normalized[key] = raw_value
            continue
        if isinstance(raw_value, float):
            if not math.isfinite(raw_value) or raw_value < 0:
                raise ValueError(f"{field} numeric values must be finite and non-negative")
            normalized[key] = round(raw_value, 6)
            continue
        if isinstance(raw_value, str):
            normalized[key] = _bounded_token(raw_value, f"{field} value")
            continue
        raise TypeError(f"{field} values must be scalar and content-free")
    return normalized


def _optional_http_status(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("http_status must be an integer")
    if value < 100 or value > 599:
        raise ValueError("http_status must be between 100 and 599")
    return value


def _finite_non_negative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        return None
    return normalized


def _timestamp_ns(clock_ns: Callable[[], int], value: int | None) -> int:
    timestamp = clock_ns() if value is None else value
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise TypeError("monotonic_ns must be an integer")
    if timestamp < 0:
        raise ValueError("monotonic_ns must be non-negative")
    return timestamp


@dataclass(frozen=True, slots=True)
class ProviderTimingNormalization:
    """Validated direct-Provider or Pi-evaluation timing on a monotonic clock."""

    status: str
    source_clock: str | None
    stages: Mapping[str, int]
    attempt_started_monotonic_ns: int | None
    evaluation: Mapping[str, int]
    invalid_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_clock": self.source_clock,
            "stages": dict(self.stages),
            "attempt_started_monotonic_ns": self.attempt_started_monotonic_ns,
            "evaluation": dict(self.evaluation),
            "invalid_reasons": list(self.invalid_reasons),
        }


def normalize_provider_timing_stages(
    timings: Any,
    *,
    wall_time_ns: int | None = None,
    monotonic_ns: int | None = None,
    provider_attempt_count: int = 1,
) -> ProviderTimingNormalization:
    """Normalize direct Provider timing or Pi agent-evaluation timing.

    Epoch values cannot be written into a monotonic trace directly. A paired
    wall/monotonic anchor converts the Pi bridge timestamps while preserving its
    observed deltas. Pi start/completion describe the whole agent evaluation,
    not Provider connection/completion, so they are retained separately and do
    not fabricate Provider latency stages. Invalid or mixed contracts return an
    explicit failure instead of dropping affected SLO stages silently.
    """

    if isinstance(provider_attempt_count, bool) or not isinstance(provider_attempt_count, int):
        raise TypeError("provider_attempt_count must be an integer")
    if provider_attempt_count <= 0:
        raise ValueError("provider_attempt_count must be positive")

    if timings is None:
        return ProviderTimingNormalization(
            status="missing",
            source_clock=None,
            stages=MappingProxyType({}),
            attempt_started_monotonic_ns=None,
            evaluation=MappingProxyType({}),
            invalid_reasons=("missing_timings",),
        )
    if not isinstance(timings, Mapping):
        return ProviderTimingNormalization(
            status="invalid",
            source_clock=None,
            stages=MappingProxyType({}),
            attempt_started_monotonic_ns=None,
            evaluation=MappingProxyType({}),
            invalid_reasons=("timings_not_mapping",),
        )

    direct_fields = set(PROVIDER_TIMING_STAGES.values()) | {"started_at"}
    epoch_fields = set(PI_EVALUATION_TIMING_FIELDS.values())
    has_direct = any(field in timings and timings.get(field) is not None for field in direct_fields)
    has_epoch = any(field in timings and timings.get(field) is not None for field in epoch_fields)
    raw_clock = str(timings.get("clock") or "").strip()

    invalid_reasons: list[str] = []
    if has_direct and has_epoch:
        invalid_reasons.append("mixed_clock_fields")
    if raw_clock == "unix_epoch_ms":
        source_fields = PI_EVALUATION_TIMING_FIELDS
        source_clock = "unix_epoch_ms"
        if has_direct:
            invalid_reasons.append("monotonic_fields_with_epoch_clock")
    elif raw_clock in {"", "monotonic_seconds"}:
        source_fields = PROVIDER_TIMING_STAGES
        source_clock = "monotonic_seconds" if has_direct else None
        if has_epoch:
            invalid_reasons.append("epoch_fields_without_epoch_clock")
    else:
        source_fields = {}
        source_clock = raw_clock
        invalid_reasons.append("unsupported_timing_clock")

    if invalid_reasons:
        return ProviderTimingNormalization(
            status="invalid",
            source_clock=source_clock,
            stages=MappingProxyType({}),
            attempt_started_monotonic_ns=None,
            evaluation=MappingProxyType({}),
            invalid_reasons=tuple(dict.fromkeys(invalid_reasons)),
        )
    if not has_direct and not has_epoch:
        return ProviderTimingNormalization(
            status="missing",
            source_clock=source_clock,
            stages=MappingProxyType({}),
            attempt_started_monotonic_ns=None,
            evaluation=MappingProxyType({}),
            invalid_reasons=("missing_timing_fields",),
        )

    raw_values: dict[str, float] = {}
    for stage, field in source_fields.items():
        if field not in timings or timings.get(field) is None:
            continue
        normalized = _finite_non_negative_number(timings.get(field))
        if normalized is None:
            invalid_reasons.append(f"invalid_{field}")
        else:
            raw_values[stage] = normalized

    source_order = (
        ("evaluation_started", "first_token", "evaluation_completed")
        if source_clock == "unix_epoch_ms"
        else tuple(PROVIDER_TIMING_STAGES)
    )
    ordered_values = [raw_values[stage] for stage in source_order if stage in raw_values]
    if any(later < earlier for earlier, later in zip(ordered_values, ordered_values[1:])):
        invalid_reasons.append("provider_timing_order_invalid")
    if invalid_reasons:
        return ProviderTimingNormalization(
            status="invalid",
            source_clock=source_clock,
            stages=MappingProxyType({}),
            attempt_started_monotonic_ns=None,
            evaluation=MappingProxyType({}),
            invalid_reasons=tuple(dict.fromkeys(invalid_reasons)),
        )

    if source_clock == "unix_epoch_ms":
        anchor_wall_ns = time.time_ns() if wall_time_ns is None else wall_time_ns
        anchor_monotonic_ns = time.monotonic_ns() if monotonic_ns is None else monotonic_ns
        for value, field in ((anchor_wall_ns, "wall_time_ns"), (anchor_monotonic_ns, "monotonic_ns")):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field} must be an integer")
            if value < 0:
                raise ValueError(f"{field} must be non-negative")
        offset_ns = anchor_monotonic_ns - anchor_wall_ns
        evaluation = {
            stage: int(round(value * 1_000_000)) + offset_ns
            for stage, value in raw_values.items()
        }
        if any(value < 0 for value in evaluation.values()):
            return ProviderTimingNormalization(
                status="invalid",
                source_clock=source_clock,
                stages=MappingProxyType({}),
                attempt_started_monotonic_ns=None,
                evaluation=MappingProxyType({}),
                invalid_reasons=("epoch_timestamp_before_monotonic_origin",),
            )
        stages = (
            {"first_token": evaluation["first_token"]}
            if "first_token" in evaluation
            else {}
        )
        attempt_started_monotonic_ns = None
        status = "partial"
        missing_fields = [
            "pi_provider_connected_unobserved",
            "pi_provider_completed_unobserved",
        ]
        if "first_token" not in stages:
            missing_fields.append("missing_first_token_at_ms")
    else:
        stages = {
            stage: int(round(value * 1_000_000_000))
            for stage, value in raw_values.items()
            if stage in PROVIDER_TIMING_STAGES
        }
        evaluation = {}
        raw_started_at = _finite_non_negative_number(timings.get("started_at"))
        attempt_started_monotonic_ns = (
            int(round(raw_started_at * 1_000_000_000))
            if raw_started_at is not None
            else None
        )
        if provider_attempt_count > 1 and "provider_completed" in stages:
            # run_realtime_intelligence currently combines the first attempt's
            # connect/TTFT with the repair attempt's completion. Keep the valid
            # first-attempt prefix, but never publish a synthetic total latency.
            stages.pop("provider_completed")
        status = "complete" if set(stages) == set(PROVIDER_TIMING_STAGES) else "partial"
        missing_fields = [
            f"missing_{PROVIDER_TIMING_STAGES[stage]}"
            for stage in PROVIDER_TIMING_STAGES
            if stage not in stages
        ]
        if provider_attempt_count > 1:
            missing_fields.append("multiple_provider_attempts_require_attempt_timings")
    return ProviderTimingNormalization(
        status=status,
        source_clock=source_clock,
        stages=MappingProxyType(stages),
        attempt_started_monotonic_ns=attempt_started_monotonic_ns,
        evaluation=MappingProxyType(evaluation),
        invalid_reasons=tuple(missing_fields),
    )


def observe_provider_timing(
    collector: "PipelineTraceCollector",
    *,
    trace_id: str,
    meeting_id: str,
    timings: Any,
    job_id: str | None = None,
    generation_id: str | None = None,
    provider_attempt_count: int = 1,
    wall_time_ns: int | None = None,
    monotonic_ns: int | None = None,
) -> ProviderTimingNormalization:
    """Normalize and record a timing contract without leaking Provider data."""

    normalization = normalize_provider_timing_stages(
        timings,
        wall_time_ns=wall_time_ns,
        monotonic_ns=monotonic_ns,
        provider_attempt_count=provider_attempt_count,
    )
    trace = collector.create(
        trace_id=trace_id,
        meeting_id=meeting_id,
        job_id=job_id,
        generation_id=generation_id,
    )
    dropped_stages: list[str] = []
    for stage, timestamp in normalization.stages.items():
        observed = collector.observe(
            trace_id,
            stage,
            meeting_id=meeting_id,
            job_id=job_id,
            generation_id=generation_id,
            monotonic_ns=timestamp,
            attributes={
                "timing_source_clock": normalization.source_clock,
            },
        )
        if observed is None:
            dropped_stages.append(stage)
    trace.record_timing_contract(
        normalization,
        additional_invalid_reasons=tuple(
            f"trace_order_rejected_{stage}" for stage in dropped_stages
        ),
    )
    return normalization


def classify_pipeline_failure(error: BaseException, *, deadline_reached: bool = False) -> str:
    """Map an exception to a bounded result outcome without retaining details."""

    error_name = type(error).__name__.strip().lower()
    durable_error = str(getattr(error, "durable_error_class", "") or "").strip().lower()
    category_value = getattr(error, "category", "")
    category = str(getattr(category_value, "value", category_value) or "").strip().lower()
    status_code = getattr(error, "status_code", None)
    combined = " ".join((error_name, durable_error, category))
    if deadline_reached or "timeout" in combined or "deadline" in combined:
        return "timeout"
    if status_code == 429 or category == "rate_limit":
        return "rate_limit"
    if (type(status_code) is int and 500 <= status_code <= 599) or category == "provider_server":
        return "provider_5xx"
    if category == "transport" or "transport" in combined or "requesterror" in combined:
        return "transport_error"
    if "cancel" in combined:
        return "cancelled"
    if "validation" in combined or category in {"protocol", "empty_response"}:
        return "validation_error"
    return "failed"


@dataclass(frozen=True, slots=True)
class StageMark:
    """The first observation of one pipeline stage on a monotonic clock."""

    stage: str
    monotonic_ns: int
    attributes: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "monotonic_ns": self.monotonic_ns,
            "attributes": dict(self.attributes),
        }


class PipelineTrace:
    """One correlated audio-to-UI pipeline execution.

    Stage observations use first-write-wins semantics. This makes retries and
    duplicate event delivery harmless while preserving the original latency.
    """

    def __init__(
        self,
        *,
        trace_id: str,
        meeting_id: str,
        job_id: str | None = None,
        generation_id: str | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.trace_id = _required(trace_id, "trace_id")
        self.meeting_id = _required(meeting_id, "meeting_id")
        self._job_id = _optional_id(job_id, "job_id")
        self._generation_id = _optional_id(generation_id, "generation_id")
        self._clock_ns = clock_ns
        self._marks: dict[str, StageMark] = {}
        self._lane: str | None = None
        self._retry_count = 0
        self._cancelled = False
        self._route: dict[str, Any] | None = None
        self._provider_attempts: dict[int, dict[str, Any]] = {}
        self._cancellation: dict[str, Any] | None = None
        self._terminal: dict[str, Any] | None = None
        self._timing_contract: dict[str, Any] | None = None
        self._provenance: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    @property
    def job_id(self) -> str | None:
        with self._lock:
            return self._job_id

    @property
    def generation_id(self) -> str | None:
        with self._lock:
            return self._generation_id

    @property
    def stage_marks(self) -> Mapping[str, StageMark]:
        with self._lock:
            ordered = {stage: self._marks[stage] for stage in PIPELINE_STAGES if stage in self._marks}
        return MappingProxyType(ordered)

    def _record_provenance_stage_locked(
        self,
        stage: str,
        *,
        status: str,
        reason: str | None,
        monotonic_ns: int,
        attributes: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Record one bounded provenance fact while ``self._lock`` is held."""

        existing = self._provenance.get(stage)
        requested = {
            "status": status,
            "reason": reason,
            "at_monotonic_ns": monotonic_ns,
            "attributes": dict(attributes or {}),
        }
        if existing is None:
            self._provenance[stage] = requested
            return dict(requested)

        existing_status = str(existing.get("status") or "")
        # A lookup may initially establish that a stage was not observed and
        # later receive the actual event.  Permit that one-way refinement;
        # terminal facts are otherwise immutable and duplicate delivery is
        # idempotent.
        transitioned_from_placeholder = False
        if existing_status != status:
            if existing_status in {"not_observed", "unavailable", "not_required"} and status not in {
                "not_observed",
                "unavailable",
                "not_required",
            }:
                existing["status"] = status
                transitioned_from_placeholder = True
            elif existing_status in {"not_observed", "unavailable"} and status == "not_required":
                # A terminal seal may conservatively mark a stage
                # unavailable before a later lifecycle callback proves that
                # the stage was not applicable to this lane.
                existing["status"] = status
                transitioned_from_placeholder = True
            elif status in {"not_observed", "unavailable"} and existing_status not in {
                "not_observed",
                "unavailable",
            }:
                status = existing_status
            else:
                raise ValueError(f"provenance stage {stage} is already {existing_status!r}")

        if transitioned_from_placeholder:
            # Placeholder facts (for example the ``unknown`` candidate at
            # job claim time or a retry's not-required terminal) must not
            # block a later concrete observation.
            existing["reason"] = reason
            existing["at_monotonic_ns"] = monotonic_ns
            existing["attributes"] = dict(attributes or {})
            return dict(existing)

        current_reason = existing.get("reason")
        if current_reason is not None and reason is not None and current_reason != reason:
            raise ValueError(f"provenance stage {stage} reason is already bound")
        if current_reason is None and reason is not None:
            existing["reason"] = reason

        current_at = existing.get("at_monotonic_ns")
        if current_at is None:
            existing["at_monotonic_ns"] = monotonic_ns
        elif current_at != monotonic_ns and existing_status == status:
            # First-write-wins for timestamps.  A duplicate event with a
            # different clock sample must not move a latency anchor.
            pass

        current_attributes = existing.setdefault("attributes", {})
        for key, value in (attributes or {}).items():
            if key in current_attributes and current_attributes[key] != value:
                raise ValueError(f"provenance stage {stage} attribute {key!r} is already bound")
            current_attributes.setdefault(key, value)
        return dict(existing)

    def record_provenance_stage(
        self,
        stage: str,
        *,
        status: str = "observed",
        reason: str | None = None,
        monotonic_ns: int | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a content-free stage status for failure attribution.

        Unlike :meth:`mark`, a provenance stage may explicitly be
        ``not_required``, ``not_observed``, ``failed`` or ``unavailable``.
        Those states are essential for distinguishing a real denial/timeout
        from a telemetry gap without inventing Provider latency.
        """

        normalized_stage = _bounded_token(stage, "provenance stage")
        if normalized_stage not in _PROVENANCE_STAGE_SET:
            raise ValueError(f"unsupported provenance stage: {normalized_stage!r}")
        normalized_status = _bounded_token(status, "provenance status")
        if normalized_status not in PROVENANCE_STATUSES:
            raise ValueError(f"unsupported provenance status: {normalized_status!r}")
        normalized_reason = _optional_bounded_token(reason, "provenance reason")
        timestamp = _timestamp_ns(self._clock_ns, monotonic_ns)
        normalized_attributes = _content_free_attributes(attributes)
        with self._lock:
            return self._record_provenance_stage_locked(
                normalized_stage,
                status=normalized_status,
                reason=normalized_reason,
                monotonic_ns=timestamp,
                attributes=normalized_attributes,
            )

    def record_failure_provenance(
        self,
        stage: str,
        *,
        reason: str,
        monotonic_ns: int | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convenience wrapper for a failed, attributable stage."""

        return self.record_provenance_stage(
            stage,
            status="failed",
            reason=reason,
            monotonic_ns=monotonic_ns,
            attributes=attributes,
        )

    def required_stage_completeness(self) -> dict[str, Any]:
        """Summarize whether every required provenance stage is accounted for."""

        with self._lock:
            provenance = {
                stage: dict(entry)
                for stage, entry in self._provenance.items()
            }
        statuses = {
            stage: str(provenance.get(stage, {}).get("status") or "not_observed")
            for stage in REQUIRED_PROVENANCE_STAGES
        }
        missing = [
            stage
            for stage, status in statuses.items()
            if status == "not_observed"
        ]
        failed = [stage for stage, status in statuses.items() if status == "failed"]
        unavailable = [
            stage for stage, status in statuses.items() if status == "unavailable"
        ]
        not_required = [
            stage for stage, status in statuses.items() if status == "not_required"
        ]
        observed = [stage for stage, status in statuses.items() if status == "observed"]
        accounted_count = len(REQUIRED_PROVENANCE_STAGES) - len(missing)
        required_count = len(REQUIRED_PROVENANCE_STAGES)
        return {
            "required_count": required_count,
            "observed_count": len(observed),
            "accounted_count": accounted_count,
            "completeness_ratio": round(accounted_count / required_count, 6),
            "complete": not missing,
            "missing_stages": missing,
            "failed_stages": failed,
            "unavailable_stages": unavailable,
            "not_required_stages": not_required,
        }

    def seal_unobserved_provenance(
        self,
        *,
        reason: str = "trace_terminal_before_stage_observed",
        monotonic_ns: int | None = None,
    ) -> tuple[str, ...]:
        """Account for provenance gaps when a trace reaches a durable terminal.

        A terminal trace must distinguish an observed failure from a missing
        telemetry record.  Stages that are still ``not_observed`` at that
        point are therefore recorded as ``unavailable`` without inventing a
        latency timestamp or a Provider event.  Later late-result/UI events
        may still refine this placeholder to an observed or failed stage.
        """

        timestamp = _timestamp_ns(self._clock_ns, monotonic_ns)
        with self._lock:
            missing = tuple(
                stage
                for stage in REQUIRED_PROVENANCE_STAGES
                if str(self._provenance.get(stage, {}).get("status") or "not_observed")
                == "not_observed"
            )
        for stage in missing:
            try:
                self.record_provenance_stage(
                    stage,
                    status="unavailable",
                    reason=reason,
                    monotonic_ns=timestamp,
                    attributes={"sealed": True},
                )
            except (TypeError, ValueError):
                _log.debug(
                    "Pipeline provenance seal skipped; stage=%s",
                    stage,
                    exc_info=True,
                )
        return missing

    def bind(
        self,
        *,
        job_id: str | None = None,
        generation_id: str | None = None,
    ) -> PipelineTrace:
        """Attach durable identifiers once they exist.

        Rebinding the same values is idempotent; changing an existing binding
        is rejected so unrelated executions cannot be merged accidentally.
        """

        job_id = _optional_id(job_id, "job_id")
        generation_id = _optional_id(generation_id, "generation_id")
        with self._lock:
            bound_job_id = self._bind_one("job_id", self._job_id, job_id)
            bound_generation_id = self._bind_one(
                "generation_id",
                self._generation_id,
                generation_id,
            )
            self._job_id = bound_job_id
            self._generation_id = bound_generation_id
        return self

    @staticmethod
    def _bind_one(field: str, current: str | None, requested: str | None) -> str | None:
        if requested is None:
            return current
        if current is not None and current != requested:
            raise ValueError(f"{field} is already bound to {current!r}")
        return requested

    def mark(
        self,
        stage: str,
        *,
        monotonic_ns: int | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> StageMark:
        if stage not in _STAGE_INDEX:
            raise ValueError(f"unsupported pipeline stage: {stage!r}")

        timestamp = self._clock_ns() if monotonic_ns is None else monotonic_ns
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise TypeError("monotonic_ns must be an integer")
        if timestamp < 0:
            raise ValueError("monotonic_ns must be non-negative")
        mark_attributes = MappingProxyType(dict(attributes or {}))

        with self._lock:
            existing = self._marks.get(stage)
            if existing is not None:
                return existing
            requested_lane = mark_attributes.get("lane")
            if requested_lane is not None:
                normalized_lane = _required(str(requested_lane), "lane")
                if self._lane is not None and self._lane != normalized_lane:
                    raise ValueError(f"lane is already bound to {self._lane!r}")
            self._validate_stage_order(stage, timestamp)
            if requested_lane is not None:
                self._lane = normalized_lane
            mark = StageMark(
                stage=stage,
                monotonic_ns=timestamp,
                attributes=mark_attributes,
            )
            self._marks[stage] = mark
            return mark

    def record_retry(self, *, count: int = 1) -> int:
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("retry count must be an integer")
        if count <= 0:
            raise ValueError("retry count must be positive")
        with self._lock:
            self._retry_count += count
            return self._retry_count

    def record_route(
        self,
        route: str,
        *,
        candidate_outcome: str | None = None,
        circuit_outcome: str | None = None,
        monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        normalized_route = _bounded_token(route, "route")
        normalized_candidate = _optional_bounded_token(candidate_outcome, "candidate_outcome")
        normalized_circuit = _optional_bounded_token(circuit_outcome, "circuit_outcome")
        timestamp = _timestamp_ns(self._clock_ns, monotonic_ns)
        with self._lock:
            requested = {
                "name": normalized_route,
                "candidate_outcome": normalized_candidate,
                "circuit_outcome": normalized_circuit,
                "decided_at_monotonic_ns": timestamp,
            }
            if self._route is None:
                self._route = requested
            else:
                for field in ("name", "candidate_outcome", "circuit_outcome"):
                    existing = self._route.get(field)
                    value = requested.get(field)
                    if existing is not None and value is not None and existing != value:
                        placeholder = (
                            (field == "name" and existing == "unknown")
                            or (field == "candidate_outcome" and existing == "unknown")
                            or (field == "circuit_outcome" and existing == "not_checked")
                        )
                        if placeholder:
                            self._route[field] = value
                        else:
                            raise ValueError(f"route {field} is already bound to {existing!r}")
                    if existing is None and value is not None:
                        self._route[field] = value
            result = dict(self._route)
        # Route facts are useful even when no Provider attempt is made (for
        # example candidate suppression or a circuit denial).  They therefore
        # live in the independent provenance contract as well as the legacy
        # execution route object.
        try:
            self.record_provenance_stage(
                "route_decision",
                status=("not_observed" if normalized_route == "unknown" else "observed"),
                reason=("route_decision_not_available" if normalized_route == "unknown" else None),
                monotonic_ns=timestamp,
                attributes={"route": normalized_route},
            )
            if normalized_candidate is not None:
                self.record_provenance_stage(
                    "candidate_gate",
                    status=(
                        "not_observed"
                        if normalized_candidate == "unknown"
                        else "observed"
                    ),
                    reason=(
                        "candidate_gate_not_available"
                        if normalized_candidate == "unknown"
                        else None
                    ),
                    monotonic_ns=timestamp,
                    attributes={"outcome": normalized_candidate},
                )
            if normalized_circuit is not None:
                if normalized_circuit == "not_checked":
                    self.record_provenance_stage(
                        "circuit_admission",
                        status="not_observed",
                        reason="circuit_not_checked",
                        monotonic_ns=timestamp,
                        attributes={"outcome": normalized_circuit},
                    )
                else:
                    self.record_provenance_stage(
                        "circuit_admission",
                        monotonic_ns=timestamp,
                        attributes={"outcome": normalized_circuit},
                    )
        except (TypeError, ValueError):
            # Provenance is diagnostic-only.  Preserve the already-recorded
            # route and let the caller continue if a duplicate/conflicting
            # auxiliary fact arrives.
            _log.debug("Pipeline route provenance update skipped", exc_info=True)
        return result

    @staticmethod
    def _attempt_index(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("attempt_index must be an integer")
        if value <= 0:
            raise ValueError("attempt_index must be positive")
        return value

    def record_provider_attempt(
        self,
        attempt_index: int,
        *,
        branch: str,
        runtime: str | None = None,
        monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        index = self._attempt_index(attempt_index)
        normalized_branch = _bounded_token(branch, "attempt branch")
        normalized_runtime = _optional_bounded_token(runtime, "attempt runtime")
        timestamp = _timestamp_ns(self._clock_ns, monotonic_ns)
        with self._lock:
            attempt = self._provider_attempts.setdefault(
                index,
                {
                    "attempt_index": index,
                    "branch": normalized_branch,
                    "runtime": normalized_runtime,
                    "started_at_monotonic_ns": timestamp,
                    "completed_at_monotonic_ns": None,
                    "outcome": None,
                    "http_status": None,
                    "error_class": None,
                },
            )
            for field, value in (("branch", normalized_branch), ("runtime", normalized_runtime)):
                existing = attempt.get(field)
                if existing is not None and value is not None and existing != value:
                    raise ValueError(f"attempt {field} is already bound to {existing!r}")
                if existing is None and value is not None:
                    attempt[field] = value
            result = dict(attempt)
        try:
            self.record_provenance_stage(
                "provider_attempt_start",
                monotonic_ns=timestamp,
                attributes={
                    "attempt_index": index,
                    "branch": normalized_branch,
                    **(
                        {"runtime": normalized_runtime}
                        if normalized_runtime is not None
                        else {}
                    ),
                },
            )
        except (TypeError, ValueError):
            _log.debug("Provider attempt provenance update skipped", exc_info=True)
        return result

    def has_open_provider_attempt(self) -> bool:
        with self._lock:
            return any(
                attempt.get("outcome") is None
                for attempt in self._provider_attempts.values()
            )

    def open_provider_attempt_indices(self) -> tuple[int, ...]:
        """Return started Provider attempts that have no outcome yet."""

        with self._lock:
            return tuple(
                index
                for index, attempt in sorted(self._provider_attempts.items())
                if attempt.get("outcome") is None
            )

    def record_provider_attempt_outcome(
        self,
        attempt_index: int,
        outcome: str,
        *,
        branch: str = "unknown",
        runtime: str | None = None,
        http_status: int | None = None,
        error_class: str | None = None,
        monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        index = self._attempt_index(attempt_index)
        normalized_outcome = _bounded_token(outcome, "provider attempt outcome")
        if normalized_outcome not in PROVIDER_ATTEMPT_OUTCOMES:
            raise ValueError(f"unsupported Provider attempt outcome: {normalized_outcome!r}")
        normalized_branch = _bounded_token(branch, "attempt branch")
        normalized_runtime = _optional_bounded_token(runtime, "attempt runtime")
        normalized_status = _optional_http_status(http_status)
        normalized_error = _optional_bounded_token(error_class, "error_class")
        timestamp = _timestamp_ns(self._clock_ns, monotonic_ns)
        with self._lock:
            attempt = self._provider_attempts.setdefault(
                index,
                {
                    "attempt_index": index,
                    "branch": normalized_branch,
                    "runtime": normalized_runtime,
                    "started_at_monotonic_ns": None,
                    "completed_at_monotonic_ns": timestamp,
                    "outcome": normalized_outcome,
                    "http_status": normalized_status,
                    "error_class": normalized_error,
                },
            )
            for field, value in (
                ("branch", normalized_branch),
                ("runtime", normalized_runtime),
                ("outcome", normalized_outcome),
                ("http_status", normalized_status),
                ("error_class", normalized_error),
            ):
                existing = attempt.get(field)
                if field == "branch" and existing == "unknown" and value != "unknown":
                    attempt[field] = value
                    continue
                if existing is not None and value is not None and existing != value:
                    raise ValueError(f"attempt {field} is already bound to {existing!r}")
                if existing is None and value is not None:
                    attempt[field] = value
            if attempt.get("completed_at_monotonic_ns") is None:
                attempt["completed_at_monotonic_ns"] = timestamp
            started_at = attempt.get("started_at_monotonic_ns")
            if started_at is not None and timestamp < started_at:
                raise ValueError("Provider attempt completion precedes its start")
            result = dict(attempt)
        # An attempt outcome is an observed terminal for the attempt.  A
        # non-success outcome marks the completion boundary as failed rather
        # than pretending that Provider completion was successful.
        try:
            self.record_provenance_stage(
                "provider_completed",
                status="observed" if normalized_outcome == "success" else "failed",
                reason=(None if normalized_outcome == "success" else normalized_outcome),
                monotonic_ns=timestamp,
                attributes={
                    "attempt_index": index,
                    "outcome": normalized_outcome,
                    **(
                        {"http_status": normalized_status}
                        if normalized_status is not None
                        else {}
                    ),
                },
            )
        except (TypeError, ValueError):
            _log.debug("Provider completion provenance update skipped", exc_info=True)
        return result

    def revise_provider_attempt_validation(
        self,
        attempt_index: int,
        *,
        branch: str = "unknown",
        runtime: str | None = None,
        error_class: str | None = None,
    ) -> dict[str, Any]:
        """Correct a transport-successful attempt rejected by response validation.

        Provider transport completion is observed before the caller parses the
        response. A single monotonic correction keeps that attempt in the
        denominator while preserving its original start/completion timestamps.
        No other outcome transition is permitted.
        """

        index = self._attempt_index(attempt_index)
        normalized_branch = _bounded_token(branch, "attempt branch")
        normalized_runtime = _optional_bounded_token(runtime, "attempt runtime")
        normalized_error = _optional_bounded_token(error_class, "error_class")
        with self._lock:
            attempt = self._provider_attempts.get(index)
            if attempt is None:
                raise KeyError(f"unknown Provider attempt: {index}")
            if attempt.get("outcome") != "success":
                raise ValueError("only a successful Provider attempt may be revised")
            for field, value in (
                ("branch", normalized_branch),
                ("runtime", normalized_runtime),
            ):
                existing = attempt.get(field)
                if field == "branch" and existing == "unknown" and value != "unknown":
                    attempt[field] = value
                    continue
                if existing is not None and value is not None and existing != value:
                    raise ValueError(f"attempt {field} is already bound to {existing!r}")
                if existing is None and value is not None:
                    attempt[field] = value
            attempt["outcome"] = "validation_error"
            attempt["error_class"] = normalized_error
            return dict(attempt)

    def record_timing_contract(
        self,
        normalization: ProviderTimingNormalization,
        *,
        additional_invalid_reasons: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if not isinstance(normalization, ProviderTimingNormalization):
            raise TypeError("normalization must be ProviderTimingNormalization")
        extra = tuple(
            _bounded_token(reason, "timing invalid reason")
            for reason in additional_invalid_reasons
        )
        reasons = tuple(dict.fromkeys((*normalization.invalid_reasons, *extra)))
        status = "invalid" if extra else normalization.status
        if status not in TIMING_CONTRACT_STATUSES:
            raise ValueError(f"unsupported timing contract status: {status!r}")
        contract = {
            "status": status,
            "source_clock": normalization.source_clock,
            "invalid_reasons": list(reasons),
            "attempt_started_monotonic_ns": normalization.attempt_started_monotonic_ns,
            "evaluation": dict(normalization.evaluation),
        }
        with self._lock:
            if self._timing_contract is None or status == "invalid":
                self._timing_contract = contract
            result = dict(self._timing_contract)
        try:
            for stage, stage_timestamp in normalization.stages.items():
                if stage in _PROVENANCE_STAGE_SET:
                    self.record_provenance_stage(
                        stage,
                        monotonic_ns=stage_timestamp,
                        attributes={"timing_source_clock": normalization.source_clock or "unknown"},
                    )
        except (TypeError, ValueError):
            _log.debug("Provider timing provenance update skipped", exc_info=True)
        return result

    def record_terminal(
        self,
        outcome: str,
        *,
        result_outcome: str | None = None,
        error_class: str | None = None,
        http_status: int | None = None,
        monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        normalized_outcome = _bounded_token(outcome, "terminal outcome")
        if normalized_outcome not in TERMINAL_OUTCOMES:
            raise ValueError(f"unsupported terminal outcome: {normalized_outcome!r}")
        normalized_result = _optional_bounded_token(result_outcome, "result_outcome")
        if normalized_result is not None and normalized_result not in RESULT_OUTCOMES:
            raise ValueError(f"unsupported result outcome: {normalized_result!r}")
        normalized_error = _optional_bounded_token(error_class, "error_class")
        normalized_status = _optional_http_status(http_status)
        timestamp = _timestamp_ns(self._clock_ns, monotonic_ns)
        requested = {
            "outcome": normalized_outcome,
            "result_outcome": normalized_result or (
                normalized_outcome if normalized_outcome in RESULT_OUTCOMES else None
            ),
            "error_class": normalized_error,
            "http_status": normalized_status,
            "at_monotonic_ns": timestamp,
        }
        with self._lock:
            if self._terminal is None:
                self._terminal = requested
            else:
                for field in ("outcome", "result_outcome", "error_class", "http_status"):
                    existing = self._terminal.get(field)
                    value = requested.get(field)
                    if existing is not None and value is not None and existing != value:
                        raise ValueError(f"terminal {field} is already bound to {existing!r}")
                    if existing is None and value is not None:
                        self._terminal[field] = value
            result = dict(self._terminal)
        try:
            self.record_provenance_stage(
                "durable_terminal",
                monotonic_ns=timestamp,
                attributes={
                    "outcome": normalized_outcome,
                    **(
                        {"result_outcome": normalized_result or normalized_outcome}
                        if (normalized_result or normalized_outcome) in RESULT_OUTCOMES
                        else {}
                    ),
                },
            )
        except (TypeError, ValueError):
            _log.debug("Terminal provenance update skipped", exc_info=True)
        self.seal_unobserved_provenance(
            reason=(
                "trace_terminal_before_stage_observed"
                if normalized_outcome != "success"
                else "trace_terminal_without_stage_observation"
            ),
            monotonic_ns=timestamp,
        )
        return result

    def record_cancellation_requested(
        self,
        *,
        result_outcome: str = "cancelled",
        terminal_outcome: str | None = None,
        error_class: str | None = None,
        monotonic_ns: int | None = None,
    ) -> bool:
        normalized_terminal = terminal_outcome or (
            "timeout" if result_outcome == "timeout" else "cancelled"
        )
        normalized_terminal = _bounded_token(normalized_terminal, "terminal outcome")
        if normalized_terminal not in TERMINAL_OUTCOMES:
            raise ValueError(f"unsupported terminal outcome: {normalized_terminal!r}")
        timestamp = _timestamp_ns(self._clock_ns, monotonic_ns)
        with self._lock:
            self._cancelled = True
            if self._cancellation is None:
                self._cancellation = {
                    "requested": True,
                    "requested_at_monotonic_ns": timestamp,
                    "local_abort_ack": None,
                    "remote_abort_ack": None,
                    "remote_ack_unavailable": None,
                }
        self.record_terminal(
            normalized_terminal,
            result_outcome=result_outcome,
            error_class=error_class,
            monotonic_ns=timestamp,
        )
        try:
            self.record_provenance_stage(
                "cancel_requested",
                monotonic_ns=timestamp,
                attributes={
                    "result_outcome": result_outcome,
                    "terminal_outcome": normalized_terminal,
                },
            )
        except (TypeError, ValueError):
            _log.debug("Cancellation provenance update skipped", exc_info=True)
        return True

    def record_cancelled(self) -> bool:
        """Compatibility counter for legacy cancellation observers.

        The caller has not supplied a classified durable terminal outcome, so
        this flag alone must not manufacture one in new snapshots.
        """

        with self._lock:
            self._cancelled = True
            return self._cancelled

    def record_abort_ack(
        self,
        *,
        local: bool | None = None,
        remote: bool | None = None,
        remote_unavailable: bool | None = None,
    ) -> dict[str, Any]:
        for value, field in (
            (local, "local"),
            (remote, "remote"),
            (remote_unavailable, "remote_unavailable"),
        ):
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"{field} abort acknowledgement must be a boolean")
        if remote is True and remote_unavailable is True:
            raise ValueError("remote abort acknowledgement and unavailable are mutually exclusive")
        with self._lock:
            if self._cancellation is None:
                self._cancellation = {
                    "requested": False,
                    "requested_at_monotonic_ns": None,
                    "local_abort_ack": None,
                    "remote_abort_ack": None,
                    "remote_ack_unavailable": None,
                }
            updates = {
                "local_abort_ack": local,
                "remote_abort_ack": remote,
                "remote_ack_unavailable": remote_unavailable,
            }
            if (
                self._cancellation.get("remote_abort_ack") is True
                and remote_unavailable is True
            ) or (
                self._cancellation.get("remote_ack_unavailable") is True
                and remote is True
            ):
                raise ValueError("remote abort acknowledgement and unavailable are mutually exclusive")
            for field, value in updates.items():
                existing = self._cancellation.get(field)
                if existing is not None and value is not None and existing != value:
                    raise ValueError(f"{field} is already recorded as {existing!r}")
                if value is not None:
                    self._cancellation[field] = value
            result = dict(self._cancellation)
        try:
            if local is True:
                status = "observed"
                reason = None
            elif remote is True:
                status = "observed"
                reason = None
            elif remote_unavailable is True:
                status = "unavailable"
                reason = "remote_abort_ack_unavailable"
            else:
                status = "not_observed"
                reason = "abort_ack_pending"
            self.record_provenance_stage(
                "abort_ack",
                status=status,
                reason=reason,
                attributes={
                    "local": local,
                    "remote": remote,
                    "remote_unavailable": remote_unavailable,
                },
            )
        except (TypeError, ValueError):
            _log.debug("Abort acknowledgement provenance update skipped", exc_info=True)
        return result

    def execution_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = {
                "route": dict(self._route) if self._route is not None else None,
                "provider_attempts": [
                    dict(self._provider_attempts[index])
                    for index in sorted(self._provider_attempts)
                ],
                "cancellation": (
                    dict(self._cancellation) if self._cancellation is not None else None
                ),
                "terminal": dict(self._terminal) if self._terminal is not None else None,
                "timing_contract": (
                    dict(self._timing_contract)
                    if self._timing_contract is not None
                    else None
                ),
            }
            if self._provenance:
                snapshot["provenance"] = {
                    stage: {
                        **dict(entry),
                        "attributes": dict(entry.get("attributes") or {}),
                    }
                    for stage, entry in self._provenance.items()
                }
                snapshot["required_stage_completeness"] = self.required_stage_completeness()
            return snapshot

    def slo_snapshot(self) -> dict[str, Any]:
        """Return the allowlist-only state consumed by realtime SLO aggregation."""

        with self._lock:
            stages = {stage: self._marks[stage].monotonic_ns for stage in PIPELINE_STAGES if stage in self._marks}
            lane = self._lane
            retry_count = self._retry_count
            cancelled = self._cancelled
        return {
            "trace_id": self.trace_id,
            "meeting_id": self.meeting_id,
            "lane": lane or "unknown",
            "stages": stages,
            "retry_count": retry_count,
            "cancelled": cancelled,
            "execution": self.execution_snapshot(),
        }

    def _validate_stage_order(self, stage: str, timestamp: int) -> None:
        stage_index = _STAGE_INDEX[stage]
        earlier = [mark for recorded_stage, mark in self._marks.items() if _STAGE_INDEX[recorded_stage] < stage_index]
        later = [mark for recorded_stage, mark in self._marks.items() if _STAGE_INDEX[recorded_stage] > stage_index]
        if earlier and timestamp < max(mark.monotonic_ns for mark in earlier):
            raise ValueError(f"{stage} timestamp precedes the latest recorded stage")
        if later and timestamp > min(mark.monotonic_ns for mark in later):
            raise ValueError(f"{stage} timestamp follows the earliest later stage")

    def latency_breakdown(self) -> dict[str, Any]:
        with self._lock:
            marks = [self._marks[stage] for stage in PIPELINE_STAGES if stage in self._marks]
            missing = [stage for stage in PIPELINE_STAGES if stage not in self._marks]

        if not marks:
            return {
                "complete": False,
                "missing_stages": missing,
                "total_ms": None,
                "stage_delta_ms": {},
                "from_start_ms": {},
                "transitions_ms": {},
            }

        first = marks[0]
        previous = first
        stage_delta_ms: dict[str, float] = {first.stage: 0.0}
        from_start_ms: dict[str, float] = {first.stage: 0.0}
        transitions_ms: dict[str, float] = {}
        for mark in marks[1:]:
            delta_ms = _milliseconds(mark.monotonic_ns - previous.monotonic_ns)
            stage_delta_ms[mark.stage] = delta_ms
            from_start_ms[mark.stage] = _milliseconds(mark.monotonic_ns - first.monotonic_ns)
            transitions_ms[f"{previous.stage}->{mark.stage}"] = delta_ms
            previous = mark

        return {
            "complete": not missing,
            "missing_stages": missing,
            "total_ms": _milliseconds(marks[-1].monotonic_ns - first.monotonic_ns),
            "stage_delta_ms": stage_delta_ms,
            "from_start_ms": from_start_ms,
            "transitions_ms": transitions_ms,
        }

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            job_id = self._job_id
            generation_id = self._generation_id
            stages = {stage: self._marks[stage].to_dict() for stage in PIPELINE_STAGES if stage in self._marks}
        return {
            "trace_id": self.trace_id,
            "meeting_id": self.meeting_id,
            "job_id": job_id,
            "generation_id": generation_id,
            "stages": stages,
            "latency": self.latency_breakdown(),
            "retry_count": self._retry_count,
            "cancelled": self._cancelled,
            "execution": self.execution_snapshot(),
        }


class PipelineTraceCollector:
    """Thread-safe bounded in-memory collector for lightweight pipeline traces."""

    def __init__(
        self,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        max_traces: int = DEFAULT_MAX_TRACES,
        on_evict: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if isinstance(max_traces, bool) or not isinstance(max_traces, int):
            raise TypeError("max_traces must be an integer")
        if max_traces <= 0:
            raise ValueError("max_traces must be positive")
        if on_evict is not None and not callable(on_evict):
            raise TypeError("on_evict must be callable")
        self._clock_ns = clock_ns
        self._max_traces = max_traces
        self._on_evict = on_evict
        self._traces: OrderedDict[str, PipelineTrace] = OrderedDict()
        self._lock = RLock()

    def create(
        self,
        *,
        trace_id: str,
        meeting_id: str,
        job_id: str | None = None,
        generation_id: str | None = None,
    ) -> PipelineTrace:
        trace_id = _required(trace_id, "trace_id")
        meeting_id = _required(meeting_id, "meeting_id")
        job_id = _optional_id(job_id, "job_id")
        generation_id = _optional_id(generation_id, "generation_id")

        with self._lock:
            trace, evicted = self._create_locked(
                trace_id=trace_id,
                meeting_id=meeting_id,
                job_id=job_id,
                generation_id=generation_id,
            )
        self._notify_evicted(evicted)
        return trace

    def _create_locked(
        self,
        *,
        trace_id: str,
        meeting_id: str,
        job_id: str | None,
        generation_id: str | None,
    ) -> tuple[PipelineTrace, list[dict[str, Any]]]:
        existing = self._traces.get(trace_id)
        if existing is not None:
            if existing.meeting_id != meeting_id:
                raise ValueError(f"trace_id {trace_id!r} is already associated with meeting_id {existing.meeting_id!r}")
            return existing.bind(job_id=job_id, generation_id=generation_id), []
        trace = PipelineTrace(
            trace_id=trace_id,
            meeting_id=meeting_id,
            job_id=job_id,
            generation_id=generation_id,
            clock_ns=self._clock_ns,
        )
        self._traces[trace_id] = trace
        evicted: list[dict[str, Any]] = []
        while len(self._traces) > self._max_traces:
            _, oldest = self._traces.popitem(last=False)
            evicted.append(oldest.slo_snapshot())
        return trace, evicted

    def _notify_evicted(self, snapshots: list[dict[str, Any]]) -> None:
        if self._on_evict is None:
            return
        for snapshot in snapshots:
            try:
                self._on_evict(snapshot)
            except Exception as exc:
                _log.error(
                    "Pipeline trace eviction sink failed; error_class=%s",
                    type(exc).__name__,
                )

    def get(self, trace_id: str) -> PipelineTrace:
        trace_id = _required(trace_id, "trace_id")
        with self._lock:
            try:
                return self._traces[trace_id]
            except KeyError as exc:
                raise KeyError(f"unknown pipeline trace: {trace_id!r}") from exc

    def record(
        self,
        trace_id: str,
        stage: str,
        *,
        meeting_id: str,
        job_id: str | None = None,
        generation_id: str | None = None,
        monotonic_ns: int | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> StageMark:
        trace_id = _required(trace_id, "trace_id")
        meeting_id = _required(meeting_id, "meeting_id")
        job_id = _optional_id(job_id, "job_id")
        generation_id = _optional_id(generation_id, "generation_id")
        evicted: list[dict[str, Any]] = []
        try:
            with self._lock:
                trace, evicted = self._create_locked(
                    trace_id=trace_id,
                    meeting_id=meeting_id,
                    job_id=job_id,
                    generation_id=generation_id,
                )
                mark = trace.mark(
                    stage,
                    monotonic_ns=monotonic_ns,
                    attributes=attributes,
                )
        finally:
            self._notify_evicted(evicted)
        return mark

    def observe(
        self,
        trace_id: str,
        stage: str,
        *,
        meeting_id: str,
        job_id: str | None = None,
        generation_id: str | None = None,
        monotonic_ns: int | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> StageMark | None:
        """Record an operational metric without allowing it to stop product work."""

        try:
            return self.record(
                trace_id,
                stage,
                meeting_id=meeting_id,
                job_id=job_id,
                generation_id=generation_id,
                monotonic_ns=monotonic_ns,
                attributes=attributes,
            )
        except (TypeError, ValueError) as exc:
            _log.warning(
                "Pipeline trace observation dropped; stage=%s error_class=%s",
                stage,
                type(exc).__name__,
            )
            return None

    def record_retry(self, trace_id: str, *, count: int = 1) -> int:
        with self._lock:
            return self.get(trace_id).record_retry(count=count)

    def record_route(
        self,
        trace_id: str,
        route: str,
        *,
        candidate_outcome: str | None = None,
        circuit_outcome: str | None = None,
        monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        return self.get(trace_id).record_route(
            route,
            candidate_outcome=candidate_outcome,
            circuit_outcome=circuit_outcome,
            monotonic_ns=monotonic_ns,
        )

    def record_provenance_stage(
        self,
        trace_id: str,
        stage: str,
        *,
        status: str = "observed",
        reason: str | None = None,
        monotonic_ns: int | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.get(trace_id).record_provenance_stage(
            stage,
            status=status,
            reason=reason,
            monotonic_ns=monotonic_ns,
            attributes=attributes,
        )

    def record_failure_provenance(
        self,
        trace_id: str,
        stage: str,
        *,
        reason: str,
        monotonic_ns: int | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.get(trace_id).record_failure_provenance(
            stage,
            reason=reason,
            monotonic_ns=monotonic_ns,
            attributes=attributes,
        )

    def required_stage_completeness(self, trace_id: str) -> dict[str, Any]:
        return self.get(trace_id).required_stage_completeness()

    def record_provider_attempt(
        self,
        trace_id: str,
        attempt_index: int,
        *,
        branch: str,
        runtime: str | None = None,
        monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        return self.get(trace_id).record_provider_attempt(
            attempt_index,
            branch=branch,
            runtime=runtime,
            monotonic_ns=monotonic_ns,
        )

    def record_provider_attempt_outcome(
        self,
        trace_id: str,
        attempt_index: int,
        outcome: str,
        *,
        branch: str = "unknown",
        runtime: str | None = None,
        http_status: int | None = None,
        error_class: str | None = None,
        monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        return self.get(trace_id).record_provider_attempt_outcome(
            attempt_index,
            outcome,
            branch=branch,
            runtime=runtime,
            http_status=http_status,
            error_class=error_class,
            monotonic_ns=monotonic_ns,
        )

    def open_provider_attempt_indices(self, trace_id: str) -> tuple[int, ...]:
        return self.get(trace_id).open_provider_attempt_indices()

    def revise_provider_attempt_validation(
        self,
        trace_id: str,
        attempt_index: int,
        *,
        branch: str = "unknown",
        runtime: str | None = None,
        error_class: str | None = None,
    ) -> dict[str, Any]:
        return self.get(trace_id).revise_provider_attempt_validation(
            attempt_index,
            branch=branch,
            runtime=runtime,
            error_class=error_class,
        )

    def record_timing_contract(
        self,
        trace_id: str,
        normalization: ProviderTimingNormalization,
        *,
        additional_invalid_reasons: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        return self.get(trace_id).record_timing_contract(
            normalization,
            additional_invalid_reasons=additional_invalid_reasons,
        )

    def record_terminal(
        self,
        trace_id: str,
        outcome: str,
        *,
        result_outcome: str | None = None,
        error_class: str | None = None,
        http_status: int | None = None,
        monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        return self.get(trace_id).record_terminal(
            outcome,
            result_outcome=result_outcome,
            error_class=error_class,
            http_status=http_status,
            monotonic_ns=monotonic_ns,
        )

    def record_cancelled(
        self,
        trace_id: str,
        *,
        result_outcome: str = "cancelled",
        error_class: str | None = None,
        monotonic_ns: int | None = None,
    ) -> bool:
        with self._lock:
            trace = self.get(trace_id)
            if (
                result_outcome == "cancelled"
                and error_class is None
                and monotonic_ns is None
            ):
                return trace.record_cancelled()
            return trace.record_cancellation_requested(
                result_outcome=result_outcome,
                error_class=error_class,
                monotonic_ns=monotonic_ns,
            )

    def record_cancellation_requested(
        self,
        trace_id: str,
        *,
        result_outcome: str = "cancelled",
        terminal_outcome: str | None = None,
        error_class: str | None = None,
        monotonic_ns: int | None = None,
    ) -> bool:
        return self.get(trace_id).record_cancellation_requested(
            result_outcome=result_outcome,
            terminal_outcome=terminal_outcome,
            error_class=error_class,
            monotonic_ns=monotonic_ns,
        )

    def record_abort_ack(
        self,
        trace_id: str,
        *,
        local: bool | None = None,
        remote: bool | None = None,
        remote_unavailable: bool | None = None,
    ) -> dict[str, Any]:
        return self.get(trace_id).record_abort_ack(
            local=local,
            remote=remote,
            remote_unavailable=remote_unavailable,
        )

    def find(
        self,
        *,
        meeting_id: str | None = None,
        job_id: str | None = None,
        generation_id: str | None = None,
    ) -> list[PipelineTrace]:
        meeting_id = _optional_id(meeting_id, "meeting_id")
        job_id = _optional_id(job_id, "job_id")
        generation_id = _optional_id(generation_id, "generation_id")
        with self._lock:
            traces = list(self._traces.values())
        return [
            trace
            for trace in traces
            if (meeting_id is None or trace.meeting_id == meeting_id)
            and (job_id is None or trace.job_id == job_id)
            and (generation_id is None or trace.generation_id == generation_id)
        ]

    def export(self, trace_id: str) -> dict[str, Any]:
        return self.get(trace_id).to_dict()

    def export_all(self) -> list[dict[str, Any]]:
        with self._lock:
            traces = list(self._traces.values())
        return [trace.to_dict() for trace in traces]

    def slo_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            traces = list(self._traces.values())
        return [trace.slo_snapshot() for trace in traces]

    def __len__(self) -> int:
        with self._lock:
            return len(self._traces)
