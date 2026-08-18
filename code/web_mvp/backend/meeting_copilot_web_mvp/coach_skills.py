"""Static realtime-coach skill packs shared by direct and Pi execution."""

from __future__ import annotations

from copy import deepcopy
import json
from types import MappingProxyType
from typing import Any, Mapping


_SKILLS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "general": {
            "id": "general",
            "version": 1,
            "name": "General conversation coach",
            "objective": (
                "Protect the user's immediate conversational goal while keeping interventions "
                "rare, specific, and directly speakable."
            ),
            "intervention_style": (
                "Prefer one short next sentence. Do not summarize or restate the dialogue."
            ),
            "checklist": (),
        },
        "decision": {
            "id": "decision",
            "version": 1,
            "name": "Decision readiness coach",
            "objective": (
                "Prevent premature decisions by checking the decision statement, alternatives, "
                "evidence, dissent, owner, and success criteria."
            ),
            "intervention_style": (
                "Ask for the single missing condition that most affects whether the decision is safe."
            ),
            "checklist": (
                {
                    "id": "decision_readiness",
                    "event_type": "decision_readiness",
                    "question": (
                        "Is a decision about to be accepted while a material alternative, evidence, "
                        "owner, success criterion, or unresolved objection is still missing?"
                    ),
                },
            ),
        },
        "project": {
            "id": "project",
            "version": 1,
            "name": "Project execution coach",
            "objective": (
                "Turn project discussion into executable next steps without losing blockers, "
                "dependencies, owners, deadlines, or acceptance criteria."
            ),
            "intervention_style": (
                "Prompt for one missing execution field and name the concrete delivery risk."
            ),
            "checklist": (
                {
                    "id": "execution_readiness",
                    "event_type": "execution_gap",
                    "question": (
                        "Is an action or commitment being closed without an owner, deadline, "
                        "dependency, acceptance criterion, or blocker resolution?"
                    ),
                },
            ),
        },
        "interview": {
            "id": "interview",
            "version": 1,
            "name": "User interview coach",
            "objective": (
                "Help the interviewer uncover concrete behavior, context, frequency, impact, and "
                "counterexamples without leading the interviewee."
            ),
            "intervention_style": (
                "Offer one neutral follow-up question grounded in the interviewee's exact words."
            ),
            "checklist": (
                {
                    "id": "discovery_depth",
                    "event_type": "discovery_gap",
                    "question": (
                        "Is the conversation leaving a claimed pain, need, or behavior before a "
                        "concrete example, context, frequency, impact, or workaround is understood?"
                    ),
                },
            ),
        },
        "brainstorm": {
            "id": "brainstorm",
            "version": 1,
            "name": "Brainstorm convergence coach",
            "objective": (
                "Preserve useful divergence, then help convert a promising idea into a falsifiable "
                "assumption and a small next experiment."
            ),
            "intervention_style": (
                "Do not rank ideas too early. Intervene when one promising direction can be made testable."
            ),
            "checklist": (
                {
                    "id": "experiment_readiness",
                    "event_type": "experiment_gap",
                    "question": (
                        "Has a promising idea reached a point where the key assumption, smallest "
                        "experiment, or success signal should be made explicit?"
                    ),
                },
            ),
        },
    }
)

SUPPORTED_COACH_SKILL_IDS = frozenset(_SKILLS)
BASE_COACH_EVENT_TYPES = frozenset(
    {
        "question_to_user",
        "commitment_risk",
        "goal_at_risk",
        "contradiction",
        "communication_clarity",
    }
)
SCENE_COACH_EVENT_TYPES = frozenset(
    {
        "decision_readiness",
        "execution_gap",
        "discovery_gap",
        "experiment_gap",
    }
)


def normalize_coach_skill_id(value: Any) -> str:
    normalized = str(value or "general").strip().lower()
    return normalized if normalized in SUPPORTED_COACH_SKILL_IDS else "general"


def coach_skill_payload(value: Any) -> dict[str, Any]:
    skill = deepcopy(dict(_SKILLS[normalize_coach_skill_id(value)]))
    skill["checklist"] = [dict(item) for item in skill.get("checklist") or ()]
    return skill


def coach_skill_prompt(value: Any) -> str:
    payload = coach_skill_payload(value)
    return " Active coach skill: " + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def coach_skill_event_types(value: Any) -> frozenset[str]:
    """Return the base events plus the one bounded scene event for this skill."""

    payload = coach_skill_payload(value)
    return BASE_COACH_EVENT_TYPES | {
        str(item["event_type"])
        for item in payload.get("checklist") or ()
        if str(item.get("event_type") or "") in SCENE_COACH_EVENT_TYPES
    }
