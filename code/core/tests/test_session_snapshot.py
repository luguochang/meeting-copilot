import pytest

from meeting_copilot_core.session_snapshot import (
    build_markdown_report,
    build_session_snapshot,
)


def _transcript_report():
    return {
        "provider": "funasr",
        "latency_ms": 1800,
        "rtf": 0.42,
        "text": "payment-gateway 先灰度 10%。还没有确认回滚负责人。",
        "normalized_text": "payment-gateway 先灰度 10%。还没有确认回滚负责人。",
        "segments": [
            {
                "id": "seg_001",
                "start_ms": 0,
                "end_ms": 5000,
                "text": "payment-gateway 先灰度 10%。",
                "confidence": 0.91,
            },
            {
                "id": "seg_002",
                "start_ms": 5000,
                "end_ms": 9000,
                "text": "还没有确认回滚负责人。",
                "confidence": 0.88,
            },
        ],
        "evidence_spans": [
            {
                "id": "ev_001",
                "segment_id": "seg_001",
                "start_ms": 0,
                "end_ms": 5000,
                "quote": "payment-gateway 先灰度 10%。",
            },
            {
                "id": "ev_002",
                "segment_id": "seg_002",
                "start_ms": 5000,
                "end_ms": 9000,
                "quote": "还没有确认回滚负责人。",
            },
        ],
    }


def _engineering_analysis():
    return {
        "summary": "讨论 payment-gateway 灰度发布。",
        "meeting_context": {
            "is_engineering_meeting": True,
            "reason": "包含灰度、回滚负责人等发布评审内容。",
        },
        "states": {
            "decision_candidates": [
                {
                    "id": "decision_001",
                    "statement": "payment-gateway 先灰度 10%",
                    "evidence_span_id": "ev_001",
                }
            ],
            "action_items": [],
            "risks": [],
            "open_questions": [
                {
                    "id": "question_001",
                    "question": "谁负责回滚？",
                    "evidence_span_ids": ["ev_002"],
                }
            ],
        },
        "suggestion_cards": [
            {
                "id": "card_001",
                "type": "owner_gap",
                "suggested_question": "是否需要确认回滚负责人？",
                "evidence_span_id": "ev_002",
                "state_refs": ["open_question:question_001"],
                "state_event_ids": ["event_001"],
                "gap_rule_id": "owner.required",
                "trigger_reason": "候选灰度决策缺少回滚负责人",
                "trigger_source": "state_gap_detector",
                "final_segment_at_ms": 9000,
                "state_event_at_ms": 9600,
                "card_created_at_ms": 13800,
                "latency_ms": 4800,
                "prompt_version": "suggestion-card.v1",
                "model": "gpt-5.5",
                "usage": {"total_tokens": 321},
                "schema_result": "valid",
                "show_or_silence_decision": "show",
                "segment_batch": ["seg_002"],
            }
        ],
    }


def _state_events():
    return [
        {
            "id": "event_001",
            "target_type": "OpenQuestion",
            "target_id": "question_001",
            "event_type": "created",
            "created_at_ms": 9600,
            "evidence_span_ids": ["ev_002"],
        }
    ]


def test_build_session_snapshot_keeps_traceable_engineering_copilot_state():
    snapshot = build_session_snapshot(
        session_id="meeting_001",
        transcript_report=_transcript_report(),
        analysis=_engineering_analysis(),
        state_events=_state_events(),
        llm_usage={
            "model": "gpt-5.5",
            "call_count": 1,
            "usage": {"total_tokens": 1234},
        },
    )

    assert snapshot["session_id"] == "meeting_001"
    assert snapshot["transcript"]["text"] == _transcript_report()["text"]
    assert snapshot["transcript"]["normalized_text"] == _transcript_report()["normalized_text"]
    assert snapshot["states"]["decision_candidates"][0]["evidence_span_ids"] == ["ev_001"]
    assert snapshot["states"]["open_questions"][0]["evidence_span_ids"] == ["ev_002"]
    assert snapshot["suggestion_cards"][0]["status"] == "new"
    assert snapshot["suggestion_cards"][0]["evidence_span_ids"] == ["ev_002"]
    assert snapshot["quality"] == {
        "provider": "funasr",
        "latency_ms": 1800,
        "rtf": 0.42,
        "is_engineering_meeting": True,
        "evidence_span_count": 2,
        "state_event_count": 1,
        "suggestion_card_count": 1,
        "llm_call_count": 1,
        "llm_total_tokens": 1234,
        "degradation_reasons": [],
    }


