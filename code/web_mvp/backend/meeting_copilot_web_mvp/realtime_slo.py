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

from .pipeline_trace import PIPELINE_STAGES


SCHEMA_VERSION = "meeting_copilot.realtime_ai_slo.v1"
STORE_SCHEMA_VERSION = "meeting_copilot.realtime_ai_slo_store.v1"
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

# P95 targets follow the accepted NEXT-005 product windows. Component targets
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
    return {
        "trace_id": trace_id,
        "meeting_id": meeting_id,
        "lane": lane,
        "stages": stages,
        "retry_count": retry_count,
        "cancelled": cancelled,
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
        for stage in PIPELINE_STAGES:
            self.missing_stage_counts[stage] += other.missing_stage_counts[stage]
        for metric in METRIC_STAGE_PAIRS:
            self.metrics[metric].merge(other.metrics[metric])

    def to_state(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "missing_trace_count": self.missing_trace_count,
            "missing_stage_counts": dict(self.missing_stage_counts),
            "cancelled_count": self.cancelled_count,
            "retry_count": self.retry_count,
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
        missing = value.get("missing_stage_counts", {})
        metrics = value.get("metrics", {})
        if not isinstance(missing, Mapping) or not isinstance(metrics, Mapping):
            raise TypeError("persisted lane counters and metrics must be mappings")
        lane.missing_stage_counts = {
            stage: _non_negative_int(missing.get(stage, 0), f"missing {stage} count") for stage in PIPELINE_STAGES
        }
        lane.metrics = {
            metric: _MetricAccumulator.from_state(metrics.get(metric, {}), max_samples=max_samples)
            for metric in METRIC_STAGE_PAIRS
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
) -> dict[str, Any]:
    lane_thresholds = thresholds.get(lane_name)
    if lane_thresholds is None:
        return {
            "status": "not_configured",
            "basis": "p95_ms",
            "metrics": {},
        }

    metric_verdicts: dict[str, dict[str, Any]] = {}
    statuses: list[str] = []
    for metric in METRIC_STAGE_PAIRS:
        threshold = lane_thresholds.get(metric)
        observed = metrics[metric]["p95_ms"]
        if threshold is None:
            status = "not_configured"
        elif observed is None:
            status = "no_data"
        else:
            status = "pass" if observed <= threshold else "fail"
        metric_verdicts[metric] = {
            "status": status,
            "threshold_ms": threshold,
            "observed_p95_ms": observed,
        }
        statuses.append(status)

    if "fail" in statuses:
        status = "fail"
    elif all(item == "no_data" for item in statuses):
        status = "no_data"
    elif "no_data" in statuses or "not_configured" in statuses:
        status = "insufficient_data"
    else:
        status = "pass"
    return {
        "status": status,
        "basis": "p95_ms",
        "metrics": metric_verdicts,
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
        verdict = _lane_verdict(lane_name, metrics, thresholds)
        lane_statuses[lane_name] = verdict["status"]
        lane_reports[lane_name] = {
            "count": lane.count,
            "percentile_sample_limit": lane.max_samples,
            "missing_trace_count": lane.missing_trace_count,
            "missing_stage_counts": dict(lane.missing_stage_counts),
            "cancelled_count": lane.cancelled_count,
            "retry_count": lane.retry_count,
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
        if not isinstance(value, Mapping) or value.get("schema_version") != STORE_SCHEMA_VERSION:
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
