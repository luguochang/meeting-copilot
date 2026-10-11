"""Shared normalization helpers for user-editable review document exports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
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
    # The persistence layer creates an unmodified user-final snapshot when an
    # AI document is first generated.  That snapshot is not an intentional
    # user edit and must not freeze exports at an older transcript/minutes
    # revision after a later correction or regeneration succeeds.
    if (
        not isinstance(final, Mapping)
        or final.get("content") is None
        or not bool(final.get("modified"))
    ):
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


_LIBRARY_STATUS_LABELS = {
    "confirmed": "已确认", "candidate": "待确认", "dismissed": "已放弃",
    "open": "待处理", "in_progress": "进行中", "done": "已完成",
    "unknown": "待核对", "needs_review": "待核对",
}
_LIBRARY_METADATA_KEYS = frozenset({
    "id", "segment_id", "segmentId", "transcript_seq", "transcriptSeq",
    "evidence_segment_id", "evidenceSegmentId", "evidence_segment_ids", "evidenceSegmentIds",
    "evidence_hash", "evidenceHash", "speaker_id", "speakerId", "speaker_confidence", "speakerConfidence",
})


def _library_json_section(label: str, value: Any) -> str:
    return f"{label}\n\n```json\n{json.dumps(value, ensure_ascii=False, indent=2)}\n```"


def _library_status(value: Any) -> str:
    if value is None or value == "":
        return "未标明"
    if isinstance(value, str) and value in _LIBRARY_STATUS_LABELS:
        return _LIBRARY_STATUS_LABELS[value]
    original = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return f"待核对（原状态：{original}）"


def _library_extra_fields(item: Mapping[str, Any], consumed: set[str]) -> dict[str, Any]:
    # These IDs remain in the original saved source and provenance, not the reading body.
    extra = {key: value for key, value in item.items() if key not in consumed | _LIBRARY_METADATA_KEYS}
    evidence = extra.get("evidence")
    if isinstance(evidence, Mapping):
        remaining = {key: value for key, value in evidence.items()
                     if key not in _LIBRARY_METADATA_KEYS | {"hash", "revision"}}
        if remaining:
            extra["evidence"] = remaining
        else:
            extra.pop("evidence")
    return extra


def _library_item_content(kind: str, item: Any) -> str:
    if isinstance(item, str):
        if not item.strip():
            return _library_json_section("原始条目（空正文，完整保留）", item)
        return f"- {item}\n  状态：未标明" if kind != "transcript" else item
    if not isinstance(item, Mapping):
        return _library_json_section("原始条目（未识别结构，完整保留）", item)
    text_keys = ("normalized_text", "text") if kind == "transcript" else ("text", "item")
    text_key = next((key for key in text_keys if isinstance(item.get(key), str) and item[key].strip()), None)
    if text_key is None:
        return _library_json_section("原始条目（未识别正文，完整保留）", item)
    consumed = {text_key}
    if kind == "transcript":
        segment: dict[str, Any] = {"text": item[text_key]}
        for target, aliases in (
            ("started_at_ms", ("started_at_ms", "startedAtMs")),
            ("ended_at_ms", ("ended_at_ms", "endedAtMs")),
            ("speaker_label", ("speaker_label", "speakerLabel")),
        ):
            selected = next((key for key in aliases if item.get(key) is not None), None)
            if selected is not None:
                segment[target] = item[selected]
                consumed.add(selected)
        timing = [segment.get(key) for key in ("started_at_ms", "ended_at_ms")]
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                     or isinstance(value, float) and not math.isfinite(value) or value < 0)
               for value in timing):
            return _library_json_section("原始条目（时间格式待核对，完整保留）", item)
        if timing[0] is not None and timing[1] is not None and timing[1] < timing[0]:
            return _library_json_section("原始条目（时间顺序待核对，完整保留）", item)
        if timing[0] is not None:
            rendered = transcript_display_line(segment)
        else:
            speaker = value_text(segment, "speaker_label")
            rendered = f"{speaker}：{item[text_key]}" if speaker else item[text_key]
            if timing[1] is not None:
                rendered = f"[结束 {format_offset(timing[1])}] {rendered}"
    else:
        consumed.add("status")
        rendered = f"- {value_text(item, text_key)}\n  状态：{_library_status(item.get('status'))}"
        if kind == "action_items":
            details = (("负责人", ("owner",)), ("截止时间", ("deadline", "due", "due_date", "dueDate")))
        elif kind == "risks":
            details = (("级别", ("severity", "level")), ("缓解措施", ("mitigation",)),
                       ("负责人", ("owner",)), ("影响", ("impact",)), ("概率", ("probability",)))
        else:
            details = ()
        for label, aliases in details:
            selected = next((key for key in aliases if key in item), None)
            if selected is not None:
                value = item[selected]
                display = "未填写" if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                rendered += f"\n  {label}：{display}"
                consumed.add(selected)
    extra = _library_extra_fields(item, consumed)
    return f"{rendered}\n\n{_library_json_section('补充字段（原值保留）', extra)}" if extra else rendered


def review_library_content(kind: str, content: Any) -> str:
    """Render only new copied bodies; raw source/hash/version and old copies stay authoritative."""
    if isinstance(content, str):
        return content
    if kind == "minutes":
        markdown = content.get("markdown") if isinstance(content, Mapping) else None
        return markdown if isinstance(markdown, str) else json.dumps(content, ensure_ascii=False, indent=2)
    headings = {"decisions": "决策记录", "action_items": "待办", "risks": "风险", "transcript": "会议文字"}
    if kind not in headings:
        return _library_json_section("原始内容（未识别文稿类型，完整保留）", content)
    items = content_items(content, kind)
    if items is None:
        markdown = content.get("markdown") if isinstance(content, Mapping) else None
        if isinstance(markdown, str) and set(content) == {"markdown"}:
            return markdown
        return _library_json_section("原始内容（未识别结构，完整保留）", content)
    parts = [f"## {headings[kind]}"]
    parts.extend(_library_item_content(kind, item) for item in items)
    if not items:
        parts.append("此终稿列表为空。")
    if isinstance(content, Mapping):
        keys = (kind, "items", "segments") if kind == "transcript" else (kind, "items")
        selected_key = next(key for key in keys if content.get(key) is not None)
        extra = {key: value for key, value in content.items() if key != selected_key}
        if extra:
            parts.append(_library_json_section("补充内容（原值保留）", extra))
    return "\n\n".join(parts)
