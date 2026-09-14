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


def _failure_class(item: Mapping[str, Any], prediction: Mapping[str, Any]) -> str | None:
    """Normalize replay failure provenance for scoring.

    Older replay records may not have ``failure_class`` yet, so derive the
    conservative equivalent from their status and fallback fields.  A
    ``protected_silent`` or ``not_triggered`` record without an explicit
    failure marker remains a valid non-error decision.
    """

    status = str(prediction.get("status") or item.get("status") or "").strip().lower()
    requested_runtime = str(item.get("runtime_requested") or "").strip().lower()
    attempted = item.get("decision_attempted", True)
    # Replay records from a normal trigger gate and the explicitly requested
    # local baseline are valid non-error outcomes. Older reports may contain a
    # derived ``runtime_fallback`` marker for these statuses; override that
    # stale marker before trusting explicit provenance.
    if status == "not_triggered" and attempted is False and not item.get(
        "fallback_error_code"
    ):
        return None
    if (
        requested_runtime == "local"
        and status == "protected_silent"
        and not item.get("fallback_error_code")
        and not item.get("error")
    ):
        return None
    explicit = str(item.get("failure_class") or "").strip()
    if explicit:
        return explicit
    code = str(
        item.get("fallback_error_code")
        or item.get("fallback_reason")
        or prediction.get("status_reason")
        or ""
    ).strip().lower()
    if status == "timed_out" or code in {
        "provider_timeout",
        "soft_deadline_exceeded",
        "agent_deadline_exceeded",
        "deadline_budget_exhausted",
        "evaluation_deadline_exceeded",
    }:
        return "timeout"
    if "429" in code or "rate_limit" in code or "rate_limited" in code:
        return "rate_limit"
    if "5xx" in code or "provider_http_5" in code or code.startswith("provider_5"):
        return "provider_5xx"
    if "transport" in code or "connection" in code:
        return "transport_error"
    if "validation" in code or "invalid_response" in code:
        return "validation_error"
    if status in {"failed", "error"}:
        return "execution_error"
    return "runtime_fallback" if code else None


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
    intervention_evidence_checks = 0
    intervention_evidence_hits = 0
    silent_reason_checks = 0
    silent_reason_hits = 0
    duplicate_intervention_count = 0
    lifecycle_checks = 0
    lifecycle_hits = 0
    supersession_checks = 0
    supersession_hits = 0
    production_trigger_checks = 0
    production_trigger_hits = 0
    production_trigger_predicted = 0
    production_trigger_false_positive_count = 0
    agent_decision_checks = 0
    agent_decision_hits = 0
    timed_out_count = 0
    failed_count = 0
    failed_open_silent_count = 0
    failure_class_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    latencies: list[float] = []
    completed_latencies: list[float] = []
    timeout_latencies: list[float] = []
    agent_turns: list[float] = []
    seen_interventions: dict[str, set[tuple[str, tuple[str, ...]]]] = {}

    for item in items:
        expected = item.get("expected") if isinstance(item.get("expected"), Mapping) else {}
        prediction = item.get("prediction") if isinstance(item.get("prediction"), Mapping) else {}
        expected_action = str(expected.get("action") or "silent")
        predicted_action = str(prediction.get("action") or "error")
        expected_types = _expected_event_types(expected)
        predicted_type = str(prediction.get("event_type") or "")
        expected_lifecycle = str(expected.get("lifecycle_action") or "").strip()
        if expected_lifecycle:
            lifecycle_checks += 1
            lifecycle_hits += int(
                str(prediction.get("lifecycle_action") or "").strip() == expected_lifecycle
            )
        expected_supersedes = str(expected.get("supersedes_case_id") or "").strip()
        if expected_supersedes:
            supersession_checks += 1
            supersession_hits += int(
                str(prediction.get("supersedes_case_id") or "").strip()
                == expected_supersedes
            )
        status = str(prediction.get("status") or item.get("status") or "").strip()
        failure_class = _failure_class(item, prediction)
        if failure_class is not None:
            failure_class_counts[failure_class] = failure_class_counts.get(failure_class, 0) + 1
            if predicted_action == "silent":
                failed_open_silent_count += 1
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        if status == "timed_out":
            timed_out_count += 1
        elif status in {"failed", "error"}:
            failed_count += 1
        production_triggered = item.get("production_triggered")
        if isinstance(production_triggered, bool):
            production_trigger_checks += int(expected_action == "intervention")
            production_trigger_hits += int(expected_action == "intervention" and production_triggered)
            production_trigger_predicted += int(production_triggered)
            production_trigger_false_positive_count += int(
                expected_action == "silent" and production_triggered
            )
        if item.get("error"):
            error_count += 1
        if (
            item.get("runtime_requested") == "pi"
            and item.get("decision_attempted", True) is not False
            and item.get("runtime_used") != "pi"
        ):
            fallback_count += 1
        latency = item.get("latency_ms")
        decision_completed = (
            predicted_action in {"silent", "intervention"}
            and failure_class is None
            and status not in {"timed_out", "failed", "error"}
            and not item.get("error")
        )
        if (
            isinstance(latency, (int, float))
            and not isinstance(latency, bool)
            and math.isfinite(latency)
            and latency >= 0
            and item.get("decision_attempted", True) is not False
        ):
            latencies.append(float(latency))
            if decision_completed:
                completed_latencies.append(float(latency))
            elif status == "timed_out":
                timeout_latencies.append(float(latency))
            deadline = expected.get("deadline_ms")
            if isinstance(deadline, (int, float)) and deadline > 0:
                latency_checks += 1
                latency_hits += int(decision_completed and latency <= deadline)
        turns = item.get("agent_turns")
        if isinstance(turns, (int, float)) and turns >= 0:
            agent_turns.append(float(turns))

        if expected_action == "intervention":
            expected_interventions += 1
        else:
            expected_silences += 1
        if predicted_action == "intervention":
            predicted_interventions += 1
            evidence_ids = tuple(
                sorted({str(value) for value in prediction.get("evidence_segment_ids") or [] if str(value)})
            )
            intervention_evidence_checks += 1
            intervention_evidence_hits += int(bool(evidence_ids))
            session_id = str(item.get("session_id") or item.get("case_id") or "")
            fingerprint = (predicted_type, evidence_ids)
            session_interventions = seen_interventions.setdefault(session_id, set())
            if fingerprint in session_interventions:
                duplicate_intervention_count += 1
            else:
                session_interventions.add(fingerprint)
        elif predicted_action == "silent":
            silent_reason_checks += 1
            silent_reason_hits += int(bool(str(prediction.get("decision_reason") or "").strip()))

        intervention_correct = (
            expected_action == "intervention"
            and predicted_action == "intervention"
            and (not expected_types or predicted_type in expected_types)
        )
        if expected_action == "intervention" and production_triggered is not False:
            agent_decision_checks += 1
            agent_decision_hits += int(intervention_correct)
        if intervention_correct:
            correct_interventions += 1
        elif predicted_action == "intervention":
            false_positives += 1
        if expected_action == "intervention" and not intervention_correct:
            false_negatives += 1
        if (
            expected_action == "silent"
            and predicted_action == "silent"
            and failure_class is None
            and status not in {"timed_out", "failed", "error"}
        ):
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
        "intervention_evidence_rate": _ratio(intervention_evidence_hits, intervention_evidence_checks),
        "silent_reason_rate": _ratio(silent_reason_hits, silent_reason_checks),
        "duplicate_intervention_count": duplicate_intervention_count,
        "expected_lifecycle_count": lifecycle_checks,
        "lifecycle_action_accuracy": _ratio(lifecycle_hits, lifecycle_checks),
        "expected_supersession_count": supersession_checks,
        "supersession_accuracy": _ratio(supersession_hits, supersession_checks),
        "production_trigger_recall": _ratio(production_trigger_hits, production_trigger_checks),
        "production_trigger_precision": _ratio(
            production_trigger_hits,
            production_trigger_predicted,
        ),
        "production_trigger_false_positive_count": production_trigger_false_positive_count,
        "agent_decision_recall": _ratio(agent_decision_hits, agent_decision_checks),
        "end_to_end_recall": _ratio(correct_interventions, expected_interventions),
        "deadline_pass_rate": _ratio(latency_hits, latency_checks),
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "latency_max_ms": round(max(latencies), 2) if latencies else None,
        # A cancelled request's elapsed time is not its unknown completion time.
        "latency_semantics": "attempt_elapsed_including_failures.v1",
        "completed_decision_latency_count": len(completed_latencies),
        "completed_decision_latency_p50_ms": _percentile(completed_latencies, 0.5),
        "completed_decision_latency_p95_ms": _percentile(completed_latencies, 0.95),
        "timeout_observed_latency_count": len(timeout_latencies),
        "timeout_observed_latency_p50_ms": _percentile(timeout_latencies, 0.5),
        "average_agent_turns": (
            round(sum(agent_turns) / len(agent_turns), 2) if agent_turns else None
        ),
        "fallback_count": fallback_count,
        "error_count": error_count,
        "timed_out_count": timed_out_count,
        "failed_count": failed_count,
        "reliability_error_count": sum(failure_class_counts.values()),
        "failure_class_counts": failure_class_counts,
        "failed_open_silent_count": failed_open_silent_count,
        "status_counts": status_counts,
    }
