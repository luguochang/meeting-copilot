from __future__ import annotations

import json

import pytest

from meeting_copilot_web_mvp.realtime_answer_copilot import (
    build_realtime_answer_messages,
    detect_answer_trigger,
    detect_discussion_trigger,
    ground_realtime_answer,
    is_low_information_question_tail,
)


def test_discussion_deduplicates_repeated_fragments_and_requires_new_information():
    text = "财政贴息降低居民融资成本但银行收益还需要确认贷款需求和风险。"
    segments = [{"transcript_seq": index + 1, "text": text, "started_at_ms": index * 10_000,
                 "ended_at_ms": (index + 1) * 10_000} for index in range(8)]
    assert not detect_discussion_trigger(segments=segments, answer_jobs=[]).should_answer
    assert not detect_discussion_trigger(segments=segments, answer_jobs=[{"input_transcript_seq": 8}]).should_answer


def test_discussion_prompt_asks_for_incremental_response_not_invented_question():
    messages = build_realtime_answer_messages(question_text="当前讨论重点", context_segments=[], response_mode="discussion")
    assert json.loads(messages[1]["content"])["response_mode"] == "discussion"
    assert "不要虚构对方提问" in messages[0]["content"]


@pytest.mark.parametrize("text", [
    "我们还要把居民是否得到实际减负作为政策效果的判断依据。",
    "需要确认是否产生新增需求。",
])
def test_embedded_uncertainty_does_not_replace_the_current_question(text):
    assert not detect_answer_trigger(text=text, source_track="system_audio").should_answer


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


@pytest.mark.parametrize("text", ["你吗？", "是吗", "那呢？", "可以嘛？"])
def test_low_information_question_tail_is_detected(text: str) -> None:
    assert is_low_information_question_tail(text) is True


@pytest.mark.parametrize("text", ["能上线吗？", "为什么呢？", "风险是什么？"])
def test_meaningful_short_question_is_not_treated_as_a_tail(text: str) -> None:
    assert is_low_information_question_tail(text) is False


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
    assert "未逐字出现的具体日期" in messages[0]["content"]
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


def test_answer_grounding_removes_unseen_deadline_and_owner_but_keeps_safe_clause() -> None:
    grounded = ground_realtime_answer(
        "周四下班前由王工负责闭环，压测达标后再决定是否上线。",
        evidence_texts=["本周五到底能不能上线？压测达标是放行前提。"],
    )

    assert grounded.changed is True
    assert grounded.removed_claim_types == ("deadline", "owner")
    assert "周四" not in grounded.text
    assert "王工" not in grounded.text
    assert grounded.text.startswith("压测达标后再决定是否上线。")
    assert grounded.text.endswith("具体时间、责任归属以会议原话为准。")


def test_answer_grounding_keeps_numbers_and_states_present_in_evidence() -> None:
    answer = "错误率低于0.1%才上线，目前还没有确认达标。"
    grounded = ground_realtime_answer(
        answer,
        evidence_texts=["错误率要低于0.1%，现在还没有确认达标。"],
    )

    assert grounded.changed is False
    assert grounded.text == answer


def test_answer_grounding_allows_a_cautious_non_commitment() -> None:
    answer = "目前不能直接承诺周五上线，需要先确认放行条件。"
    grounded = ground_realtime_answer(
        answer,
        evidence_texts=["周五可以上线吗？放行条件是什么？"],
    )

    assert grounded.changed is False
    assert grounded.text == answer


def test_answer_grounding_falls_back_when_every_clause_invents_material_facts() -> None:
    grounded = ground_realtime_answer(
        "明天下午六点前必须完成500并发压测。",
        evidence_texts=["压测方案还需要继续讨论。"],
    )

    assert grounded.changed is True
    assert grounded.removed_claim_types == ("number", "deadline")
    assert "明天" not in grounded.text
    assert "500" not in grounded.text
    assert "需要先确认" in grounded.text
