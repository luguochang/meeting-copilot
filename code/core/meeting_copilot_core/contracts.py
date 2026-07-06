from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


TRANSCRIPT_EVENT_KINDS = {"partial", "final", "revision"}
EVIDENCE_SPAN_STATUSES = {"active", "stale", "superseded"}
CARD_STATUSES_V1 = {
    "new",
    "kept",
    "dismissed",
    "marked_wrong",
    "too_late",
    "too_intrusive",
}


@dataclass(frozen=True)
class TranscriptSegmentV1:
    id: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    finalized_at_ms: int | None = None
    revision_of: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TranscriptSegmentV1":
        segment = cls(
            id=_required_str(value, "id", "transcript segment"),
            start_ms=_required_int(value, "start_ms", "transcript segment"),
            end_ms=_required_int(value, "end_ms", "transcript segment"),
            text=_required_str(value, "text", "transcript segment"),
            confidence=_optional_confidence(value.get("confidence"), "transcript segment"),
            finalized_at_ms=_optional_int(value.get("finalized_at_ms"), "finalized_at_ms", "transcript segment"),
            revision_of=_optional_str(value.get("revision_of"), "revision_of", "transcript segment"),
        )
        _validate_time_range(segment.start_ms, segment.end_ms, "transcript segment")
        if segment.finalized_at_ms is not None and segment.finalized_at_ms < segment.end_ms:
            raise ValueError("transcript segment finalized_at_ms must be >= end_ms")
        return segment

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
        }
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.finalized_at_ms is not None:
            result["finalized_at_ms"] = self.finalized_at_ms
        if self.revision_of is not None:
            result["revision_of"] = self.revision_of
        return result


@dataclass(frozen=True)
class EvidenceSpanV1:
    id: str
    segment_id: str
    start_ms: int
    end_ms: int
    quote: str
    status: str = "active"
    revision_of: str | None = None
    replaced_by: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceSpanV1":
        status = str(value.get("status", "active")).strip() or "active"
        if status not in EVIDENCE_SPAN_STATUSES:
            raise ValueError(f"unsupported evidence span status: {status}")
        span = cls(
            id=_required_str(value, "id", "evidence span"),
            segment_id=_required_str(value, "segment_id", "evidence span"),
            start_ms=_required_int(value, "start_ms", "evidence span"),
            end_ms=_required_int(value, "end_ms", "evidence span"),
            quote=_required_str(value, "quote", "evidence span"),
            status=status,
            revision_of=_optional_str(value.get("revision_of"), "revision_of", "evidence span"),
            replaced_by=_optional_str(value.get("replaced_by"), "replaced_by", "evidence span"),
        )
        _validate_time_range(span.start_ms, span.end_ms, f"evidence span {span.id}")
        return span

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "segment_id": self.segment_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "quote": self.quote,
            "status": self.status,
        }
        if self.revision_of is not None:
            result["revision_of"] = self.revision_of
        if self.replaced_by is not None:
            result["replaced_by"] = self.replaced_by
        return result


@dataclass(frozen=True)
class TranscriptReportV1:
    provider: str
    latency_ms: int
    rtf: float
    text: str
    normalized_text: str
    segments: tuple[TranscriptSegmentV1, ...]
    evidence_spans: tuple[EvidenceSpanV1, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TranscriptReportV1":
        segments = tuple(
            TranscriptSegmentV1.from_dict(item)
            for item in value.get("segments", [])
        )
        evidence_spans = tuple(
            EvidenceSpanV1.from_dict(item)
            for item in value.get("evidence_spans", [])
        )
        _reject_duplicate_ids((segment.id for segment in segments), "transcript segment")
        _reject_duplicate_ids((span.id for span in evidence_spans), "evidence span")

        segment_by_id = {segment.id: segment for segment in segments}
        for span in evidence_spans:
            segment = segment_by_id.get(span.segment_id)
            if segment is None:
                raise ValueError(
                    f"evidence span references unknown segment_id: {span.segment_id}"
                )
            if span.start_ms < segment.start_ms or span.end_ms > segment.end_ms:
                raise ValueError(
                    f"evidence span {span.id} must be inside segment {span.segment_id}"
                )

        latency_ms = int(value.get("latency_ms", 0))
        if latency_ms < 0:
            raise ValueError("transcript report latency_ms must be non-negative")
        rtf = float(value.get("rtf", 0.0))
        if rtf < 0:
            raise ValueError("transcript report rtf must be non-negative")

        return cls(
            provider=str(value.get("provider", "unknown")),
            latency_ms=latency_ms,
            rtf=rtf,
            text=str(value.get("text", "")),
            normalized_text=str(value.get("normalized_text", "")),
            segments=segments,
            evidence_spans=evidence_spans,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "latency_ms": self.latency_ms,
            "rtf": self.rtf,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "segments": [segment.to_dict() for segment in self.segments],
            "evidence_spans": [span.to_dict() for span in self.evidence_spans],
        }


@dataclass(frozen=True)
class StreamingTranscriptEventV1:
    id: str
    segment_id: str
    kind: str
    start_ms: int
    end_ms: int
    text: str
    received_at_ms: int
    confidence: float | None = None
    replaces_event_id: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StreamingTranscriptEventV1":
        kind = _required_str(value, "kind", "streaming transcript event")
        if kind not in TRANSCRIPT_EVENT_KINDS:
            raise ValueError(f"unsupported transcript event kind: {kind}")
        text = str(value.get("text", ""))
        if kind in {"final", "revision"} and not text.strip():
            raise ValueError(f"{kind} transcript event requires text")
        event = cls(
            id=_required_str(value, "id", "streaming transcript event"),
            segment_id=_required_str(value, "segment_id", "streaming transcript event"),
            kind=kind,
            start_ms=_required_int(value, "start_ms", "streaming transcript event"),
            end_ms=_required_int(value, "end_ms", "streaming transcript event"),
            text=text,
            received_at_ms=_required_int(value, "received_at_ms", "streaming transcript event"),
            confidence=_optional_confidence(value.get("confidence"), "streaming transcript event"),
            replaces_event_id=_optional_str(value.get("replaces_event_id"), "replaces_event_id", "streaming transcript event"),
        )
        _validate_time_range(event.start_ms, event.end_ms, "streaming transcript event")
        if event.received_at_ms < event.end_ms:
            raise ValueError("streaming transcript event received_at_ms must be >= end_ms")
        return event

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "segment_id": self.segment_id,
            "kind": self.kind,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "received_at_ms": self.received_at_ms,
        }
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.replaces_event_id is not None:
            result["replaces_event_id"] = self.replaces_event_id
        return result


