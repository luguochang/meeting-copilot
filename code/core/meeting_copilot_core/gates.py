from __future__ import annotations

from typing import Any

from meeting_copilot_core.contracts import MeetingStateEventV1

REALTIME_CARD_WINDOW_MS = 30_000
ALLOWED_CARD_TYPES = {
    "owner_gap",
    "rollback_gap",
    "test_verification_gap",
    "metric_monitoring_gap",
}
VALID_SCHEMA_RESULTS = {"valid", "failed", "timeout", "invalid"}
BLOCKING_SCHEMA_RESULTS = {"failed", "timeout", "invalid"}
NON_STRONG_DECISIONS = {
    "silence",
    "too_late",
    "after_meeting_pending",
    "draft",
    "degraded",
}
NON_STRONG_STATUSES = {
    "dismissed",
    "marked_wrong",
    "too_late",
    "too_intrusive",
}


def validate_snapshot_gates(snapshot: dict[str, Any]) -> None:
    evidence_by_id = _evidence_by_id(snapshot)
    state_index = _state_index(snapshot)
    segment_index = _segment_index(snapshot)
    state_events = _state_events(snapshot, evidence_by_id, state_index)
    state_events_by_id = {event.id: event for event in state_events}
    degradation_reasons = list(
        snapshot.get("quality", {}).get("degradation_reasons", [])
    )
    degradation = snapshot.get("quality", {}).get("degradation", {})
    blocks_strong_suggestions = bool(
        degradation_reasons
        or degradation.get("blocks_strong_suggestions", False)
    )

    for card in snapshot.get("suggestion_cards", []):
        card_id = str(card.get("id", "suggestion card"))
        _validate_card_trace(card_id, card, evidence_by_id, state_index, state_events_by_id)
        _validate_realtime_window(card_id, card, segment_index, state_events_by_id)
        _validate_llm_trace(card_id, card)
        if blocks_strong_suggestions and _is_strong_card(card):
            raise ValueError(
                f"degradation blocks strong suggestion card: {card_id}"
            )


def _evidence_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence_by_id = {}
    for evidence in snapshot.get("transcript", {}).get("evidence_spans", []):
        evidence_id = str(evidence.get("id", ""))
        if not evidence_id:
            raise ValueError("evidence span missing id")
        quote = str(evidence.get("quote", "")).strip()
        if not quote:
            raise ValueError(f"evidence span {evidence_id} missing quote")
        evidence_by_id[evidence_id] = evidence
    return evidence_by_id


def _state_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    state_collections = {
        "decision_candidates": "decision_candidate",
        "action_items": "action_item",
        "risks": "risk",
        "open_questions": "open_question",
    }
    for collection_name, ref_prefix in state_collections.items():
        for state in snapshot.get("states", {}).get(collection_name, []):
            state_id = str(state.get("id", "")).strip()
            if state_id:
                index[f"{ref_prefix}:{state_id}"] = state
    return index


def _segment_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index = {}
    for segment in snapshot.get("transcript", {}).get("segments", []):
        segment_id = str(segment.get("id", "")).strip()
        if segment_id:
            index[segment_id] = segment
    return index


def _state_events(
    snapshot: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    state_index: dict[str, dict[str, Any]],
) -> list[MeetingStateEventV1]:
    events = []
    for raw_event in snapshot.get("state_events", []):
        event = MeetingStateEventV1.from_dict(raw_event)
        for evidence_id in event.evidence_span_ids:
            if evidence_id not in evidence_by_id:
                raise ValueError(
                    f"state event {event.id} references unknown evidence_span_id: {evidence_id}"
                )
        target_ref = _state_ref(event.target_type, event.target_id)
        if target_ref not in state_index:
            raise ValueError(f"state event {event.id} target missing: {target_ref}")
        events.append(event)
    return events


def _validate_card_trace(
    card_id: str,
    card: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    state_index: dict[str, dict[str, Any]],
    state_events_by_id: dict[str, MeetingStateEventV1],
) -> None:
    card_type = str(card.get("type", "")).strip()
    if card_type not in ALLOWED_CARD_TYPES:
        raise ValueError(f"unsupported suggestion card type: {card_type}")

    for field_name in ("evidence_span_ids", "state_refs", "state_event_ids"):
        values = _string_list(card.get(field_name))
        if not values:
            raise ValueError(f"{card_id} missing {field_name}")
        card[field_name] = values

    for field_name in ("gap_rule_id", "trigger_reason"):
        if not str(card.get(field_name, "")).strip():
            raise ValueError(f"{card_id} missing {field_name}")

    for evidence_id in card["evidence_span_ids"]:
        if evidence_id not in evidence_by_id:
            raise ValueError(f"{card_id} references unknown evidence_span_id: {evidence_id}")
        evidence_status = str(evidence_by_id[evidence_id].get("status", "active"))
        if evidence_status != "active" and _is_strong_card(card):
            raise ValueError(
                f"{card_id} references stale evidence_span_id: {evidence_id}"
            )

    for state_ref in card["state_refs"]:
        if state_ref not in state_index:
            raise ValueError(f"{card_id} references unknown state_ref: {state_ref}")

    for event_id in card["state_event_ids"]:
        if event_id not in state_events_by_id:
            raise ValueError(f"{card_id} references unknown state_event_id: {event_id}")

    event_targets = {
        _state_ref(event.target_type, event.target_id)
        for event in state_events_by_id.values()
        if event.id in card["state_event_ids"]
    }
    missing_event_targets = set(card["state_refs"]) - event_targets
    if missing_event_targets:
        missing = sorted(missing_event_targets)[0]
        raise ValueError(f"{card_id} state_ref has no matching state_event: {missing}")


