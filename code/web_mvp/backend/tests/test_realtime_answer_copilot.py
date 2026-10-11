from __future__ import annotations

import json

import pytest

from meeting_copilot_web_mvp.coach_contract import COACH_ROLE_CONTRACT, COACH_ROLE_CONTRACT_VERSION
from meeting_copilot_web_mvp.realtime_answer_copilot import (
    AnswerTrigger,
    automatic_answer_policy_gate,
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
    assert not detect_discussion_trigger(segments=segments, answer_jobs=[], meeting_goal="核对政策实际效果").should_answer
    assert not detect_discussion_trigger(segments=segments, answer_jobs=[{"input_transcript_seq": 8}],
                                         meeting_goal="核对政策实际效果").should_answer


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
    assert [{"speaker": item["speaker"], "text": item["text"]} for item in payload["recent_dialogue"]] == [
        {"speaker": "我", "text": "当前团队已经维护 Redis，流量规模是每秒一千条。"},
        {"speaker": "对方", "text": "为什么选择 Redis Stream，而不是 Kafka？"},
    ]
    assert payload["role_action"] == "job_candidate_answer"


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
    assert [{"speaker": item["speaker"], "text": item["text"]} for item in payload["recent_dialogue"]] == [
        {"speaker": "参会者（单轨混合）", "text": "我们当时把订单号作为幂等键。"},
        {"speaker": "参会者（单轨混合）", "text": "请介绍一下你负责的项目。"},
    ]


@pytest.mark.parametrize("participant_role", [None, "用户研究访谈者", "访谈主持人"])
def test_research_interview_gives_neutral_follow_up_instead_of_candidate_answer(participant_role):
    messages = build_realtime_answer_messages(
        question_text="使用这个功能时有什么困难？",
        context_segments=[{"source_track": "system_audio", "text": "这个功能很不好用。"}],
        preset_id="interview",
        meeting_goal="了解用户真实行为和困难",
        participant_role=participant_role,
    )

    payload = json.loads(messages[1]["content"])
    assert payload["role_action"] == "neutral_research_follow_up"
    assert "给针对原话的中立追问" in messages[0]["content"]
    assert "采用结论先行或 STAR" not in messages[0]["content"]
    assert messages[0]["content"].startswith(COACH_ROLE_CONTRACT)
    assert payload["role_contract_version"] == COACH_ROLE_CONTRACT_VERSION


def test_research_participant_role_answers_instead_of_interviewing_the_interviewer():
    messages = build_realtime_answer_messages(
        question_text="最近一次使用是在什么场景？", context_segments=[],
        preset_id="interview", participant_role="受访者",
    )
    assert json.loads(messages[1]["content"])["role_action"] == "research_participant_answer"
    assert "仅使用已提供的真实经历和行为" in messages[0]["content"]


def test_answer_prompt_preserves_revision_and_review_state_for_critical_evidence():
    messages = build_realtime_answer_messages(
        question_text="可以交付吗？",
        context_segments=[
            {"segment_id": "s1", "source_track": "system_audio", "normalized_text": "已验收。",
             "revision": 1, "correction_status": "pending"},
            {"segment_id": "s2", "source_track": "microphone", "normalized_text": "未验收。",
             "revision": 2, "correction_status": "changed"},
            {"segment_id": "s3", "text": "没有质量状态的原话。"},
        ], single_track_mixed=True,
    )

    dialogue = json.loads(messages[1]["content"])["recent_dialogue"]
    assert [(item["segment_id"], item["revision"], item["correction_status"], item["evidence_status"])
            for item in dialogue] == [
        ("s1", 1, "pending", "provisional"), ("s2", 2, "changed", "reviewed"),
        ("s3", None, "unknown", "unknown"),
    ]
    assert dialogue[1]["speaker"] == "参会者（单轨混合）"
    assert "不能升级为确定事实" in messages[0]["content"]


def test_fast_answer_accepts_structured_goal_without_losing_goal_or_role():
    messages = build_realtime_answer_messages(
        question_text="有什么值得追问的？", context_segments=[], preset_id="interview",
        meeting_goal={"meeting_goal": "核对实际工作流", "participant_role": "访谈者",
                      "focus_points": ["实际行为", "替代方案"]},
    )
    payload = json.loads(messages[1]["content"])
    assert payload["meeting_goal"] == "核对实际工作流"
    assert payload["my_role"] == "访谈者"
    assert payload["focus_points"] == ["实际行为", "替代方案"]


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


def test_grounding_retains_neutral_example_request_and_refused_deadline():
    for answer in ("能回忆最近一次没有提交订单时发生了什么吗？", "如果想不起具体一次，也可以说说当时场景。",
                   "今天先不发布。需要先核对压测。", "现在不能承诺明天一定交付。请核对验收结果。"):
        result = ground_realtime_answer(answer, evidence_texts=["原因未知，验收尚未完成。"])
        assert result.text == answer and not result.changed


def test_grounding_still_rejects_actual_unsupported_counts_and_mixed_promises():
    for answer in ("发生了三次故障。", "不能保证周五完成但明天一定上线。", "明天已完成验收。"):
        result = ground_realtime_answer(answer, evidence_texts=["原因未知，验收尚未完成。"])
        assert result.changed


def test_grounding_distinguishes_refused_assumption_and_evidenced_restoration():
    answer = "现在还不能把发布当作已确定的下一步。"
    assert not ground_realtime_answer(answer, evidence_texts=["复核部署配置后再决定是否发布。"]).changed
    answer = "连接问题已经解决，现已恢复。"
    assert not ground_realtime_answer(answer, evidence_texts=["用户已核对，连接已恢复。"]).changed
    assert ground_realtime_answer("连接问题已经解决。", evidence_texts=["连接尚未恢复。"]).changed


def test_grounding_preserves_refusal_to_assign_an_unknown_person_and_refused_publication_assumption():
    answer = "还没有指定验收负责人，不能先默认由某个人负责。"
    assert not ground_realtime_answer(answer, evidence_texts=["验收负责人还未指定。"]).changed
    answer = "复核完成前，先不要把发布视为已确定。"
    assert not ground_realtime_answer(answer, evidence_texts=["复核部署配置后再决定是否发布。"]).changed
    assert ground_realtime_answer("现在由王五负责，发布已经确定。", evidence_texts=["尚未确认负责人。"]).changed


@pytest.mark.parametrize("text", [
    "他问我为什么选这个方案。",
    "主持人问：这个活动要怎么报名？",
    "有观众问我，什么时候发布新节目？",
    "“你们的目标是什么？”这是采访里引用的问题。",
    'The host asked why the cache was slow.',
    '"How do you use this feature?" was the question in the recording.',
])
def test_quoted_or_reported_questions_do_not_create_an_answer(text):
    assert not detect_answer_trigger(text=text, source_track="system_audio").should_answer


@pytest.mark.parametrize("text", [
    "聚会人多了，最后谁付过钱就给忘了。",
    "我并不理解什么意思。",
    "我们会介绍这个功能是什么。",
    "他想怎么安排都可以。",
    "这是决定谁来负责的依据。",
    "I don't know why he changed the schedule.",
])
def test_question_words_inside_statements_are_not_pending_questions(text):
    assert not detect_answer_trigger(text=text, source_track="system_audio").should_answer


@pytest.mark.parametrize("text", [
    "大家出门以后忘了谁拿了行李然后找了很久最后发现行李已经由工作人员送到房间后来就没有再追问了",
    "讲到这个节目里的人物我自己不知道那是什么设定其实也不理解内容作者究竟想表达什么意思",
    "是不是所有顾客都应该为我",
    "你是不是想说关于",
    "你要讲的这个",
])
def test_unpunctuated_narratives_and_dangling_question_grammar_do_not_trigger(text):
    assert not detect_answer_trigger(text=text, source_track="system_audio").should_answer


@pytest.mark.parametrize("text", [
    "难道这还需要解释吗？",
    "谁不知道要先检查配置？",
    "对不对？",
    "缓存本来就是定时清理的，你知道吗？",
    "那你是",
    "你能介绍一下你负责的",
])
def test_rhetorical_tags_and_incomplete_asr_questions_are_silent(text):
    assert not detect_answer_trigger(text=text, source_track="system_audio").should_answer


@pytest.mark.parametrize("text", [
    "为什么换存储方案？因为原方案维护成本太高。",
    "延迟的原因是什么？答案是缓存没有命中。",
    "为什么采用这套架构，因为团队已经熟悉它了。",
    "Why did you choose the queue? Because the retry policy was clearer.",
    "如何处理这个错误？不用回答，我刚才已经解释过了。",
])
def test_a_question_answered_in_the_same_turn_does_not_trigger_again(text):
    trigger = detect_answer_trigger(text=text, source_track="system_audio")
    assert not trigger.should_answer
    assert trigger.reason == "question_already_answered"


@pytest.mark.parametrize("text", [
    "你们为什么采用消息队列，而不是同步调用？",
    "负责人是谁？",
    "这两个方案的区别是什么",
    "延迟怎么排查",
    "请解释一下订单重试的条件。",
    "刚才的流程我没听懂，你能解释一下吗？",
    "他问过这个问题。你现在能介绍一下实际处理流程吗？",
    "“CacheBox”为什么没有缓存命中？",
    "最近一次使用这个功能是在什么场景？",
    "How would you handle a failed retry?",
    "Please describe the incident response.",
    "你负责的项目为什么没有选择数据库",
    "你们为什么不先压测然后再决定是否上线",
    "我不理解这一步，你能解释一下吗？",
    "为什么要用这个？",
])
def test_complete_questions_and_direct_requests_survive_local_admission(text):
    assert detect_answer_trigger(text=text, source_track="system_audio", preset_id="interview").should_answer


def _discussion_segments():
    return [{"transcript_seq": index + 1, "text": f"方案{index}：" + "当前流程的限制和风险需要结合真实用户行为继续核对。" * 3,
             "started_at_ms": index * 20_000, "ended_at_ms": (index + 1) * 20_000} for index in range(4)]


@pytest.mark.parametrize("goal", [None, "", "  ", {"participant_role": "主持人"}, {"focus_points": ["成本"]}])
def test_discussion_requires_an_explicit_goal_not_just_accumulated_speech(goal):
    trigger = detect_discussion_trigger(segments=_discussion_segments(), answer_jobs=[], meeting_goal=goal)
    assert not trigger.should_answer
    assert trigger.reason == "discussion_no_goal"


@pytest.mark.parametrize("policy,expected", [("low_frequency", False), ("standard", True), ("off", False)])
def test_discussion_low_frequency_and_standard_have_distinct_cooldowns(policy, expected):
    trigger = detect_discussion_trigger(
        segments=_discussion_segments(), answer_jobs=[{"input_transcript_seq": 2}],
        meeting_goal={"goal": "核对用户操作障碍"}, proactive_policy=policy,
    )
    assert trigger.should_answer is expected


def test_low_frequency_discussion_can_resume_after_sixty_seconds_of_new_goal_relevant_speech():
    trigger = detect_discussion_trigger(
        segments=_discussion_segments(), answer_jobs=[{"input_transcript_seq": 1}],
        meeting_goal="核对用户操作障碍", proactive_policy="low_frequency",
    )
    assert trigger.should_answer
    assert trigger.reason == "discussion"


def test_automatic_gate_never_reenables_a_rejected_question_and_off_blocks_all_auto_questions():
    rejected = AnswerTrigger(False, None, "question_already_answered", 0.9)
    assert automatic_answer_policy_gate(rejected, proactive_policy="standard") is rejected
    question = AnswerTrigger(True, "为什么延迟？", "question_mark", 0.98)
    assert automatic_answer_policy_gate(question, proactive_policy="off").reason == "automatic_policy_off"
    assert not automatic_answer_policy_gate(question, proactive_policy="invalid").should_answer
    assert automatic_answer_policy_gate(question, proactive_policy="low_frequency") is question


def test_discussion_prompt_does_not_force_a_reply_or_a_condition():
    messages = build_realtime_answer_messages(
        question_text="当前讨论重点", context_segments=[], response_mode="discussion",
        meeting_goal="核对用户操作障碍",
    )
    assert "不强制添加接话、追问或待确认条件" in messages[0]["content"]
    assert "话还没说完时不要抢答" in messages[0]["content"]