@dataclass(frozen=True)
class MeetingStateEventV1:
    id: str
    target_type: str
    target_id: str
    event_type: str
    evidence_span_ids: tuple[str, ...]
    created_at_ms: int | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MeetingStateEventV1":
        event = cls(
            id=_required_str(value, "id", "meeting state event"),
            target_type=_required_str(value, "target_type", "meeting state event"),
            target_id=_required_str(value, "target_id", "meeting state event"),
            event_type=_required_str(value, "event_type", "meeting state event"),
            evidence_span_ids=_required_str_tuple(value, "evidence_span_ids", "meeting state event"),
            created_at_ms=_optional_int(value.get("created_at_ms"), "created_at_ms", "meeting state event"),
        )
        if event.created_at_ms is not None and event.created_at_ms < 0:
            raise ValueError("meeting state event created_at_ms must be non-negative")
        return event

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "event_type": self.event_type,
            "evidence_span_ids": list(self.evidence_span_ids),
        }
        if self.created_at_ms is not None:
            result["created_at_ms"] = self.created_at_ms
        return result


@dataclass(frozen=True)
class SuggestionCardV1:
    id: str
    type: str
    evidence_span_ids: tuple[str, ...]
    state_refs: tuple[str, ...]
    state_event_ids: tuple[str, ...]
    gap_rule_id: str
    trigger_reason: str
    trigger_source: str
    final_segment_at_ms: int
    state_event_at_ms: int
    card_created_at_ms: int
    latency_ms: int
    prompt_version: str
    model: str
    usage: dict[str, Any]
    schema_result: str
    show_or_silence_decision: str
    segment_batch: tuple[str, ...]
    status: str = "new"
    title: str | None = None
    suggested_question: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SuggestionCardV1":
        card_id = _required_str(value, "id", "suggestion card")
        status = str(value.get("status", "new"))
        if status not in CARD_STATUSES_V1:
            raise ValueError(f"unsupported suggestion card status: {status}")
        usage = value.get("usage")
        if not isinstance(usage, dict):
            raise ValueError(f"suggestion card {card_id} missing usage")
        card = cls(
            id=card_id,
            type=_required_str(value, "type", "suggestion card"),
            evidence_span_ids=_required_str_tuple(value, "evidence_span_ids", "suggestion card"),
            state_refs=_required_str_tuple(value, "state_refs", "suggestion card"),
            state_event_ids=_required_str_tuple(value, "state_event_ids", "suggestion card"),
            gap_rule_id=_required_str(value, "gap_rule_id", "suggestion card"),
            trigger_reason=_required_str(value, "trigger_reason", "suggestion card"),
            trigger_source=_required_str(value, "trigger_source", "suggestion card"),
            final_segment_at_ms=_required_int(value, "final_segment_at_ms", "suggestion card"),
            state_event_at_ms=_required_int(value, "state_event_at_ms", "suggestion card"),
            card_created_at_ms=_required_int(value, "card_created_at_ms", "suggestion card"),
            latency_ms=_required_int(value, "latency_ms", "suggestion card"),
            prompt_version=_required_str(value, "prompt_version", "suggestion card"),
            model=_required_str(value, "model", "suggestion card"),
            usage=dict(usage),
            schema_result=_required_str(value, "schema_result", "suggestion card"),
            show_or_silence_decision=_required_str(value, "show_or_silence_decision", "suggestion card"),
            segment_batch=_required_str_tuple(value, "segment_batch", "suggestion card"),
            status=status,
            title=_optional_str(value.get("title"), "title", "suggestion card"),
            suggested_question=_optional_str(value.get("suggested_question"), "suggested_question", "suggestion card"),
            extra=_extra_fields(value, _SUGGESTION_CARD_FIELDS),
        )
        _validate_non_negative(card.final_segment_at_ms, "suggestion card final_segment_at_ms")
        _validate_non_negative(card.state_event_at_ms, "suggestion card state_event_at_ms")
        _validate_non_negative(card.card_created_at_ms, "suggestion card card_created_at_ms")
        _validate_non_negative(card.latency_ms, "suggestion card latency_ms")
        return card

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.extra)
        result.update(
            {
                "id": self.id,
                "type": self.type,
                "evidence_span_ids": list(self.evidence_span_ids),
                "state_refs": list(self.state_refs),
                "state_event_ids": list(self.state_event_ids),
                "gap_rule_id": self.gap_rule_id,
                "trigger_reason": self.trigger_reason,
                "trigger_source": self.trigger_source,
                "final_segment_at_ms": self.final_segment_at_ms,
                "state_event_at_ms": self.state_event_at_ms,
                "card_created_at_ms": self.card_created_at_ms,
                "latency_ms": self.latency_ms,
                "prompt_version": self.prompt_version,
                "model": self.model,
                "usage": dict(self.usage),
                "schema_result": self.schema_result,
                "show_or_silence_decision": self.show_or_silence_decision,
                "segment_batch": list(self.segment_batch),
                "status": self.status,
            }
        )
        if self.title is not None:
            result["title"] = self.title
        if self.suggested_question is not None:
            result["suggested_question"] = self.suggested_question
        return result


