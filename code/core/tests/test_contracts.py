import pytest

from meeting_copilot_core.contracts import (
    EvidenceSpanV1,
    StreamingTranscriptEventV1,
    SuggestionCardV1,
    TranscriptReportV1,
    TranscriptSegmentV1,
)


def test_transcript_report_contract_validates_segment_and_evidence_links():
    report = TranscriptReportV1.from_dict(
        {
            "provider": "funasr",
            "latency_ms": 1200,
            "rtf": 0.35,
            "text": "payment-gateway 先灰度 10%。",
            "normalized_text": "payment-gateway 先灰度 10%。",
            "segments": [
                {
                    "id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "text": "payment-gateway 先灰度 10%。",
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
                    "quote": "payment-gateway 先灰度 10%。",
                }
            ],
        }
    )

    assert report.segments[0] == TranscriptSegmentV1(
        id="seg_001",
        start_ms=0,
        end_ms=5000,
        text="payment-gateway 先灰度 10%。",
        confidence=0.91,
        finalized_at_ms=7000,
    )
    assert report.evidence_spans[0] == EvidenceSpanV1(
        id="ev_001",
        segment_id="seg_001",
        start_ms=0,
        end_ms=5000,
        quote="payment-gateway 先灰度 10%。",
        status="active",
        revision_of=None,
        replaced_by=None,
    )
    assert report.to_dict()["evidence_spans"][0]["segment_id"] == "seg_001"


def test_evidence_span_contract_preserves_revision_lifecycle_fields():
    report = TranscriptReportV1.from_dict(
        {
            "segments": [
                {
                    "id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "text": "旧文本。",
                },
                {
                    "id": "seg_001_rev",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "text": "修正后的文本。",
                    "revision_of": "seg_001",
                },
            ],
            "evidence_spans": [
                {
                    "id": "ev_001",
                    "segment_id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "quote": "旧文本。",
                    "status": "stale",
                    "replaced_by": "ev_001_rev",
                },
                {
                    "id": "ev_001_rev",
                    "segment_id": "seg_001_rev",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "quote": "修正后的文本。",
                    "status": "active",
                    "revision_of": "ev_001",
                },
            ],
        }
    )

    assert report.to_dict()["evidence_spans"][0]["status"] == "stale"
    assert report.to_dict()["segments"][1]["revision_of"] == "seg_001"
    assert report.to_dict()["evidence_spans"][0]["replaced_by"] == "ev_001_rev"
    assert report.to_dict()["evidence_spans"][1]["revision_of"] == "ev_001"


def test_evidence_span_contract_rejects_unknown_lifecycle_status():
    with pytest.raises(ValueError, match="unsupported evidence span status"):
        EvidenceSpanV1.from_dict(
            {
                "id": "ev_001",
                "segment_id": "seg_001",
                "start_ms": 0,
                "end_ms": 5000,
                "quote": "旧文本。",
                "status": "maybe",
            }
        )


def test_transcript_report_contract_rejects_evidence_for_unknown_segment():
    with pytest.raises(ValueError, match="evidence span references unknown segment_id"):
        TranscriptReportV1.from_dict(
            {
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 0,
                        "end_ms": 5000,
                        "text": "payment-gateway 先灰度 10%。",
                    }
                ],
                "evidence_spans": [
                    {
                        "id": "ev_001",
                        "segment_id": "seg_missing",
                        "start_ms": 0,
                        "end_ms": 5000,
                        "quote": "payment-gateway 先灰度 10%。",
                    }
                ],
            }
        )


def test_streaming_transcript_event_contract_rejects_empty_final_text():
    with pytest.raises(ValueError, match="final transcript event requires text"):
        StreamingTranscriptEventV1.from_dict(
            {
                "id": "evt_001",
                "segment_id": "seg_001",
                "kind": "final",
                "start_ms": 0,
                "end_ms": 5000,
                "text": "",
                "received_at_ms": 7100,
            }
        )


def test_suggestion_card_contract_requires_state_and_realtime_trace_fields():
    card = SuggestionCardV1.from_dict(
        {
            "id": "card_001",
            "type": "owner_gap",
            "title": "确认回滚负责人",
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
        }
    )

    assert card.state_refs == ("open_question:question_001",)
    assert card.state_event_ids == ("event_001",)
    assert card.to_dict()["gap_rule_id"] == "owner.required"


def test_suggestion_card_contract_rejects_missing_trigger_reason():
    with pytest.raises(ValueError, match="suggestion card missing trigger_reason"):
        SuggestionCardV1.from_dict(
            {
                "id": "card_001",
                "type": "owner_gap",
                "evidence_span_ids": ["ev_001"],
                "state_refs": ["open_question:question_001"],
                "state_event_ids": ["event_001"],
                "gap_rule_id": "owner.required",
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
            }
        )
