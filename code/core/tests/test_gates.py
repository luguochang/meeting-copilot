import pytest

from meeting_copilot_core.gates import validate_snapshot_gates


def _snapshot_with_card(**card_overrides):
    card = {
        "id": "card_001",
        "type": "owner_gap",
        "suggested_question": "是否需要确认回滚负责人？",
        "evidence_span_ids": ["ev_001"],
        "state_refs": ["open_question:question_001"],
        "state_event_ids": ["event_001"],
        "gap_rule_id": "owner.required",
        "trigger_reason": "候选决策存在但缺少回滚 owner",
        "trigger_source": "state_gap_detector",
        "final_segment_at_ms": 7000,
        "state_event_at_ms": 7600,
        "card_created_at_ms": 12000,
        "latency_ms": 5000,
        "prompt_version": "suggestion-card.v1",
        "model": "gpt-5.5",
        "usage": {"total_tokens": 321},
        "schema_result": "valid",
        "show_or_silence_decision": "show",
        "segment_batch": ["seg_001"],
        "status": "new",
    }
    card.update(card_overrides)
    return {
        "session_id": "meeting_001",
        "transcript": {
            "segments": [
                {
                    "id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "text": "还没有确认回滚负责人。",
                    "confidence": 0.91,
                    "finalized_at_ms": 7000,
                }
            ],
            "evidence_spans": [
                {
                    "id": "ev_001",
                    "segment_id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "quote": "还没有确认回滚负责人。",
                }
            ],
        },
        "state_events": [
            {
                "id": "event_001",
                "target_type": "OpenQuestion",
                "target_id": "question_001",
                "event_type": "created",
                "created_at_ms": 7600,
                "evidence_span_ids": ["ev_001"],
            }
        ],
        "states": {
            "decision_candidates": [],
            "action_items": [],
            "risks": [],
            "open_questions": [
                {
                    "id": "question_001",
                    "question": "谁负责回滚？",
                    "evidence_span_ids": ["ev_001"],
                }
            ],
        },
        "quality": {
            "is_engineering_meeting": True,
            "degradation_reasons": [],
        },
        "suggestion_cards": [card],
    }


def test_validate_snapshot_gates_accepts_traceable_realtime_card():
    validate_snapshot_gates(_snapshot_with_card())


def test_validate_snapshot_gates_rejects_card_without_state_refs():
    snapshot = _snapshot_with_card(state_refs=[])

    with pytest.raises(ValueError, match="card_001 missing state_refs"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_unknown_state_ref():
    snapshot = _snapshot_with_card(state_refs=["open_question:missing"])

    with pytest.raises(ValueError, match="card_001 references unknown state_ref"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_skeletal_state_event():
    snapshot = _snapshot_with_card()
    snapshot["state_events"] = [{"id": "event_001"}]

    with pytest.raises(ValueError, match="meeting state event missing target_type"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_state_event_target_mismatch():
    snapshot = _snapshot_with_card()
    snapshot["state_events"][0]["target_id"] = "question_missing"

    with pytest.raises(ValueError, match="state event event_001 target missing"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_card_without_state_event_trace():
    snapshot = _snapshot_with_card(state_event_ids=["event_missing"])

    with pytest.raises(ValueError, match="references unknown state_event_id"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_unknown_segment_batch_id():
    snapshot = _snapshot_with_card(segment_batch=["seg_missing"])

    with pytest.raises(ValueError, match="card_001 references unknown segment_id"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_forged_final_segment_time():
    snapshot = _snapshot_with_card(final_segment_at_ms=6000, latency_ms=6000)

    with pytest.raises(ValueError, match="final_segment_at_ms must match segment batch"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_forged_state_event_time():
    snapshot = _snapshot_with_card(state_event_at_ms=9000, card_created_at_ms=13400, latency_ms=6400)

    with pytest.raises(ValueError, match="state_event_at_ms must match referenced events"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_late_strong_realtime_card():
    snapshot = _snapshot_with_card(card_created_at_ms=45000, latency_ms=38000)

    with pytest.raises(ValueError, match="exceeds realtime window"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_allows_late_card_when_marked_too_late():
    snapshot = _snapshot_with_card(
        card_created_at_ms=45000,
        latency_ms=38000,
        show_or_silence_decision="too_late",
        status="too_late",
    )

    validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_strong_card_during_degradation():
    snapshot = _snapshot_with_card()
    snapshot["quality"]["degradation_reasons"] = ["asr_low_confidence"]

    with pytest.raises(ValueError, match="degradation blocks strong suggestion card"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_schema_failure_shown_as_strong_card():
    snapshot = _snapshot_with_card(schema_result="failed")

    with pytest.raises(ValueError, match="schema_result failed blocks strong suggestion"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_negative_total_tokens():
    snapshot = _snapshot_with_card(usage={"total_tokens": -1})

    with pytest.raises(ValueError, match="usage.total_tokens must be non-negative"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_unknown_card_type():
    snapshot = _snapshot_with_card(type="compatibility_gap")

    with pytest.raises(ValueError, match="unsupported suggestion card type"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_missing_llm_trace():
    snapshot = _snapshot_with_card(model="")

    with pytest.raises(ValueError, match="card_001 missing model"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_empty_evidence_quote():
    snapshot = _snapshot_with_card()
    snapshot["transcript"]["evidence_spans"][0]["quote"] = ""

    with pytest.raises(ValueError, match="evidence span ev_001 missing quote"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_rejects_strong_card_using_stale_evidence():
    snapshot = _snapshot_with_card()
    snapshot["transcript"]["evidence_spans"][0]["status"] = "stale"
    snapshot["transcript"]["evidence_spans"][0]["replaced_by"] = "ev_001_rev"

    with pytest.raises(ValueError, match="card_001 references stale evidence_span_id"):
        validate_snapshot_gates(snapshot)


def test_validate_snapshot_gates_allows_non_strong_card_using_stale_evidence_for_audit():
    snapshot = _snapshot_with_card(
        show_or_silence_decision="draft",
        status="dismissed",
    )
    snapshot["transcript"]["evidence_spans"][0]["status"] = "stale"
    snapshot["transcript"]["evidence_spans"][0]["replaced_by"] = "ev_001_rev"

    validate_snapshot_gates(snapshot)