@dataclass(frozen=True)
class DegradationStateV1:
    level: str
    reasons: tuple[str, ...]
    blocks_strong_suggestions: bool

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DegradationStateV1":
        level = str(value.get("level", "ok"))
        if level not in {"ok", "warn", "blocking"}:
            raise ValueError(f"unsupported degradation level: {level}")
        reasons = tuple(str(item) for item in value.get("reasons", []))
        blocks = bool(value.get("blocks_strong_suggestions", bool(reasons)))
        return cls(
            level=level,
            reasons=reasons,
            blocks_strong_suggestions=blocks,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "reasons": list(self.reasons),
            "blocks_strong_suggestions": self.blocks_strong_suggestions,
        }


_SUGGESTION_CARD_FIELDS = {
    "id",
    "type",
    "evidence_span_ids",
    "state_refs",
    "state_event_ids",
    "gap_rule_id",
    "trigger_reason",
    "trigger_source",
    "final_segment_at_ms",
    "state_event_at_ms",
    "card_created_at_ms",
    "latency_ms",
    "prompt_version",
    "model",
    "usage",
    "schema_result",
    "show_or_silence_decision",
    "segment_batch",
    "status",
    "title",
    "suggested_question",
}


def _required_str(value: dict[str, Any], field_name: str, label: str) -> str:
    result = str(value.get(field_name, "")).strip()
    if not result:
        raise ValueError(f"{label} missing {field_name}")
    return result


def _optional_str(value: Any, field_name: str, label: str) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        raise ValueError(f"{label} has empty {field_name}")
    return result


def _required_str_tuple(value: dict[str, Any], field_name: str, label: str) -> tuple[str, ...]:
    raw = value.get(field_name)
    if raw is None:
        raise ValueError(f"{label} missing {field_name}")
    if isinstance(raw, str):
        items = [raw]
    else:
        items = list(raw)
    result = tuple(str(item).strip() for item in items if str(item).strip())
    if not result:
        raise ValueError(f"{label} missing {field_name}")
    return result


def _required_int(value: dict[str, Any], field_name: str, label: str) -> int:
    if field_name not in value:
        raise ValueError(f"{label} missing {field_name}")
    try:
        return int(value[field_name])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} {field_name} must be an integer") from exc


def _optional_int(value: Any, field_name: str, label: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} {field_name} must be an integer") from exc


def _optional_confidence(value: Any, label: str) -> float | None:
    if value is None:
        return None
    result = float(value)
    if result < 0 or result > 1:
        raise ValueError(f"{label} confidence must be between 0 and 1")
    return result


def _validate_time_range(start_ms: int, end_ms: int, label: str) -> None:
    _validate_non_negative(start_ms, f"{label} start_ms")
    _validate_non_negative(end_ms, f"{label} end_ms")
    if end_ms < start_ms:
        raise ValueError(f"{label} end_ms must be >= start_ms")


def _validate_non_negative(value: int, label: str) -> None:
    if value < 0:
        raise ValueError(f"{label} must be non-negative")


def _reject_duplicate_ids(ids: Any, label: str) -> None:
    seen: set[str] = set()
    for item_id in ids:
        if item_id in seen:
            raise ValueError(f"duplicate {label} id: {item_id}")
        seen.add(item_id)


def _extra_fields(value: dict[str, Any], known_fields: set[str]) -> dict[str, Any]:
    return {
        key: field_value
        for key, field_value in value.items()
        if key not in known_fields
    }
