from meeting_copilot_web_mvp.demo_evaluation import evaluate_demo_snapshot


def test_engineering_snapshot_fails_when_gap_rule_coverage_is_too_low():
    summary = evaluate_demo_snapshot(
        {
            "meeting_context": {"is_engineering_meeting": True},
            "quality": {
                "is_engineering_meeting": True,
                "state_event_count": 4,
            },
            "states": {
                "decision_candidates": [{"id": "decision_001"}],
                "action_items": [{"id": "action_001"}],
                "risks": [{"id": "risk_001"}],
                "open_questions": [{"id": "question_001"}],
            },
            "suggestion_cards": [
                {
                    "id": "card_001",
                    "status": "new",
                    "show_or_silence_decision": "show",
                    "gap_rule_id": "owner.required",
                }
            ],
        },
        expected_gap_rule_count=2,
    )

    assert summary["passes_minimum_gate"] is False
    assert summary["gap_rule_count"] == 1
    assert summary["effective_card_count"] == 1
    assert summary["failures"] == ["insufficient_gap_rules"]


def test_non_engineering_snapshot_counts_cards_as_false_positives():
    summary = evaluate_demo_snapshot(
        {
            "meeting_context": {"is_engineering_meeting": False},
            "quality": {
                "is_engineering_meeting": False,
                "state_event_count": 0,
            },
            "states": {
                "decision_candidates": [],
                "action_items": [],
                "risks": [],
                "open_questions": [],
            },
            "suggestion_cards": [
                {
                    "id": "card_001",
                    "status": "new",
                    "show_or_silence_decision": "show",
                    "gap_rule_id": "owner.required",
                }
            ],
        },
        expected_gap_rule_count=0,
    )

    assert summary["passes_minimum_gate"] is False
    assert summary["false_positive_count"] == 1
    assert summary["failures"] == ["non_engineering_cards"]


def test_schema_blocked_cards_are_counted_but_not_effective():
    summary = evaluate_demo_snapshot(
        {
            "meeting_context": {"is_engineering_meeting": True},
            "quality": {
                "is_engineering_meeting": True,
                "state_event_count": 4,
            },
            "states": {
                "decision_candidates": [{"id": "decision_001"}],
                "action_items": [{"id": "action_001"}],
                "risks": [{"id": "risk_001"}],
                "open_questions": [{"id": "question_001"}],
            },
            "suggestion_cards": [
                {
                    "id": "card_show",
                    "status": "new",
                    "show_or_silence_decision": "show",
                    "schema_result": "valid",
                    "gap_rule_id": "rollback.required",
                },
                {
                    "id": "card_timeout",
                    "status": "new",
                    "show_or_silence_decision": "silence",
                    "schema_result": "timeout",
                    "gap_rule_id": "llm.timeout",
                },
                {
                    "id": "card_invalid",
                    "status": "new",
                    "show_or_silence_decision": "silence",
                    "schema_result": "invalid",
                    "gap_rule_id": "llm.invalid",
                },
            ],
        },
        expected_gap_rule_count=1,
    )

    assert summary["passes_minimum_gate"] is True
    assert summary["effective_card_count"] == 1
    assert summary["gap_rule_ids"] == ["rollback.required"]
    assert summary["silenced_card_count"] == 2
    assert summary["schema_blocked_count"] == 2
    assert summary["schema_result_counts"] == {
        "invalid": 1,
        "timeout": 1,
        "valid": 1,
    }
