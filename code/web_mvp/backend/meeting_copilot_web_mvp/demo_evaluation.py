from __future__ import annotations

from typing import Any


GATE_VERSION = "web_mvp_fixture.v1"
STATE_COLLECTIONS = (
    "decision_candidates",
    "action_items",
    "risks",
    "open_questions",
)
NON_EFFECTIVE_STATUSES = {
    "dismissed",
    "marked_wrong",
    "too_late",
    "too_intrusive",
}
BLOCKING_SCHEMA_RESULTS = {"failed", "timeout", "invalid"}
KNOWN_SCHEMA_RESULTS = ("failed", "invalid", "timeout", "valid")


def evaluate_demo_snapshot(
    snapshot: dict[str, Any],
    *,
    expected_gap_rule_count: int = 2,
    source: str = "fixture",
) -> dict[str, Any]:
    is_engineering = bool(
        snapshot.get("quality", {}).get(
            "is_engineering_meeting",
            snapshot.get("meeting_context", {}).get("is_engineering_meeting", False),
        )
    )
    states = snapshot.get("states", {})
    cards = list(snapshot.get("suggestion_cards", []))
    state_counts = {
        collection_name: len(states.get(collection_name, []))
        for collection_name in STATE_COLLECTIONS
    }
    effective_cards = [
        card
        for card in cards
        if _is_effective_card(card)
    ]
    silenced_cards = [
        card
        for card in cards
        if not _is_show_decision(card)
    ]
    schema_result_counts = _schema_result_counts(cards)
    gap_rule_ids = sorted(
        {
            str(card.get("gap_rule_id", "")).strip()
            for card in effective_cards
            if str(card.get("gap_rule_id", "")).strip()
        }
    )
    failures = _gate_failures(
        is_engineering=is_engineering,
        state_counts=state_counts,
        suggestion_card_count=len(cards),
        effective_card_count=len(effective_cards),
        gap_rule_count=len(gap_rule_ids),
        expected_gap_rule_count=expected_gap_rule_count,
    )
    return {
        "gate_version": GATE_VERSION,
        "source": source,
        "is_engineering_meeting": is_engineering,
        "state_counts": state_counts,
        "state_event_count": int(snapshot.get("quality", {}).get("state_event_count", 0)),
        "suggestion_card_count": len(cards),
        "effective_card_count": len(effective_cards),
        "gap_rule_ids": gap_rule_ids,
        "gap_rule_count": len(gap_rule_ids),
        "expected_gap_rule_count": expected_gap_rule_count,
        "false_positive_count": _false_positive_count(is_engineering, cards),
        "too_late_count": _status_count(cards, "too_late"),
        "kept_count": _status_count(cards, "kept"),
        "silenced_card_count": len(silenced_cards),
        "schema_blocked_count": sum(
            schema_result_counts.get(result, 0)
            for result in BLOCKING_SCHEMA_RESULTS
        ),
        "schema_result_counts": schema_result_counts,
        "failures": failures,
        "passes_minimum_gate": not failures,
    }


def _is_effective_card(card: dict[str, Any]) -> bool:
    status = str(card.get("status", "new"))
    return status not in NON_EFFECTIVE_STATUSES and _is_show_decision(card)


def _is_show_decision(card: dict[str, Any]) -> bool:
    return str(card.get("show_or_silence_decision", "show")) == "show"


def _gate_failures(
    *,
    is_engineering: bool,
    state_counts: dict[str, int],
    suggestion_card_count: int,
    effective_card_count: int,
    gap_rule_count: int,
    expected_gap_rule_count: int,
) -> list[str]:
    failures = []
    if not is_engineering:
        if suggestion_card_count:
            failures.append("non_engineering_cards")
        return failures

    missing_state_types = [
        collection_name
        for collection_name, count in state_counts.items()
        if count < 1
    ]
    if missing_state_types:
        failures.append("missing_core_state_types")
    if effective_card_count < 1:
        failures.append("missing_engineering_cards")
    if gap_rule_count < expected_gap_rule_count:
        failures.append("insufficient_gap_rules")
    return failures


def _false_positive_count(is_engineering: bool, cards: list[dict[str, Any]]) -> int:
    marked_wrong_count = _status_count(cards, "marked_wrong")
    if is_engineering:
        return marked_wrong_count
    return marked_wrong_count + len(cards)


def _status_count(cards: list[dict[str, Any]], status: str) -> int:
    return sum(1 for card in cards if str(card.get("status", "new")) == status)


def _schema_result_counts(cards: list[dict[str, Any]]) -> dict[str, int]:
    counts = {result: 0 for result in KNOWN_SCHEMA_RESULTS}
    for card in cards:
        result = str(card.get("schema_result", "")).strip()
        if result:
            counts[result] = counts.get(result, 0) + 1
    return {result: count for result, count in counts.items() if count > 0}
