from scripts.meeting_events import MeetingStateEvent, build_state_events


def test_build_state_events_creates_events_for_states_and_cards():
    analysis = {
        "states": {
            "decision_candidates": [
                {
                    "id": "dc_001",
                    "title": "先灰度 10%",
                    "evidence_span_id": "ev_001",
                }
            ],
            "action_items": [
                {
                    "id": "ai_001",
                    "title": "补兼容性测试",
                    "evidence_span_id": "ev_002",
                }
            ],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [
            {
                "id": "sc_001",
                "type": "owner_gap",
                "title": "确认回滚负责人",
                "evidence_span_id": "ev_001",
            }
        ],
    }

    events = build_state_events(analysis, created_at_ms=12000)

    assert events == [
        MeetingStateEvent(
            id="state_event_001",
            event_type="created",
            target_type="DecisionCandidate",
            target_id="dc_001",
            before=None,
            after={
                "id": "dc_001",
                "title": "先灰度 10%",
                "evidence_span_id": "ev_001",
            },
            evidence_span_ids=["ev_001"],
            source="llm_analysis",
            created_at_ms=12000,
            reason="Created DecisionCandidate from LLM analysis.",
        ),
        MeetingStateEvent(
            id="state_event_002",
            event_type="created",
            target_type="ActionItem",
            target_id="ai_001",
            before=None,
            after={
                "id": "ai_001",
                "title": "补兼容性测试",
                "evidence_span_id": "ev_002",
            },
            evidence_span_ids=["ev_002"],
            source="llm_analysis",
            created_at_ms=12000,
            reason="Created ActionItem from LLM analysis.",
        ),
        MeetingStateEvent(
            id="state_event_003",
            event_type="created",
            target_type="SuggestionCard",
            target_id="sc_001",
            before=None,
            after={
                "id": "sc_001",
                "type": "owner_gap",
                "title": "确认回滚负责人",
                "evidence_span_id": "ev_001",
            },
            evidence_span_ids=["ev_001"],
            source="llm_analysis",
            created_at_ms=12000,
            reason="Created SuggestionCard from LLM analysis.",
        ),
    ]


def test_build_state_events_generates_missing_target_ids():
    analysis = {
        "states": {
            "decision_candidates": [{"statement": "先灰度", "evidence_span_id": "ev_001"}],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [],
    }

    events = build_state_events(analysis, created_at_ms=0)

    assert events[0].target_id == "decision_candidate_001"
    assert events[0].after["id"] == "decision_candidate_001"
