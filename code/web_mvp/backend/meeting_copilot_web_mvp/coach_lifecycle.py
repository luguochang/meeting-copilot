"""Shared lifecycle rules for durable coach interventions."""

from __future__ import annotations

from typing import Any, Mapping


def coach_decision_prompt_profile(decision: Mapping[str, Any]) -> str:
    profile = str(decision.get("prompt_profile") or "").strip()
    if profile:
        return profile
    metrics = decision.get("agent_metrics")
    if isinstance(metrics, Mapping):
        return str(metrics.get("prompt_profile") or "").strip()
    return ""


def is_deep_answer_coach_decision(decision: Mapping[str, Any]) -> bool:
    return coach_decision_prompt_profile(decision) == "deep_answer"


def coach_decision_replaces_prior_intervention(
    payload: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> bool:
    """Return whether one append-only decision changes the visible coach card."""

    if isinstance(payload.get("coach_intervention"), Mapping):
        return True
    if str(decision.get("lifecycle_action") or "") == "retract":
        return True
    return bool(
        decision.get("lifecycle_refresh") is True
        and str(decision.get("lifecycle_action") or "") == "deprioritize"
    )
