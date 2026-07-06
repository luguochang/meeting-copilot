from __future__ import annotations

from copy import deepcopy
from typing import Any

from meeting_copilot_core.contracts import CARD_STATUSES_V1, TranscriptReportV1
from meeting_copilot_core.gates import validate_snapshot_gates

CARD_STATUSES = CARD_STATUSES_V1
STATE_COLLECTIONS = (
    "decision_candidates",
    "action_items",
    "risks",
    "open_questions",
)


def build_session_snapshot(
    *,
    session_id: str,
    transcript_report: dict[str, Any],
    analysis: dict[str, Any],
    state_events: list[dict[str, Any]],
    llm_usage: dict[str, Any] | None = None,
    card_statuses: dict[str, str] | None = None,
    degradation_reasons: list[str] | None = None,
) -> dict[str, Any]:
    normalized_report = TranscriptReportV1.from_dict(transcript_report).to_dict()
    evidence_by_id = {
        str(item["id"]): deepcopy(item)
        for item in normalized_report.get("evidence_spans", [])
    }
    states = _normalize_states(analysis.get("states", {}), evidence_by_id)
    cards = _normalize_cards(
        analysis=analysis,
        evidence_by_id=evidence_by_id,
        card_statuses=card_statuses or {},
    )
    usage = llm_usage or {}
    snapshot = {
        "session_id": session_id,
        "summary": str(analysis.get("summary", "")),
        "meeting_context": deepcopy(analysis.get("meeting_context", {})),
        "transcript": {
            "text": str(normalized_report.get("text", "")),
            "normalized_text": str(normalized_report.get("normalized_text", "")),
            "segments": deepcopy(normalized_report.get("segments", [])),
            "evidence_spans": list(evidence_by_id.values()),
        },
        "states": states,
        "suggestion_cards": cards,
        "state_events": deepcopy(state_events),
        "quality": {
            "provider": transcript_report.get("provider", "unknown"),
            "latency_ms": int(transcript_report.get("latency_ms", 0)),
            "rtf": float(transcript_report.get("rtf", 0.0)),
            "is_engineering_meeting": bool(
                analysis.get("meeting_context", {}).get("is_engineering_meeting", False)
            ),
            "evidence_span_count": len(evidence_by_id),
            "state_event_count": len(state_events),
            "suggestion_card_count": len(cards),
            "llm_call_count": int(usage.get("call_count", 0)),
            "llm_total_tokens": int(usage.get("usage", {}).get("total_tokens", 0)),
            "degradation_reasons": list(degradation_reasons or []),
        },
    }
    validate_snapshot_gates(snapshot)
    return snapshot


def build_markdown_report(snapshot: dict[str, Any]) -> str:
    lines = [
        f"# Meeting {snapshot['session_id']}",
        "",
        "## Summary",
        snapshot.get("summary", ""),
        "",
        "## States",
    ]
    states = snapshot.get("states", {})
    for collection_name in STATE_COLLECTIONS:
        lines.append(f"### {collection_name}")
        items = states.get(collection_name, [])
        if not items:
            lines.append("- None")
            continue
        for item in items:
            text = _state_display_text(collection_name, item)
            lines.append(f"- {text} (evidence: {', '.join(item['evidence_span_ids'])})")

    lines.extend(["", "## Suggestion Cards"])
    cards = snapshot.get("suggestion_cards", [])
    visible_cards = [
        card
        for card in cards
        if str(card.get("show_or_silence_decision", "show")) == "show"
    ]
    silenced_cards = [
        card
        for card in cards
        if str(card.get("show_or_silence_decision", "show")) != "show"
    ]
    if not visible_cards:
        lines.append("- None")
    for card in visible_cards:
        question = card.get("title") or card.get("suggested_question") or card.get("id")
        lines.append(
            f"- [{card['status']}] {question} "
            f"(type: {card.get('type', 'unknown')}; evidence: {', '.join(card['evidence_span_ids'])})"
        )

    if silenced_cards:
        lines.extend(["", "## Silenced Suggestion Records"])
        for card in silenced_cards:
            question = card.get("title") or card.get("suggested_question") or card.get("id")
            decision = str(card.get("show_or_silence_decision", "silence"))
            schema_result = str(card.get("schema_result", "unknown"))
            lines.append(
                f"- [{decision}; schema: {schema_result}] {question} "
                f"(type: {card.get('type', 'unknown')}; evidence: {', '.join(card['evidence_span_ids'])})"
            )

    lines.extend(["", "## Evidence"])
    for evidence in snapshot.get("transcript", {}).get("evidence_spans", []):
        lines.append(f"- {evidence['id']}: {evidence.get('quote', '')}")
    return "\n".join(lines).rstrip() + "\n"


def _normalize_states(
    states: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    normalized: dict[str, list[dict[str, Any]]] = {}
    for collection_name in STATE_COLLECTIONS:
        normalized[collection_name] = [
            _normalize_evidence_item(item, evidence_by_id, f"states.{collection_name}")
            for item in states.get(collection_name, [])
        ]
    return normalized


def _normalize_cards(
    *,
    analysis: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    card_statuses: dict[str, str],
) -> list[dict[str, Any]]:
    cards = analysis.get("suggestion_cards", [])
    is_engineering = bool(
        analysis.get("meeting_context", {}).get("is_engineering_meeting", False)
    )
    if cards and not is_engineering:
        raise ValueError("non-engineering meeting must not expose suggestion cards")

    normalized_cards = []
    for index, card in enumerate(cards, start=1):
        normalized = _normalize_evidence_item(card, evidence_by_id, "suggestion card")
        card_id = str(normalized.get("id") or f"card_{index:03d}")
        status = card_statuses.get(card_id, "new")
        if status not in CARD_STATUSES:
            raise ValueError(f"unsupported suggestion card status: {status}")
        normalized["id"] = card_id
        normalized["status"] = status
        normalized_cards.append(normalized)
    return normalized_cards


def _normalize_evidence_item(
    item: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    normalized = deepcopy(item)
    evidence_ids = _evidence_ids(normalized)
    if not evidence_ids:
        if label == "suggestion card":
            raise ValueError("suggestion card missing evidence_span_ids")
        raise ValueError(f"{label} missing evidence_span_ids")
    for evidence_id in evidence_ids:
        if evidence_id not in evidence_by_id:
            raise ValueError(f"{label} references unknown evidence_span_id: {evidence_id}")
    normalized["evidence_span_ids"] = evidence_ids
    normalized.pop("evidence_span_id", None)
    normalized.pop("evidence_spans", None)
    return normalized


def _evidence_ids(item: dict[str, Any]) -> list[str]:
    if "evidence_span_ids" in item:
        return [str(value) for value in item["evidence_span_ids"]]
    if "evidence_spans" in item:
        return [str(value) for value in item["evidence_spans"]]
    if "evidence_span_id" in item:
        return [str(item["evidence_span_id"])]
    return []


def _state_display_text(collection_name: str, item: dict[str, Any]) -> str:
    if collection_name == "decision_candidates":
        return str(item.get("statement") or item.get("description") or item.get("id"))
    if collection_name == "action_items":
        return str(item.get("description") or item.get("id"))
    if collection_name == "risks":
        return str(item.get("description") or item.get("id"))
    if collection_name == "open_questions":
        return str(item.get("question") or item.get("description") or item.get("id"))
    return str(item.get("id", ""))
