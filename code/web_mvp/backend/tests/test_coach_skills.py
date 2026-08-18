from __future__ import annotations

from meeting_copilot_web_mvp.coach_skills import (
    BASE_COACH_EVENT_TYPES,
    SCENE_COACH_EVENT_TYPES,
    SUPPORTED_COACH_SKILL_IDS,
    coach_skill_event_types,
    coach_skill_payload,
    normalize_coach_skill_id,
)


def test_all_supported_skills_have_a_bounded_versioned_contract() -> None:
    assert SUPPORTED_COACH_SKILL_IDS == {
        "general",
        "decision",
        "project",
        "interview",
        "brainstorm",
    }
    for skill_id in SUPPORTED_COACH_SKILL_IDS:
        skill = coach_skill_payload(skill_id)
        assert skill["id"] == skill_id
        assert skill["version"] == 1
        assert skill["name"]
        assert skill["objective"]
        assert skill["intervention_style"]
        assert len(skill["checklist"]) <= 4
        assert all(item["event_type"] in SCENE_COACH_EVENT_TYPES for item in skill["checklist"])


def test_unknown_skill_fails_closed_to_general() -> None:
    assert normalize_coach_skill_id("untrusted-custom-prompt") == "general"
    assert coach_skill_payload("untrusted-custom-prompt")["id"] == "general"


def test_each_scene_skill_only_enables_its_own_extra_event() -> None:
    assert coach_skill_event_types("general") == BASE_COACH_EVENT_TYPES
    assert coach_skill_event_types("interview") == BASE_COACH_EVENT_TYPES | {"discovery_gap"}
    assert "execution_gap" not in coach_skill_event_types("interview")
