from __future__ import annotations

import json
from typing import Any


EVENT_ORDER = {
    "transcript_final": 10,
    "state_event": 20,
    "llm_scheduled": 30,
    "llm_schema_result": 40,
    "suggestion_silenced": 45,
    "suggestion_card": 50,
    "evaluation_summary": 90,
}


def build_replay_events(
    snapshot: dict[str, Any],
    *,
    evaluation_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    events.extend(_transcript_events(snapshot))
    events.extend(_state_events(snapshot))
    events.extend(_llm_trace_events(snapshot))
    events.extend(_suggestion_events(snapshot))
    if evaluation_summary is not None:
        events.append(_evaluation_event(snapshot, evaluation_summary))
    return [
        {
            **event,
            "source": "replay_snapshot",
            "trace_kind": "replay_derived",
            "sequence": index,
        }
        for index, event in enumerate(
            sorted(events, key=_event_sort_key),
            start=1,
        )
    ]


def render_sse_events(events: list[dict[str, Any]]) -> str:
    chunks = []
    for event in events:
        chunks.append(
            "\n".join(
                [
                    f"id: {event['sequence']}",
                    f"event: {event['event_type']}",
                    f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}",
                    "",
                ]
            )
        )
    return "\n".join(chunks) + "\n"


def _event_sort_key(event: dict[str, Any]) -> tuple[int, int, str, str]:
    event_type = str(event["event_type"])
    return (
        int(event["at_ms"]),
        EVENT_ORDER.get(event_type, 999),
        event_type,
        str(event["id"]),
    )


def _transcript_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"transcript:{segment['id']}",
            "event_type": "transcript_final",
            "at_ms": int(segment.get("finalized_at_ms", segment.get("end_ms", 0))),
            "payload": {
                "segment_id": str(segment["id"]),
                "start_ms": int(segment.get("start_ms", 0)),
                "end_ms": int(segment.get("end_ms", 0)),
                "text": str(segment.get("text", "")),
                "confidence": segment.get("confidence"),
            },
        }
        for segment in snapshot.get("transcript", {}).get("segments", [])
    ]


def _state_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"state:{event['id']}",
            "event_type": "state_event",
            "at_ms": int(event.get("created_at_ms", 0)),
            "payload": {
                "event_id": str(event["id"]),
                "target_type": str(event.get("target_type", "")),
                "target_id": str(event.get("target_id", "")),
                "event_type": str(event.get("event_type", "")),
                "evidence_span_ids": list(event.get("evidence_span_ids", [])),
            },
        }
        for event in snapshot.get("state_events", [])
    ]


def _suggestion_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _card_display_event(card)
        for card in snapshot.get("suggestion_cards", [])
    ]


def _card_display_event(card: dict[str, Any]) -> dict[str, Any]:
    event_type = "suggestion_card" if _should_show_card(card) else "suggestion_silenced"
    return {
        "id": f"{event_type}:{card['id']}",
        "event_type": event_type,
        "at_ms": int(card.get("card_created_at_ms", 0)),
        "payload": {
            "card_id": str(card["id"]),
            "type": str(card.get("type", "")),
            "status": str(card.get("status", "new")),
            "gap_rule_id": str(card.get("gap_rule_id", "")),
            "trigger_source": str(card.get("trigger_source", "")),
            "trigger_reason": str(card.get("trigger_reason", "")),
            "segment_batch": list(card.get("segment_batch", [])),
            "prompt_version": str(card.get("prompt_version", "")),
            "model": str(card.get("model", "")),
            "usage": dict(card.get("usage", {})),
            "schema_result": str(card.get("schema_result", "")),
            "show_or_silence_decision": str(card.get("show_or_silence_decision", "")),
            "latency_ms": int(card.get("latency_ms", 0)),
            "evidence_span_ids": list(card.get("evidence_span_ids", [])),
            "state_event_ids": list(card.get("state_event_ids", [])),
        },
    }


def _should_show_card(card: dict[str, Any]) -> bool:
    return str(card.get("show_or_silence_decision", "show")) == "show"


def _llm_trace_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for card in snapshot.get("suggestion_cards", []):
        card_id = str(card["id"])
        events.append(
            {
                "id": f"llm_scheduled:{card_id}",
                "event_type": "llm_scheduled",
                "at_ms": int(card.get("state_event_at_ms", card.get("final_segment_at_ms", 0))),
                "payload": {
                    "card_id": card_id,
                    "gap_rule_id": str(card.get("gap_rule_id", "")),
                    "trigger_source": str(card.get("trigger_source", "")),
                    "trigger_reason": str(card.get("trigger_reason", "")),
                    "segment_batch": list(card.get("segment_batch", [])),
                    "state_event_ids": list(card.get("state_event_ids", [])),
                    "prompt_version": str(card.get("prompt_version", "")),
                    "model": str(card.get("model", "")),
                },
            }
        )
        events.append(
            {
                "id": f"llm_schema_result:{card_id}",
                "event_type": "llm_schema_result",
                "at_ms": int(card.get("card_created_at_ms", 0)),
                "payload": {
                    "card_id": card_id,
                    "schema_result": str(card.get("schema_result", "")),
                    "show_or_silence_decision": str(card.get("show_or_silence_decision", "")),
                    "usage": dict(card.get("usage") or {}),
                    "latency_ms": int(card.get("latency_ms", 0)),
                },
            }
        )
    return events


def _evaluation_event(
    snapshot: dict[str, Any],
    evaluation_summary: dict[str, Any],
) -> dict[str, Any]:
    latest_at_ms = 0
    for card in snapshot.get("suggestion_cards", []):
        latest_at_ms = max(latest_at_ms, int(card.get("card_created_at_ms", 0)))
    for event in snapshot.get("state_events", []):
        latest_at_ms = max(latest_at_ms, int(event.get("created_at_ms", 0)))
    for segment in snapshot.get("transcript", {}).get("segments", []):
        latest_at_ms = max(latest_at_ms, int(segment.get("finalized_at_ms", segment.get("end_ms", 0))))
    return {
        "id": "evaluation:summary",
        "event_type": "evaluation_summary",
        "at_ms": latest_at_ms + 1,
        "payload": dict(evaluation_summary),
    }
