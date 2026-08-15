from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 2)


def _expected_event_types(expected: Mapping[str, Any]) -> set[str]:
    values = expected.get("event_types")
    if isinstance(values, list):
        return {str(value) for value in values if str(value)}
    value = str(expected.get("event_type") or "")
    return {value} if value else set()


def score_predictions(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = [dict(item) for item in records]
    expected_interventions = 0
    predicted_interventions = 0
    correct_interventions = 0
    false_positives = 0
    false_negatives = 0
    correct_silences = 0
    expected_silences = 0
    evidence_checks = 0
    evidence_hits = 0
    latency_checks = 0
    latency_hits = 0
    fallback_count = 0
    error_count = 0
    latencies: list[float] = []
    agent_turns: list[float] = []

    for item in items:
        expected = item.get("expected") if isinstance(item.get("expected"), Mapping) else {}
        prediction = item.get("prediction") if isinstance(item.get("prediction"), Mapping) else {}
        expected_action = str(expected.get("action") or "silent")
        predicted_action = str(prediction.get("action") or "error")
        expected_types = _expected_event_types(expected)
        predicted_type = str(prediction.get("event_type") or "")
        if item.get("error"):
            error_count += 1
        if item.get("runtime_requested") == "pi" and item.get("runtime_used") != "pi":
            fallback_count += 1
        latency = item.get("latency_ms")
        if isinstance(latency, (int, float)) and latency >= 0:
            latencies.append(float(latency))
            deadline = expected.get("deadline_ms")
            if isinstance(deadline, (int, float)) and deadline > 0:
                latency_checks += 1
                latency_hits += int(latency <= deadline)
        turns = item.get("agent_turns")
        if isinstance(turns, (int, float)) and turns >= 0:
            agent_turns.append(float(turns))

        if expected_action == "intervention":
            expected_interventions += 1
        else:
            expected_silences += 1
        if predicted_action == "intervention":
            predicted_interventions += 1

        intervention_correct = (
            expected_action == "intervention"
            and predicted_action == "intervention"
            and (not expected_types or predicted_type in expected_types)
        )
        if intervention_correct:
            correct_interventions += 1
        elif predicted_action == "intervention":
            false_positives += 1
        if expected_action == "intervention" and not intervention_correct:
            false_negatives += 1
        if expected_action == "silent" and predicted_action == "silent":
            correct_silences += 1

        required_evidence = {
            str(value) for value in expected.get("required_evidence_ids") or [] if str(value)
        }
        if expected_action == "intervention" and required_evidence:
            evidence_checks += 1
            predicted_evidence = {
                str(value) for value in prediction.get("evidence_segment_ids") or [] if str(value)
            }
            evidence_hits += int(required_evidence.issubset(predicted_evidence))

    return {
        "case_count": len(items),
        "expected_intervention_count": expected_interventions,
        "predicted_intervention_count": predicted_interventions,
        "correct_intervention_count": correct_interventions,
        "false_positive_count": false_positives,
        "false_negative_count": false_negatives,
        "precision": _ratio(correct_interventions, predicted_interventions),
        "recall": _ratio(correct_interventions, expected_interventions),
        "silent_accuracy": _ratio(correct_silences, expected_silences),
        "required_evidence_accuracy": _ratio(evidence_hits, evidence_checks),
        "deadline_pass_rate": _ratio(latency_hits, latency_checks),
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "average_agent_turns": (
            round(sum(agent_turns) / len(agent_turns), 2) if agent_turns else None
        ),
        "fallback_count": fallback_count,
        "error_count": error_count,
    }
