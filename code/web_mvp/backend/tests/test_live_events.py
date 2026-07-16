import json

import pytest

from meeting_copilot_web_mvp.live_events import (
    build_mock_live_events,
    render_sse_events,
)
from meeting_copilot_web_mvp.asr_live_events import (
    build_asr_live_events,
    asr_event_source_metadata,
)


def _snapshot():
    return {
        "session_id": "live_release_review",
        "transcript": {
            "segments": [
                {
                    "id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "finalized_at_ms": 5200,
                    "text": "payment-gateway 先灰度 10%。",
                    "confidence": 0.91,
                },
                {
                    "id": "seg_002",
                    "start_ms": 5000,
                    "end_ms": 11000,
                    "finalized_at_ms": 11200,
                    "text": "如果 P99 超过 800 毫秒或者错误率超过 0.1% 就回滚。",
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
                    "end_ms": 11000,
                    "quote": "如果 P99 超过 800 毫秒或者错误率超过 0.1% 就回滚。",
                },
            ],
        },
        "states": {
            "decision_candidates": [
                {
                    "id": "decision_001",
                    "statement": "payment-gateway 先灰度 10%",
                    "evidence_span_ids": ["ev_001"],
                }
            ],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "state_events": [
            {
                "id": "event_001",
                "target_type": "DecisionCandidate",
                "target_id": "decision_001",
                "event_type": "created",
                "created_at_ms": 12100,
                "evidence_span_ids": ["ev_001"],
            }
        ],
        "suggestion_cards": [
            {
                "id": "card_001",
                "type": "owner_gap",
                "title": "确认回滚负责人",
                "suggested_question": "谁负责 payment-gateway 灰度回滚？",
                "gap_rule_id": "release.rollback.owner.required",
                "trigger_source": "state_gap_detector",
                "trigger_reason": "候选灰度决策缺少回滚负责人",
                "state_refs": ["decision_candidate:decision_001"],
                "segment_batch": ["seg_002"],
                "state_event_ids": ["event_001"],
                "prompt_version": "suggestion-card.v1",
                "model": "gpt-5.5",
                "usage": {"total_tokens": 336},
                "schema_result": "valid",
                "show_or_silence_decision": "show",
                "final_segment_at_ms": 11200,
                "state_event_at_ms": 12100,
                "card_created_at_ms": 16800,
                "latency_ms": 5600,
                "evidence_span_ids": ["ev_002"],
            }
        ],
    }


def _snapshot_with_revision():
    snapshot = _snapshot()
    snapshot["transcript"]["segments"].append(
        {
            "id": "seg_001_rev1",
            "revision_of": "seg_001",
            "start_ms": 0,
            "end_ms": 5200,
            "finalized_at_ms": 7600,
            "text": "payment-gateway 先灰度 5%，不是 10%。",
            "confidence": 0.94,
        }
    )
    snapshot["transcript"]["evidence_spans"].append(
        {
            "id": "ev_001_rev1",
            "segment_id": "seg_001_rev1",
            "revision_of": "ev_001",
            "start_ms": 0,
            "end_ms": 5200,
            "quote": "payment-gateway 先灰度 5%，不是 10%。",
        }
    )
    snapshot["suggestion_cards"][0]["evidence_span_ids"] = ["ev_001"]
    return snapshot


def _asr_stream_events():
    return [
        {
            "event_type": "partial",
            "segment_id": "asr_seg_001",
            "text": "先灰度",
            "start_ms": 0,
            "end_ms": 1200,
            "received_at_ms": 1300,
            "confidence": 0.72,
        },
        {
            "event_type": "final",
            "segment_id": "asr_seg_001",
            "text": "先灰度 10%。",
            "start_ms": 0,
            "end_ms": 3200,
            "received_at_ms": 3500,
            "confidence": 0.91,
        },
        {
            "event_type": "revision",
            "segment_id": "asr_seg_001_rev1",
            "revision_of": "asr_seg_001",
            "text": "先灰度 5%，不是 10%。",
            "start_ms": 0,
            "end_ms": 3400,
            "received_at_ms": 5200,
            "confidence": 0.94,
        },
        {
            "event_type": "error",
            "segment_id": "asr_error_001",
            "text": "local asr buffer underrun",
            "start_ms": 5200,
            "end_ms": 5200,
            "received_at_ms": 5300,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 5400,
            "end_ms": 5400,
            "received_at_ms": 5400,
        },
    ]


def test_build_asr_live_events_maps_streaming_contract_to_live_envelope():
    events = build_asr_live_events(
        session_id="local_asr_contract",
        provider="local_mock_asr",
        streaming_events=_asr_stream_events(),
    )

    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert {event["source"] for event in events} == {"live_asr_stream"}
    assert {event["trace_kind"] for event in events} == {"live_event"}
    assert [event["event_type"] for event in events] == [
        "transcript_partial",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_revision",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "provider_error",
        "evaluation_summary",
    ]

    partial = events[0]
    assert partial["payload"]["segment_id"] == "asr_seg_001"
    assert partial["payload"]["is_final"] is False
    assert "evidence_spans" not in partial["payload"]

    final = events[1]
    assert final["payload"]["is_final"] is True
    assert final["payload"]["evidence_spans"] == [
        {
            "id": "asr_ev_asr_seg_001",
            "segment_id": "asr_seg_001",
            "start_ms": 0,
            "end_ms": 3200,
            "quote": "先灰度 10%。",
            "status": "active",
        }
    ]

    revision = next(event for event in events if event["event_type"] == "transcript_revision")
    assert revision["payload"]["revision_of"] == "asr_seg_001"
    assert revision["payload"]["supersedes_segment_id"] == "asr_seg_001"
    assert revision["payload"]["evidence_spans"] == [
        {
            "id": "asr_ev_asr_seg_001_rev1",
            "segment_id": "asr_seg_001_rev1",
            "revision_of": "asr_ev_asr_seg_001",
            "start_ms": 0,
            "end_ms": 3400,
            "quote": "先灰度 5%，不是 10%。",
            "status": "active",
        }
    ]

    assert revision["payload"]["superseded_evidence_spans"] == [
        {
            "id": "asr_ev_asr_seg_001",
            "segment_id": "asr_seg_001",
            "start_ms": 0,
            "end_ms": 3200,
            "quote": "先灰度 10%。",
            "status": "superseded",
            "replaced_by": "asr_ev_asr_seg_001_rev1",
        }
    ]

    provider_error = next(event for event in events if event["event_type"] == "provider_error")
    assert provider_error["payload"]["provider"] == "local_mock_asr"
    assert provider_error["payload"]["message"] == "local asr buffer underrun"

    evaluation = events[-1]
    assert evaluation["payload"]["source"] == "live_asr_stream"
    assert evaluation["payload"]["provider"] == "local_mock_asr"
    assert evaluation["payload"]["partial_event_count"] == 1
    assert evaluation["payload"]["final_event_count"] == 1
    assert evaluation["payload"]["revision_event_count"] == 1
    assert evaluation["payload"]["error_event_count"] == 1
    assert evaluation["payload"]["end_of_stream_event_count"] == 1


def test_build_asr_live_events_emits_no_cost_partial_hint_for_risk_partial():
    events = build_asr_live_events(
        session_id="partial_hint_session",
        provider="sherpa_onnx_realtime",
        is_mock=False,
        streaming_events=[
            {
                "event_type": "partial",
                "segment_id": "asr_seg_partial_risk",
                "text": "如果错误率超过零点一就回滚，接口负责人需要确认",
                "start_ms": 0,
                "end_ms": 1800,
                "received_at_ms": 1900,
                "confidence": 0.76,
            }
        ],
    )

    event_types = [event["event_type"] for event in events]
    assert event_types == ["transcript_partial", "partial_hint_event"]
    hint = events[1]
    assert hint["source"] == "live_asr_stream"
    assert hint["trace_kind"] == "live_event"
    assert hint["payload"]["hint_policy_version"] == "partial-hint-policy.v1"
    assert hint["payload"]["hint_origin"] == "local_deterministic_partial"
    assert hint["payload"]["llm_call_status"] == "not_called"
    assert hint["payload"]["card_status"] == "not_created"
    assert hint["payload"]["segment_batch"] == ["asr_seg_partial_risk"]
    assert hint["payload"]["source_event_ids"] == ["transcript_partial:asr_seg_partial_risk"]
    assert "确认" in hint["payload"]["suggested_prompt"]
    assert "partial_not_final" in hint["payload"]["degradation_reasons"]


def test_build_asr_live_events_partial_hint_balances_precision_and_recall():
    cases = [
        (
            "casual_chat",
            "我们晚上吃什么，要不要点奶茶",
            None,
        ),
        (
            "benign_engineering",
            "接口负责人已经确认没有风险，监控看板也正常",
            None,
        ),
        (
            "action_missing_details",
            "请李四今天确认缓存窗口和回滚负责人",
            "action_confirmation",
        ),
        (
            "open_question",
            "P99 延迟这个风险有没有 owner",
            "question_followup",
        ),
        (
            "risk_threshold",
            "如果 P99 延迟超过九百毫秒就要回滚",
            "risk_confirmation",
        ),
    ]

    for segment_id, text, expected_hint_type in cases:
        events = build_asr_live_events(
            session_id=f"partial_hint_eval_{segment_id}",
            provider="sherpa_onnx_realtime",
            is_mock=False,
            streaming_events=[
                {
                    "event_type": "partial",
                    "segment_id": segment_id,
                    "text": text,
                    "start_ms": 0,
                    "end_ms": 1500,
                    "received_at_ms": 1600,
                    "confidence": 0.8,
                }
            ],
        )
        hints = [event for event in events if event["event_type"] == "partial_hint_event"]
        if expected_hint_type is None:
            assert hints == [], segment_id
        else:
            assert len(hints) == 1, segment_id
            assert hints[0]["payload"]["hint_type"] == expected_hint_type


def test_build_asr_live_events_prioritizes_explicit_action_over_incidental_risk_wording():
    action_events = build_asr_live_events(
        session_id="partial_hint_action_with_backlog",
        provider="sherpa_onnx_realtime",
        is_mock=False,
        streaming_events=[
            {
                "event_type": "partial",
                "segment_id": "action_backlog_001",
                "text": "需要网母今天处理科消费堆积",
                "start_ms": 0,
                "end_ms": 1600,
                "received_at_ms": 1700,
                "confidence": 0.78,
            }
        ],
    )
    action_hints = [
        event for event in action_events if event["event_type"] == "partial_hint_event"
    ]

    risk_events = build_asr_live_events(
        session_id="partial_hint_risk_backlog",
        provider="sherpa_onnx_realtime",
        is_mock=False,
        streaming_events=[
            {
                "event_type": "partial",
                "segment_id": "risk_backlog_001",
                "text": "如果 Kafka 消费堆积导致积压，就需要回滚",
                "start_ms": 0,
                "end_ms": 1800,
                "received_at_ms": 1900,
                "confidence": 0.78,
            }
        ],
    )
    risk_hints = [
        event for event in risk_events if event["event_type"] == "partial_hint_event"
    ]

    assert len(action_hints) == 1
    assert action_hints[0]["payload"]["hint_type"] == "action_confirmation"
    assert len(risk_hints) == 1
    assert risk_hints[0]["payload"]["hint_type"] == "risk_confirmation"


def test_build_asr_live_events_deduplicates_progressive_partial_hints_by_semantic_key():
    events = build_asr_live_events(
        session_id="partial_hint_dedupe_session",
        provider="sherpa_onnx_realtime",
        is_mock=False,
        streaming_events=[
            {
                "event_type": "partial",
                "segment_id": "risk_partial_001",
                "text": "如果 P99 延迟超过九百毫秒",
                "start_ms": 0,
                "end_ms": 900,
                "received_at_ms": 950,
                "confidence": 0.78,
            },
            {
                "event_type": "partial",
                "segment_id": "risk_partial_002",
                "text": "如果 P99 延迟超过九百毫秒就要回滚",
                "start_ms": 0,
                "end_ms": 1500,
                "received_at_ms": 1550,
                "confidence": 0.8,
            },
            {
                "event_type": "partial",
                "segment_id": "risk_partial_003",
                "text": "如果 P99 延迟超过九百毫秒就要回滚，owner 张三确认",
                "start_ms": 0,
                "end_ms": 2100,
                "received_at_ms": 2150,
                "confidence": 0.82,
            },
        ],
    )

    assert [event["event_type"] for event in events].count("transcript_partial") == 3
    hints = [event for event in events if event["event_type"] == "partial_hint_event"]
    assert len(hints) == 1
    assert hints[0]["payload"]["hint_type"] == "risk_confirmation"
    assert hints[0]["payload"]["dedupe_key"] == "risk_confirmation:p99:latency_threshold"


def test_build_asr_live_events_suppresses_already_closed_partial_action_hint():
    events = build_asr_live_events(
        session_id="partial_hint_closed_action_session",
        provider="sherpa_onnx_realtime",
        is_mock=False,
        streaming_events=[
            {
                "event_type": "partial",
                "segment_id": "closed_action_partial",
                "text": "李四今天已经确认缓存窗口和回滚负责人，监控看板也正常",
                "start_ms": 0,
                "end_ms": 1800,
                "received_at_ms": 1900,
                "confidence": 0.86,
            }
        ],
    )

    assert [event["event_type"] for event in events] == ["transcript_partial"]


def test_build_asr_live_events_extracts_state_candidates_from_normalized_final_text():
    events = build_asr_live_events(
        session_id="normalized_final_candidate_session",
        provider="sherpa_onnx_realtime",
        is_mock=False,
        streaming_events=[
            {
                "event_type": "final",
                "segment_id": "normalized_final_001",
                "text": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚",
                "start_ms": 0,
                "end_ms": 2400,
                "received_at_ms": 2500,
                "confidence": 0.86,
            }
        ],
    )

    final = next(event for event in events if event["event_type"] == "transcript_final")
    assert final["payload"]["text"] == "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚"
    assert final["payload"]["normalized_text"] == "接口先灰度百分之五，如果 P99延迟超过九百毫秒就回滚"
    assert final["payload"]["evidence_spans"][0]["quote"] == final["payload"]["text"]

    state_types = [
        event["payload"]["target_type"]
        for event in events
        if event["event_type"] == "state_event"
    ]
    assert "DecisionCandidate" in state_types
    assert "Risk" in state_types
    decision = next(
        event
        for event in events
        if event["event_type"] == "state_event"
        and event["payload"]["target_type"] == "DecisionCandidate"
    )
    assert "先灰度" in decision["payload"]["state_item"]["statement"]


def test_build_asr_live_events_emits_local_state_and_scheduler_skeleton():
    events = build_asr_live_events(
        session_id="local_asr_contract",
        provider="local_mock_asr",
        streaming_events=_asr_stream_events(),
    )

    event_types = [event["event_type"] for event in events]
    assert event_types == [
        "transcript_partial",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_revision",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "provider_error",
        "evaluation_summary",
    ]
    assert "llm_schema_result" not in event_types
    assert "suggestion_card" not in event_types
    assert "suggestion_silenced" not in event_types

    final_state = next(
        event for event in events if event["id"] == "state:asr_state_event_asr_seg_001"
    )
    assert final_state["source"] == "live_asr_stream"
    assert final_state["trace_kind"] == "live_event"
    assert final_state["payload"] == {
        "event_id": "asr_state_event_asr_seg_001",
        "target_type": "DecisionCandidate",
        "target_id": "asr_decision_asr_seg_001",
        "state_event_type": "created",
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "state_item": {
            "id": "asr_decision_asr_seg_001",
            "statement": "先灰度 10%。",
            "evidence_span_ids": ["asr_ev_asr_seg_001"],
            "source": "live_asr_stream",
            "state_origin": "local_deterministic_asr_skeleton",
        },
    }

    final_scheduler = next(
        event for event in events if event["id"] == "scheduler:asr_state_event_asr_seg_001"
    )
    assert final_scheduler["payload"] == {
        "scheduler_event_type": "llm_candidate_queued",
        "card_id": "",
        "gap_rule_id": "asr.state_candidate.review",
        "trigger_source": "live_asr_scheduler_log",
        "trigger_reason": "state_change",
        "decision_reason": "state_change",
        "would_call_llm": True,
        "llm_call_status": "not_called",
        "cooldown_remaining_ms": 0,
        "call_count_last_hour": 1,
        "budget_remaining": 79,
        "segment_batch": ["asr_seg_001"],
        "source_event_ids": ["asr_state_event_asr_seg_001"],
        "prompt_version": "not-called",
        "model": "not-called",
    }

    revision_state = next(
        event
        for event in events
        if event["id"] == "state:asr_state_event_asr_seg_001_rev1"
    )
    assert revision_state["payload"]["state_item"]["statement"] == "先灰度 5%，不是 10%。"
    assert revision_state["payload"]["evidence_span_ids"] == ["asr_ev_asr_seg_001_rev1"]

    revision_scheduler = next(
        event
        for event in events
        if event["id"] == "scheduler:asr_state_event_asr_seg_001_rev1"
    )
    assert revision_scheduler["payload"]["scheduler_event_type"] == "llm_candidate_skipped"
    assert revision_scheduler["payload"]["decision_reason"] == "cooldown"
    assert revision_scheduler["payload"]["would_call_llm"] is False
    assert revision_scheduler["payload"]["llm_call_status"] == "not_called"
    assert revision_scheduler["payload"]["cooldown_remaining_ms"] == 8300
    assert revision_scheduler["payload"]["call_count_last_hour"] == 1
    assert revision_scheduler["payload"]["budget_remaining"] == 79
    assert revision_scheduler["payload"]["source_event_ids"] == [
        "asr_state_event_asr_seg_001_rev1"
    ]
    assert revision_scheduler["payload"]["model"] == "not-called"


def test_build_asr_live_events_emits_scheduler_decision_log_for_state_candidates():
    events = build_asr_live_events(
        session_id="local_asr_contract",
        provider="local_mock_asr",
        streaming_events=_asr_stream_events(),
    )

    scheduler_events = [
        event for event in events if event["event_type"] == "scheduler_event"
    ]

    assert [event["payload"]["scheduler_event_type"] for event in scheduler_events] == [
        "llm_candidate_queued",
        "llm_candidate_skipped",
    ]
    assert [event["payload"]["decision_reason"] for event in scheduler_events] == [
        "state_change",
        "cooldown",
    ]
    assert [event["payload"]["would_call_llm"] for event in scheduler_events] == [
        True,
        False,
    ]
    assert {event["payload"]["llm_call_status"] for event in scheduler_events} == {
        "not_called"
    }
    assert {event["payload"]["prompt_version"] for event in scheduler_events} == {
        "not-called"
    }
    assert {event["payload"]["model"] for event in scheduler_events} == {"not-called"}


def test_build_asr_live_events_extracts_open_question_state_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_question_001",
            "text": "谁负责回滚？",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_question_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    assert state["payload"]["target_type"] == "OpenQuestion"
    assert state["payload"]["target_id"] == "asr_question_asr_seg_question_001"
    assert state["payload"]["state_item"] == {
        "id": "asr_question_asr_seg_question_001",
        "question": "谁负责回滚？",
        "evidence_span_ids": ["asr_ev_asr_seg_question_001"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }


def test_build_asr_live_events_suppresses_non_engineering_open_question_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_social_001",
            "text": "我们今天先确认团建时间，周五下午大家是否都方便，预算按两百来估，名单我会后整理一下明天发到群。",
            "start_ms": 0,
            "end_ms": 5000,
            "received_at_ms": 5200,
            "confidence": 0.92,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 5200,
            "end_ms": 5200,
            "received_at_ms": 5300,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_non_engineering_control",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [event["event_type"] for event in events] == [
        "transcript_final",
        "evaluation_summary",
    ]
    assert "state_event" not in [event["event_type"] for event in events]
    assert "suggestion_candidate_event" not in [event["event_type"] for event in events]
    assert "llm_request_draft_event" not in [event["event_type"] for event in events]


def test_build_asr_live_events_extracts_general_software_architecture_question():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_architecture_question_001",
            "text": "公司里的工具是封装成 SDK，还是提供一个 toolkit？",
            "start_ms": 0,
            "end_ms": 4200,
            "received_at_ms": 4400,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 4500,
            "end_ms": 4500,
            "received_at_ms": 4500,
        },
    ]

    events = build_asr_live_events(
        session_id="general_architecture_question",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    candidate = next(event for event in events if event["event_type"] == "suggestion_candidate_event")
    assert state["payload"]["target_type"] == "OpenQuestion"
    assert state["payload"]["state_item"]["question"] == streaming_events[0]["text"]
    assert candidate["payload"]["target_type"] == "OpenQuestion"
    assert candidate["payload"]["scheduler_event_type"] == "llm_candidate_queued"


def test_build_asr_live_events_extracts_action_item_state_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_action_001",
            "text": "张三下周三补充兼容性测试用例。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_action_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    scheduler = next(event for event in events if event["event_type"] == "scheduler_event")
    assert state["payload"]["target_type"] == "ActionItem"
    assert state["payload"]["target_id"] == "asr_action_asr_seg_action_001"
    assert state["payload"]["state_item"] == {
        "id": "asr_action_asr_seg_action_001",
        "description": "张三下周三补充兼容性测试用例。",
        "owner": "张三",
        "deadline": "下周三",
        "status": "candidate",
        "evidence_span_ids": ["asr_ev_asr_seg_action_001"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    assert scheduler["payload"]["source_event_ids"] == [
        "asr_action_event_asr_seg_action_001"
    ]
    assert scheduler["payload"]["llm_call_status"] == "not_called"


def test_build_asr_live_events_emits_suggestion_candidate_after_action_scheduler():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_action_001",
            "text": "张三下周三补充兼容性测试用例。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_action_candidate_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    event_types = [event["event_type"] for event in events]
    assert event_types == [
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]
    state_event = events[1]
    scheduler_event = events[2]
    candidate_event = events[3]
    assert candidate_event["payload"] == {
        "candidate_id": "asr_suggestion_candidate_asr_action_event_asr_seg_action_001",
        "candidate_type": "state_gap_review",
        "candidate_policy_version": "asr-candidate-policy.v1",
        "confidence_source": "local_deterministic_heuristic",
        "target_type": "ActionItem",
        "target_id": "asr_action_asr_seg_action_001",
        "gap_rule_id": "action.owner.deadline.confirmation",
        "suggested_prompt": "确认行动项 owner、deadline 和验收口径是否完整。",
        "trigger_reason": "Live ASR captured an action item that may need owner/deadline confirmation.",
        "decision_reason": "state_change",
        "source_event_ids": ["asr_action_event_asr_seg_action_001"],
        "scheduler_event_type": "llm_candidate_queued",
        "evidence_span_ids": ["asr_ev_asr_seg_action_001"],
        "segment_batch": ["asr_seg_action_001"],
        "llm_call_status": "not_called",
        "card_status": "not_created",
        "confidence": 0.9,
        "confidence_level": "high",
        "degradation_reasons": [],
        "source": "live_asr_stream",
        "candidate_origin": "local_deterministic_asr_skeleton",
    }
    assert scheduler_event["payload"]["source_event_ids"] == [
        state_event["payload"]["event_id"]
    ]


def test_build_asr_live_events_can_queue_candidate_from_stable_partial_before_final():
    streaming_events = [
        {
            "event_type": "partial",
            "segment_id": "asr_seg_live_partial_001",
            "text": "发布评审 payment-gateway 先灰度百分之五，如果 P99 延迟超过九百毫秒就回滚，张三今天补 SLO 看板。",
            "start_ms": 0,
            "end_ms": 7200,
            "received_at_ms": 7200,
            "confidence": 0.88,
            "candidate_eligible": True,
            "candidate_source": "stable_partial",
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 7500,
            "end_ms": 7500,
            "received_at_ms": 7500,
        },
    ]

    events = build_asr_live_events(
        session_id="stable_partial_candidate_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    event_types = [event["event_type"] for event in events]
    assert event_types == [
        "transcript_partial",
        "partial_hint_event",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]
    candidates = [
        event for event in events if event["event_type"] == "suggestion_candidate_event"
    ]
    assert candidates[0]["payload"]["scheduler_event_type"] == "llm_candidate_deferred"
    assert candidates[0]["payload"]["decision_reason"] == "partial_not_final"
    assert candidates[0]["payload"]["llm_call_status"] == "not_called"
    assert candidates[0]["payload"]["candidate_origin"] == "local_deterministic_asr_stable_partial_skeleton"
    assert candidates[0]["payload"]["confidence"] == 0.85
    assert candidates[0]["payload"]["confidence_level"] == "high"
    assert candidates[0]["payload"]["degradation_reasons"] == ["partial_not_final"]
    assert candidates[0]["payload"]["evidence_span_ids"] == [
        "asr_partial_ev_asr_seg_live_partial_001"
    ]


def test_stable_partial_does_not_consume_scheduler_cooldown_before_final():
    streaming_events = [
        {
            "event_type": "partial",
            "segment_id": "partial_seg",
            "text": "发布评审先灰度百分之五，如果错误率超过百分之零点一就回滚。",
            "start_ms": 0,
            "end_ms": 30_000,
            "received_at_ms": 30_000,
            "confidence": 0.88,
            "candidate_eligible": True,
            "candidate_source": "stable_partial",
        },
        {
            "event_type": "final",
            "segment_id": "final_seg",
            "text": "发布评审先灰度百分之五，如果错误率超过百分之零点一就回滚。",
            "start_ms": 0,
            "end_ms": 31_000,
            "received_at_ms": 31_000,
            "confidence": 0.9,
        },
    ]

    events = build_asr_live_events(
        session_id="partial_then_final_scheduler",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )
    candidates = [event for event in events if event["event_type"] == "suggestion_candidate_event"]
    partial_candidates = [
        event for event in candidates
        if event["payload"].get("candidate_origin") == "local_deterministic_asr_stable_partial_skeleton"
    ]
    final_candidates = [
        event for event in candidates
        if event["payload"].get("candidate_origin") == "local_deterministic_asr_skeleton"
    ]

    assert partial_candidates
    assert all(event["payload"]["scheduler_event_type"] == "llm_candidate_deferred" for event in partial_candidates)
    assert final_candidates
    assert final_candidates[0]["payload"]["scheduler_event_type"] == "llm_candidate_queued"
    final_scheduler = next(
        event for event in events
        if event["event_type"] == "scheduler_event"
        and event["payload"].get("source_event_ids") == ["asr_state_event_final_seg"]
    )
    assert final_scheduler["payload"]["call_count_last_hour"] == 1


def test_same_segment_final_requeues_candidate_deferred_from_stable_partial():
    streaming_events = [
        {
            "event_type": "partial",
            "segment_id": "same_segment",
            "text": "发布评审先灰度百分之五，如果错误率超过百分之零点一就回滚。",
            "start_ms": 0,
            "end_ms": 30_000,
            "received_at_ms": 30_000,
            "confidence": 0.88,
            "candidate_eligible": True,
            "candidate_source": "stable_partial",
        },
        {
            "event_type": "final",
            "segment_id": "same_segment",
            "text": "发布评审先灰度百分之五，如果错误率超过百分之零点一就回滚。",
            "start_ms": 0,
            "end_ms": 31_000,
            "received_at_ms": 31_000,
            "confidence": 0.9,
        },
    ]

    events = build_asr_live_events(
        session_id="same_segment_partial_final",
        provider="funasr_realtime",
        is_mock=False,
        streaming_events=streaming_events,
    )

    candidates = [
        event for event in events if event["event_type"] == "suggestion_candidate_event"
    ]
    partial_candidates = [
        event for event in candidates
        if event["payload"].get("candidate_origin") == "local_deterministic_asr_stable_partial_skeleton"
    ]
    final_candidates = [
        event for event in candidates
        if event["payload"].get("candidate_origin") == "local_deterministic_asr_skeleton"
    ]

    assert partial_candidates
    assert final_candidates
    assert final_candidates[0]["payload"]["scheduler_event_type"] == "llm_candidate_queued"
    assert final_candidates[0]["payload"]["candidate_id"].endswith("_final")


def test_build_asr_live_events_emits_llm_request_draft_after_suggestion_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_action_001",
            "text": "张三下周三补充兼容性测试用例。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_action_request_draft_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [event["event_type"] for event in events] == [
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]
    candidate = events[3]
    request_draft = events[4]
    assert request_draft["payload"] == {
        "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_action_event_asr_seg_action_001",
        "request_type": "llm_suggestion_card_draft",
        "request_status": "draft_only",
        "target_candidate_id": "asr_suggestion_candidate_asr_action_event_asr_seg_action_001",
        "target_type": "ActionItem",
        "target_id": "asr_action_asr_seg_action_001",
        "gap_rule_id": "action.owner.deadline.confirmation",
        "prompt_version": "not-called",
        "model": "not-called",
        "llm_call_status": "not_called",
        "card_status": "not_created",
        "schema_status": "not_generated",
        "suggested_prompt": "确认行动项 owner、deadline 和验收口径是否完整。",
        "input_summary": "ActionItem asr_action_asr_seg_action_001 from asr_seg_action_001 using asr_ev_asr_seg_action_001",
        "source_event_ids": ["asr_action_event_asr_seg_action_001"],
        "evidence_span_ids": ["asr_ev_asr_seg_action_001"],
        "segment_batch": ["asr_seg_action_001"],
        "candidate_confidence": 0.9,
        "candidate_confidence_level": "high",
        "candidate_degradation_reasons": [],
        "request_origin": "local_deterministic_asr_request_draft",
        "source": "live_asr_stream",
    }
    assert request_draft["payload"]["target_candidate_id"] == candidate["payload"]["candidate_id"]


def test_build_asr_live_events_scores_high_confidence_action_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_action_001",
            "text": "张三下周三补充兼容性测试用例。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_action_candidate_quality",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    candidate = next(
        event for event in events if event["event_type"] == "suggestion_candidate_event"
    )
    assert candidate["payload"]["candidate_policy_version"] == "asr-candidate-policy.v1"
    assert candidate["payload"]["confidence_source"] == "local_deterministic_heuristic"
    assert candidate["payload"]["confidence"] == 0.9
    assert candidate["payload"]["confidence_level"] == "high"
    assert candidate["payload"]["degradation_reasons"] == []
    assert "model" not in candidate["payload"]
    assert "prompt_version" not in candidate["payload"]


def test_build_asr_live_events_degrades_low_confidence_skipped_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_001",
            "text": "先灰度 10%。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.91,
        },
        {
            "event_type": "revision",
            "segment_id": "asr_seg_001_rev1",
            "revision_of": "asr_seg_001",
            "text": "先灰度 5%，不是 10%。",
            "start_ms": 0,
            "end_ms": 2500,
            "received_at_ms": 5200,
            "confidence": 0.72,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 5300,
            "end_ms": 5300,
            "received_at_ms": 5300,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_degraded_candidate_quality",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    candidate = next(
        event
        for event in events
        if event["id"] == "suggestion_candidate:asr_state_event_asr_seg_001_rev1"
    )
    assert candidate["payload"]["scheduler_event_type"] == "llm_candidate_skipped"
    assert candidate["payload"]["decision_reason"] == "cooldown"
    assert candidate["payload"]["confidence"] == 0.7
    assert candidate["payload"]["confidence_level"] == "medium"
    assert candidate["payload"]["degradation_reasons"] == ["low_asr_confidence"]


def test_build_asr_live_events_degrades_missing_asr_confidence_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_missing_confidence_001",
            "text": "先灰度 10%。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_missing_confidence_candidate_quality",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    candidate = next(
        event for event in events if event["event_type"] == "suggestion_candidate_event"
    )
    assert candidate["payload"]["confidence"] == 0.75
    assert candidate["payload"]["confidence_level"] == "medium"
    assert candidate["payload"]["degradation_reasons"] == ["missing_asr_confidence"]


def test_build_asr_live_events_degrades_incomplete_action_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_action_incomplete_001",
            "text": "张三补充兼容性测试用例。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_incomplete_action_candidate_quality",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    candidate = next(
        event for event in events if event["event_type"] == "suggestion_candidate_event"
    )
    assert state["payload"]["state_item"]["owner"] == "张三"
    assert state["payload"]["state_item"]["deadline"] is None
    assert candidate["payload"]["confidence"] == 0.8
    assert candidate["payload"]["confidence_level"] == "high"
    assert candidate["payload"]["degradation_reasons"] == ["action_deadline_missing"]


def test_build_asr_live_events_degrades_risk_without_mitigation():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_risk_no_mitigation_001",
            "text": "如果错误率超过 0.1% 就需要关注。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_risk_no_mitigation_candidate_quality",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    candidate = next(
        event for event in events if event["event_type"] == "suggestion_candidate_event"
    )
    assert state["payload"]["state_item"]["mitigation"] == ""
    assert candidate["payload"]["confidence"] == 0.8
    assert candidate["payload"]["confidence_level"] == "high"
    assert candidate["payload"]["degradation_reasons"] == ["risk_mitigation_missing"]


def test_build_asr_live_events_extracts_three_character_action_owner():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_action_owner_001",
            "text": "王小明下周三补充兼容性测试用例。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_action_owner_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    assert state["payload"]["target_type"] == "ActionItem"
    assert state["payload"]["state_item"]["owner"] == "王小明"


def test_build_asr_live_events_extracts_owner_after_assignment_cue():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_action_assignee_001",
            "text": "由王小明负责回归测试。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_action_assignee_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    assert state["payload"]["target_type"] == "ActionItem"
    assert state["payload"]["state_item"]["owner"] == "王小明"


def test_build_asr_live_events_does_not_treat_plain_confirmation_as_action_item():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_plain_confirmation_001",
            "text": "我们先确认一下影响范围。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_plain_confirmation_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [
        event["payload"]["target_type"]
        for event in events
        if event["event_type"] == "state_event"
    ] == []


def test_build_asr_live_events_does_not_extract_owner_from_responsible_noun():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_responsible_noun_001",
            "text": "业务负责人确认这个方案已经评审过。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_responsible_noun_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [
        event["payload"]["target_type"]
        for event in events
        if event["event_type"] == "state_event"
    ] == []


