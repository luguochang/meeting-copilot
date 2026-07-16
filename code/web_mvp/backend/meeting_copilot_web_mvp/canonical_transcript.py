from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "canonical-transcript.v1"

_AUTHORITY = {
    "transcript_partial": 0,
    "partial": 0,
    "transcript_final": 1,
    "final": 1,
    "transcript_revision": 2,
    "revision": 2,
}


def project_canonical_transcript(*, session_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    projected: dict[str, dict[str, Any]] = {}
    updated_at_ms = 0

    for index, event in enumerate(events or []):
        event_type = str(event.get("event_type") or "")
        if event_type not in _AUTHORITY:
            continue
        payload = dict(event.get("payload") or {})
        event_at_ms = _int_value(event.get("at_ms"), payload.get("end_ms"), default=index)
        updated_at_ms = max(updated_at_ms, event_at_ms)
        target_segment_id, revision_supplement = _target_segment_id(event, payload, index)
        if not target_segment_id:
            continue
        projection_key = _projection_key(target_segment_id, revision_supplement)
        if (
            event_type in {"transcript_revision", "revision"}
            and not revision_supplement
            and projection_key not in projected
        ):
            identity = str(event.get("id") or payload.get("segment_id") or index).strip()
            target_segment_id = f"revision-supplement:{identity}"
            revision_supplement = True
            projection_key = _projection_key(target_segment_id, revision_supplement)
        text = _display_candidate(event, payload)
        if not text:
            continue
        rank = _AUTHORITY[event_type]
        previous = projected.get(projection_key)
        if previous and rank < previous["rank"]:
            continue

        original_text = _original_text(event_type, event, payload, previous, text)
        status = "corrected" if rank == 2 else "final" if rank == 1 else "partial"
        projected[projection_key] = {
            "projection_key": projection_key,
            "segment_id": target_segment_id,
            "source_segment_id": str(
                payload.get("segment_id")
                or event.get("segment_id")
                or event.get("id")
                or target_segment_id
            ),
            "order": previous["order"] if previous else index,
            "rank": rank,
            "sequence": previous["sequence"] if previous else index + 1,
            "start_ms": _int_value(payload.get("start_ms"), event.get("start_ms"), default=0),
            "end_ms": _int_value(payload.get("end_ms"), event.get("end_ms"), event_at_ms, default=0),
            "updated_at_ms": event_at_ms,
            "raw_text": str(payload.get("text") or event.get("text") or "").strip(),
            "normalized_text": str(payload.get("normalized_text") or event.get("normalized_text") or "").strip(),
            "corrected_text": text if rank == 2 else None,
            "display_text": text,
            "original_text": original_text,
            "status": status,
            "evidence_ids": _evidence_ids(payload),
            "revision_supplement": revision_supplement,
            "projection_reconciled": bool(
                payload.get("projection_reconciled") or event.get("projection_reconciled")
            ),
            "source_snapshot_text": str(
                payload.get("source_snapshot_text")
                or event.get("source_snapshot_text")
                or ""
            ).strip(),
        }

    ordered = sorted(projected.values(), key=lambda item: (item["order"], item["updated_at_ms"]))
    committed = _reconcile_committed_segments(
        [_public_segment(item) for item in ordered if item["rank"] >= 1]
    )
    unresolved_partials = [item for item in ordered if item["rank"] == 0]
    active_tail = None
    if unresolved_partials:
        newest_partial = max(
            unresolved_partials,
            key=lambda item: (item["updated_at_ms"], item["order"]),
        )
        active_tail = _public_segment(newest_partial)

    committed, active_tail = _reconcile_active_tail(committed, active_tail)

    committed_text = "".join(segment["display_text"] for segment in committed)
    active_text = active_tail["display_text"] if active_tail else ""
    full_text = committed_text + active_text
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "segments": committed,
        "active_tail": active_tail,
        "committed_text": committed_text,
        "full_text": full_text,
        "committed_char_count": len(committed_text),
        "full_char_count": len(full_text),
        "updated_at_ms": updated_at_ms,
    }


