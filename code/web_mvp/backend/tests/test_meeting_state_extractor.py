from __future__ import annotations

from copy import deepcopy

import pytest

from meeting_copilot_web_mvp.meeting_state_extractor import extract_meeting_state


def segment(seq: int, text: str, *, normalized_text: str | None = None) -> dict[str, object]:
    return {
        "meeting_id": "meeting-1",
        "segment_id": f"segment-{seq}",
        "transcript_seq": seq,
        "text": text,
        "normalized_text": normalized_text if normalized_text is not None else text,
        "started_at_ms": seq * 1_000 - 900,
        "ended_at_ms": seq * 1_000,
        "updated_at_ms": seq * 1_000,
    }


@pytest.mark.parametrize(
    "text",
    [
        "这个接口的超时时间是多少？",
        "我们是否支持灰度发布？",
        "线上请求为什么会反复超时？",
        "缓存击穿应该怎么处理？",
        "谁负责数据库迁移？",
        "服务什么时候切到新集群？",
        "这里有没有幂等保护？",
        "能否把重试和熔断拆开配置？",
        "回滚方案还没确定。",
        "需要确认日志到底保留多少天。",
        "P99 延迟是不是超过目标了？",
        "这次发布要不要先压测？",
        "多租户数据具体如何隔离？",
        "哪个版本包含这个修复？",
        "这个接口为什么一直超时",
        "数据库迁移谁负责",
        "发布窗口什么时候开始",
        "这个功能是否支持离线模式",
        "这里怎么做比较稳",
    ],
)
def test_detects_common_chinese_technical_questions(text: str) -> None:
    state = extract_meeting_state([segment(1, text)])

    assert len(state["open_questions"]) == 1
    question = state["open_questions"][0]
    assert question["text"] == text
    assert question["status"] == "open"
    assert question["evidence_segment_ids"] == ["segment-1"]
    assert question["updated_at_ms"] == 1_000


def test_uses_canonical_normalized_text_without_inventing_content() -> None:
    raw = "这个接囗的超时是多少？"
    canonical = "这个接口的超时是多少？"

    state = extract_meeting_state([segment(1, raw, normalized_text=canonical)])

    assert state["current_topic"]["text"] == canonical
    assert state["open_questions"][0]["text"] == canonical
    assert "负责人" not in repr(state)
    assert "截止" not in repr(state)
    assert "结论" not in repr(state)


def test_latest_meaningful_sentence_becomes_topic_and_filler_does_not_replace_it() -> None:
    state = extract_meeting_state(
        [
            segment(1, "今天讨论订单服务从单库迁移到分片架构。"),
            segment(2, "嗯，好的。"),
            segment(3, "收到。"),
        ]
    )

    assert state["current_topic"] == {
        "id": state["current_topic"]["id"],
        "text": "今天讨论订单服务从单库迁移到分片架构。",
        "status": "active",
        "confidence": 0.8,
        "evidence": {
            "segment_id": "segment-1",
            "transcript_seq": 1,
            "start_ms": 100,
            "end_ms": 1_000,
            "quote": "今天讨论订单服务从单库迁移到分片架构。",
        },
        "evidence_segment_ids": ["segment-1"],
        "updated_at_ms": 1_000,
    }
    assert not state["current_topic"]["text"].isdigit()


def test_topic_moves_to_recent_substantive_technical_discussion() -> None:
    state = extract_meeting_state(
        [
            segment(1, "先讨论登录链路的限流策略。"),
            segment(2, "接下来讨论数据库迁移期间的双写校验。"),
        ]
    )

    assert state["current_topic"]["text"] == "接下来讨论数据库迁移期间的双写校验。"
    assert state["current_topic"]["evidence_segment_ids"] == ["segment-2"]


def test_repeated_question_is_merged_with_all_real_evidence() -> None:
    state = extract_meeting_state(
        [
            segment(1, "接口超时时间是多少？"),
            segment(2, "我再确认一下，接口超时时间是多少？"),
        ]
    )

    assert len(state["open_questions"]) == 1
    question = state["open_questions"][0]
    assert question["text"] == "接口超时时间是多少？"
    assert question["evidence_segment_ids"] == ["segment-1", "segment-2"]
    assert question["updated_at_ms"] == 2_000


