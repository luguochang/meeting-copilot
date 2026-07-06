from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StreamingTranscriptEvent:
    event_type: str
    segment_id: str
    text: str
    start_ms: int
    end_ms: int
    received_at_ms: int
    confidence: float | None = None
    revision_of: str | None = None


def build_provider_transcript_from_stream(
    provider: str,
    events: list[StreamingTranscriptEvent],
) -> dict:
    segments_by_id: dict[str, dict] = {}
    partial_count = 0
    final_count = 0
    revision_count = 0
    error_count = 0
    end_of_stream_count = 0
    errors: list[dict] = []
    latency_ms = 0

    for event in events:
        latency_ms = max(latency_ms, event.received_at_ms)
        if event.event_type == "partial":
            partial_count += 1
            continue
        if event.event_type == "final":
            final_count += 1
            _validate_segment_event(event)
            segments_by_id[event.segment_id] = _segment_from_event(event)
            continue
        if event.event_type == "revision":
            revision_count += 1
            _validate_segment_event(event)
            target_id = event.revision_of or event.segment_id
            segment = _segment_from_event(event)
            segment["id"] = target_id
            segment["revision_of"] = target_id
            segments_by_id[target_id] = segment
            continue
        if event.event_type == "error":
            error_count += 1
            errors.append(
                {
                    "id": event.segment_id,
                    "message": event.text,
                    "received_at_ms": event.received_at_ms,
                }
            )
            continue
        if event.event_type == "end_of_stream":
            end_of_stream_count += 1
            continue
        raise ValueError(f"unsupported streaming event_type: {event.event_type}")

    segments = sorted(segments_by_id.values(), key=lambda item: (item["start_ms"], item["id"]))
    return {
        "text": "".join(segment["text"] for segment in segments),
        "latency_ms": latency_ms,
        "segments": segments,
        "raw": {
            "provider": provider,
            "mode": "streaming_contract",
            "partial_event_count": partial_count,
            "final_event_count": final_count,
            "revision_event_count": revision_count,
            "error_event_count": error_count,
            "end_of_stream_event_count": end_of_stream_count,
            "errors": errors,
        },
    }


def _segment_from_event(event: StreamingTranscriptEvent) -> dict:
    return {
        "id": event.segment_id,
        "start_ms": event.start_ms,
        "end_ms": event.end_ms,
        "text": event.text,
        "confidence": event.confidence,
        "is_final": True,
    }


def _validate_segment_event(event: StreamingTranscriptEvent) -> None:
    if not event.text.strip():
        raise ValueError(f"empty final/revision text: {event.segment_id}")
    if event.start_ms < 0 or event.end_ms < 0 or event.received_at_ms < 0:
        raise ValueError(f"streaming event timestamps must be non-negative: {event.segment_id}")
    if event.end_ms < event.start_ms:
        raise ValueError(f"end_ms must be >= start_ms: {event.segment_id}")
    if event.confidence is not None and not 0 <= event.confidence <= 1:
        raise ValueError(f"confidence must be between 0 and 1: {event.segment_id}")
