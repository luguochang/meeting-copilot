from __future__ import annotations

import json

import pytest

from meeting_copilot_web_mvp.realtime_answer_copilot import (
    build_realtime_answer_messages,
    detect_answer_trigger,
)


@pytest.mark.parametrize(
    "text",
    [
        "你们为什么没有选择 Kafka，而是用了 Redis Stream",
        "介绍一下你负责的项目",
        "能不能讲讲这套架构是怎么演进的",
        "What was the hardest tradeoff you made",
        "Walk me through the incident response",
    ],
)
def test_system_audio_question_and_interview_requests_trigger(text: str) -> None:
    trigger = detect_answer_trigger(
        text=text,
        source_track="system_audio",
        preset_id="interview",
    )

    assert trigger.should_answer is True
    assert trigger.question_text == text
    assert trigger.confidence >= 0.9


def test_microphone_speech_never_triggers_an_answer() -> None:
    trigger = detect_answer_trigger(
        text="我来介绍一下我负责的项目，可以吗？",
        source_track="microphone",
        preset_id="interview",
    )

    assert trigger.should_answer is False
    assert trigger.reason == "source_track_not_remote"


def test_explicit_browser_single_track_question_triggers_with_lower_confidence() -> None:
    trigger = detect_answer_trigger(
        text="你们为什么选择 Redis Stream，而不是 Kafka？",
        source_track="microphone",
        preset_id="interview",
        allow_single_track_microphone=True,
    )

    assert trigger.should_answer is True
    assert trigger.question_text == "你们为什么选择 Redis Stream，而不是 Kafka？"
    assert trigger.reason == "single_track_question_mark"
    assert trigger.confidence == pytest.approx(0.88)


def test_dual_track_microphone_question_does_not_trigger_answer() -> None:
    trigger = detect_answer_trigger(
        text="你们为什么选择 Redis Stream，而不是 Kafka？",
        source_track="microphone",
        preset_id="interview",
        allow_single_track_microphone=False,
    )

    assert trigger.should_answer is False
    assert trigger.reason == "source_track_not_remote"


def test_browser_single_track_focus_terms_reject_unrelated_room_question() -> None:
    trigger = detect_answer_trigger(
        text="观澜湖新城不就有吗？",
        source_track="microphone",
        preset_id="general",
        allow_single_track_microphone=True,
        single_track_focus_terms=("Redis Stream", "Kafka", "架构取舍"),
    )

    assert trigger.should_answer is False
    assert trigger.reason == "single_track_off_focus"


def test_browser_single_track_focus_terms_keep_relevant_question() -> None:
    trigger = detect_answer_trigger(
        text="为什么选择 Redis Stream，而不是 Kafka？",
        source_track="microphone",
        preset_id="general",
        allow_single_track_microphone=True,
        single_track_focus_terms=("Redis Stream", "Kafka", "架构取舍"),
    )

    assert trigger.should_answer is True
    assert trigger.reason == "single_track_question_mark"


def test_browser_single_track_focus_terms_tolerate_partial_hotword_asr() -> None:
    trigger = detect_answer_trigger(
        text="为什么会选这个 Redis，而不是别的方案？",
        source_track="microphone",
        preset_id="general",
        allow_single_track_microphone=True,
        single_track_focus_terms=("Redis Stream", "Kafka", "架构取舍"),
    )

    assert trigger.should_answer is True
    assert trigger.reason == "single_track_question_mark"


@pytest.mark.parametrize(
    "text",
    [
        "好的收到",
        "谢谢，继续",
        "@@@ ### ???",
        "x",
    ],
)
def test_non_questions_and_unreadable_noise_do_not_trigger(text: str) -> None:
    trigger = detect_answer_trigger(
        text=text,
        source_track="system_audio",
        preset_id="general",
    )

    assert trigger.should_answer is False


def test_answer_prompt_is_bounded_role_aware_and_forbids_invention() -> None:
    messages = build_realtime_answer_messages(
        question_text="为什么选择 Redis Stream？",
        context_segments=[
            {
                "source_track": "microphone",
                "normalized_text": "当前团队已经维护 Redis，流量规模是每秒一千条。",
            },
            {
                "source_track": "system_audio",
                "normalized_text": "为什么选择 Redis Stream，而不是 Kafka？",
            },
        ],
        preset_id="interview",
        meeting_goal="完成后端工程师技术面试",
        participant_role="候选人",
        focus_points=["架构取舍", "可靠性"],
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "第一句就回答问题" in messages[0]["content"]
    assert "绝不编造" in messages[0]["content"]
    assert "会议摘要" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert payload["current_question"] == "为什么选择 Redis Stream？"
    assert payload["scene"] == "interview"
    assert payload["personal_experience_question"] is False
    assert payload["user_fact_available"] is True
    assert payload["recent_dialogue"] == [
        {"speaker": "我", "text": "当前团队已经维护 Redis，流量规模是每秒一千条。"},
        {"speaker": "对方", "text": "为什么选择 Redis Stream，而不是 Kafka？"},
    ]


def test_personal_experience_prompt_requires_real_user_facts() -> None:
    messages = build_realtime_answer_messages(
        question_text="这次项目中最困难的问题是什么？",
        context_segments=[
            {
                "source_track": "system_audio",
                "normalized_text": "这次项目中最困难的问题是什么？",
            },
        ],
        preset_id="interview",
    )

    payload = json.loads(messages[1]["content"])
    assert payload["personal_experience_question"] is True
    assert payload["user_fact_available"] is False
    assert "不得写成‘我负责’" in messages[0]["content"]


def test_single_track_prompt_never_treats_mixed_speech_as_user_history() -> None:
    messages = build_realtime_answer_messages(
        question_text="请介绍一下你负责的项目",
        context_segments=[
            {
                "source_track": "microphone",
                "normalized_text": "我们当时把订单号作为幂等键。",
            },
            {
                "source_track": "microphone",
                "normalized_text": "请介绍一下你负责的项目。",
            },
        ],
        preset_id="interview",
        single_track_mixed=True,
    )

    payload = json.loads(messages[1]["content"])
    assert payload["speaker_attribution"] == "mixed_single_track"
    assert payload["user_fact_available"] is False
    assert payload["recent_dialogue"] == [
        {"speaker": "参会者（单轨混合）", "text": "我们当时把订单号作为幂等键。"},
        {"speaker": "参会者（单轨混合）", "text": "请介绍一下你负责的项目。"},
    ]
