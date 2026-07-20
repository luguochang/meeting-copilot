"""Shared normalization helpers for user-editable review document exports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


REVIEW_DOCUMENT_KINDS = frozenset({"minutes", "decisions", "action_items", "risks", "transcript"})
EXPORT_TIMEZONE = timezone(timedelta(hours=8))


def user_final_content(payload: Mapping[str, Any], kind: str) -> tuple[bool, Any]:
    """Return whether a persisted user-final document exists and its content."""

    documents = payload.get("documents")
    if not isinstance(documents, Mapping):
        return False, None
    document = documents.get(kind)
    if not isinstance(document, Mapping):
        return False, None
    final = document.get("user_final")
    if not isinstance(final, Mapping) or final.get("content") is None:
        return False, None
    return True, final.get("content")


def content_items(content: Any, key: str) -> list[Any] | None:
    if isinstance(content, list):
        return list(content)
    if not isinstance(content, Mapping):
        return None
    value = content.get(key)
    if value is None:
        value = content.get("items")
    if value is None and key == "transcript":
        value = content.get("segments")
    return list(value) if isinstance(value, list) else None


def export_fact_items(
    payload: Mapping[str, Any],
    *,
    kind: str,
    key: str,
    fallback: list[Any],
) -> list[Any]:
    found, content = user_final_content(payload, kind)
    if not found:
        return list(fallback)
    return content_items(content, key) or []


def export_transcript(payload: Mapping[str, Any]) -> list[Any]:
    found, content = user_final_content(payload, "transcript")
    if not found:
        return list(payload.get("transcript") or [])
    return content_items(content, "transcript") or []


def export_minutes(payload: Mapping[str, Any]) -> Any:
    """Return the persisted user-final minutes, including an intentional empty value."""

    found, content = user_final_content(payload, "minutes")
    return content if found else payload.get("minutes")


def export_minutes_markdown(payload: Mapping[str, Any]) -> str:
    content = export_minutes(payload)
    if isinstance(content, Mapping):
        return str(content.get("markdown") or "")
    return str(content or "")


def format_offset(milliseconds: Any) -> str:
    try:
        total_seconds = max(0, int(milliseconds or 0) // 1_000)
    except (TypeError, ValueError):
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if hours
        else f"{minutes:02d}:{seconds:02d}"
    )


def format_meeting_datetime(meeting: Mapping[str, Any]) -> str:
    for key in ("started_at_ms", "created_at_ms"):
        try:
            milliseconds = max(0, int(meeting.get(key)))
            return datetime.fromtimestamp(
                milliseconds / 1_000,
                tz=EXPORT_TIMEZONE,
            ).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, TypeError, ValueError):
            continue
    return "未知"


def meeting_duration_ms(meeting: Mapping[str, Any]) -> int | None:
    try:
        started_at_ms = int(meeting.get("started_at_ms"))
        ended_at_ms = int(meeting.get("ended_at_ms"))
    except (TypeError, ValueError):
        return None
    return max(0, ended_at_ms - started_at_ms)


def format_meeting_duration(meeting: Mapping[str, Any]) -> str:
    duration_ms = meeting_duration_ms(meeting)
    return format_offset(duration_ms) if duration_ms is not None else "进行中"


def transcript_text(segment: Any) -> str:
    return value_text(segment, "normalized_text", "text")


def transcript_speaker(segment: Any) -> str:
    return value_text(segment, "speaker_label", "speaker_id")


def transcript_timing(segment: Any) -> tuple[Any, Any]:
    if not isinstance(segment, Mapping):
        return 0, None
    started_at_ms = segment.get("started_at_ms", segment.get("startedAtMs", 0))
    ended_at_ms = segment.get("ended_at_ms", segment.get("endedAtMs"))
    return started_at_ms, ended_at_ms


def transcript_display_line(segment: Any) -> str:
    started_at_ms, ended_at_ms = transcript_timing(segment)
    timing = format_offset(started_at_ms)
    if ended_at_ms is not None:
        timing = f"{timing}-{format_offset(ended_at_ms)}"
    speaker = transcript_speaker(segment)
    speaker_prefix = f"{speaker}：" if speaker else ""
    return f"[{timing}] {speaker_prefix}{transcript_text(segment)}"


def build_transcript_versions(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep raw ASR, AI correction and user-final text in one portable JSON view."""

    source_segments = list(payload.get("source_transcript") or payload.get("transcript") or [])
    has_user_final, user_content = user_final_content(payload, "transcript")
    user_segments = content_items(user_content, "transcript") or [] if has_user_final else []
    user_by_id = {
        str(item.get("segment_id")): item
        for item in user_segments
        if isinstance(item, Mapping) and item.get("segment_id") is not None
    }
    user_by_seq = {
        int(item["transcript_seq"]): item
        for item in user_segments
        if isinstance(item, Mapping)
        and item.get("transcript_seq") is not None
        and str(item.get("transcript_seq")).isdigit()
    }
    versions: list[dict[str, Any]] = []
    for index, source in enumerate(source_segments):
        if not isinstance(source, Mapping):
            continue
        segment_id = str(source.get("segment_id") or "")
        transcript_seq = source.get("transcript_seq")
        user_segment = user_by_id.get(segment_id)
        if user_segment is None and isinstance(transcript_seq, int):
            user_segment = user_by_seq.get(transcript_seq)
        if user_segment is None and has_user_final and index < len(user_segments):
            positional = user_segments[index]
            user_segment = positional if isinstance(positional, Mapping) else None

        raw_text = value_text(source, "text")
        ai_text = value_text(source, "correction_after_text", "normalized_text", "text")
        user_text = transcript_text(user_segment) if user_segment is not None else ""
        if user_segment is not None:
            effective_source = "user_final"
            effective_text = user_text
        elif source.get("correction_after_text") is not None:
            effective_source = "ai_corrected"
            effective_text = ai_text
        elif ai_text != raw_text:
            effective_source = "normalized_asr"
            effective_text = ai_text
        else:
            effective_source = "raw_asr"
            effective_text = raw_text
        versions.append(
            {
                "segment_id": segment_id,
                "transcript_seq": transcript_seq,
                "raw_asr": {"text": raw_text},
                "ai_corrected": {
                    "text": ai_text,
                    "before_text": source.get("correction_before_text"),
                    "status": source.get("correction_status") or "pending",
                    "error_class": source.get("correction_error_class"),
                    "updated_at_ms": source.get("correction_updated_at_ms"),
                },
                "user_final": dict(user_segment) if user_segment is not None else None,
                "effective": {"source": effective_source, "text": effective_text},
                "speaker": {
                    "id": source.get("speaker_id"),
                    "label": source.get("speaker_label"),
                    "confidence": source.get("speaker_confidence"),
                },
                "timing": {
                    "started_at_ms": source.get("started_at_ms"),
                    "ended_at_ms": source.get("ended_at_ms"),
                },
                "evidence": {
                    "hash": source.get("evidence_hash"),
                    "revision": source.get("revision"),
                },
                "audit": {
                    "created_at_ms": source.get("created_at_ms"),
                    "updated_at_ms": source.get("updated_at_ms"),
                },
            }
        )
    return versions


def value_text(value: Any, *keys: str) -> str:
    if isinstance(value, Mapping):
        for key in keys:
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip()
        return ""
    return str(value or "").strip()
