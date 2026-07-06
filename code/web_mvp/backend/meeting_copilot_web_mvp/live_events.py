from __future__ import annotations

import json
from typing import Any


LIVE_SOURCE = "live_mock_stream"
LIVE_TRACE_KIND = "live_event"

EVENT_ORDER = {
    "transcript_partial": 5,
    "transcript_final": 10,
    "transcript_revision": 15,
    "state_event": 20,
    "scheduler_event": 30,
    "llm_schema_result": 40,
    "suggestion_invalidated": 44,
    "suggestion_silenced": 45,
    "suggestion_card": 50,
    "provider_error": 80,
    "evaluation_summary": 90,
}


def event_source_metadata() -> dict[str, Any]:
    return {
        "source": LIVE_SOURCE,
        "trace_kind": LIVE_TRACE_KIND,
        "transport": "sse",
        "is_mock": True,
    }


def build_mock_live_events(
    snapshot: dict[str, Any],
    *,
    evaluation_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    transcript_events = _transcript_events(snapshot)
    suggestion_invalidation_events = _suggestion_invalidation_events(snapshot, transcript_events)
    events.extend(transcript_events)
    events.extend(_state_events(snapshot))
    events.extend(suggestion_invalidation_events)
    events.extend(_card_trace_events(snapshot, suggestion_invalidation_events))
    if evaluation_summary is not None:
        events.append(_evaluation_event(snapshot, evaluation_summary))
    return [
        {
            **event,
            "source": LIVE_SOURCE,
            "trace_kind": LIVE_TRACE_KIND,
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
    events: list[dict[str, Any]] = []
    evidence_by_segment = _evidence_by_segment(snapshot)
    for segment in snapshot.get("transcript", {}).get("segments", []):
        segment_id = str(segment["id"])
        start_ms = int(segment.get("start_ms", 0))
        end_ms = int(segment.get("end_ms", 0))
        finalized_at_ms = int(segment.get("finalized_at_ms", end_ms))
        text = str(segment.get("text", ""))
        partial_at_ms = start_ms + max(1, min(1000, (end_ms - start_ms) // 2))
        events.append(
            {
                "id": f"transcript_partial:{segment_id}",
                "event_type": "transcript_partial",
                "at_ms": partial_at_ms,
                "payload": {
                    "segment_id": segment_id,
                    "start_ms": start_ms,
                    "end_ms": partial_at_ms,
                    "text": _partial_text(text),
                    "confidence": segment.get("confidence"),
                    "is_final": False,
                },
            }
        )
        final_event_type = "transcript_revision" if segment.get("revision_of") else "transcript_final"
        payload = {
            "segment_id": segment_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "confidence": segment.get("confidence"),
            "is_final": True,
            "evidence_spans": [
                dict(evidence)
                for evidence in evidence_by_segment.get(segment_id, [])
            ],
        }
        if segment.get("revision_of"):
            replaced_segment_id = str(segment["revision_of"])
            payload["revision_of"] = replaced_segment_id
            payload["supersedes_segment_id"] = replaced_segment_id
            replaced_segment_evidence = evidence_by_segment.get(replaced_segment_id, [])
            payload["evidence_spans"] = [
                _active_revision_evidence(
                    segment_id,
                    evidence,
                    replaced_segment_id,
                    replaced_segment_evidence,
                )
                for evidence in payload["evidence_spans"]
            ]
            if not payload["evidence_spans"]:
                raise ValueError(f"{segment_id} revision missing replacement evidence")
            payload["superseded_evidence_spans"] = _superseded_evidence_for_revision(
                payload["evidence_spans"],
                replaced_segment_evidence,
            )
            if not payload["superseded_evidence_spans"]:
                raise ValueError(f"{segment_id} revision missing superseded evidence")
        events.append(
            {
                "id": f"{final_event_type}:{segment_id}",
                "event_type": final_event_type,
                "at_ms": finalized_at_ms,
                "payload": payload,
            }
        )
    return events


def _evidence_by_segment(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    evidence_by_segment: dict[str, list[dict[str, Any]]] = {}
    for evidence in snapshot.get("transcript", {}).get("evidence_spans", []):
        evidence_by_segment.setdefault(str(evidence.get("segment_id", "")), []).append(evidence)
    return evidence_by_segment


def _active_revision_evidence(
    segment_id: str,
    evidence: dict[str, Any],
    replaced_segment_id: str,
    replaced_segment_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    result = dict(evidence)
    result.setdefault("status", "active")
    revision_of = result.get("revision_of")
    if not revision_of:
        raise ValueError(f"{segment_id} revision evidence missing revision_of")
    replaced_evidence_ids = {
        str(old_evidence.get("id", ""))
        for old_evidence in replaced_segment_evidence
    }
    if str(revision_of) not in replaced_evidence_ids:
        raise ValueError(
            f"{segment_id} revision evidence {result.get('id')} "
            f"does not replace evidence from {replaced_segment_id}"
        )
    result.pop("replaced_by", None)
    return result


def _superseded_evidence_for_revision(
    replacement_evidence: list[dict[str, Any]],
    replaced_segment_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    replacement_by_old_id = {
        str(evidence.get("revision_of")): str(evidence.get("id"))
        for evidence in replacement_evidence
        if evidence.get("revision_of") and evidence.get("id")
    }
    superseded: list[dict[str, Any]] = []
    for old_evidence in replaced_segment_evidence:
        old_evidence_id = str(old_evidence.get("id", ""))
        replacement_id = replacement_by_old_id.get(old_evidence_id)
        if not replacement_id:
            continue
        superseded_evidence = dict(old_evidence)
        superseded_evidence["status"] = "superseded"
        superseded_evidence["replaced_by"] = replacement_id
        superseded.append(superseded_evidence)
    return superseded


def _state_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    state_items = _state_items_by_type(snapshot)
    events: list[dict[str, Any]] = []
    for event in snapshot.get("state_events", []):
        target_type = str(event.get("target_type", ""))
        target_id = str(event.get("target_id", ""))
        state_item = state_items.get(target_type, {}).get(target_id)
        if not state_item:
            raise ValueError(
                f"{event['id']} references unknown state item: {target_type}:{target_id}"
            )
        events.append(
            {
                "id": f"state:{event['id']}",
                "event_type": "state_event",
                "at_ms": int(event.get("created_at_ms", 0)),
                "payload": {
                    "event_id": str(event["id"]),
                    "target_type": target_type,
                    "target_id": target_id,
                    "state_event_type": str(event.get("event_type", "")),
                    "evidence_span_ids": list(event.get("evidence_span_ids", [])),
                    "state_item": dict(state_item),
                },
            }
        )
    return events


def _card_trace_events(
    snapshot: dict[str, Any],
    suggestion_invalidation_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    invalidated_cards = _latest_invalidated_cards_by_id(suggestion_invalidation_events or [])
    state_event_ids = {
        str(event["id"])
        for event in snapshot.get("state_events", [])
    }
    for card in snapshot.get("suggestion_cards", []):
        card_id = str(card["id"])
        missing_state_refs = [
            event_id
            for event_id in card.get("state_event_ids", [])
            if str(event_id) not in state_event_ids
        ]
        if missing_state_refs:
            raise ValueError(
                f"{card_id} references unknown state_event_id: {missing_state_refs[0]}"
            )
        card_display_at_ms = int(card.get("card_created_at_ms", 0))
        display_card = invalidated_cards.get(card_id, (0, card))
        if display_card[0] > card_display_at_ms:
            display_card = (0, card)
        events.append(_scheduler_event(card))
        events.append(_schema_result_event(card))
        events.append(_card_display_event(display_card[1]))
    return events


def _suggestion_invalidation_events(
    snapshot: dict[str, Any],
    transcript_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for revision_event in transcript_events:
        if revision_event["event_type"] != "transcript_revision":
            continue
        payload = revision_event.get("payload", {})
        stale_evidence_ids = [
            str(evidence["id"])
            for evidence in payload.get("superseded_evidence_spans", [])
        ]
        if not stale_evidence_ids:
            continue
        replacement_by_stale_evidence_id = _replacement_evidence_by_stale_id(
            payload.get("evidence_spans", [])
        )
        for card in snapshot.get("suggestion_cards", []):
            card_evidence_ids = {str(evidence_id) for evidence_id in card.get("evidence_span_ids", [])}
            impacted_evidence_ids = [
                evidence_id
                for evidence_id in stale_evidence_ids
                if evidence_id in card_evidence_ids
            ]
            if not impacted_evidence_ids:
                continue
            replacement_evidence_ids = [
                replacement_by_stale_evidence_id[evidence_id]
                for evidence_id in impacted_evidence_ids
                if evidence_id in replacement_by_stale_evidence_id
            ]
            invalidated_card = dict(card)
            invalidated_card["show_or_silence_decision"] = "silence"
            invalidated_card["invalidation_reason"] = "stale_evidence"
            invalidated_card["invalidated_by_event_id"] = str(revision_event["id"])
            invalidated_card["stale_evidence_span_ids"] = impacted_evidence_ids
            invalidated_card["replacement_evidence_span_ids"] = replacement_evidence_ids
            events.append(
                {
                    "id": f"suggestion_invalidated:{card['id']}:{payload['segment_id']}",
                    "event_type": "suggestion_invalidated",
                    "at_ms": int(revision_event["at_ms"]),
                    "payload": {
                        "card_id": str(card["id"]),
                        "reason": "stale_evidence",
                        "invalidated_by_event_id": str(revision_event["id"]),
                        "stale_evidence_span_ids": impacted_evidence_ids,
                        "replacement_evidence_span_ids": replacement_evidence_ids,
                        "card": invalidated_card,
                    },
                }
            )
    return events


def _replacement_evidence_by_stale_id(
    replacement_evidence: list[dict[str, Any]],
) -> dict[str, str]:
    return {
        str(evidence["revision_of"]): str(evidence["id"])
        for evidence in replacement_evidence
        if evidence.get("revision_of") and evidence.get("id")
    }


def _latest_invalidated_cards_by_id(
    suggestion_invalidation_events: list[dict[str, Any]],
) -> dict[str, tuple[int, dict[str, Any]]]:
    invalidated_cards: dict[str, tuple[int, dict[str, Any]]] = {}
    for event in sorted(suggestion_invalidation_events, key=_event_sort_key):
        card = event.get("payload", {}).get("card")
        if not card:
            continue
        invalidated_cards[str(card["id"])] = (int(event["at_ms"]), dict(card))
    return invalidated_cards


def _scheduler_event(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"scheduler:{card['id']}",
        "event_type": "scheduler_event",
        "at_ms": int(card.get("state_event_at_ms", card.get("final_segment_at_ms", 0))),
        "payload": {
            "scheduler_event_type": "llm_scheduled",
            "card_id": str(card["id"]),
            "gap_rule_id": str(card.get("gap_rule_id", "")),
            "trigger_source": str(card.get("trigger_source", "")),
            "trigger_reason": str(card.get("trigger_reason", "")),
            "segment_batch": list(card.get("segment_batch", [])),
            "source_event_ids": list(card.get("state_event_ids", [])),
            "prompt_version": str(card.get("prompt_version", "")),
            "model": str(card.get("model", "")),
        },
    }


def _schema_result_event(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"llm_schema_result:{card['id']}",
        "event_type": "llm_schema_result",
        "at_ms": int(card.get("card_created_at_ms", 0)),
        "payload": {
            "card_id": str(card["id"]),
            "schema_result": str(card.get("schema_result", "")),
            "show_or_silence_decision": str(card.get("show_or_silence_decision", "")),
            "usage": dict(card.get("usage") or {}),
            "latency_ms": int(card.get("latency_ms", 0)),
        },
    }


def _card_display_event(card: dict[str, Any]) -> dict[str, Any]:
    event_type = (
        "suggestion_card"
        if str(card.get("show_or_silence_decision", "show")) == "show"
        else "suggestion_silenced"
    )
    return {
        "id": f"{event_type}:{card['id']}",
        "event_type": event_type,
        "at_ms": int(card.get("card_created_at_ms", 0)),
        "payload": {
            "card_id": str(card["id"]),
            "card": dict(card),
            "type": str(card.get("type", "")),
            "gap_rule_id": str(card.get("gap_rule_id", "")),
            "trigger_source": str(card.get("trigger_source", "")),
            "trigger_reason": str(card.get("trigger_reason", "")),
            "segment_batch": list(card.get("segment_batch", [])),
            "source_event_ids": list(card.get("state_event_ids", [])),
            "schema_result": str(card.get("schema_result", "")),
            "show_or_silence_decision": str(card.get("show_or_silence_decision", "")),
            "latency_ms": int(card.get("latency_ms", 0)),
            "evidence_span_ids": list(card.get("evidence_span_ids", [])),
        },
    }


def _state_items_by_type(snapshot: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    states = snapshot.get("states", {})
    collections = {
        "DecisionCandidate": "decision_candidates",
        "ActionItem": "action_items",
        "Risk": "risks",
        "OpenQuestion": "open_questions",
    }
    return {
        target_type: {
            str(item["id"]): item
            for item in states.get(collection_name, [])
        }
        for target_type, collection_name in collections.items()
    }


def _evaluation_event(
    snapshot: dict[str, Any],
    evaluation_summary: dict[str, Any],
) -> dict[str, Any]:
    latest_at_ms = 0
    for segment in snapshot.get("transcript", {}).get("segments", []):
        latest_at_ms = max(latest_at_ms, int(segment.get("finalized_at_ms", segment.get("end_ms", 0))))
    for event in snapshot.get("state_events", []):
        latest_at_ms = max(latest_at_ms, int(event.get("created_at_ms", 0)))
    for card in snapshot.get("suggestion_cards", []):
        latest_at_ms = max(latest_at_ms, int(card.get("card_created_at_ms", 0)))
    return {
        "id": "evaluation:summary",
        "event_type": "evaluation_summary",
        "at_ms": latest_at_ms + 1,
        "payload": dict(evaluation_summary),
    }


def _partial_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "..."
    prefix_length = max(1, min(len(stripped), len(stripped) // 2 or 1))
    return f"{stripped[:prefix_length]}..."