def _validate_realtime_window(
    card_id: str,
    card: dict[str, Any],
    segment_index: dict[str, dict[str, Any]],
    state_events_by_id: dict[str, MeetingStateEventV1],
) -> None:
    final_segment_at_ms = _required_int(card_id, card, "final_segment_at_ms")
    state_event_at_ms = _required_int(card_id, card, "state_event_at_ms")
    card_created_at_ms = _required_int(card_id, card, "card_created_at_ms")
    latency_ms = _required_int(card_id, card, "latency_ms")
    segment_batch = _string_list(card.get("segment_batch"))
    if not segment_batch:
        raise ValueError(f"{card_id} missing segment_batch")
    segment_final_times = []
    for segment_id in segment_batch:
        segment = segment_index.get(segment_id)
        if segment is None:
            raise ValueError(f"{card_id} references unknown segment_id: {segment_id}")
        segment_final_times.append(
            int(segment.get("finalized_at_ms", segment.get("end_ms", 0)))
        )
    expected_final_segment_at_ms = max(segment_final_times)
    if final_segment_at_ms != expected_final_segment_at_ms:
        raise ValueError(
            f"{card_id} final_segment_at_ms must match segment batch"
        )

    event_times = []
    for event_id in _string_list(card.get("state_event_ids")):
        event = state_events_by_id.get(event_id)
        if event is not None and event.created_at_ms is not None:
            event_times.append(event.created_at_ms)
    if event_times and state_event_at_ms != max(event_times):
        raise ValueError(
            f"{card_id} state_event_at_ms must match referenced events"
        )

    if state_event_at_ms < final_segment_at_ms:
        raise ValueError(f"{card_id} state_event_at_ms must be >= final_segment_at_ms")
    if card_created_at_ms < state_event_at_ms:
        raise ValueError(f"{card_id} card_created_at_ms must be >= state_event_at_ms")
    observed_latency_ms = card_created_at_ms - final_segment_at_ms
    if observed_latency_ms != latency_ms:
        raise ValueError(f"{card_id} latency_ms must equal card_created_at_ms - final_segment_at_ms")
    if observed_latency_ms > REALTIME_CARD_WINDOW_MS and _is_strong_card(card):
        raise ValueError(f"{card_id} exceeds realtime window")


def _validate_llm_trace(card_id: str, card: dict[str, Any]) -> None:
    for field_name in (
        "trigger_source",
        "prompt_version",
        "model",
        "schema_result",
        "show_or_silence_decision",
    ):
        if not str(card.get(field_name, "")).strip():
            raise ValueError(f"{card_id} missing {field_name}")
    schema_result = str(card.get("schema_result", "")).strip()
    if schema_result not in VALID_SCHEMA_RESULTS:
        raise ValueError(f"{card_id} unsupported schema_result: {schema_result}")
    if schema_result in BLOCKING_SCHEMA_RESULTS and _is_strong_card(card):
        raise ValueError(f"{card_id} schema_result {schema_result} blocks strong suggestion")
    usage = card.get("usage")
    if not isinstance(usage, dict):
        raise ValueError(f"{card_id} missing usage")
    if "total_tokens" not in usage:
        raise ValueError(f"{card_id} missing usage.total_tokens")
    try:
        total_tokens = int(usage["total_tokens"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{card_id} usage.total_tokens must be an integer") from exc
    if total_tokens < 0:
        raise ValueError(f"{card_id} usage.total_tokens must be non-negative")


def _is_strong_card(card: dict[str, Any]) -> bool:
    decision = str(card.get("show_or_silence_decision", "show"))
    status = str(card.get("status", "new"))
    return decision not in NON_STRONG_DECISIONS and status not in NON_STRONG_STATUSES


def _required_int(card_id: str, card: dict[str, Any], field_name: str) -> int:
    if field_name not in card:
        raise ValueError(f"{card_id} missing {field_name}")
    try:
        value = int(card[field_name])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{card_id} {field_name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{card_id} {field_name} must be non-negative")
    return value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = [value]
    else:
        raw_values = list(value)
    return [str(item).strip() for item in raw_values if str(item).strip()]


def _state_ref(target_type: str, target_id: str) -> str:
    target_map = {
        "DecisionCandidate": "decision_candidate",
        "ActionItem": "action_item",
        "Risk": "risk",
        "OpenQuestion": "open_question",
    }
    prefix = target_map.get(str(target_type), str(target_type))
    return f"{prefix}:{target_id}"
