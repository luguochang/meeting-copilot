from __future__ import annotations

from dataclasses import dataclass
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

_STAGE_INDEX = {stage: index for index, stage in enumerate(PIPELINE_STAGES)}


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
            ordered = {
                stage: self._marks[stage]
                for stage in PIPELINE_STAGES
                if stage in self._marks
            }
        return MappingProxyType(ordered)

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
            self._validate_stage_order(stage, timestamp)
            mark = StageMark(
                stage=stage,
                monotonic_ns=timestamp,
                attributes=mark_attributes,
            )
            self._marks[stage] = mark
            return mark

    def _validate_stage_order(self, stage: str, timestamp: int) -> None:
        stage_index = _STAGE_INDEX[stage]
        earlier = [
            mark
            for recorded_stage, mark in self._marks.items()
            if _STAGE_INDEX[recorded_stage] < stage_index
        ]
        later = [
            mark
            for recorded_stage, mark in self._marks.items()
            if _STAGE_INDEX[recorded_stage] > stage_index
        ]
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
            stages = {
                stage: self._marks[stage].to_dict()
                for stage in PIPELINE_STAGES
                if stage in self._marks
            }
        return {
            "trace_id": self.trace_id,
            "meeting_id": self.meeting_id,
            "job_id": job_id,
            "generation_id": generation_id,
            "stages": stages,
            "latency": self.latency_breakdown(),
        }


class PipelineTraceCollector:
    """Thread-safe in-memory collector for lightweight pipeline traces."""

    def __init__(self, *, clock_ns: Callable[[], int] = time.monotonic_ns) -> None:
        self._clock_ns = clock_ns
        self._traces: dict[str, PipelineTrace] = {}
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
            existing = self._traces.get(trace_id)
            if existing is not None:
                if existing.meeting_id != meeting_id:
                    raise ValueError(
                        f"trace_id {trace_id!r} is already associated with meeting_id "
                        f"{existing.meeting_id!r}"
                    )
                return existing.bind(job_id=job_id, generation_id=generation_id)
            trace = PipelineTrace(
                trace_id=trace_id,
                meeting_id=meeting_id,
                job_id=job_id,
                generation_id=generation_id,
                clock_ns=self._clock_ns,
            )
            self._traces[trace_id] = trace
            return trace

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
        trace = self.create(
            trace_id=trace_id,
            meeting_id=meeting_id,
            job_id=job_id,
            generation_id=generation_id,
        )
        return trace.mark(
            stage,
            monotonic_ns=monotonic_ns,
            attributes=attributes,
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

    def __len__(self) -> int:
        with self._lock:
            return len(self._traces)