def test_build_asr_live_events_does_not_treat_facilitation_confirmation_as_action_item():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_facilitation_confirmation_001",
            "text": "请大家先确认一下影响范围。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_facilitation_confirmation_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [
        event["payload"]["target_type"]
        for event in events
        if event["event_type"] == "state_event"
    ] == []


def test_build_asr_live_events_extracts_risk_state_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_risk_001",
            "text": "如果错误率超过 0.1% 就回滚。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_risk_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    scheduler = next(event for event in events if event["event_type"] == "scheduler_event")
    assert state["payload"]["target_type"] == "Risk"
    assert state["payload"]["target_id"] == "asr_risk_asr_seg_risk_001"
    assert state["payload"]["state_item"] == {
        "id": "asr_risk_asr_seg_risk_001",
        "description": "如果错误率超过 0.1% 就回滚。",
        "impact": "condition_exceeded",
        "mitigation": "回滚",
        "status": "open",
        "evidence_span_ids": ["asr_ev_asr_seg_risk_001"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    assert scheduler["payload"]["source_event_ids"] == [
        "asr_risk_event_asr_seg_risk_001"
    ]
    assert scheduler["payload"]["llm_call_status"] == "not_called"


def test_build_asr_live_events_extracts_runtime_incident_risk_from_backlog_final():
    events = build_asr_live_events(
        session_id="local_asr_incident_risk_contract",
        provider="local_mock_asr",
        streaming_events=[
            {
                "event_type": "final",
                "segment_id": "asr_seg_incident_001",
                "text": "a凌晨autoker消费堆积lag最高到了八万，告警延迟六分钟。",
                "start_ms": 0,
                "end_ms": 5200,
                "received_at_ms": 5400,
                "confidence": 0.91,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 5400,
                "end_ms": 5400,
                "received_at_ms": 5400,
            },
        ],
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    candidate = next(event for event in events if event["event_type"] == "suggestion_candidate_event")
    final = next(event for event in events if event["event_type"] == "transcript_final")

    assert final["payload"]["text"] == "a凌晨autoker消费堆积lag最高到了八万，告警延迟六分钟。"
    assert final["payload"]["normalized_text"] == "a凌晨order-worker消费堆积lag最高到了八万，告警延迟六分钟。"
    assert state["payload"]["target_type"] == "Risk"
    assert state["payload"]["state_item"]["description"] == "a凌晨order-worker消费堆积lag最高到了八万，告警延迟六分钟。"
    assert state["payload"]["state_item"]["impact"] == "runtime_issue"
    assert candidate["payload"]["gap_rule_id"] == "risk.rollback.validation"
    assert candidate["payload"]["llm_call_status"] == "not_called"


def test_build_asr_live_events_uses_adjacent_final_context_for_split_release_risk():
    events = build_asr_live_events(
        session_id="local_asr_split_release_context",
        provider="local_mock_asr",
        streaming_events=[
            {
                "event_type": "final",
                "segment_id": "funasr_001",
                "text": "a这次trcoutservice service周晚上灰",
                "start_ms": 0,
                "end_ms": 3600,
                "received_at_ms": 1221,
                "confidence": 0.91,
            },
            {
                "event_type": "final",
                "segment_id": "funasr_002",
                "text": "度百分之十先看errorate和p九九",
                "start_ms": 3600,
                "end_ms": 7200,
                "received_at_ms": 2411,
                "confidence": 0.91,
            },
            {
                "event_type": "final",
                "segment_id": "funasr_003",
                "text": "b如果指标异常我们暂停扩量但回滚",
                "start_ms": 7200,
                "end_ms": 10800,
                "received_at_ms": 3663,
                "confidence": 0.91,
            },
            {
                "event_type": "final",
                "segment_id": "funasr_004",
                "text": "脚本还没有在",
                "start_ms": 10800,
                "end_ms": 11297,
                "received_at_ms": 4063,
                "confidence": 0.91,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 11297,
                "end_ms": 11297,
                "received_at_ms": 4063,
            },
        ],
    )

    candidates = [event for event in events if event["event_type"] == "suggestion_candidate_event"]
    split_candidate = next(
        event
        for event in candidates
        if event["payload"]["target_id"].endswith("funasr_004")
    )

    assert split_candidate["payload"]["gap_rule_id"] in {
        "open.question.followup",
        "risk.rollback.validation",
    }
    assert split_candidate["payload"]["evidence_span_ids"] == [
        "asr_ev_funasr_003",
        "asr_ev_funasr_004",
    ]
    assert split_candidate["payload"]["segment_batch"] == ["funasr_003", "funasr_004"]
    assert split_candidate["payload"]["llm_call_status"] == "not_called"


def test_build_asr_live_events_extracts_architecture_review_gap_candidates():
    events = build_asr_live_events(
        session_id="local_asr_architecture_review_contract",
        provider="local_mock_asr",
        streaming_events=[
            {
                "event_type": "final",
                "segment_id": "asr_seg_architecture_risk_001",
                "text": "QPS 峰值按两万估，缓存穿透时可能会打到 mysql。",
                "start_ms": 0,
                "end_ms": 5000,
                "received_at_ms": 5500,
                "confidence": 0.95,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_architecture_owner_001",
                "text": "降级方案先写在设计文档里，压测 owner 还没安排。",
                "start_ms": 5000,
                "end_ms": 9000,
                "received_at_ms": 9500,
                "confidence": 0.95,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 9000,
                "end_ms": 9000,
                "received_at_ms": 9600,
            },
        ],
    )

    state_events = [event for event in events if event["event_type"] == "state_event"]
    candidates = [
        event for event in events if event["event_type"] == "suggestion_candidate_event"
    ]

    assert [event["payload"]["target_type"] for event in state_events] == [
        "Risk",
        "OpenQuestion",
    ]
    assert state_events[0]["payload"]["state_item"] == {
        "id": "asr_risk_asr_seg_architecture_risk_001",
        "description": "QPS 峰值按两万估，缓存穿透时可能会打到 mysql。",
        "impact": "runtime_issue",
        "mitigation": "",
        "status": "open",
        "evidence_span_ids": ["asr_ev_asr_seg_architecture_risk_001"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    assert state_events[1]["payload"]["state_item"] == {
        "id": "asr_question_asr_seg_architecture_owner_001",
        "question": "降级方案先写在设计文档里，压测 owner 还没安排。",
        "evidence_span_ids": ["asr_ev_asr_seg_architecture_owner_001"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    assert [candidate["payload"]["gap_rule_id"] for candidate in candidates] == [
        "risk.rollback.validation",
        "open.question.followup",
    ]


def test_build_asr_live_events_does_not_treat_negated_risk_as_open_risk():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_no_risk_001",
            "text": "没有风险。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_no_risk_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [
        event["payload"]["target_type"]
        for event in events
        if event["event_type"] == "state_event"
    ] == []


@pytest.mark.parametrize(
    "text",
    [
        "无风险。",
        "风险可控。",
        "风险已解除。",
        "如果错误率超过 0.1% 就回滚，风险可控。",
    ],
)
def test_build_asr_live_events_does_not_treat_resolved_risk_as_open_risk(text):
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_resolved_risk_001",
            "text": text,
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_resolved_risk_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [
        event["payload"]["target_type"]
        for event in events
        if event["event_type"] == "state_event"
    ] == []


def test_build_asr_live_events_keeps_multiple_state_candidates_next_to_scheduler():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_multi_001",
            "text": "先灰度 10%，谁负责回滚？",
            "start_ms": 0,
            "end_ms": 3200,
            "received_at_ms": 3500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 3600,
            "end_ms": 3600,
            "received_at_ms": 3600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_multi_state_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [event["event_type"] for event in events] == [
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]
    assert events[1]["payload"]["target_type"] == "DecisionCandidate"
    assert events[2]["payload"]["source_event_ids"] == [
        "asr_state_event_asr_seg_multi_001"
    ]
    assert events[3]["payload"]["target_type"] == "DecisionCandidate"
    assert events[3]["payload"]["gap_rule_id"] == "release.rollback.owner.required"
    assert events[4]["payload"]["target_candidate_id"] == events[3]["payload"]["candidate_id"]
    assert events[5]["payload"]["target_type"] == "OpenQuestion"
    assert events[6]["payload"]["source_event_ids"] == [
        "asr_question_event_asr_seg_multi_001"
    ]
    assert events[7]["payload"]["target_type"] == "OpenQuestion"
    assert events[7]["payload"]["gap_rule_id"] == "open.question.followup"
    assert events[8]["payload"]["target_candidate_id"] == events[7]["payload"]["candidate_id"]


def test_build_asr_live_events_keeps_dense_final_state_scheduler_groups_adjacent():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_001",
            "text": "先灰度 10%。",
            "start_ms": 0,
            "end_ms": 1000,
            "received_at_ms": 1000,
            "confidence": 0.91,
        },
        {
            "event_type": "revision",
            "segment_id": "asr_seg_001_rev1",
            "revision_of": "asr_seg_001",
            "text": "先灰度 5%，不是 10%。",
            "start_ms": 0,
            "end_ms": 1000,
            "received_at_ms": 1001,
            "confidence": 0.94,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 1002,
            "end_ms": 1002,
            "received_at_ms": 1002,
        },
    ]

    events = build_asr_live_events(
        session_id="dense_local_asr_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    assert [event["event_type"] for event in events] == [
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_revision",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]
    assert events[0]["payload"]["segment_id"] == "asr_seg_001"
    assert events[1]["payload"]["target_id"] == "asr_decision_asr_seg_001"
    assert events[2]["payload"]["source_event_ids"] == ["asr_state_event_asr_seg_001"]
    assert events[3]["payload"]["source_event_ids"] == ["asr_state_event_asr_seg_001"]
    assert events[4]["payload"]["target_candidate_id"] == events[3]["payload"]["candidate_id"]
    assert events[5]["payload"]["segment_id"] == "asr_seg_001_rev1"
    assert events[6]["payload"]["target_id"] == "asr_decision_asr_seg_001_rev1"
    assert events[7]["payload"]["source_event_ids"] == [
        "asr_state_event_asr_seg_001_rev1"
    ]
    assert events[9]["payload"]["target_candidate_id"] == events[8]["payload"]["candidate_id"]


def test_asr_event_source_metadata_uses_distinct_source_boundary():
    metadata = asr_event_source_metadata(provider="local_mock_asr")

    assert metadata == {
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "provider": "local_mock_asr",
        "is_mock": True,
    }


def test_build_mock_live_events_uses_live_boundary_and_includes_partial_final_scheduler_card():
    events = build_mock_live_events(
        _snapshot(),
        evaluation_summary={"passes_minimum_gate": True},
    )

    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert {event["source"] for event in events} == {"live_mock_stream"}
    assert {event["trace_kind"] for event in events} == {"live_event"}
    assert [event["at_ms"] for event in events] == sorted(event["at_ms"] for event in events)
    assert {event["event_type"] for event in events} >= {
        "transcript_partial",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "llm_schema_result",
        "suggestion_card",
        "evaluation_summary",
    }

    partial = next(event for event in events if event["event_type"] == "transcript_partial")
    assert partial["payload"]["is_final"] is False
    assert partial["payload"]["segment_id"] == "seg_001"
    assert partial["payload"]["text"].endswith("...")
    assert "evidence_spans" not in partial["payload"]

    final = next(
        event
        for event in events
        if event["event_type"] == "transcript_final"
        and event["payload"]["segment_id"] == "seg_001"
    )
    assert final["payload"]["is_final"] is True
    assert final["payload"]["text"] == "payment-gateway 先灰度 10%。"
    assert final["payload"]["evidence_spans"] == [
        {
            "id": "ev_001",
            "segment_id": "seg_001",
            "start_ms": 0,
            "end_ms": 5000,
            "quote": "payment-gateway 先灰度 10%。",
        }
    ]

    scheduler = next(event for event in events if event["event_type"] == "scheduler_event")
    assert scheduler["payload"]["scheduler_event_type"] == "llm_scheduled"
    assert scheduler["payload"]["source_event_ids"] == ["event_001"]
    assert scheduler["payload"]["segment_batch"] == ["seg_002"]

    state_event = next(event for event in events if event["event_type"] == "state_event")
    assert state_event["payload"]["state_item"] == {
        "id": "decision_001",
        "statement": "payment-gateway 先灰度 10%",
        "evidence_span_ids": ["ev_001"],
    }

    card_event = next(event for event in events if event["event_type"] == "suggestion_card")
    assert card_event["payload"]["card"]["title"] == "确认回滚负责人"
    assert card_event["payload"]["card"]["suggested_question"] == "谁负责 payment-gateway 灰度回滚？"
    assert card_event["payload"]["card"]["state_refs"] == ["decision_candidate:decision_001"]
    assert card_event["payload"]["card"]["usage"] == {"total_tokens": 336}


def test_build_mock_live_events_transcript_revision_supersedes_replaced_evidence():
    events = build_mock_live_events(_snapshot_with_revision())

    revision = next(event for event in events if event["event_type"] == "transcript_revision")

    assert revision["payload"]["segment_id"] == "seg_001_rev1"
    assert revision["payload"]["revision_of"] == "seg_001"
    assert revision["payload"]["supersedes_segment_id"] == "seg_001"
    assert revision["payload"]["superseded_evidence_spans"] == [
        {
            "id": "ev_001",
            "segment_id": "seg_001",
            "start_ms": 0,
            "end_ms": 5000,
            "quote": "payment-gateway 先灰度 10%。",
            "status": "superseded",
            "replaced_by": "ev_001_rev1",
        }
    ]
    assert revision["payload"]["evidence_spans"] == [
        {
            "id": "ev_001_rev1",
            "segment_id": "seg_001_rev1",
            "revision_of": "ev_001",
            "start_ms": 0,
            "end_ms": 5200,
            "quote": "payment-gateway 先灰度 5%，不是 10%。",
            "status": "active",
        }
    ]


def test_build_mock_live_events_emits_suggestion_invalidated_after_revision():
    events = build_mock_live_events(_snapshot_with_revision())

    invalidated = next(
        event for event in events if event["event_type"] == "suggestion_invalidated"
    )

    assert invalidated["at_ms"] == 7600
    assert invalidated["payload"]["card_id"] == "card_001"
    assert invalidated["payload"]["reason"] == "stale_evidence"
    assert invalidated["payload"]["invalidated_by_event_id"] == "transcript_revision:seg_001_rev1"
    assert invalidated["payload"]["stale_evidence_span_ids"] == ["ev_001"]
    assert invalidated["payload"]["replacement_evidence_span_ids"] == ["ev_001_rev1"]
    assert invalidated["payload"]["card"]["show_or_silence_decision"] == "silence"
    assert invalidated["payload"]["card"]["schema_result"] == "valid"
    assert invalidated["payload"]["card"]["invalidation_reason"] == "stale_evidence"
    assert invalidated["payload"]["card"]["invalidated_by_event_id"] == "transcript_revision:seg_001_rev1"

    revision_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "transcript_revision"
    )
    invalidated_index = events.index(invalidated)
    card_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] in {"suggestion_card", "suggestion_silenced"}
        and event["payload"].get("card_id") == "card_001"
    )
    assert revision_index < invalidated_index < card_index


def test_build_mock_live_events_downgrades_later_card_display_after_invalidation():
    events = build_mock_live_events(_snapshot_with_revision())

    invalidated = next(
        event for event in events if event["event_type"] == "suggestion_invalidated"
    )
    card_display_events = [
        event
        for event in events
        if event["payload"].get("card_id") == "card_001"
        and event["event_type"] in {"suggestion_card", "suggestion_silenced"}
    ]

    assert invalidated["at_ms"] < card_display_events[-1]["at_ms"]
    assert card_display_events[-1]["event_type"] == "suggestion_silenced"
    assert card_display_events[-1]["payload"]["show_or_silence_decision"] == "silence"
    assert card_display_events[-1]["payload"]["card"]["show_or_silence_decision"] == "silence"
    assert card_display_events[-1]["payload"]["card"]["invalidation_reason"] == "stale_evidence"
    assert (
        card_display_events[-1]["payload"]["card"]["invalidated_by_event_id"]
        == "transcript_revision:seg_001_rev1"
    )


def test_build_mock_live_events_limits_replacement_ids_to_impacted_card_evidence():
    snapshot = _snapshot_with_revision()
    snapshot["transcript"]["evidence_spans"].extend(
        [
            {
                "id": "ev_001_extra",
                "segment_id": "seg_001",
                "start_ms": 2500,
                "end_ms": 5000,
                "quote": "灰度 10%。",
            },
            {
                "id": "ev_001_extra_rev1",
                "segment_id": "seg_001_rev1",
                "revision_of": "ev_001_extra",
                "start_ms": 2500,
                "end_ms": 5200,
                "quote": "灰度 5%。",
            },
        ]
    )
    snapshot["suggestion_cards"][0]["evidence_span_ids"] = ["ev_001"]

    events = build_mock_live_events(snapshot)

    invalidated = next(
        event for event in events if event["event_type"] == "suggestion_invalidated"
    )
    assert invalidated["payload"]["stale_evidence_span_ids"] == ["ev_001"]
    assert invalidated["payload"]["replacement_evidence_span_ids"] == ["ev_001_rev1"]
    assert invalidated["payload"]["card"]["replacement_evidence_span_ids"] == ["ev_001_rev1"]


def test_build_mock_live_events_rejects_revision_without_evidence_lineage():
    snapshot = _snapshot_with_revision()
    snapshot["transcript"]["evidence_spans"][-1].pop("revision_of")

    with pytest.raises(ValueError, match="seg_001_rev1 revision evidence missing revision_of"):
        build_mock_live_events(snapshot)


def test_build_mock_live_events_rejects_revision_without_replacement_evidence():
    snapshot = _snapshot_with_revision()
    snapshot["transcript"]["evidence_spans"] = [
        evidence
        for evidence in snapshot["transcript"]["evidence_spans"]
        if evidence["id"] != "ev_001_rev1"
    ]

    with pytest.raises(ValueError, match="seg_001_rev1 revision missing replacement evidence"):
        build_mock_live_events(snapshot)


def test_build_mock_live_events_rejects_revision_lineage_outside_superseded_segment():
    snapshot = _snapshot_with_revision()
    snapshot["transcript"]["evidence_spans"][-1]["revision_of"] = "ev_002"

    with pytest.raises(ValueError, match="seg_001_rev1 revision evidence ev_001_rev1 does not replace evidence from seg_001"):
        build_mock_live_events(snapshot)


def test_build_mock_live_events_marks_silenced_cards_without_strong_card_event():
    snapshot = _snapshot()
    card = snapshot["suggestion_cards"][0]
    card["schema_result"] = "timeout"
    card["show_or_silence_decision"] = "silence"

    events = build_mock_live_events(snapshot)

    assert "suggestion_card" not in {event["event_type"] for event in events}
    silenced = next(event for event in events if event["event_type"] == "suggestion_silenced")
    assert silenced["payload"]["card_id"] == "card_001"
    assert silenced["payload"]["schema_result"] == "timeout"


def test_render_live_sse_events_uses_sequence_id_and_event_type():
    events = build_mock_live_events(_snapshot())

    rendered = render_sse_events(events)

    assert "event: transcript_partial" in rendered
    assert "event: scheduler_event" in rendered
    sse_events = [
        json.loads(line.removeprefix("data: "))
        for line in rendered.splitlines()
        if line.startswith("data: ")
    ]
    assert sse_events == events


def test_build_mock_live_events_rejects_unknown_card_state_event_reference():
    snapshot = _snapshot()
    snapshot["suggestion_cards"][0]["state_event_ids"] = ["missing_event"]

    with pytest.raises(ValueError, match="card_001 references unknown state_event_id"):
        build_mock_live_events(snapshot)


def test_build_mock_live_events_rejects_state_event_without_renderable_state_item():
    snapshot = _snapshot()
    snapshot["state_events"][0]["target_id"] = "missing_decision"

    with pytest.raises(ValueError, match="event_001 references unknown state item"):
        build_mock_live_events(snapshot)