def test_similar_topic_but_distinct_questions_are_not_merged() -> None:
    state = extract_meeting_state(
        [
            segment(1, "接口超时时间是多少？"),
            segment(2, "接口最大重试次数是多少？"),
        ]
    )

    assert [item["text"] for item in state["open_questions"]] == [
        "接口超时时间是多少？",
        "接口最大重试次数是多少？",
    ]


def test_clear_answer_marks_matching_question_answered() -> None:
    state = extract_meeting_state(
        [
            segment(1, "订单接口的超时时间是多少？"),
            segment(2, "订单接口的超时时间确定为三秒。"),
        ]
    )

    assert state["open_questions"] == [
        {
            "id": state["open_questions"][0]["id"],
                "text": "订单接口的超时时间是多少？",
                "status": "answered",
                "confidence": 0.8,
                "evidence": {
                    "segment_id": "segment-2",
                    "transcript_seq": 2,
                    "start_ms": 1_100,
                    "end_ms": 2_000,
                    "quote": "订单接口的超时时间确定为三秒。",
                },
                "evidence_segment_ids": ["segment-1", "segment-2"],
            "updated_at_ms": 2_000,
        }
    ]


def test_direct_yes_answer_can_close_immediately_preceding_question() -> None:
    state = extract_meeting_state(
        [
            segment(1, "发布平台是否支持百分之五灰度？"),
            segment(2, "支持百分之五灰度，今晚先给内部租户开放。"),
        ]
    )

    assert state["open_questions"][0]["status"] == "answered"
    assert state["open_questions"][0]["evidence_segment_ids"] == ["segment-1", "segment-2"]


@pytest.mark.parametrize(
    "uncertain_answer",
    [
        "订单接口的超时时间可能是三秒。",
        "订单接口的超时时间还要再确认。",
        "订单接口的超时时间应该是三秒。",
        "订单接口的超时时间目前不确定。",
    ],
)
def test_uncertain_response_keeps_question_open(uncertain_answer: str) -> None:
    state = extract_meeting_state(
        [
            segment(1, "订单接口的超时时间是多少？"),
            segment(2, uncertain_answer),
        ]
    )

    assert state["open_questions"][0]["status"] == "open"
    assert state["open_questions"][0]["evidence_segment_ids"] == ["segment-1"]


def test_answer_only_closes_matching_question() -> None:
    state = extract_meeting_state(
        [
            segment(1, "数据库迁移由谁负责？"),
            segment(2, "数据库迁移什么时候开始？"),
            segment(3, "数据库迁移由李明负责。"),
        ]
    )

    questions = {item["text"]: item for item in state["open_questions"]}
    assert questions["数据库迁移由谁负责？"]["status"] == "answered"
    assert questions["数据库迁移什么时候开始？"]["status"] == "open"


def test_projection_contains_at_most_three_recent_questions() -> None:
    state = extract_meeting_state(
        [
            segment(1, "接口超时是多少？"),
            segment(2, "最大重试次数是多少？"),
            segment(3, "熔断阈值是多少？"),
            segment(4, "日志保留多少天？"),
        ]
    )

    assert len(state["open_questions"]) == 3
    assert [item["text"] for item in state["open_questions"]] == [
        "最大重试次数是多少？",
        "熔断阈值是多少？",
        "日志保留多少天？",
    ]


def test_incremental_projection_matches_full_replay() -> None:
    first_batch = [
        segment(1, "今天讨论网关限流方案。"),
        segment(2, "租户级限流阈值是多少？"),
    ]
    second_batch = [
        segment(3, "租户级限流阈值确定为每秒一千次。"),
        segment(4, "超限请求是否直接拒绝？"),
    ]

    previous = extract_meeting_state(first_batch)
    incremental = extract_meeting_state(second_batch, previous_state=previous)
    replayed = extract_meeting_state(first_batch + second_batch)

    assert incremental == replayed


def test_question_lifecycle_carries_over_then_expires_and_can_reopen() -> None:
    initial = extract_meeting_state([segment(1, "数据库迁移由谁负责？")])
    carried = extract_meeting_state(
        [{**segment(2, "嗯。"), "updated_at_ms": 6 * 60_000}],
        previous_state=initial,
    )
    expired = extract_meeting_state(
        [{**segment(3, "收到。"), "updated_at_ms": 16 * 60_000}],
        previous_state=carried,
    )
    reopened = extract_meeting_state(
        [{**segment(4, "我再确认一下，数据库迁移由谁负责？"), "updated_at_ms": 17 * 60_000}],
        previous_state=expired,
    )

    assert carried["open_questions"][0]["status"] == "carried_over"
    assert expired["open_questions"][0]["status"] == "expired"
    assert reopened["open_questions"][0]["status"] == "open"
    assert reopened["open_questions"][0]["evidence_segment_ids"] == [
        "segment-1",
        "segment-4",
    ]


