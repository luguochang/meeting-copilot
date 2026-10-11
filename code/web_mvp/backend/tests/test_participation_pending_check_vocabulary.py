from __future__ import annotations

import json

import pytest

from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    RealtimeIntelligenceRequest,
    _mark_participation_unknown_assumptions,
    parse_realtime_coach_response,
)


PUBLIC_SOURCE = "日志检查已经完成。当前在讨论订单状态与验收条件。"


def _request() -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="public-pending-check-vocabulary", state_revision=1,
        new_paragraphs=[{"id": "current", "text": PUBLIC_SOURCE, "revision": 1}],
        context_paragraphs=[], rolling_state={}, coach_profile="participation",
        trigger_type="user_request", user_request="帮我提出一个值得讨论的问题",
        request_metadata={"profile": "participation", "intent": "ask_question",
                          "topic_id": "topic-orders", "analysis_basis_revision": 1},
    )


def _card(assumption: str, **changes):
    return {
        "event_type": "question_to_user", "title": "核对取消边界", "urgency": "low",
        "recommendation": "建议先核对取消生效与页面展示的边界，再决定如何提示用户。",
        "reason": "当前正在讨论订单状态与验收条件。",
        "evidence_segment_ids": ["current"], "evidence_quote": PUBLIC_SOURCE,
        "confidence": 0.9,
        "supporting_facts": [{"source_ref": "current", "text": PUBLIC_SOURCE}],
        "assumptions": [assumption],
        **changes,
    }


def _parse(card, request):
    return parse_realtime_coach_response(
        json.dumps({"intervention": card}, ensure_ascii=False), request=request,
    )


@pytest.mark.parametrize("cue", [
    "需确认", "尚需确认", "待核实", "有待核实", "待验证", "需要核实", "需验证", "需要验证",
])
@pytest.mark.parametrize("predicate", ["已经完成", "尚未完成"])
def test_public_pending_synonyms_pass_raw_parser_and_host_flow_without_rewriting(cue, predicate):
    assumption = f"{cue}订单{predicate}。"
    request = _request()
    card = _card(assumption)
    raw = _parse(card, request)
    marked, changed_indices = _mark_participation_unknown_assumptions(card, request=request)
    normalized = _parse(marked, request)
    assert changed_indices == []
    assert marked == card
    assert raw.assumptions == normalized.assumptions == (assumption,)
    assert raw.recommendation == normalized.recommendation == card["recommendation"]
    assert raw.title == normalized.title == card["title"]
    assert raw.confidence == normalized.confidence == card["confidence"]


@pytest.mark.parametrize("assumption", [
    "需要核对订单已经完成。", "需核对订单已经完成。", "需要确认订单已经完成。",
    "待确认订单已经完成。", "订单是否已经完成？", "能否确认订单已经完成？",
    "先确认订单已经完成。", "先核实订单已经完成。", "请确认订单已经完成。",
    "请核实订单已经完成。",
])
def test_original_pending_cues_remain_accepted(assumption):
    request = _request()
    card = _card(assumption)
    assert _parse(card, request).assumptions == (assumption,)
    marked, changed_indices = _mark_participation_unknown_assumptions(card, request=request)
    assert changed_indices == []
    assert _parse(marked, request).assumptions == (assumption,)


@pytest.mark.parametrize("assumption", [
    "待核实订单已经完成，系统已经上线。",
    "系统已经上线，待核实订单已经完成。",
    "待核实订单已经完成。系统已经上线。",
    "待核实订单已经完成且系统已经上线。",
    "待核实订单已经完成但是系统已经上线。",
    "系统已经上线且待核实订单已经完成。",
    "需确认订单已经完成，同时系统已经上线。",
    "需要验证订单已经完成而且系统已经上线。",
    "订单是否已经完成，系统已经上线。",
    "需核对订单已经完成且系统已经上线。",
    "待核实并且订单已经完成。",
    "订单已经完成，待核实页面展示状态。",
])
def test_pending_cue_does_not_hide_a_separate_or_continued_fact(assumption):
    request = _request()
    card = _card(assumption)
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        _parse(card, request)
    marked, changed_indices = _mark_participation_unknown_assumptions(card, request=request)
    assert changed_indices == []
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        _parse(marked, request)


@pytest.mark.parametrize(("assumption", "violation"), [
    ("待核实订单已经完成500次。", "number"),
    ("尚需确认订单已经完成，由李工负责取消。", "owner"),
    ("需要验证订单已经完成，周五上线。", "deadline"),
])
def test_new_pending_cue_does_not_waive_quantity_owner_or_deadline(assumption, violation):
    request = _request()
    card = _card(assumption)
    for candidate in (card, _mark_participation_unknown_assumptions(card, request=request)[0]):
        with pytest.raises(IntelligenceResponseValidationError) as error:
            _parse(candidate, request)
        assert violation in error.value.claim_fields["assumptions"]


@pytest.mark.parametrize("assumption", [
    "订单已经完成。",
    "订单“已经完成”。",
    "建议订单已经完成。",
    "订单已经完成有待核实。",
    "待核实" + "业务背景" * 7 + "订单已经完成。",
    "待核实订单已经完成以及验收已经通过。",
    "需核对订单是否已完成以及验收是否已通过。",
    "订单是否已经完成的判定条件未定。",
])
def test_no_cue_distant_cue_and_deferred_multi_predicate_grammar_remain_strict(assumption):
    request = _request()
    card = _card(assumption)
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        _parse(card, request)
    marked, _ = _mark_participation_unknown_assumptions(card, request=request)
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        _parse(marked, request)


@pytest.mark.parametrize("field", ["title", "reason", "recommendation"])
def test_pending_assumption_cannot_rescue_an_unsupported_fact_in_another_field(field):
    request = _request()
    card = _card("待核实订单已经完成。", **{field: "订单已经完成全部验收。"})
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject") as error:
        _parse(card, request)
    assert "state_subject" in error.value.claim_fields[field]


@pytest.mark.parametrize("changes", [
    {"evidence_segment_ids": ["unregistered"]},
    {"evidence_quote": "订单已经完成。"},
    {"supporting_facts": [{"source_ref": "unregistered", "text": PUBLIC_SOURCE}]},
])
def test_pending_assumption_still_requires_its_current_registered_source(changes):
    request = _request()
    with pytest.raises(IntelligenceResponseValidationError):
        _parse(_card("待核实订单已经完成。", **changes), request)


def test_host_normalizer_still_only_marks_a_single_bare_negative_unknown():
    request = _request()
    card = _card("订单尚未完成。")
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        _parse(card, request)
    marked, changed_indices = _mark_participation_unknown_assumptions(card, request=request)
    assert changed_indices == [0]
    assert marked["assumptions"] == ["需要核对这项假设：订单尚未完成。"]
    assert _parse(marked, request).assumptions == tuple(marked["assumptions"])