def _target_segment_id(
    event: dict[str, Any],
    payload: dict[str, Any],
    index: int,
) -> tuple[str, bool]:
    event_type = str(event.get("event_type") or "")
    if event_type in {"transcript_revision", "revision"}:
        target = str(
            payload.get("supersedes_segment_id")
            or payload.get("revision_of")
            or event.get("supersedes_segment_id")
            or event.get("revision_of")
            or ""
        ).strip()
        if target:
            return target, False
        identity = str(
            event.get("id")
            or payload.get("id")
            or payload.get("segment_id")
            or event.get("segment_id")
            or index
        ).strip()
        return f"revision-supplement:{identity}", True
    return str(
        payload.get("segment_id")
        or event.get("segment_id")
        or event.get("id")
        or ""
    ).strip(), False


def _projection_key(segment_id: str, revision_supplement: bool) -> str:
    return segment_id if revision_supplement else f"segment:{segment_id}"


def _display_candidate(event: dict[str, Any], payload: dict[str, Any]) -> str:
    return str(
        payload.get("corrected_text")
        or payload.get("normalized_text")
        or payload.get("text")
        or event.get("corrected_text")
        or event.get("normalized_text")
        or event.get("text")
        or ""
    ).strip()


def _original_text(
    event_type: str,
    event: dict[str, Any],
    payload: dict[str, Any],
    previous: dict[str, Any] | None,
    text: str,
) -> str:
    explicit = str(payload.get("original_text") or event.get("original_text") or "").strip()
    if explicit:
        return explicit
    if event_type in {"transcript_final", "final"}:
        return text
    if previous:
        return str(previous.get("original_text") or previous.get("display_text") or "").strip()
    if event_type in {"transcript_revision", "revision"}:
        return ""
    return text


def _evidence_ids(payload: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for evidence_id in payload.get("evidence_ids") or []:
        value = str(evidence_id or "").strip()
        if value and value not in result:
            result.append(value)
    for span in payload.get("evidence_spans") or []:
        if not isinstance(span, dict):
            continue
        value = str(span.get("id") or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _public_segment(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if key not in {"rank", "order"}
    }


def _reconcile_active_tail(
    committed: list[dict[str, Any]],
    active_tail: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if active_tail is None:
        return committed, None
    committed_text = "".join(segment["display_text"] for segment in committed)
    display_text = str(active_tail.get("display_text") or "")
    source_snapshot = str(active_tail.get("source_snapshot_text") or "")

    if source_snapshot:
        if source_snapshot.startswith(committed_text):
            display_text = source_snapshot[len(committed_text):]
        elif active_tail.get("projection_reconciled"):
            common_prefix = _common_prefix_length(committed_text, source_snapshot)
            committed = _truncate_segments_to_chars(committed, common_prefix)
            display_text = source_snapshot[common_prefix:]
    elif committed_text and display_text.startswith(committed_text):
        display_text = display_text[len(committed_text):]

    if not display_text:
        return committed, None
    active_tail = {
        **active_tail,
        "display_text": display_text,
        "normalized_text": display_text,
    }
    return committed, active_tail


def _reconcile_committed_segments(
    committed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reconciled: list[dict[str, Any]] = []
    for segment in committed:
        source_snapshot = str(segment.get("source_snapshot_text") or "")
        if not segment.get("projection_reconciled") or not source_snapshot:
            reconciled.append(segment)
            continue

        committed_text = "".join(item["display_text"] for item in reconciled)
        if source_snapshot.startswith(committed_text):
            display_text = source_snapshot[len(committed_text):]
        else:
            common_prefix = _common_prefix_length(committed_text, source_snapshot)
            reconciled = _truncate_segments_to_chars(reconciled, common_prefix)
            display_text = source_snapshot[common_prefix:]

        if display_text:
            reconciled.append({
                **segment,
                "display_text": display_text,
                "normalized_text": display_text,
            })
    return reconciled


def _truncate_segments_to_chars(
    segments: list[dict[str, Any]],
    char_count: int,
) -> list[dict[str, Any]]:
    remaining = max(0, char_count)
    truncated: list[dict[str, Any]] = []
    for segment in segments:
        if remaining <= 0:
            break
        text = str(segment.get("display_text") or "")
        if len(text) <= remaining:
            truncated.append(segment)
            remaining -= len(text)
            continue
        visible = text[:remaining]
        if visible:
            truncated.append({
                **segment,
                "display_text": visible,
                "normalized_text": visible,
            })
        remaining = 0
    return truncated


def _common_prefix_length(left: str, right: str) -> int:
    length = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        length += 1
    return length


def _int_value(*values: Any, default: int) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default
