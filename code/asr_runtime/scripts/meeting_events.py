from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MeetingStateEvent:
    id: str
    event_type: str
    target_type: str
    target_id: str
    before: dict[str, Any] | None
    after: dict[str, Any]
    evidence_span_ids: list[str]
    source: str
    created_at_ms: int
    reason: str


STATE_COLLECTIONS = {
    "decision_candidates": ("DecisionCandidate", "decision_candidate"),
    "action_items": ("ActionItem", "action_item"),
    "risks": ("Risk", "risk"),
    "open_questions": ("OpenQuestion", "open_question"),
}


def build_state_events(
    analysis: dict[str, Any],
    created_at_ms: int,
    source: str = "llm_analysis",
) -> list[MeetingStateEvent]:
    events: list[MeetingStateEvent] = []
    states = analysis.get("states", {})
    for collection_name, (target_type, id_prefix) in STATE_COLLECTIONS.items():
        for index, state in enumerate(states.get(collection_name, []), start=1):
            target_id = str(state.get("id") or f"{id_prefix}_{index:03d}")
            after = dict(state)
            after.setdefault("id", target_id)
            events.append(
                _created_event(
                    ordinal=len(events) + 1,
                    target_type=target_type,
                    target_id=target_id,
                    after=after,
                    source=source,
                    created_at_ms=created_at_ms,
                )
            )

    for index, card in enumerate(analysis.get("suggestion_cards", []), start=1):
        target_id = str(card.get("id") or f"suggestion_card_{index:03d}")
        after = dict(card)
        after.setdefault("id", target_id)
        events.append(
            _created_event(
                ordinal=len(events) + 1,
                target_type="SuggestionCard",
                target_id=target_id,
                after=after,
                source=source,
                created_at_ms=created_at_ms,
            )
        )
    return events


def _created_event(
    ordinal: int,
    target_type: str,
    target_id: str,
    after: dict[str, Any],
    source: str,
    created_at_ms: int,
) -> MeetingStateEvent:
    return MeetingStateEvent(
        id=f"state_event_{ordinal:03d}",
        event_type="created",
        target_type=target_type,
        target_id=target_id,
        before=None,
        after=after,
        evidence_span_ids=_evidence_span_ids(after),
        source=source,
        created_at_ms=created_at_ms,
        reason=f"Created {target_type} from LLM analysis.",
    )


def _evidence_span_ids(item: dict[str, Any]) -> list[str]:
    if "evidence_span_ids" in item:
        return [str(value) for value in item["evidence_span_ids"]]
    if "evidence_spans" in item:
        return [str(value) for value in item["evidence_spans"]]
    if "evidence_span_id" in item:
        return [str(item["evidence_span_id"])]
    return []
