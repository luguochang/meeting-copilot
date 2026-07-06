from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def evaluate_demo_outputs(
    analysis: dict[str, Any],
    transcript_report: dict[str, Any],
    golden: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids = {item["id"] for item in transcript_report.get("evidence_spans", [])}
    card_types = sorted({card.get("type", "") for card in analysis.get("suggestion_cards", [])})
    unknown_evidence = _unknown_evidence_references(analysis, events, evidence_ids)
    raw_entity_recall = _technical_entity_recall(golden, transcript_report.get("text", ""))
    entity_recall = _technical_entity_recall(
        golden,
        transcript_report.get("normalized_text") or transcript_report.get("text", ""),
    )
    failures = _failures(
        analysis=analysis,
        events=events,
        unknown_evidence=unknown_evidence,
        entity_recall=entity_recall,
    )
    return {
        "is_engineering_meeting": bool(
            analysis.get("meeting_context", {}).get("is_engineering_meeting")
        ),
        "state_counts": _state_counts(analysis),
        "suggestion_card_count": len(analysis.get("suggestion_cards", [])),
        "suggestion_card_types": card_types,
        "state_event_count": len(events),
        "unknown_evidence_references": unknown_evidence,
        "raw_technical_entity_recall": raw_entity_recall,
        "technical_entity_recall": entity_recall,
        "failures": failures,
        "passes_minimum_gate": not failures,
    }


def _unknown_evidence_references(
    analysis: dict[str, Any],
    events: list[dict[str, Any]],
    evidence_ids: set[str],
) -> list[str]:
    references: set[str] = set()
    for collection in analysis.get("states", {}).values():
        for item in collection:
            references.update(_evidence_ids(item))
    for card in analysis.get("suggestion_cards", []):
        references.update(_evidence_ids(card))
    for event in events:
        references.update(str(value) for value in event.get("evidence_span_ids", []))
    return sorted(references - evidence_ids)


def _evidence_ids(item: dict[str, Any]) -> list[str]:
    if "evidence_span_ids" in item:
        return [str(value) for value in item["evidence_span_ids"]]
    if "evidence_spans" in item:
        return [str(value) for value in item["evidence_spans"]]
    if "evidence_span_id" in item:
        return [str(item["evidence_span_id"])]
    return []


def _technical_entity_recall(golden: dict[str, Any], transcript_text: str) -> float:
    entities = [str(item["normalized"]) for item in golden.get("technical_entities", [])]
    if not entities:
        return 1.0
    matched = sum(1 for entity in entities if entity in transcript_text)
    return round(matched / len(entities), 6)


def _failures(
    analysis: dict[str, Any],
    events: list[dict[str, Any]],
    unknown_evidence: list[str],
    entity_recall: float,
) -> list[str]:
    failures: list[str] = []
    is_engineering = bool(analysis.get("meeting_context", {}).get("is_engineering_meeting"))
    card_count = len(analysis.get("suggestion_cards", []))
    if not is_engineering and card_count:
        failures.append("non_engineering_cards")
    if is_engineering and card_count == 0:
        failures.append("missing_engineering_cards")
    if is_engineering and any(count == 0 for count in _state_counts(analysis).values()):
        failures.append("missing_core_state_types")
    if is_engineering and not _has_core_state_events(events):
        failures.append("missing_core_state_events")
    if is_engineering and len(events) < 5:
        failures.append("insufficient_state_events")
    if unknown_evidence:
        failures.append("unknown_evidence")
    if is_engineering and entity_recall < 0.7:
        failures.append("low_technical_entity_recall")
    return failures


def _state_counts(analysis: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(analysis.get("states", {}).get(key, []))
        for key in ["decision_candidates", "action_items", "risks", "open_questions"]
    }


def _has_core_state_events(events: list[dict[str, Any]]) -> bool:
    target_types = {str(event.get("target_type", "")) for event in events}
    return {
        "DecisionCandidate",
        "ActionItem",
        "Risk",
        "OpenQuestion",
    }.issubset(target_types)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Meeting Copilot demo outputs.")
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--transcript-report", required=True, type=Path)
    parser.add_argument("--golden", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    transcript_report = json.loads(args.transcript_report.read_text(encoding="utf-8"))
    golden = json.loads(args.golden.read_text(encoding="utf-8")) if args.golden else {}
    events = json.loads(args.events.read_text(encoding="utf-8")) if args.events else []
    report = evaluate_demo_outputs(analysis, transcript_report, golden, events)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
