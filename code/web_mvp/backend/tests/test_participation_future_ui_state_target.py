from __future__ import annotations

import json
from dataclasses import replace

import pytest

from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    RealtimeIntelligenceRequest,
    parse_realtime_coach_response,
)


PUBLIC_SOURCE = "页面取消后的结果展示尚待讨论，任务处理器何时停止仍需评审。"
FUTURE_TARGET = "建议等服务端停止后再将页面状态显示为“已确认”。"


def _request(text: str = PUBLIC_SOURCE) -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="public-future-ui-target", state_revision=1,
        new_paragraphs=[{"id": "current", "text": text, "revision": 1}],
        context_paragraphs=[], rolling_state={}, trigger_type="user_request",
        user_request="帮我提出一个值得讨论的问题", coach_profile="participation",
        request_metadata={"profile": "participation", "intent": "ask_question",
                          "topic_id": "topic-cancel", "analysis_basis_revision": 1},
    )


def _card(**changes):
    return {
        "event_type": "question_to_user", "title": "核对取消结果展示", "urgency": "low",
        "recommendation": FUTURE_TARGET,
        "reason": "当前在讨论取消结果和处理器停止之间的关系。",
        "evidence_segment_ids": ["current"], "evidence_quote": PUBLIC_SOURCE, "confidence": 0.9,
        "supporting_facts": [{"source_ref": "current", "text": PUBLIC_SOURCE}], "assumptions": [],
        **changes,
    }


def _parse(card, request=None):
    return parse_realtime_coach_response(
        json.dumps({"intervention": card}, ensure_ascii=False), request=request or _request(),
    )


@pytest.mark.parametrize("recommendation", [
    "建议等服务端停止后将页面状态显示为已确认。",
    FUTURE_TARGET,
    "建议在服务端处理完成后把页面状态显示为“已确认”。",
    "考虑待服务端停止以后才把页面提示标记为'已完成'。",
    "可以在回归测试通过之后再将状态转换为「已确认」。",
    "能否等校验验收后把提示标记为『尚未完成』？",
    "是否可以在人工校验后将文案设为‘已经完成’。",
    '建议在服务端校验后把页面状态切换为"已确认"。',
])
def test_whole_parser_accepts_an_explicit_future_boundary_and_complete_display_target(recommendation):
    parsed = _parse(_card(recommendation=recommendation))
    assert parsed.recommendation == recommendation
    assert parsed.title == _card()["title"]
    assert parsed.reason == _card()["reason"]
    assert parsed.confidence == _card()["confidence"]


@pytest.mark.parametrize("recommendation", [
    "建议在校验完成后把提示切换为已确认。",
    "可以在测试通过之后将提示标记为已完成。",
    "建议在处理完成后把任务切换为已确认。",
])
def test_existing_future_target_forms_remain_accepted(recommendation):
    assert _parse(_card(recommendation=recommendation)).recommendation == recommendation


@pytest.mark.parametrize("recommendation", [
    "系统已经完成验收，建议等服务端停止后将页面状态显示为“已确认”。",
    "系统“已经完成验收”，建议等服务端停止后将页面状态显示为“已确认”。",
    "建议等服务端停止后将页面状态显示为“已确认”，但是系统已经完成验收。",
    "建议等服务端停止后将页面状态显示为“已确认”。系统已经完成验收。",
    "建议等服务端停止后将页面状态显示为“已确认”且系统已经完成验收。",
    "建议等服务端停止后将页面状态显示为“已确认且系统已经完成验收”。",
    "建议等服务端停止后将页面状态显示为已确认系统已经完成验收。",
    "建议等服务端停止后将页面状态显示为“已确认已经完成验收”。",
    "建议等系统已经完成验收且服务端停止后将页面状态显示为“已确认”。",
    "建议等服务端停止后系统已验收后将页面状态显示为“已确认”。",
    "请确认报告是否已经完成，系统已经完成验收。",
])
def test_future_display_target_does_not_hide_an_independent_state_assertion(recommendation):
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        _parse(_card(recommendation=recommendation))


@pytest.mark.parametrize("recommendation", [
    "建议等服务端停止后将页面状态显示为“已确认」。",
    "建议等服务端停止后将页面状态显示为“已确认。",
    "建议等服务端停止后将页面状态显示为已确认”。",
    "建议等服务端停止后将页面状态显示为“已确认””。",
    "建议将取消结果的页面文案定义为“已确认”，具体生效时刻仍需核对。",
    "建议将页面状态显示为“已确认”。",
    "建议服务端停止后将页面状态显示为“已确认”。",
    "建议等服务端停止后将数据库记录显示为“已确认”。",
    "建议等服务端停止后将页面状态显示为“已确认系统验收”。",
])
def test_ambiguous_grammar_label_definition_and_incomplete_targets_stay_source_bound(recommendation):
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        _parse(_card(recommendation=recommendation))


@pytest.mark.parametrize("field", ["title", "reason", "assumptions", "supporting_facts"])
def test_future_target_permission_only_applies_to_the_recommendation(field):
    value = [FUTURE_TARGET] if field == "assumptions" else (
        [{"source_ref": "current", "text": FUTURE_TARGET}] if field == "supporting_facts" else FUTURE_TARGET
    )
    with pytest.raises(IntelligenceResponseValidationError):
        _parse(_card(**{field: value}))


@pytest.mark.parametrize(("recommendation", "violation"), [
    ("建议等服务端停止后将页面状态显示为“已确认”，每日最多重试500次。", "number"),
    ("建议等服务端停止后将页面状态显示为“已确认”，由李工负责取消。", "owner"),
    ("建议等服务端停止后将页面状态显示为“已确认”，周五上线。", "deadline"),
    ("建议在周五验收后把页面状态显示为“已确认”。", "deadline"),
])
def test_future_display_target_does_not_waive_numbers_owners_or_deadlines(recommendation, violation):
    with pytest.raises(IntelligenceResponseValidationError, match=violation):
        _parse(_card(recommendation=recommendation))


@pytest.mark.parametrize("changes", [
    {"evidence_segment_ids": ["unregistered"]},
    {"evidence_quote": "服务端已经完成验收。"},
])
def test_future_display_target_still_requires_the_current_registered_source(changes):
    with pytest.raises(IntelligenceResponseValidationError):
        _parse(_card(**changes))


def test_another_completed_subject_cannot_be_borrowed_around_a_future_display_target():
    text = "旧系统已经完成验收。" + PUBLIC_SOURCE
    request = _request(text)
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        _parse(_card(
            recommendation="新系统已经完成验收，建议等服务端停止后将页面状态显示为“已确认”。",
            evidence_quote=text,
        ), request)


def test_future_display_target_does_not_change_legacy_grounding():
    request = replace(_request(), coach_profile="legacy", request_metadata={})
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        _parse(_card(), request)