def test_topic_expires_when_no_meaningful_discussion_replaces_it() -> None:
    initial = extract_meeting_state([segment(1, "先讨论订单服务的灰度方案。")])

    expired = extract_meeting_state(
        [{**segment(2, "好的。"), "updated_at_ms": 6 * 60_000}],
        previous_state=initial,
    )

    assert expired["current_topic"]["text"] == "先讨论订单服务的灰度方案。"
    assert expired["current_topic"]["status"] == "expired"


def test_input_and_previous_projection_are_not_mutated() -> None:
    segments = [segment(1, "数据库切换时间是什么时候？")]
    previous = extract_meeting_state([segment(0, "先讨论数据库切换方案。")])
    original_segments = deepcopy(segments)
    original_previous = deepcopy(previous)

    extract_meeting_state(segments, previous_state=previous)

    assert segments == original_segments
    assert previous == original_previous


def test_speaker_prefix_is_not_part_of_question_but_text_remains_a_real_quote() -> None:
    state = extract_meeting_state([segment(1, "张三：缓存预热什么时候完成？")])

    assert state["open_questions"][0]["text"] == "缓存预热什么时候完成？"
    assert state["current_topic"]["text"] == "缓存预热什么时候完成？"


def test_empty_or_non_substantive_transcript_has_no_invented_state() -> None:
    state = extract_meeting_state(
        [
            segment(1, "嗯。"),
            segment(2, "好的。"),
            segment(3, "大家能听见吗？"),
        ]
    )

    assert state == {"current_topic": None, "open_questions": []}


def test_explanatory_sentence_with_question_word_is_not_an_open_question() -> None:
    state = extract_meeting_state(
        [segment(1, "这部分需要说明为什么采用本地 ASR。")]
    )

    assert state["open_questions"] == []
    assert state["current_topic"]["text"] == "这部分需要说明为什么采用本地 ASR。"


def test_extracts_conservative_decision_action_and_risk_facts_with_canonical_evidence() -> None:
    state = extract_meeting_state(
        [
            segment(
                1,
                "结论是采用蓝绿发布方案。由李明负责在周五前完成数据库迁移。"
                "风险是双写数据不一致，需要先做校验。",
            )
        ]
    )

    decision = state["decision_candidates"][0]
    assert decision["id"]
    assert decision["text"] == "结论是采用蓝绿发布方案。"
    assert decision["status"] == "candidate"
    assert decision["confidence"] >= 0.8
    assert decision["evidence"] == {
        "segment_id": "segment-1",
        "transcript_seq": 1,
        "start_ms": 100,
        "end_ms": 1_000,
        "quote": "结论是采用蓝绿发布方案。",
    }
    assert decision["evidence_segment_ids"] == ["segment-1"]

    action = state["action_items"][0]
    assert action["text"] == "由李明负责在周五前完成数据库迁移。"
    assert action["owner"] == "李明"
    assert action["deadline"] == "周五"
    assert action["evidence"]["quote"] == action["text"]

    risk = state["risks"][0]
    assert risk["text"] == "风险是双写数据不一致，需要先做校验。"
    assert risk["mitigation"] == "先做校验"
    assert risk["evidence"]["segment_id"] == "segment-1"


def test_candidate_facts_are_not_invented_from_uncertain_or_generic_sentences() -> None:
    state = extract_meeting_state(
        [
            segment(1, "可能采用蓝绿发布方案。") ,
            segment(2, "大家讨论一下回滚风险。") ,
            segment(3, "以后可以让李明看看迁移。") ,
        ]
    )

    assert state.get("decision_candidates", []) == []
    assert state.get("action_items", []) == []
    assert state.get("risks", []) == []


def test_action_item_keeps_unknown_owner_and_deadline_as_null() -> None:
    state = extract_meeting_state([segment(1, "需要在下周完成数据库迁移。")])

    action = state["action_items"][0]
    assert action["owner"] is None
    assert action["deadline"] == "下周"
