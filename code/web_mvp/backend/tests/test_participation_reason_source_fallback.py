from __future__ import annotations

import json

import pytest

from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    RealtimeIntelligenceRequest,
    _ground_pi_intervention_reason,
    _validate_coach_claim_grounding,
    parse_realtime_coach_response,
)


def _request(quote: str, *, source_type: str = "transcript") -> RealtimeIntelligenceRequest:
    transcript = quote if source_type == "transcript" else "当前讨论取消功能。"
    sources = () if source_type == "transcript" else (
        {"source_type": "meeting_attachment", "source_id": "public-doc", "quote": quote},
    )
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="public-reason-fallback", state_revision=1,
        new_paragraphs=[{"id": "s1", "text": transcript, "revision": 1}],
        context_paragraphs=[], rolling_state={}, document_evidence=sources,
        coach_profile="participation", trigger_type="user_request",
        user_request="帮我提出一个值得讨论的问题",
        request_metadata={"profile": "participation", "intent": "ask_question",
                          "topic_id": "topic-cancel", "analysis_basis_revision": 1},
    )


def _card(request: RealtimeIntelligenceRequest, **changes):
    transcript = request.new_paragraphs[0].text
    facts = [{"source_ref": "s1", "text": transcript}]
    facts.extend({"source_ref": source["source_id"], "text": source["quote"]}
                 for source in request.document_evidence if len(source["quote"]) <= 300)
    return {
        "event_type": "question_to_user", "title": "核对取消边界", "urgency": "low",
        "recommendation": "建议先核对取消实际生效边界，再展示任务结果。",
        "reason": "当前服务器已经完成验收。",
        "evidence_segment_ids": ["s1"], "evidence_quote": transcript, "confidence": 0.9,
        "supporting_facts": facts, "assumptions": [], **changes,
    }


def _parse(card, request):
    return parse_realtime_coach_response(
        json.dumps({"intervention": card}, ensure_ascii=False), request=request,
    )


@pytest.mark.parametrize("source_type", ["transcript", "document"])
@pytest.mark.parametrize("quote", ["回归检查已经完成。", "回归检查尚未完成。"])
def test_actual_helper_quotes_completed_and_unfinished_states_without_changing_the_subject(source_type, quote):
    request = _request(quote, source_type=source_type)
    card = _card(request)
    grounded, diagnostic = _ground_pi_intervention_reason(card, request=request)
    parsed = _parse(grounded, request)
    assert parsed.reason == "原文依据：\n" + quote
    assert parsed.recommendation == card["recommendation"]
    assert parsed.title == card["title"]
    assert parsed.confidence == card["confidence"]
    assert diagnostic == {"mode": "source_quote_fallback", "removed_lines": 1,
                          "retained_lines": 0, "source_type": source_type}


@pytest.mark.parametrize("source_type", ["transcript", "document"])
def test_fallback_truncates_at_a_complete_source_sentence_instead_of_a_state_predicate(source_type):
    prefix = "公开说明。" * 57
    quote = prefix + "回归检查已经完成测试。"
    request = _request(quote, source_type=source_type)
    grounded, diagnostic = _ground_pi_intervention_reason(_card(request), request=request)
    assert grounded["reason"] == "原文依据：\n" + prefix
    assert len(grounded["reason"]) <= 300
    _parse(grounded, request)
    assert diagnostic["source_type"] == source_type


@pytest.mark.parametrize("alias", ["why_now", "whyNow"])
def test_bounded_source_reason_updates_existing_aliases_and_preserves_their_contract(alias):
    quote = "公开说明。" * 57 + "回归已经完成说明。"
    request = _request(quote)
    card = _card(request)
    card[alias] = card["reason"]
    grounded, _ = _ground_pi_intervention_reason(card, request=request)
    assert grounded["reason"] == "原文依据：\n" + quote
    assert len(grounded["reason"]) == 300
    assert grounded[alias] == grounded["reason"]
    assert _parse(grounded, request).reason == grounded["reason"]


@pytest.mark.parametrize("field", ["recommendation", "title"])
def test_source_explanation_cannot_rescue_an_unsupported_state_subject(field):
    request = _request("回归检查已经完成。")
    card = _card(request, **{field: "订单检查已经完成。建议确认取消边界。"})
    unchanged, diagnostic = _ground_pi_intervention_reason(card, request=request)
    assert unchanged is card and diagnostic == {}
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        _parse(unchanged, request)


@pytest.mark.parametrize("changes", [
    {"evidence_quote": "订单检查已经完成。"},
    {"evidence_segment_ids": ["unregistered"]},
])
def test_fallback_requires_the_own_registered_transcript_evidence(changes):
    request = _request("回归检查已经完成。")
    card = _card(request, **changes)
    unchanged, diagnostic = _ground_pi_intervention_reason(card, request=request)
    assert unchanged is card and diagnostic == {}
    with pytest.raises(IntelligenceResponseValidationError):
        _parse(unchanged, request)


@pytest.mark.parametrize("reason", ["订单检查已经完成。", "原文依据：回归检查已经完成。"])
def test_validator_does_not_strip_labels_or_accept_a_different_state_subject(reason):
    request = _request("回归检查已经完成。")
    card = _card(request)
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        _validate_coach_claim_grounding(
            title=card["title"], recommendation=card["recommendation"], reason=reason,
            evidence_quote=card["evidence_quote"], evidence_ids=("s1",), request=request,
        )


def test_conflicting_reason_alias_is_left_for_the_strict_parser():
    request = _request("回归检查已经完成。")
    card = _card(request, why_now="另一份说明。")
    unchanged, diagnostic = _ground_pi_intervention_reason(card, request=request)
    assert unchanged is card and diagnostic == {}
    with pytest.raises(IntelligenceResponseValidationError):
        _parse(unchanged, request)


def test_a_long_source_without_a_complete_bounded_clause_keeps_the_failure():
    request = _request("公开说明" * 74 + "回归检查已经完成。")
    with pytest.raises(IntelligenceResponseValidationError, match="no grounded line or valid source quote"):
        _ground_pi_intervention_reason(_card(request), request=request)


@pytest.mark.parametrize("source_type", ["transcript", "document"])
def test_an_already_grounded_state_explanation_remains_unchanged(source_type):
    quote = "回归检查已经完成。"
    request = _request(quote, source_type=source_type)
    card = _card(request, reason=quote)
    unchanged, diagnostic = _ground_pi_intervention_reason(card, request=request)
    assert unchanged is card and diagnostic == {}
    assert _parse(unchanged, request).reason == quote
