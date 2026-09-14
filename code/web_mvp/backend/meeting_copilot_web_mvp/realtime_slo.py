"""Bounded, content-free aggregation for realtime AI pipeline SLOs."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Iterable, Mapping
import json
import math
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

from .pipeline_trace import (
    PIPELINE_STAGES,
    PROVENANCE_ACCOUNTED_STATUSES,
    PROVENANCE_STATUSES,
    PROVIDER_ATTEMPT_OUTCOMES,
    REQUIRED_PROVENANCE_STAGES,
    RESULT_OUTCOMES,
    TERMINAL_OUTCOMES,
    TIMING_CONTRACT_STATUSES,
)


SCHEMA_VERSION = "meeting_copilot.realtime_ai_slo.v2"
STORE_SCHEMA_VERSION = "meeting_copilot.realtime_ai_slo_store.v2"
LEGACY_STORE_SCHEMA_VERSIONS = frozenset({"meeting_copilot.realtime_ai_slo_store.v1"})
DEFAULT_MAX_SAMPLES_PER_METRIC = 2_048
DEFAULT_MAX_MEETINGS = 256
DEFAULT_MAX_CHECKPOINT_TRACES = 2_048

METRIC_STAGE_PAIRS = {
    "final_to_first_token_ms": ("final_committed", "first_token"),
    "final_to_event_emitted_ms": ("final_committed", "event_emitted"),
    "queue_wait_ms": ("job_queued", "job_claimed"),
    "provider_ttft_ms": ("provider_connected", "first_token"),
    "provider_total_ms": ("provider_connected", "provider_completed"),
    "event_to_ui_ms": ("event_emitted", "ui_rendered"),
}

# P95 targets follow the accepted product latency windows. Component targets
# make a failed end-to-end verdict diagnosable without redefining the product SLO.
DEFAULT_REALTIME_SLO_THRESHOLDS_MS: dict[str, dict[str, float]] = {
    "correction": {
        "final_to_first_token_ms": 5_000.0,
        "final_to_event_emitted_ms": 6_000.0,
        "queue_wait_ms": 2_000.0,
        "provider_ttft_ms": 3_000.0,
        "provider_total_ms": 5_000.0,
        "event_to_ui_ms": 1_000.0,
    },
    "intelligence": {
        "final_to_first_token_ms": 8_000.0,
        "final_to_event_emitted_ms": 10_000.0,
        "queue_wait_ms": 4_000.0,
        "provider_ttft_ms": 4_000.0,
        "provider_total_ms": 8_000.0,
        "event_to_ui_ms": 1_000.0,
    },
    "suggestion": {
        "final_to_first_token_ms": 10_000.0,
        "final_to_event_emitted_ms": 15_000.0,
        "queue_wait_ms": 4_000.0,
        "provider_ttft_ms": 5_000.0,
        "provider_total_ms": 12_000.0,
        "event_to_ui_ms": 1_000.0,
    },
}


def _required(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _optional_non_negative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    return _non_negative_int(value, field)


def _bounded_token(value: Any, field: str, *, max_length: int = 120) -> str:
    normalized = _required(value, field)
    if len(normalized) > max_length:
        raise ValueError(f"{field} must not exceed {max_length} characters")
    if not all(character.isalnum() or character in "._:-" for character in normalized):
        raise ValueError(f"{field} must be a content-free identifier")
    return normalized


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _normalized_provenance(
    raw_provenance: Any,
) -> tuple[dict[str, dict[str, Any]] | None, dict[str, Any] | None]:
    """Normalize the content-free provenance extension.

    ``None`` means the trace predates the extension and keeps legacy SLO
    semantics.  Once the key is present, omitted required stages are treated
    as ``not_observed`` so a telemetry gap cannot silently become success.
    """

    if raw_provenance is None:
        return None, None
    if not isinstance(raw_provenance, Mapping):
        raise TypeError("trace provenance must be a mapping")
    provenance: dict[str, dict[str, Any]] = {}
    for raw_stage, raw_entry in raw_provenance.items():
        stage = _bounded_token(raw_stage, "provenance stage")
        if stage not in REQUIRED_PROVENANCE_STAGES:
            raise ValueError(f"unsupported provenance stage: {stage!r}")
        if not isinstance(raw_entry, Mapping):
            raise TypeError("provenance stage must be a mapping")
        status = _bounded_token(raw_entry.get("status"), "provenance status")
        if status not in PROVENANCE_STATUSES:
            raise ValueError(f"unsupported provenance status: {status!r}")
        reason = (
            _bounded_token(raw_entry.get("reason"), "provenance reason")
            if raw_entry.get("reason") is not None
            else None
        )
        at_monotonic_ns = _optional_non_negative_int(
            raw_entry.get("at_monotonic_ns"),
            "provenance at_monotonic_ns",
        )
        raw_attributes = raw_entry.get("attributes", {})
        if raw_attributes is None:
            raw_attributes = {}
        if not isinstance(raw_attributes, Mapping):
            raise TypeError("provenance attributes must be a mapping")
        # Validate and retain only bounded scalar values.  This mirrors the
        # writer-side allowlist and ensures an imported snapshot cannot inject
        # transcript content or arbitrary nested data into diagnostics.
        attributes: dict[str, Any] = {}
        for raw_key, raw_value in raw_attributes.items():
            key = _bounded_token(raw_key, "provenance attribute key")
            if raw_value is None or isinstance(raw_value, bool):
                attributes[key] = raw_value
            elif isinstance(raw_value, int) and not isinstance(raw_value, bool):
                if raw_value < 0:
                    raise ValueError("provenance integer attributes must be non-negative")
                attributes[key] = raw_value
            elif isinstance(raw_value, float):
                if not math.isfinite(raw_value) or raw_value < 0:
                    raise ValueError("provenance numeric attributes must be finite and non-negative")
                attributes[key] = round(raw_value, 6)
            elif isinstance(raw_value, str):
                attributes[key] = _bounded_token(raw_value, "provenance attribute value")
            else:
                raise TypeError("provenance attributes must be scalar")
        provenance[stage] = {
            "status": status,
            "reason": reason,
            "at_monotonic_ns": at_monotonic_ns,
            "attributes": attributes,
        }

    statuses = {
        stage: str(provenance.get(stage, {}).get("status") or "not_observed")
        for stage in REQUIRED_PROVENANCE_STAGES
    }
    missing = [stage for stage, status in statuses.items() if status == "not_observed"]
    failed = [stage for stage, status in statuses.items() if status == "failed"]
    unavailable = [stage for stage, status in statuses.items() if status == "unavailable"]
    not_required = [stage for stage, status in statuses.items() if status == "not_required"]
    observed = [stage for stage, status in statuses.items() if status == "observed"]
    required_count = len(REQUIRED_PROVENANCE_STAGES)
    accounted_count = sum(
        1 for status in statuses.values() if status in PROVENANCE_ACCOUNTED_STATUSES
    )
    completeness = {
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
    return provenance, completeness


def _milliseconds(duration_ns: int) -> float:
    return round(duration_ns / 1_000_000, 6)


def _percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = (len(ordered) - 1) * quantile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(float(ordered[lower]), 6)
    fraction = rank - lower
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return round(float(interpolated), 6)


def _outcome_rates(
    counts: Mapping[str, int],
    denominator: int,
) -> dict[str, float | None]:
    """Return bounded per-outcome rates without turning an empty sample into 0."""

    if denominator <= 0:
        return {str(outcome): None for outcome in counts}
    return {
        str(outcome): round(float(count) / float(denominator), 6)
        for outcome, count in counts.items()
    }


def _named_outcome_rates(
    counts: Mapping[str, int],
    denominator: int,
    *,
    incomplete_count: int = 0,
) -> dict[str, Any]:
    """Expose stable rate aliases alongside the complete outcome map.

    The denominator is always the corresponding request/attempt count.  For
    attempts this means an unfinished attempt is still part of the failure
    rate, with its own explicit ``incomplete_rate`` field.
    """

    rates = _outcome_rates(counts, denominator)
    if denominator <= 0:
        success_rate = failure_rate = None
        incomplete_rate = None
    else:
        success_count = int(counts.get("success", 0))
        success_rate = round(float(success_count) / float(denominator), 6)
        failure_rate = round(
            float(max(0, denominator - success_count)) / float(denominator),
            6,
        )
        incomplete_rate = round(float(incomplete_count) / float(denominator), 6)
    return {
        "rates": rates,
        "success_rate": success_rate,
        "failure_rate": failure_rate,
        "timeout_rate": rates.get("timeout"),
        "rate_limit_rate": rates.get("rate_limit"),
        "provider_5xx_rate": rates.get("provider_5xx"),
        "transport_error_rate": rates.get("transport_error"),
        "cancelled_rate": rates.get("cancelled"),
        "incomplete_rate": incomplete_rate,
    }


def _normalized_trace(trace: Any) -> dict[str, Any]:
    if not isinstance(trace, Mapping):
        snapshot = getattr(trace, "slo_snapshot", None)
        if not callable(snapshot):
            raise TypeError("trace must be a mapping or expose slo_snapshot()")
        trace = snapshot()

    trace_id = _required(trace.get("trace_id"), "trace_id")
    meeting_id = _required(trace.get("meeting_id"), "meeting_id")
    lane_value = trace.get("lane")
    raw_stages = trace.get("stages")
    if not isinstance(raw_stages, Mapping):
        raise TypeError("trace stages must be a mapping")

    stages: dict[str, int] = {}
    inferred_lane: str | None = None
    for stage in PIPELINE_STAGES:
        if stage not in raw_stages:
            continue
        raw_mark = raw_stages[stage]
        if isinstance(raw_mark, Mapping):
            timestamp = raw_mark.get("monotonic_ns")
            attributes = raw_mark.get("attributes")
            if isinstance(attributes, Mapping) and attributes.get("lane") is not None:
                inferred_lane = _required(attributes.get("lane"), "lane")
        else:
            timestamp = raw_mark
        stages[stage] = _non_negative_int(timestamp, f"{stage} monotonic_ns")

    lane = _required(lane_value if lane_value is not None else inferred_lane or "unknown", "lane")
    retry_count = _non_negative_int(trace.get("retry_count", 0), "retry_count")
    cancelled = trace.get("cancelled", False)
    if not isinstance(cancelled, bool):
        raise TypeError("cancelled must be a boolean")
    execution = _normalized_execution(trace.get("execution"), stages=stages, cancelled=cancelled)
    return {
        "trace_id": trace_id,
        "meeting_id": meeting_id,
        "lane": lane,
        "stages": stages,
        "retry_count": retry_count,
        "cancelled": cancelled,
        "execution": execution,
    }


def _normalized_execution(
    raw_execution: Any,
    *,
    stages: Mapping[str, int],
    cancelled: bool,
) -> dict[str, Any]:
    if raw_execution is None:
        raw_execution = {}
    if not isinstance(raw_execution, Mapping):
        raise TypeError("trace execution must be a mapping")

    raw_route = raw_execution.get("route")
    route: dict[str, Any] | None = None
    if raw_route is not None:
        if not isinstance(raw_route, Mapping):
            raise TypeError("trace route must be a mapping")
        route = {
            "name": _bounded_token(raw_route.get("name"), "route name"),
            "candidate_outcome": (
                _bounded_token(raw_route.get("candidate_outcome"), "candidate outcome")
                if raw_route.get("candidate_outcome") is not None
                else None
            ),
            "circuit_outcome": (
                _bounded_token(raw_route.get("circuit_outcome"), "circuit outcome")
                if raw_route.get("circuit_outcome") is not None
                else None
            ),
        }

    raw_attempts = raw_execution.get("provider_attempts", [])
    if not isinstance(raw_attempts, list):
        raise TypeError("provider_attempts must be a list")
    attempts: list[dict[str, Any]] = []
    seen_attempts: set[int] = set()
    for raw_attempt in raw_attempts:
        if not isinstance(raw_attempt, Mapping):
            raise TypeError("Provider attempt must be a mapping")
        attempt_index = _non_negative_int(raw_attempt.get("attempt_index"), "attempt_index")
        if attempt_index <= 0:
            raise ValueError("attempt_index must be positive")
        if attempt_index in seen_attempts:
            raise ValueError("Provider attempt indexes must be unique")
        seen_attempts.add(attempt_index)
        raw_outcome = raw_attempt.get("outcome")
        outcome = None
        if raw_outcome is not None:
            outcome = _bounded_token(raw_outcome, "Provider attempt outcome")
            if outcome not in PROVIDER_ATTEMPT_OUTCOMES:
                raise ValueError(f"unsupported Provider attempt outcome: {outcome!r}")
        raw_status = raw_attempt.get("http_status")
        http_status = _optional_non_negative_int(raw_status, "Provider HTTP status")
        if http_status is not None and not 100 <= http_status <= 599:
            raise ValueError("Provider HTTP status must be between 100 and 599")
        attempts.append(
            {
                "attempt_index": attempt_index,
                "branch": _bounded_token(raw_attempt.get("branch") or "unknown", "attempt branch"),
                "runtime": (
                    _bounded_token(raw_attempt.get("runtime"), "attempt runtime")
                    if raw_attempt.get("runtime") is not None
                    else None
                ),
                "outcome": outcome,
                "http_status": http_status,
                "error_class": (
                    _bounded_token(raw_attempt.get("error_class"), "attempt error class")
                    if raw_attempt.get("error_class") is not None
                    else None
                ),
            }
        )
    attempts.sort(key=lambda item: item["attempt_index"])

    raw_terminal = raw_execution.get("terminal")
    terminal: dict[str, Any] | None = None
    if raw_terminal is not None:
        if not isinstance(raw_terminal, Mapping):
            raise TypeError("trace terminal must be a mapping")
        outcome = _bounded_token(raw_terminal.get("outcome"), "terminal outcome")
        if outcome not in TERMINAL_OUTCOMES:
            raise ValueError(f"unsupported terminal outcome: {outcome!r}")
        raw_result = raw_terminal.get("result_outcome")
        result_outcome = None
        if raw_result is not None:
            result_outcome = _bounded_token(raw_result, "result outcome")
            if result_outcome not in RESULT_OUTCOMES:
                raise ValueError(f"unsupported result outcome: {result_outcome!r}")
        raw_terminal_status = raw_terminal.get("http_status")
        terminal_status = _optional_non_negative_int(
            raw_terminal_status,
            "terminal HTTP status",
        )
        if terminal_status is not None and not 100 <= terminal_status <= 599:
            raise ValueError("terminal HTTP status must be between 100 and 599")
        terminal = {
            "outcome": outcome,
            # A terminal failure without a separate result taxonomy is still
            # a failed request and must enter the availability denominator.
            "result_outcome": result_outcome
            or (
                outcome
                if outcome in RESULT_OUTCOMES
                else "cancelled"
                if outcome in {"cancelled", "superseded"}
                else None
            ),
            "error_class": (
                _bounded_token(raw_terminal.get("error_class"), "terminal error class")
                if raw_terminal.get("error_class") is not None
                else None
            ),
            "http_status": terminal_status,
        }

    raw_cancellation = raw_execution.get("cancellation")
    cancellation: dict[str, Any] | None = None
    if raw_cancellation is not None:
        if not isinstance(raw_cancellation, Mapping):
            raise TypeError("trace cancellation must be a mapping")
        cancellation = {
            "requested": bool(raw_cancellation.get("requested", False)),
            "local_abort_ack": _optional_bool(
                raw_cancellation.get("local_abort_ack"),
                "local_abort_ack",
            ),
            "remote_abort_ack": _optional_bool(
                raw_cancellation.get("remote_abort_ack"),
                "remote_abort_ack",
            ),
            "remote_ack_unavailable": _optional_bool(
                raw_cancellation.get("remote_ack_unavailable"),
                "remote_ack_unavailable",
            ),
        }
        if cancellation["remote_abort_ack"] and cancellation["remote_ack_unavailable"]:
            raise ValueError(
                "remote abort acknowledgement and unavailable are mutually exclusive"
            )

    provenance, required_stage_completeness = _normalized_provenance(
        raw_execution.get("provenance")
    )

    raw_timing = raw_execution.get("timing_contract")
    timing_contract: dict[str, Any] | None = None
    if raw_timing is not None:
        if not isinstance(raw_timing, Mapping):
            raise TypeError("timing_contract must be a mapping")
        status = _bounded_token(raw_timing.get("status"), "timing contract status")
        if status not in TIMING_CONTRACT_STATUSES:
            raise ValueError(f"unsupported timing contract status: {status!r}")
        raw_reasons = raw_timing.get("invalid_reasons", [])
        if not isinstance(raw_reasons, list):
            raise TypeError("timing invalid_reasons must be a list")
        timing_contract = {
            "status": status,
            "source_clock": (
                _bounded_token(raw_timing.get("source_clock"), "timing source clock")
                if raw_timing.get("source_clock") is not None
                else None
            ),
            "invalid_reasons": [
                _bounded_token(reason, "timing invalid reason") for reason in raw_reasons
            ],
        }

    # Old snapshots predate execution outcomes. A cancellation flag does not
    # distinguish deadline, supersession, user cancellation, or shutdown, so
    # keep it as an unclassified counter. Only infer success when both Provider
    # completion and durable event projection were already observed.
    if terminal is None and {"provider_completed", "event_emitted"} <= set(stages):
        terminal = {"outcome": "success", "result_outcome": "success"}
    if not attempts and {"provider_connected", "provider_completed"} <= set(stages):
        attempts = [
            {
                "attempt_index": 1,
                "branch": "legacy",
                "runtime": None,
                "outcome": "success",
                "http_status": None,
                "error_class": None,
            }
        ]
    if timing_contract is None:
        observed_provider_stages = set(stages).intersection(
            {"provider_connected", "first_token", "provider_completed"}
        )
        if len(observed_provider_stages) == 3:
            timing_contract = {
                "status": "complete",
                "source_clock": "legacy_monotonic_ns",
                "invalid_reasons": [],
            }
        elif observed_provider_stages:
            timing_contract = {
                "status": "partial",
                "source_clock": "legacy_monotonic_ns",
                "invalid_reasons": ["legacy_partial_provider_timing"],
            }

    return {
        "route": route,
        "provider_attempts": attempts,
        "cancellation": cancellation,
        "terminal": terminal,
        "timing_contract": timing_contract,
        "provenance": provenance,
        "required_stage_completeness": required_stage_completeness,
    }


class _MetricAccumulator:
    def __init__(self, max_samples: int) -> None:
        self.max_samples = max_samples
        self.count = 0
        self.max_ms: float | None = None
        self.samples: deque[float] = deque(maxlen=max_samples)

    def add(self, value: float) -> None:
        normalized = round(float(value), 6)
        if not math.isfinite(normalized) or normalized < 0:
            raise ValueError("latency metric must be a finite non-negative number")
        self.count += 1
        self.max_ms = normalized if self.max_ms is None else max(self.max_ms, normalized)
        self.samples.append(normalized)

    def merge(self, other: _MetricAccumulator) -> None:
        self.count += other.count
        if other.max_ms is not None:
            self.max_ms = other.max_ms if self.max_ms is None else max(self.max_ms, other.max_ms)
        self.samples.extend(other.samples)

    def summary(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "p50_ms": _percentile(self.samples, 0.50),
            "p95_ms": _percentile(self.samples, 0.95),
            "max_ms": self.max_ms,
        }

    def to_state(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "max_ms": self.max_ms,
            "samples": list(self.samples),
        }

    @classmethod
    def from_state(cls, value: Any, *, max_samples: int) -> _MetricAccumulator:
        if not isinstance(value, Mapping):
            raise TypeError("persisted metric must be a mapping")
        metric = cls(max_samples)
        metric.count = _non_negative_int(value.get("count", 0), "metric count")
        max_ms = value.get("max_ms")
        if max_ms is not None:
            if (
                isinstance(max_ms, bool)
                or not isinstance(max_ms, (int, float))
                or not math.isfinite(float(max_ms))
                or max_ms < 0
            ):
                raise ValueError("persisted metric max_ms must be a finite non-negative number")
            metric.max_ms = round(float(max_ms), 6)
        raw_samples = value.get("samples", [])
        if not isinstance(raw_samples, list):
            raise TypeError("persisted metric samples must be a list")
        for sample in raw_samples[-max_samples:]:
            if (
                isinstance(sample, bool)
                or not isinstance(sample, (int, float))
                or not math.isfinite(float(sample))
                or sample < 0
            ):
                raise ValueError("persisted metric sample must be a finite non-negative number")
            metric.samples.append(round(float(sample), 6))
        if metric.count < len(metric.samples):
            raise ValueError("persisted metric count is smaller than retained samples")
        if metric.count == 0 and metric.max_ms is not None:
            raise ValueError("empty persisted metric must not have max_ms")
        return metric


class _LaneAccumulator:
    def __init__(self, max_samples: int) -> None:
        self.max_samples = max_samples
        self.count = 0
        self.missing_trace_count = 0
        self.missing_stage_counts = {stage: 0 for stage in PIPELINE_STAGES}
        self.cancelled_count = 0
        self.retry_count = 0
        self.job_terminal_count = 0
        self.job_outcome_counts = {outcome: 0 for outcome in TERMINAL_OUTCOMES}
        self.result_outcome_counts = {outcome: 0 for outcome in RESULT_OUTCOMES}
        self.provider_attempt_count = 0
        self.provider_attempt_outcome_counts = {
            outcome: 0 for outcome in PROVIDER_ATTEMPT_OUTCOMES
        }
        self.provider_attempt_incomplete_count = 0
        self.timing_contract_counts = {
            status: 0 for status in TIMING_CONTRACT_STATUSES
        }
        self.cancel_ack_required_count = 0
        self.local_abort_ack_count = 0
        self.remote_abort_ack_count = 0
        self.remote_abort_ack_unavailable_count = 0
        self.required_provenance_trace_count = 0
        self.required_provenance_complete_count = 0
        self.required_provenance_incomplete_count = 0
        self.provenance_stage_counts = {
            stage: {status: 0 for status in PROVENANCE_STATUSES}
            for stage in REQUIRED_PROVENANCE_STAGES
        }
        self.uncategorized_failure_count = 0
        self.metrics = {metric: _MetricAccumulator(max_samples) for metric in METRIC_STAGE_PAIRS}

    def add_trace(self, trace: Mapping[str, Any]) -> None:
        stages = trace["stages"]
        self.count += 1
        missing = [stage for stage in PIPELINE_STAGES if stage not in stages]
        if missing:
            self.missing_trace_count += 1
            for stage in missing:
                self.missing_stage_counts[stage] += 1
        if trace["cancelled"]:
            self.cancelled_count += 1
        self.retry_count += trace["retry_count"]

        execution = trace["execution"]
        provenance = execution.get("provenance")
        completeness = execution.get("required_stage_completeness")
        if provenance is not None:
            self.required_provenance_trace_count += 1
            if isinstance(completeness, Mapping) and bool(completeness.get("complete")):
                self.required_provenance_complete_count += 1
            else:
                self.required_provenance_incomplete_count += 1
            for stage in REQUIRED_PROVENANCE_STAGES:
                status = (
                    str((provenance.get(stage) or {}).get("status") or "not_observed")
                    if isinstance(provenance, Mapping)
                    else "not_observed"
                )
                if status not in PROVENANCE_STATUSES:
                    status = "not_observed"
                self.provenance_stage_counts[stage][status] += 1

        terminal = execution.get("terminal")
        if terminal is not None:
            self.job_terminal_count += 1
            self.job_outcome_counts[terminal["outcome"]] += 1
            result_outcome = terminal.get("result_outcome")
            if result_outcome is not None:
                self.result_outcome_counts[result_outcome] += 1
            if terminal["outcome"] != "success" and not result_outcome:
                self.uncategorized_failure_count += 1
        for attempt in execution.get("provider_attempts", []):
            self.provider_attempt_count += 1
            attempt_outcome = attempt.get("outcome")
            if attempt_outcome is None:
                self.provider_attempt_incomplete_count += 1
            else:
                self.provider_attempt_outcome_counts[attempt_outcome] += 1
                if attempt_outcome != "success" and not attempt.get("error_class"):
                    self.uncategorized_failure_count += 1
        timing_contract = execution.get("timing_contract")
        if timing_contract is not None:
            self.timing_contract_counts[timing_contract["status"]] += 1
        cancellation = execution.get("cancellation")
        if cancellation is not None and cancellation.get("requested"):
            self.cancel_ack_required_count += 1
            if cancellation.get("local_abort_ack") is True:
                self.local_abort_ack_count += 1
            if cancellation.get("remote_abort_ack") is True:
                self.remote_abort_ack_count += 1
            if cancellation.get("remote_ack_unavailable") is True:
                self.remote_abort_ack_unavailable_count += 1

        for metric, (start_stage, end_stage) in METRIC_STAGE_PAIRS.items():
            if start_stage not in stages or end_stage not in stages:
                continue
            duration_ns = stages[end_stage] - stages[start_stage]
            if duration_ns < 0:
                raise ValueError(f"{end_stage} timestamp precedes {start_stage}")
            self.metrics[metric].add(_milliseconds(duration_ns))

    def merge(self, other: _LaneAccumulator) -> None:
        self.count += other.count
        self.missing_trace_count += other.missing_trace_count
        self.cancelled_count += other.cancelled_count
        self.retry_count += other.retry_count
        self.job_terminal_count += other.job_terminal_count
        self.provider_attempt_count += other.provider_attempt_count
        self.provider_attempt_incomplete_count += other.provider_attempt_incomplete_count
        self.cancel_ack_required_count += other.cancel_ack_required_count
        self.local_abort_ack_count += other.local_abort_ack_count
        self.remote_abort_ack_count += other.remote_abort_ack_count
        self.remote_abort_ack_unavailable_count += other.remote_abort_ack_unavailable_count
        self.required_provenance_trace_count += other.required_provenance_trace_count
        self.required_provenance_complete_count += other.required_provenance_complete_count
        self.required_provenance_incomplete_count += other.required_provenance_incomplete_count
        self.uncategorized_failure_count += other.uncategorized_failure_count
        for outcome in TERMINAL_OUTCOMES:
            self.job_outcome_counts[outcome] += other.job_outcome_counts[outcome]
        for outcome in RESULT_OUTCOMES:
            self.result_outcome_counts[outcome] += other.result_outcome_counts[outcome]
        for outcome in PROVIDER_ATTEMPT_OUTCOMES:
            self.provider_attempt_outcome_counts[outcome] += other.provider_attempt_outcome_counts[outcome]
        for status in TIMING_CONTRACT_STATUSES:
            self.timing_contract_counts[status] += other.timing_contract_counts[status]
        for stage in PIPELINE_STAGES:
            self.missing_stage_counts[stage] += other.missing_stage_counts[stage]
        for stage in REQUIRED_PROVENANCE_STAGES:
            for status in PROVENANCE_STATUSES:
                self.provenance_stage_counts[stage][status] += (
                    other.provenance_stage_counts[stage][status]
                )
        for metric in METRIC_STAGE_PAIRS:
            self.metrics[metric].merge(other.metrics[metric])

    def to_state(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "missing_trace_count": self.missing_trace_count,
            "missing_stage_counts": dict(self.missing_stage_counts),
            "cancelled_count": self.cancelled_count,
            "retry_count": self.retry_count,
            "job_terminal_count": self.job_terminal_count,
            "job_outcome_counts": dict(self.job_outcome_counts),
            "result_outcome_counts": dict(self.result_outcome_counts),
            "provider_attempt_count": self.provider_attempt_count,
            "provider_attempt_outcome_counts": dict(self.provider_attempt_outcome_counts),
            "provider_attempt_incomplete_count": self.provider_attempt_incomplete_count,
            "timing_contract_counts": dict(self.timing_contract_counts),
            "cancel_ack_required_count": self.cancel_ack_required_count,
            "local_abort_ack_count": self.local_abort_ack_count,
            "remote_abort_ack_count": self.remote_abort_ack_count,
            "remote_abort_ack_unavailable_count": self.remote_abort_ack_unavailable_count,
            "required_provenance_trace_count": self.required_provenance_trace_count,
            "required_provenance_complete_count": self.required_provenance_complete_count,
            "required_provenance_incomplete_count": self.required_provenance_incomplete_count,
            "provenance_stage_counts": {
                stage: dict(counts)
                for stage, counts in self.provenance_stage_counts.items()
            },
            "uncategorized_failure_count": self.uncategorized_failure_count,
            "metrics": {metric: accumulator.to_state() for metric, accumulator in self.metrics.items()},
        }

    @classmethod
    def from_state(cls, value: Any, *, max_samples: int) -> _LaneAccumulator:
        if not isinstance(value, Mapping):
            raise TypeError("persisted lane must be a mapping")
        lane = cls(max_samples)
        lane.count = _non_negative_int(value.get("count", 0), "lane count")
        lane.missing_trace_count = _non_negative_int(
            value.get("missing_trace_count", 0),
            "missing_trace_count",
        )
        lane.cancelled_count = _non_negative_int(value.get("cancelled_count", 0), "cancelled_count")
        lane.retry_count = _non_negative_int(value.get("retry_count", 0), "retry_count")
        lane.job_terminal_count = _non_negative_int(
            value.get("job_terminal_count", 0),
            "job_terminal_count",
        )
        lane.provider_attempt_count = _non_negative_int(
            value.get("provider_attempt_count", 0),
            "provider_attempt_count",
        )
        lane.provider_attempt_incomplete_count = _non_negative_int(
            value.get("provider_attempt_incomplete_count", 0),
            "provider_attempt_incomplete_count",
        )
        lane.cancel_ack_required_count = _non_negative_int(
            value.get("cancel_ack_required_count", 0),
            "cancel_ack_required_count",
        )
        lane.local_abort_ack_count = _non_negative_int(
            value.get("local_abort_ack_count", 0),
            "local_abort_ack_count",
        )
        lane.remote_abort_ack_count = _non_negative_int(
            value.get("remote_abort_ack_count", 0),
            "remote_abort_ack_count",
        )
        lane.remote_abort_ack_unavailable_count = _non_negative_int(
            value.get("remote_abort_ack_unavailable_count", 0),
            "remote_abort_ack_unavailable_count",
        )
        lane.required_provenance_trace_count = _non_negative_int(
            value.get("required_provenance_trace_count", 0),
            "required_provenance_trace_count",
        )
        lane.required_provenance_complete_count = _non_negative_int(
            value.get("required_provenance_complete_count", 0),
            "required_provenance_complete_count",
        )
        lane.required_provenance_incomplete_count = _non_negative_int(
            value.get("required_provenance_incomplete_count", 0),
            "required_provenance_incomplete_count",
        )
        lane.uncategorized_failure_count = _non_negative_int(
            value.get("uncategorized_failure_count", 0),
            "uncategorized_failure_count",
        )
        missing = value.get("missing_stage_counts", {})
        raw_provenance_stage_counts = value.get("provenance_stage_counts", {})
        metrics = value.get("metrics", {})
        raw_job_outcomes = value.get("job_outcome_counts", {})
        raw_result_outcomes = value.get("result_outcome_counts", {})
        raw_attempt_outcomes = value.get("provider_attempt_outcome_counts", {})
        raw_timing_contracts = value.get("timing_contract_counts", {})
        if not all(
            isinstance(item, Mapping)
            for item in (
                missing,
                metrics,
                raw_job_outcomes,
                raw_result_outcomes,
                raw_attempt_outcomes,
                raw_timing_contracts,
            )
        ):
            raise TypeError("persisted lane counters and metrics must be mappings")
        if not isinstance(raw_provenance_stage_counts, Mapping):
            raise TypeError("persisted provenance stage counts must be a mapping")
        lane.missing_stage_counts = {
            stage: _non_negative_int(missing.get(stage, 0), f"missing {stage} count") for stage in PIPELINE_STAGES
        }
        lane.provenance_stage_counts = {
            stage: {
                status: _non_negative_int(
                    (raw_provenance_stage_counts.get(stage, {}) or {}).get(status, 0),
                    f"provenance {stage} {status} count",
                )
                for status in PROVENANCE_STATUSES
            }
            for stage in REQUIRED_PROVENANCE_STAGES
        }
        lane.metrics = {
            metric: _MetricAccumulator.from_state(metrics.get(metric, {}), max_samples=max_samples)
            for metric in METRIC_STAGE_PAIRS
        }
        lane.job_outcome_counts = {
            outcome: _non_negative_int(raw_job_outcomes.get(outcome, 0), f"job {outcome} count")
            for outcome in TERMINAL_OUTCOMES
        }
        lane.result_outcome_counts = {
            outcome: _non_negative_int(raw_result_outcomes.get(outcome, 0), f"result {outcome} count")
            for outcome in RESULT_OUTCOMES
        }
        lane.provider_attempt_outcome_counts = {
            outcome: _non_negative_int(raw_attempt_outcomes.get(outcome, 0), f"attempt {outcome} count")
            for outcome in PROVIDER_ATTEMPT_OUTCOMES
        }
        lane.timing_contract_counts = {
            status: _non_negative_int(raw_timing_contracts.get(status, 0), f"timing {status} count")
            for status in TIMING_CONTRACT_STATUSES
        }
        return lane


class _SLOAccumulator:
    def __init__(self, *, max_samples: int, max_meetings: int) -> None:
        self.max_samples = max_samples
        self.max_meetings = max_meetings
        self.meetings: OrderedDict[str, OrderedDict[str, _LaneAccumulator]] = OrderedDict()

    def add_trace(self, trace: Mapping[str, Any]) -> None:
        meeting_id = trace["meeting_id"]
        lane_name = trace["lane"]
        lanes = self.meetings.setdefault(meeting_id, OrderedDict())
        self.meetings.move_to_end(meeting_id)
        lane = lanes.setdefault(lane_name, _LaneAccumulator(self.max_samples))
        lane.add_trace(trace)
        self._trim_meetings()

    def merge(self, other: _SLOAccumulator) -> None:
        for meeting_id, other_lanes in other.meetings.items():
            lanes = self.meetings.setdefault(meeting_id, OrderedDict())
            self.meetings.move_to_end(meeting_id)
            for lane_name, other_lane in other_lanes.items():
                lane = lanes.setdefault(lane_name, _LaneAccumulator(self.max_samples))
                lane.merge(other_lane)
            self._trim_meetings()

    def _trim_meetings(self) -> None:
        while len(self.meetings) > self.max_meetings:
            self.meetings.popitem(last=False)

    def clone(self) -> _SLOAccumulator:
        return self.from_state(
            self.to_state(),
            max_samples=self.max_samples,
            max_meetings=self.max_meetings,
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "meetings": [
                {
                    "meeting_id": meeting_id,
                    "lanes": [{"lane": lane_name, "state": lane.to_state()} for lane_name, lane in lanes.items()],
                }
                for meeting_id, lanes in self.meetings.items()
            ]
        }

    @classmethod
    def from_state(
        cls,
        value: Any,
        *,
        max_samples: int,
        max_meetings: int,
    ) -> _SLOAccumulator:
        if not isinstance(value, Mapping):
            raise TypeError("persisted SLO accumulator must be a mapping")
        accumulator = cls(max_samples=max_samples, max_meetings=max_meetings)
        meetings = value.get("meetings", [])
        if not isinstance(meetings, list):
            raise TypeError("persisted meetings must be a list")
        for meeting in meetings:
            if not isinstance(meeting, Mapping):
                raise TypeError("persisted meeting must be a mapping")
            meeting_id = _required(meeting.get("meeting_id"), "meeting_id")
            raw_lanes = meeting.get("lanes", [])
            if not isinstance(raw_lanes, list):
                raise TypeError("persisted lanes must be a list")
            lanes: OrderedDict[str, _LaneAccumulator] = OrderedDict()
            for raw_lane in raw_lanes:
                if not isinstance(raw_lane, Mapping):
                    raise TypeError("persisted lane entry must be a mapping")
                lane_name = _required(raw_lane.get("lane"), "lane")
                lanes[lane_name] = _LaneAccumulator.from_state(
                    raw_lane.get("state", {}),
                    max_samples=max_samples,
                )
            accumulator.meetings[meeting_id] = lanes
            accumulator._trim_meetings()
        return accumulator


def _normalized_thresholds(
    thresholds_ms: Mapping[str, Mapping[str, float]] | None,
) -> dict[str, dict[str, float]]:
    source = thresholds_ms or DEFAULT_REALTIME_SLO_THRESHOLDS_MS
    normalized: dict[str, dict[str, float]] = {}
    for lane, lane_thresholds in source.items():
        lane_name = _required(lane, "threshold lane")
        if not isinstance(lane_thresholds, Mapping):
            raise TypeError("lane thresholds must be a mapping")
        normalized[lane_name] = {}
        for metric, threshold in lane_thresholds.items():
            if metric not in METRIC_STAGE_PAIRS:
                raise ValueError(f"unsupported SLO metric: {metric!r}")
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not math.isfinite(float(threshold))
                or threshold < 0
            ):
                raise ValueError("SLO threshold must be a finite non-negative number")
            normalized[lane_name][metric] = float(threshold)
    return normalized


def _lane_verdict(
    lane_name: str,
    metrics: Mapping[str, Mapping[str, Any]],
    thresholds: Mapping[str, Mapping[str, float]],
    *,
    job_terminal_count: int,
    result_outcome_counts: Mapping[str, int],
    provider_attempt_count: int,
    provider_attempt_outcome_counts: Mapping[str, int],
    provider_attempt_incomplete_count: int,
    timing_contract_counts: Mapping[str, int],
    required_provenance_trace_count: int = 0,
    required_provenance_incomplete_count: int = 0,
    uncategorized_failure_count: int = 0,
) -> dict[str, Any]:
    lane_thresholds = thresholds.get(lane_name)
    if lane_thresholds is None:
        latency_verdict = {
            "status": "not_configured",
            "basis": "p95_ms",
            "metrics": {},
        }
    else:
        metric_verdicts: dict[str, dict[str, Any]] = {}
        statuses: list[str] = []
        for metric in METRIC_STAGE_PAIRS:
            threshold = lane_thresholds.get(metric)
            observed = metrics[metric]["p95_ms"]
            if threshold is None:
                metric_status = "not_configured"
            elif observed is None:
                metric_status = "no_data"
            else:
                metric_status = "pass" if observed <= threshold else "fail"
            metric_verdicts[metric] = {
                "status": metric_status,
                "threshold_ms": threshold,
                "observed_p95_ms": observed,
            }
            statuses.append(metric_status)

        if "fail" in statuses:
            latency_status = "fail"
        elif all(item == "no_data" for item in statuses):
            latency_status = "no_data"
        elif "no_data" in statuses or "not_configured" in statuses:
            latency_status = "insufficient_data"
        else:
            latency_status = "pass"
        latency_verdict = {
            "status": latency_status,
            "basis": "p95_ms",
            "metrics": metric_verdicts,
        }

    failed_result_count = sum(
        count for outcome, count in result_outcome_counts.items() if outcome != "success"
    )
    failed_attempt_count = sum(
        count for outcome, count in provider_attempt_outcome_counts.items() if outcome != "success"
    )
    invalid_timing_count = timing_contract_counts.get("invalid", 0)
    partial_timing_count = timing_contract_counts.get("partial", 0)
    if (
        failed_result_count
        or failed_attempt_count
        or provider_attempt_incomplete_count
        or invalid_timing_count
        or uncategorized_failure_count
        or (
            required_provenance_trace_count > 0
            and required_provenance_incomplete_count > 0
            and (failed_result_count or failed_attempt_count)
        )
    ):
        availability_status = "fail"
    elif job_terminal_count == 0 and provider_attempt_count == 0:
        availability_status = "no_data"
    elif partial_timing_count:
        availability_status = "insufficient_data"
    else:
        availability_status = "pass"

    latency_status = latency_verdict["status"]
    if availability_status == "fail" or latency_status == "fail":
        status = "fail"
    elif availability_status == "no_data" and latency_status == "no_data":
        status = "no_data"
    elif availability_status in {"no_data", "insufficient_data"} or latency_status in {
        "no_data",
        "insufficient_data",
        "not_configured",
    }:
        status = "insufficient_data"
    else:
        status = "pass"
    return {
        "status": status,
        "basis": "latency_and_outcomes",
        "metrics": latency_verdict["metrics"],
        "latency_status": latency_status,
        "availability_status": availability_status,
        "required_stage_completeness": {
            "trace_count": required_provenance_trace_count,
            "incomplete_count": required_provenance_incomplete_count,
            "status": (
                "not_available"
                if required_provenance_trace_count == 0
                else "pass"
                if required_provenance_incomplete_count == 0
                else "fail"
            ),
        },
        "uncategorized_failure_count": uncategorized_failure_count,
    }


def _report_from_accumulator(
    accumulator: _SLOAccumulator,
    *,
    meeting_id: str,
    thresholds: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    lanes = accumulator.meetings.get(meeting_id, {})
    lane_reports: dict[str, Any] = {}
    lane_statuses: dict[str, str] = {}
    for lane_name, lane in lanes.items():
        metrics = {metric: metric_accumulator.summary() for metric, metric_accumulator in lane.metrics.items()}
        verdict = _lane_verdict(
            lane_name,
            metrics,
            thresholds,
            job_terminal_count=lane.job_terminal_count,
            result_outcome_counts=lane.result_outcome_counts,
            provider_attempt_count=lane.provider_attempt_count,
            provider_attempt_outcome_counts=lane.provider_attempt_outcome_counts,
            provider_attempt_incomplete_count=lane.provider_attempt_incomplete_count,
            timing_contract_counts=lane.timing_contract_counts,
            required_provenance_trace_count=lane.required_provenance_trace_count,
            required_provenance_incomplete_count=lane.required_provenance_incomplete_count,
            uncategorized_failure_count=lane.uncategorized_failure_count,
        )
        result_count = sum(lane.result_outcome_counts.values())
        job_rates = _named_outcome_rates(
            lane.job_outcome_counts,
            lane.job_terminal_count,
        )
        result_rates = _named_outcome_rates(
            lane.result_outcome_counts,
            result_count,
        )
        attempt_rates = _named_outcome_rates(
            lane.provider_attempt_outcome_counts,
            lane.provider_attempt_count,
            incomplete_count=lane.provider_attempt_incomplete_count,
        )
        lane_statuses[lane_name] = verdict["status"]
        lane_reports[lane_name] = {
            "count": lane.count,
            "percentile_sample_limit": lane.max_samples,
            "missing_trace_count": lane.missing_trace_count,
            "missing_stage_counts": dict(lane.missing_stage_counts),
            "cancelled_count": lane.cancelled_count,
            "retry_count": lane.retry_count,
            "job_outcomes": {
                "count": lane.job_terminal_count,
                "counts": dict(lane.job_outcome_counts),
                "result_counts": dict(lane.result_outcome_counts),
                **job_rates,
            },
            "result_outcomes": {
                "count": result_count,
                "counts": dict(lane.result_outcome_counts),
                **result_rates,
            },
            "provider_attempts": {
                "count": lane.provider_attempt_count,
                "counts": dict(lane.provider_attempt_outcome_counts),
                "incomplete_count": lane.provider_attempt_incomplete_count,
                **attempt_rates,
            },
            "timing_contracts": {
                "count": sum(lane.timing_contract_counts.values()),
                "counts": dict(lane.timing_contract_counts),
            },
            "required_stage_completeness": {
                "trace_count": lane.required_provenance_trace_count,
                "complete_count": lane.required_provenance_complete_count,
                "incomplete_count": lane.required_provenance_incomplete_count,
                "ratio": (
                    round(
                        lane.required_provenance_complete_count
                        / lane.required_provenance_trace_count,
                        6,
                    )
                    if lane.required_provenance_trace_count
                    else None
                ),
                "status": (
                    "not_available"
                    if lane.required_provenance_trace_count == 0
                    else "pass"
                    if lane.required_provenance_incomplete_count == 0
                    else "fail"
                ),
            },
            "provenance_stage_counts": {
                stage: dict(counts)
                for stage, counts in lane.provenance_stage_counts.items()
            },
            "uncategorized_failure_count": lane.uncategorized_failure_count,
            "cancellation_acknowledgements": {
                "required_count": lane.cancel_ack_required_count,
                "local_ack_count": lane.local_abort_ack_count,
                "remote_ack_count": lane.remote_abort_ack_count,
                "remote_ack_unavailable_count": lane.remote_abort_ack_unavailable_count,
                "unaccounted_count": max(
                    0,
                    lane.cancel_ack_required_count
                    - lane.remote_abort_ack_count
                    - lane.remote_abort_ack_unavailable_count,
                ),
            },
            "metrics": metrics,
            "slo_verdict": verdict,
        }

    statuses = list(lane_statuses.values())
    if not statuses or all(status == "no_data" for status in statuses):
        overall_status = "no_data"
    elif "fail" in statuses:
        overall_status = "fail"
    elif any(status in {"no_data", "insufficient_data", "not_configured"} for status in statuses):
        overall_status = "insufficient_data"
    else:
        overall_status = "pass"
    return {
        "schema_version": SCHEMA_VERSION,
        "meeting_id": meeting_id,
        "trace_count": sum(lane.count for lane in lanes.values()),
        "lanes": lane_reports,
        "slo_verdict": {
            "status": overall_status,
            "lane_statuses": lane_statuses,
        },
    }


def build_realtime_slo_report(
    traces: Iterable[Any],
    *,
    meeting_id: str,
    thresholds_ms: Mapping[str, Mapping[str, float]] | None = None,
    max_samples_per_metric: int = DEFAULT_MAX_SAMPLES_PER_METRIC,
) -> dict[str, Any]:
    """Aggregate one meeting without retaining stage attributes or content."""

    meeting_id = _required(meeting_id, "meeting_id")
    max_samples = _positive_int(max_samples_per_metric, "max_samples_per_metric")
    accumulator = _SLOAccumulator(max_samples=max_samples, max_meetings=1)
    for raw_trace in traces:
        trace = _normalized_trace(raw_trace)
        if trace["meeting_id"] == meeting_id:
            accumulator.add_trace(trace)
    return _report_from_accumulator(
        accumulator,
        meeting_id=meeting_id,
        thresholds=_normalized_thresholds(thresholds_ms),
    )


def build_realtime_slo_reports(
    traces: Iterable[Any],
    *,
    thresholds_ms: Mapping[str, Mapping[str, float]] | None = None,
    max_samples_per_metric: int = DEFAULT_MAX_SAMPLES_PER_METRIC,
    max_meetings: int = DEFAULT_MAX_MEETINGS,
) -> dict[str, Any]:
    """Aggregate all retained meetings for a diagnostic endpoint."""

    max_samples = _positive_int(max_samples_per_metric, "max_samples_per_metric")
    meeting_limit = _positive_int(max_meetings, "max_meetings")
    accumulator = _SLOAccumulator(max_samples=max_samples, max_meetings=meeting_limit)
    for raw_trace in traces:
        accumulator.add_trace(_normalized_trace(raw_trace))
    thresholds = _normalized_thresholds(thresholds_ms)
    return {
        "schema_version": SCHEMA_VERSION,
        "meetings": {
            meeting_id: _report_from_accumulator(
                accumulator,
                meeting_id=meeting_id,
                thresholds=thresholds,
            )
            for meeting_id in accumulator.meetings
        },
    }


class RealtimeSLOStore:
    """Thread-safe aggregate store with bounded samples and atomic JSON state."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        max_samples_per_metric: int = DEFAULT_MAX_SAMPLES_PER_METRIC,
        max_meetings: int = DEFAULT_MAX_MEETINGS,
        max_checkpoint_traces: int = DEFAULT_MAX_CHECKPOINT_TRACES,
        thresholds_ms: Mapping[str, Mapping[str, float]] | None = None,
    ) -> None:
        self._max_samples = _positive_int(max_samples_per_metric, "max_samples_per_metric")
        self._max_meetings = _positive_int(max_meetings, "max_meetings")
        self._max_checkpoint_traces = _positive_int(max_checkpoint_traces, "max_checkpoint_traces")
        self._thresholds = _normalized_thresholds(thresholds_ms)
        self._state_path = Path(state_path).expanduser() if state_path is not None else None
        self._archived = _SLOAccumulator(
            max_samples=self._max_samples,
            max_meetings=self._max_meetings,
        )
        self._checkpoint: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = RLock()
        if self._state_path is not None and self._state_path.exists():
            self._load()

    def observe(self, trace: Any) -> None:
        """Fold one evicted trace into bounded aggregates and persist it."""

        normalized = _normalized_trace(trace)
        with self._lock:
            self._archived.add_trace(normalized)
            self._checkpoint.pop(normalized["trace_id"], None)
            self._persist_locked()

    def checkpoint(self, *, active_traces: Iterable[Any]) -> None:
        """Atomically persist a bounded snapshot of traces not yet evicted."""

        checkpoint = self._bounded_checkpoint(active_traces)
        with self._lock:
            for trace_id, previous in self._checkpoint.items():
                if trace_id not in checkpoint:
                    self._archived.add_trace(previous)
            self._checkpoint = checkpoint
            self._persist_locked()

    def _bounded_checkpoint(
        self,
        traces: Iterable[Any],
    ) -> OrderedDict[str, dict[str, Any]]:
        checkpoint: OrderedDict[str, dict[str, Any]] = OrderedDict()
        meeting_order: OrderedDict[str, None] = OrderedDict()
        for raw_trace in traces:
            trace = _normalized_trace(raw_trace)
            checkpoint[trace["trace_id"]] = trace
            checkpoint.move_to_end(trace["trace_id"])
            meeting_order[trace["meeting_id"]] = None
            meeting_order.move_to_end(trace["meeting_id"])
            while len(checkpoint) > self._max_checkpoint_traces:
                checkpoint.popitem(last=False)
            while len(meeting_order) > self._max_meetings:
                expired_meeting, _ = meeting_order.popitem(last=False)
                checkpoint = OrderedDict(
                    (trace_id, snapshot)
                    for trace_id, snapshot in checkpoint.items()
                    if snapshot["meeting_id"] != expired_meeting
                )
        return checkpoint

    def report(
        self,
        *,
        meeting_id: str,
        active_traces: Iterable[Any] | None = None,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            accumulator = self._archived.clone()
            checkpoint = list(self._checkpoint.values())
        current = checkpoint if active_traces is None else [_normalized_trace(trace) for trace in active_traces]
        for trace in current:
            if not isinstance(trace, Mapping):
                trace = _normalized_trace(trace)
            accumulator.add_trace(trace)
        return _report_from_accumulator(
            accumulator,
            meeting_id=meeting_id,
            thresholds=self._thresholds,
        )

    def report_all(self, *, active_traces: Iterable[Any] | None = None) -> dict[str, Any]:
        with self._lock:
            accumulator = self._archived.clone()
            checkpoint = list(self._checkpoint.values())
        current = checkpoint if active_traces is None else [_normalized_trace(trace) for trace in active_traces]
        for trace in current:
            if not isinstance(trace, Mapping):
                trace = _normalized_trace(trace)
            accumulator.add_trace(trace)
        return {
            "schema_version": SCHEMA_VERSION,
            "meetings": {
                meeting_id: _report_from_accumulator(
                    accumulator,
                    meeting_id=meeting_id,
                    thresholds=self._thresholds,
                )
                for meeting_id in accumulator.meetings
            },
        }

    def _load(self) -> None:
        assert self._state_path is not None
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("realtime SLO state is unreadable") from exc
        if (
            not isinstance(value, Mapping)
            or value.get("schema_version")
            not in {STORE_SCHEMA_VERSION, *LEGACY_STORE_SCHEMA_VERSIONS}
        ):
            raise ValueError("unsupported realtime SLO state schema")
        archived = _SLOAccumulator.from_state(
            value.get("archived", {}),
            max_samples=self._max_samples,
            max_meetings=self._max_meetings,
        )
        raw_active = value.get("active_traces", [])
        if not isinstance(raw_active, list):
            raise TypeError("persisted active_traces must be a list")
        self._archived = archived
        self._checkpoint = self._bounded_checkpoint(raw_active)

    def _persist_locked(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": STORE_SCHEMA_VERSION,
            "archived": self._archived.to_state(),
            "active_traces": list(self._checkpoint.values()),
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self._state_path.name}.",
            suffix=".tmp",
            dir=self._state_path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._state_path)
            try:
                directory_fd = os.open(self._state_path.parent, os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    pass
                finally:
                    os.close(directory_fd)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