def test_build_session_snapshot_rejects_cards_without_evidence():
    analysis = _engineering_analysis()
    analysis["suggestion_cards"] = [
        {
            "id": "card_without_evidence",
            "type": "owner_gap",
            "suggested_question": "是否需要确认负责人？",
        }
    ]

    with pytest.raises(ValueError, match="suggestion card missing evidence_span_ids"):
        build_session_snapshot(
            session_id="meeting_001",
            transcript_report=_transcript_report(),
            analysis=analysis,
            state_events=[],
        )


def test_build_session_snapshot_rejects_evidence_for_unknown_segment():
    transcript_report = _transcript_report()
    transcript_report["evidence_spans"][0]["segment_id"] = "seg_missing"

    with pytest.raises(ValueError, match="evidence span references unknown segment_id"):
        build_session_snapshot(
            session_id="meeting_001",
            transcript_report=transcript_report,
            analysis=_engineering_analysis(),
            state_events=_state_events(),
        )


def test_build_session_snapshot_rejects_non_engineering_cards():
    analysis = _engineering_analysis()
    analysis["meeting_context"] = {
        "is_engineering_meeting": False,
        "reason": "这是闲聊。",
    }

    with pytest.raises(ValueError, match="non-engineering meeting must not expose suggestion cards"):
        build_session_snapshot(
            session_id="meeting_001",
            transcript_report=_transcript_report(),
            analysis=analysis,
            state_events=[],
        )


def test_build_session_snapshot_applies_card_statuses():
    snapshot = build_session_snapshot(
        session_id="meeting_001",
        transcript_report=_transcript_report(),
        analysis=_engineering_analysis(),
        state_events=_state_events(),
        card_statuses={"card_001": "dismissed"},
    )

    assert snapshot["suggestion_cards"][0]["status"] == "dismissed"


def test_build_session_snapshot_rejects_unknown_card_status():
    with pytest.raises(ValueError, match="unsupported suggestion card status"):
        build_session_snapshot(
            session_id="meeting_001",
            transcript_report=_transcript_report(),
            analysis=_engineering_analysis(),
            state_events=_state_events(),
            card_statuses={"card_001": "snoozed"},
        )


def test_build_session_snapshot_rejects_cards_without_state_trace():
    analysis = _engineering_analysis()
    analysis["suggestion_cards"][0].pop("state_refs")

    with pytest.raises(ValueError, match="card_001 missing state_refs"):
        build_session_snapshot(
            session_id="meeting_001",
            transcript_report=_transcript_report(),
            analysis=analysis,
            state_events=[
                {
                    "id": "event_001",
                    "target_type": "OpenQuestion",
                    "target_id": "question_001",
                    "event_type": "created",
                    "created_at_ms": 9600,
                    "evidence_span_ids": ["ev_002"],
                }
            ],
        )


def test_build_markdown_report_includes_evidence_ids():
    snapshot = build_session_snapshot(
        session_id="meeting_001",
        transcript_report=_transcript_report(),
        analysis=_engineering_analysis(),
        state_events=_state_events(),
    )

    markdown = build_markdown_report(snapshot)

    assert "# Meeting meeting_001" in markdown
    assert "payment-gateway 先灰度 10%" in markdown
    assert "evidence: ev_001" in markdown
    assert "是否需要确认回滚负责人？" in markdown
    assert "evidence: ev_002" in markdown
