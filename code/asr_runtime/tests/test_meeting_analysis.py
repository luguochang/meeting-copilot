import subprocess
import sys

from scripts.meeting_analysis import (
    build_llm_usage,
    build_prompt,
    masked_llm_config,
    validate_analysis,
)


def test_validate_analysis_requires_evidence_for_suggestion_cards():
    analysis = {
        "summary": "讨论了灰度上线。",
        "meeting_context": {
            "is_engineering_meeting": True,
            "reason": "讨论上线、灰度、回滚。",
        },
        "states": {
            "decision_candidates": [
                {"statement": "先灰度 10%", "evidence_span_id": "ev_001"}
            ],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [
            {
                "type": "rollback_gap",
                "suggested_question": "是否需要确认回滚负责人？",
                "evidence_span_id": "ev_001",
            }
        ],
    }

    validate_analysis(analysis, evidence_span_ids={"ev_001"})


def test_validate_analysis_rejects_suggestion_without_known_evidence():
    analysis = {
        "summary": "讨论了灰度上线。",
        "meeting_context": {
            "is_engineering_meeting": True,
            "reason": "讨论上线、灰度、回滚。",
        },
        "states": {
            "decision_candidates": [],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [
            {
                "type": "rollback_gap",
                "suggested_question": "是否需要确认回滚负责人？",
                "evidence_span_id": "ev_missing",
            }
        ],
    }

    try:
        validate_analysis(analysis, evidence_span_ids={"ev_001"})
    except ValueError as exc:
        assert "unknown evidence_span_id" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_validate_analysis_rejects_unknown_suggestion_type():
    analysis = {
        "summary": "讨论了灰度上线。",
        "meeting_context": {
            "is_engineering_meeting": True,
            "reason": "讨论上线、灰度、回滚。",
        },
        "states": {
            "decision_candidates": [],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [
            {
                "type": "generic_advice",
                "suggested_question": "是否需要继续讨论？",
                "evidence_span_id": "ev_001",
            }
        ],
    }

    try:
        validate_analysis(analysis, evidence_span_ids={"ev_001"})
    except ValueError as exc:
        assert "unknown suggestion card type" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_validate_analysis_accepts_suggestion_card_evidence_span_ids():
    analysis = {
        "summary": "讨论了灰度上线。",
        "meeting_context": {
            "is_engineering_meeting": True,
            "reason": "讨论上线、灰度、回滚。",
        },
        "states": {
            "decision_candidates": [
                {"statement": "先灰度 10%", "evidence_span_id": "ev_001"}
            ],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [
            {
                "type": "rollback_gap",
                "suggested_question": "是否需要确认回滚负责人？",
                "evidence_span_ids": ["ev_001"],
            }
        ],
    }

    validate_analysis(analysis, evidence_span_ids={"ev_001"})


def test_validate_analysis_rejects_state_without_evidence():
    analysis = {
        "summary": "讨论了灰度上线。",
        "meeting_context": {
            "is_engineering_meeting": True,
            "reason": "讨论上线、灰度、回滚。",
        },
        "states": {
            "decision_candidates": [
                {"statement": "先灰度 10%", "status": "candidate"}
            ],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [],
    }

    try:
        validate_analysis(analysis, evidence_span_ids={"ev_001"})
    except ValueError as exc:
        assert "state missing evidence_span_id" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_validate_analysis_rejects_unknown_state_evidence_span_ids():
    analysis = {
        "summary": "讨论了灰度上线。",
        "meeting_context": {
            "is_engineering_meeting": True,
            "reason": "讨论上线、灰度、回滚。",
        },
        "states": {
            "decision_candidates": [
                {
                    "statement": "先灰度 10%",
                    "status": "candidate",
                    "evidence_span_ids": ["ev_missing"],
                }
            ],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [],
    }

    try:
        validate_analysis(analysis, evidence_span_ids={"ev_001"})
    except ValueError as exc:
        assert "unknown evidence_span_id" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_validate_analysis_rejects_engineering_cards_for_non_engineering_meeting():
    analysis = {
        "summary": "讨论了二级市场投资。",
        "meeting_context": {
            "is_engineering_meeting": False,
            "reason": "内容是投资观点，不是软件工程交付会议。",
        },
        "states": {
            "decision_candidates": [],
            "action_items": [],
            "risks": [],
            "open_questions": [],
        },
        "suggestion_cards": [
            {
                "type": "rollback_gap",
                "suggested_question": "是否需要确认回滚负责人？",
                "evidence_span_id": "ev_001",
            }
        ],
    }

    try:
        validate_analysis(analysis, evidence_span_ids={"ev_001"})
    except ValueError as exc:
        assert "non-engineering meeting" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_build_prompt_requires_engineering_context_gate():
    prompt = build_prompt(
        {
            "text": "今天聊股票投资和宏观经济。",
            "evidence_spans": [{"id": "ev_001", "quote": "今天聊股票投资和宏观经济。"}],
        }
    )

    assert "meeting_context" in prompt
    assert "非软件工程" in prompt
    assert "suggestion_cards 必须为空" in prompt


def test_meeting_analysis_cli_accepts_events_output_argument():
    result = subprocess.run(
        [sys.executable, "scripts/meeting_analysis.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--events-output" in result.stdout
    assert "--usage-output" in result.stdout


def test_build_llm_usage_records_model_without_api_key():
    usage = build_llm_usage(
        config={
            "base_url": "https://example.test",
            "model": "gpt-test",
            "api_key": "sk-secret-not-for-output",
        },
        response_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )

    assert usage == {
        "provider": "https://example.test",
        "model": "gpt-test",
        "prompt_version": "meeting_analysis.v1",
        "call_count": 1,
        "retry_count": 0,
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }
    assert "api_key" not in usage


def test_masked_llm_config_does_not_expose_secret():
    masked = masked_llm_config(
        {
            "base_url": "https://example.test",
            "model": "gpt-test",
            "api_key": "sk-secret-not-for-output",
        }
    )

    assert masked["api_key"] == "sk-***"
