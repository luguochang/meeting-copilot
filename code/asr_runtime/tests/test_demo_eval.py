from scripts.demo_eval import evaluate_demo_outputs


def test_evaluate_demo_outputs_scores_engineering_demo():
    analysis = {
        "meeting_context": {"is_engineering_meeting": True},
        "states": {
            "decision_candidates": [{"evidence_span_id": "ev_001"}],
            "action_items": [{"evidence_span_id": "ev_001"}],
            "risks": [{"evidence_span_id": "ev_001"}],
            "open_questions": [{"evidence_span_id": "ev_001"}],
        },
        "suggestion_cards": [
            {"type": "owner_gap", "evidence_span_id": "ev_001"},
            {"type": "metric_monitoring_gap", "evidence_span_id": "ev_001"},
        ],
    }
    transcript_report = {
        "evidence_spans": [{"id": "ev_001", "quote": "payment-gateway 先灰度 10%"}],
        "text": "payment-gateway 先灰度 10%",
    }
    golden = {
        "technical_entities": [
            {"normalized": "payment-gateway"},
            {"normalized": "10%"},
        ]
    }
    events = [
        {
            "id": "state_event_001",
            "target_type": "DecisionCandidate",
            "evidence_span_ids": ["ev_001"],
        },
        {
            "id": "state_event_002",
            "target_type": "ActionItem",
            "evidence_span_ids": ["ev_001"],
        },
        {"id": "state_event_003", "target_type": "Risk", "evidence_span_ids": ["ev_001"]},
        {
            "id": "state_event_004",
            "target_type": "OpenQuestion",
            "evidence_span_ids": ["ev_001"],
        },
        {
            "id": "state_event_005",
            "target_type": "SuggestionCard",
            "evidence_span_ids": ["ev_001"],
        },
    ]

    report = evaluate_demo_outputs(analysis, transcript_report, golden, events)

    assert report["is_engineering_meeting"] is True
    assert report["suggestion_card_count"] == 2
    assert report["suggestion_card_types"] == ["metric_monitoring_gap", "owner_gap"]
    assert report["state_event_count"] == 5
    assert report["unknown_evidence_references"] == []
    assert report["technical_entity_recall"] == 1.0
    assert report["passes_minimum_gate"] is True


def test_evaluate_demo_outputs_uses_normalized_text_for_entity_recall():
    analysis = {
        "meeting_context": {"is_engineering_meeting": True},
        "states": {
            "decision_candidates": [{"evidence_span_id": "ev_001"}],
            "action_items": [{"evidence_span_id": "ev_001"}],
            "risks": [{"evidence_span_id": "ev_001"}],
            "open_questions": [{"evidence_span_id": "ev_001"}],
        },
        "suggestion_cards": [{"type": "owner_gap", "evidence_span_id": "ev_001"}],
    }
    transcript_report = {
        "evidence_spans": [{"id": "ev_001", "quote": "payment gate 为 先 灰度"}],
        "text": "payment gate 为 先 灰度 百 分 之 十",
        "normalized_text": "payment-gateway 先灰度 10%",
    }
    golden = {
        "technical_entities": [
            {"normalized": "payment-gateway"},
            {"normalized": "10%"},
        ]
    }
    events = [
        {
            "id": "state_event_001",
            "target_type": "DecisionCandidate",
            "evidence_span_ids": ["ev_001"],
        },
        {
            "id": "state_event_002",
            "target_type": "ActionItem",
            "evidence_span_ids": ["ev_001"],
        },
        {"id": "state_event_003", "target_type": "Risk", "evidence_span_ids": ["ev_001"]},
        {
            "id": "state_event_004",
            "target_type": "OpenQuestion",
            "evidence_span_ids": ["ev_001"],
        },
        {
            "id": "state_event_005",
            "target_type": "SuggestionCard",
            "evidence_span_ids": ["ev_001"],
        },
    ]

    report = evaluate_demo_outputs(analysis, transcript_report, golden, events)

    assert report["raw_technical_entity_recall"] == 0.0
    assert report["technical_entity_recall"] == 1.0
    assert report["passes_minimum_gate"] is True


def test_evaluate_demo_outputs_fails_non_engineering_with_cards():
    analysis = {
        "meeting_context": {"is_engineering_meeting": False},
        "states": {
            "decision_candidates": [],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [{"type": "owner_gap", "evidence_span_id": "ev_001"}],
    }
    transcript_report = {"evidence_spans": [{"id": "ev_001", "quote": "股票投资"}], "text": "股票投资"}

    report = evaluate_demo_outputs(analysis, transcript_report, golden={}, events=[])

    assert report["passes_minimum_gate"] is False
    assert "non_engineering_cards" in report["failures"]


def test_evaluate_demo_outputs_requires_all_core_state_types_for_engineering_demo():
    analysis = {
        "meeting_context": {"is_engineering_meeting": True},
        "states": {
            "decision_candidates": [{"evidence_span_id": "ev_001"}],
            "action_items": [{"evidence_span_id": "ev_001"}],
            "risks": [],
            "open_questions": [{"evidence_span_id": "ev_001"}],
        },
        "suggestion_cards": [{"type": "owner_gap", "evidence_span_id": "ev_001"}],
    }
    transcript_report = {
        "evidence_spans": [{"id": "ev_001", "quote": "payment-gateway 先灰度 10%"}],
        "text": "payment-gateway 先灰度 10%",
    }
    events = [
        {"id": f"state_event_{index:03d}", "evidence_span_ids": ["ev_001"]}
        for index in range(1, 6)
    ]

    report = evaluate_demo_outputs(analysis, transcript_report, golden={}, events=events)

    assert report["passes_minimum_gate"] is False
    assert "missing_core_state_types" in report["failures"]


def test_evaluate_demo_outputs_requires_core_state_event_types_for_engineering_demo():
    analysis = {
        "meeting_context": {"is_engineering_meeting": True},
        "states": {
            "decision_candidates": [{"evidence_span_id": "ev_001"}],
            "action_items": [{"evidence_span_id": "ev_001"}],
            "risks": [{"evidence_span_id": "ev_001"}],
            "open_questions": [{"evidence_span_id": "ev_001"}],
        },
        "suggestion_cards": [{"type": "owner_gap", "evidence_span_id": "ev_001"}],
    }
    transcript_report = {
        "evidence_spans": [{"id": "ev_001", "quote": "payment-gateway 先灰度 10%"}],
        "text": "payment-gateway 先灰度 10%",
    }
    events = [
        {
            "id": f"state_event_{index:03d}",
            "target_type": "SuggestionCard",
            "evidence_span_ids": ["ev_001"],
        }
        for index in range(1, 6)
    ]

    report = evaluate_demo_outputs(analysis, transcript_report, golden={}, events=events)

    assert report["passes_minimum_gate"] is False
    assert "missing_core_state_events" in report["failures"]
