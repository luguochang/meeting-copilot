from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from meeting_copilot_web_mvp.pi_coach_pool import PiCoachRuntimePool
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachRuntimeError, build_pi_coach_request
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar
from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    RealtimeIntelligenceRequest,
    build_realtime_coach_messages,
    parse_realtime_coach_response,
    should_run_realtime_coach,
    run_realtime_coach_via_pi,
    run_realtime_intelligence,
    _mark_participation_unknown_assumptions,
    _owner_assignment_values,
    _silent_pi_failure,
)


def request():
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="isolated-participation", state_revision=4,
        new_paragraphs=[{"id": "current", "text": "需求是支持订单重试。现在计划失败后直接重发请求。", "revision": 1}],
        context_paragraphs=[{"id": "prior", "text": "订单数据必须保持一致。", "revision": 1}],
        rolling_state={}, trigger_type="user_request", user_request="帮我提出一个值得讨论的问题",
        coach_profile="participation", request_metadata={"profile": "participation", "intent": "ask_question",
            "topic_id": "topic-orders", "analysis_basis_revision": 2},
    )


def card(**changes):
    return {"event_type": "question_to_user", "title": "核对重复请求处理", "urgency": "low",
        "recommendation": "是否可以先明确重复请求的识别和结果复用方式，避免重试产生重复订单？",
        "reason": "当前在讨论失败后的重试，先明确异常路径有助于评估方案。",
        "evidence_segment_ids": ["current"], "evidence_quote": "现在计划失败后直接重发请求。", "confidence": .9,
        "supporting_facts": [{"text": "失败后直接重发请求", "source_ref": "current"}],
        "assumptions": ["需要核对重发时是否有重复提交的可能。"], **changes}


def parse(value, context=None):
    return parse_realtime_coach_response(json.dumps({"intervention": value}, ensure_ascii=False), request=context or request())


def test_professional_contribution_does_not_require_a_fast_answer():
    context = request()
    result = parse(card(), context)
    assert result.to_dict()["analysis_kind"] == "participation"
    assert result.to_dict()["proposal_type"] == "professional_proposal"
    assert result.topic_id == "topic-orders"
    assert result.analysis_basis_revision == 2
    assert result.supporting_facts[0]["source_ref"] == "current"
    assert "current_answer" not in context.rolling_state
    assert should_run_realtime_coach(replace(context, trigger_type="discussion_update"), requested_runtime="pi")
    messages = build_realtime_coach_messages(context)
    assert "私人评审伙伴" in messages[0]["content"]
    assert json.loads(messages[1]["content"])["intent"] == "ask_question"
    packet = build_pi_coach_request(context, request_id="isolated", base_url="https://example.invalid",
        api_key="fixture-only", model="fixture-model", api_style="chat_completions", timeout_seconds=10)
    assert packet["context"]["coach_profile"] == "participation"
    assert packet["context"]["request_metadata"]["topic_id"] == "topic-orders"
    assert packet["context"]["role_contract_version"] == "talktrace.participation.v1"
    assert "私人评审伙伴" in packet["context"]["role_contract"]
    assert "外放、录播" in packet["context"]["role_contract"]


@pytest.mark.parametrize("changes", [
    {"topic_id": "old-topic"}, {"analysis_basis_revision": 1},
    {"analysis_kind": "legacy"}, {"proposal_type": "grounded_fact"},
    {"supporting_facts": [{"text": "重复提交", "source_ref": "current"}]},
    {"supporting_facts": [{"text": "订单数据必须保持一致", "source_ref": "prior"}]},
    {"assumptions": ["假设支持500并发。"]},
    {"recommendation": "我们的系统已经完成幂等处理，重试不会重复下单。"},
    {"recommendation": "建议由张三负责重试方案，周五上线。"},
])
def test_contribution_rejects_obsolete_sources_and_invented_hard_facts(changes):
    with pytest.raises(IntelligenceResponseValidationError):
        parse(card(**changes))


def test_professional_permission_does_not_relax_legacy_grounding():
    with pytest.raises(IntelligenceResponseValidationError):
        parse(card(recommendation="是否可以考虑熔断和监控的设计，明确重试策略的适用范围？"),
            replace(request(), coach_profile="legacy", request_metadata={}))


def test_cited_material_constraint_must_be_visible_in_supporting_facts():
    source = {"source_type": "meeting_attachment", "source_id": "reference",
              "quote": "超时不能判断为失败，必须先查询处理结果。"}
    context = replace(request(), document_evidence=(source,))
    with pytest.raises(IntelligenceResponseValidationError, match="verbatim supporting fact"):
        parse(card(), context)
    facts = card()["supporting_facts"] + [{"source_ref": "reference", "text": "超时不能判断为失败"}]
    assert parse(card(supporting_facts=facts), context).supporting_facts[1]["source_ref"] == "reference"
    with pytest.raises(IntelligenceResponseValidationError, match="quote a registered source"):
        parse(card(supporting_facts=[{"source_ref": "reference", "text": "超时必定失败"}]), context)


@pytest.mark.parametrize("recommendation", [
    "建议让同一订单只发放一次权益，重复回调复用先前结果；需要核对当前状态记录能否支持。",
    "是否可以让相同请求最多生效一次？重复请求读取原结果，避免重复生效。",
    "建议并发时同一订单只生效一次，需要核对状态推进能否原子完成。",
    "建议在并发回调时以同一订单为键检查处理状态，避免重复生效。",
    "建议检查并发回调的处理方式，同一订单的重复请求应复用处理结果。",
])
def test_proposed_single_effect_is_qualitative_idempotence(recommendation):
    assert parse(card(recommendation=recommendation)).recommendation == recommendation


@pytest.mark.parametrize("changes", [
    {"recommendation": "建议同一订单只重试一次，然后停止处理。"},
    {"recommendation": "建议同一用户每天只退款一次。"},
    {"recommendation": "同一请求现在只生效一次，可以考虑后续优化。"},
    {"recommendation": "同一请求现在只生效一次可以考虑后续优化。"},
    {"recommendation": "建议同一订单只发放一次权益，每日最多处理500次。"},
    {"assumptions": ["同一订单只发放一次权益。"]},
])
def test_idempotence_exception_does_not_authorize_quotas_or_existing_facts(changes):
    with pytest.raises(IntelligenceResponseValidationError):
        parse(card(**changes))


@pytest.mark.parametrize("recommendation", [
    "建议先按并发一千设计处理能力。",
    "建议把并发上限设为五百。",
    "建议并发用户数为三百，再验证处理能力。",
    "建议把预算不超过一千作为实现约束。",
    "建议错误率为百分之一时暂停处理。",
    "建议并发时同一订单只生效一次，每天最多执行100次。",
])
def test_contextual_number_relationships_keep_unsupported_limits_strict(recommendation):
    with pytest.raises(IntelligenceResponseValidationError, match="number"):
        parse(card(recommendation=recommendation))


def test_small_material_prefetch_budget_requires_host_participation_profile():
    sources = [{"source_type": "meeting_attachment", "source_id": f"source-{i}", "quote": "字" * 700}
               for i in range(7)]
    base = dict(meeting_id="isolated", state_revision=1, new_paragraphs=[], context_paragraphs=[],
                rolling_state={}, trigger_type="user_request", user_request="帮我比较方案", document_evidence=sources)
    metadata = {"profile": "participation", "intent": "compare_options", "topic_id": "topic", "analysis_basis_revision": 1}
    assert len(RealtimeIntelligenceRequest.from_payload(**base, coach_profile="participation", request_metadata=metadata).document_evidence) == 7
    with pytest.raises(ValueError, match="bounded typed"):
        RealtimeIntelligenceRequest.from_payload(**base)
    with pytest.raises(ValueError, match="bounded typed"):
        RealtimeIntelligenceRequest.from_payload(**{**base, "document_evidence": sources + sources},
                                               coach_profile="participation", request_metadata=metadata)
    with pytest.raises(ValueError, match="bounded typed"):
        RealtimeIntelligenceRequest.from_payload(**{**base, "document_evidence": [dict(sources[0], quote="字" * 8001)]},
                                               coach_profile="participation", request_metadata=metadata)


@pytest.mark.parametrize("assumption", [
    "现有服务可以持久化订单与权益发放状态；若不能，需要先确认可用的持久化机制。",
    "如果尚未完成幂等处理，需要先核实重复请求的识别方式。",
    "需要确认是否已经完成重试测试。",
    "我们的系统有没有完成幂等处理？",
    "系统是不是已经完成幂等处理？",
])
def test_explicit_conditional_state_checks_are_not_confirmed_meeting_facts(assumption):
    assert parse(card(assumptions=[assumption])).assumptions == (assumption,)


@pytest.mark.parametrize("assertion", [
    "我们的系统已经完成幂等处理；是否还需要监控？",
    "如果要增加监控，我们的系统已经完成幂等处理。",
    "目前重试测试尚未完成，建议安排核对。",
    "系统是不是需要监控？系统已经完成幂等处理。",
    "有没有其他问题？我们的系统没有完成幂等处理。",
    "建议先暂停系统拥有没有完成测试的模块。",
    "建议回滚我们持有没有通过验收的版本。",
])
def test_question_or_condition_elsewhere_cannot_hide_a_state_assertion(assertion):
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        parse(card(assumptions=[assertion]))


def test_unknown_negative_assumption_becomes_explicit_check_without_repairing_core():
    raw = card(assumptions=["幂等测试尚未完成。"])
    updated, indices = _mark_participation_unknown_assumptions(raw, request=request())
    assert indices == [0]
    assert raw["assumptions"] == ["幂等测试尚未完成。"]
    assert parse(updated).assumptions == ("需要核对这项假设：幂等测试尚未完成。",)
    assert updated["recommendation"] == raw["recommendation"]


@pytest.mark.parametrize("assumption", [
    "幂等测试已经完成。", "幂等测试尚未完成；系统已经上线。",
    "由张三负责完成测试。", "尚未完成500并发测试。",
])
def test_pending_assumption_normalization_cannot_rescue_hard_facts(assumption):
    updated, _ = _mark_participation_unknown_assumptions(card(assumptions=[assumption]), request=request())
    with pytest.raises(IntelligenceResponseValidationError):
        parse(updated)


def test_completed_state_cannot_borrow_another_subject_from_source():
    text = "报告已经完成。现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        parse(card(evidence_quote=text, recommendation="订单幂等测试已经完成，建议增加监控。"), context)
    assert parse(card(evidence_quote=text, recommendation="报告已经完成，建议核对订单重试边界。"), context)


@pytest.mark.parametrize("recommendation", [
    "建议如果测试已经通过，可以再讨论发布范围。",
    "建议若测试尚未完成，可以先核对异常路径。",
    "可以在测试已经通过之后再发布。",
    "测试已经完成了吗？可以先核对结果。",
    "测试已经完成幂等校验了吗？可以先核对结果。",
    "测试尚未完成吗？可以先核对结果。",
    "建议在校验完成后把提示切换为已确认。",
    "可以在测试通过之后将提示标记为已完成。",
])
def test_local_predicate_conditions_questions_and_future_targets_are_proposals(recommendation):
    assert parse(card(recommendation=recommendation)).recommendation == recommendation


@pytest.mark.parametrize("state", [
    "取消已完成", "处理尚未完成", "下载权限校验已经通过",
])
@pytest.mark.parametrize("cue", ["需核对", "需要核对"])
def test_explicit_pending_check_variants_do_not_assert_an_assumption(state, cue):
    assumption = cue + state + "。"
    assert parse(card(assumptions=[assumption])).assumptions == (assumption,)


def test_short_pending_check_is_not_another_subject_when_source_has_the_same_state():
    text = "处理已完成。现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    assumption = "需核对处理已完成。"
    assert parse(card(evidence_quote=text, assumptions=[assumption]), context).assumptions == (assumption,)


@pytest.mark.parametrize("field", ["recommendation", "reason"])
def test_short_pending_cue_remains_local_in_other_participation_fields(field):
    value = "建议需核对取消已完成，再评估后续结果。" if field == "recommendation" else "需核对取消已完成。"
    assert getattr(parse(card(**{field: value})), field) == value


@pytest.mark.parametrize("assumption", [
    "需核对取消已完成，处理已经完成。",
    "需核对取消已完成且处理已经完成。",
    "需核对取消是否已完成。处理尚未完成。",
    "处理已经完成，需核对取消是否已完成。",
])
def test_short_pending_cue_does_not_hide_another_actual_state(assumption):
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        parse(card(assumptions=[assumption]))


@pytest.mark.parametrize(("assumption", "category"), [
    ("需核对取消已完成，重试500次。", "number"),
    ("需核对取消已完成，由张三负责取消。", "owner"),
    ("需核对取消已完成，周五上线。", "deadline"),
])
def test_short_pending_cue_keeps_quantities_owners_and_dates_strict(assumption, category):
    with pytest.raises(IntelligenceResponseValidationError, match=category):
        parse(card(assumptions=[assumption]))


def test_short_pending_cue_does_not_relax_an_independent_state_subject():
    text = "旧订单测试已经完成。现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        parse(card(evidence_quote=text, assumptions=["需核对取消是否已完成。当前订单测试已经完成。"]), context)


def test_short_pending_cue_does_not_change_legacy_state_grounding():
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        parse(card(recommendation="建议需核对取消已完成，再评估后续结果。"),
              replace(request(), coach_profile="legacy", request_metadata={}))


@pytest.mark.parametrize("connector", ["并且", "而且", "同时", "然后", "另外", "此外"])
@pytest.mark.parametrize("assignment", ["由张三负责取消", "让张三负责取消"])
def test_explicit_owner_assignment_after_full_connector_is_not_an_unknown_assumption(connector, assignment):
    with pytest.raises(IntelligenceResponseValidationError, match="owner"):
        parse(card(assumptions=["需核对取消已完成" + connector + assignment + "。"]))


@pytest.mark.parametrize("relation", ["作为", "是", "担任"])
def test_full_connector_person_owner_relationship_is_an_assignment(relation):
    with pytest.raises(IntelligenceResponseValidationError, match="owner"):
        parse(card(assumptions=["需核对取消已完成并且张三" + relation + "负责人。"]))


@pytest.mark.parametrize("field", ["title", "recommendation", "reason", "assumptions"])
def test_connector_assignment_is_checked_in_each_formal_card_field(field):
    value = "建议先核对取消条件并且由张三负责取消。"
    changes = {field: [value] if field == "assumptions" else value}
    with pytest.raises(IntelligenceResponseValidationError, match="owner") as failure:
        parse(card(**changes))
    assert "owner" in failure.value.claim_fields[field]


@pytest.mark.parametrize("assignment", ["由张三负责取消", "张三作为负责人"])
def test_explicit_same_person_assignment_in_source_grounds_connector_assignment(assignment):
    text = "先评估异常路径并且" + assignment + "。现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    assumption = "需核对取消已完成并且" + assignment + "。"
    assert parse(card(evidence_quote=text, assumptions=[assumption]), context).assumptions == (assumption,)


@pytest.mark.parametrize("assignment", ["由张三负责取消", "让李四负责保留结果"])
def test_two_explicit_assignments_keep_each_person_separate_across_full_connector(assignment):
    text = "先评估异常并且由张三负责取消，然后让李四负责保留结果。现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    assert _owner_assignment_values(text) == {"张三", "李四"}
    assumption = "需核对取消已完成并且" + assignment + "。"
    assert parse(card(evidence_quote=text, assumptions=[assumption]), context).assumptions == (assumption,)


@pytest.mark.parametrize("mention", ["张三参加会议。", "张三参加会议，而且由李四负责取消。"])
def test_person_mention_or_other_person_assignment_does_not_ground_connector_assignment(mention):
    text = mention + "现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    with pytest.raises(IntelligenceResponseValidationError, match="owner"):
        parse(card(evidence_quote=text, assumptions=["需核对取消已完成并且由张三负责取消。"]), context)


@pytest.mark.parametrize("assumption", [
    "需核对取消已完成并且是否由张三负责取消？",
    "需核对取消已完成而且是否让张三负责取消？",
    "需核对取消已完成同时由谁负责取消？",
    "需核对取消已完成然后让谁负责取消？",
])
def test_connector_assignment_questions_remain_clarification(assumption):
    assert parse(card(assumptions=[assumption])).assumptions == (assumption,)


@pytest.mark.parametrize("bare_connector", ["并", "且"])
def test_bare_connector_characters_are_not_added_as_owner_boundaries(bare_connector):
    assert _owner_assignment_values("核对异常路径" + bare_connector + "由张三负责取消") == set()


@pytest.mark.parametrize("grounded", [False, True])
def test_full_connector_legacy_owner_assignment_stays_source_bound(grounded):
    context = request()
    context = replace(context, coach_profile="legacy", request_metadata={},
                      new_paragraphs=(replace(context.new_paragraphs[0], correction_status="no_change"),))
    value = card(recommendation="请先确认下一步并且由张三负责取消。")
    if grounded:
        text = "先确认下一步并且由张三负责取消。现在计划失败后直接重发请求。"
        context = replace(context, new_paragraphs=(replace(context.new_paragraphs[0], text=text),))
        value["evidence_quote"] = text
        assert parse(value, context).recommendation == value["recommendation"]
    else:
        with pytest.raises(IntelligenceResponseValidationError, match="owner"):
            parse(value, context)


@pytest.mark.parametrize("recommendation", [
    "建议增加监控，我们的系统已经完成测试。",
    "测试已经完成。是否还需要监控？",
    "需要确认报告是否已经完成且系统已经通过验收。",
    "建议如果要加监控就记录日志且系统已经完成测试。",
    "建议在校验完成后把提示切换为已确认且系统已经完成测试。",
    "建议把提示切换为已确认。",
    "建议核对异常路径，系统已经完成测试了吗并且上线了。",
])
def test_other_cues_do_not_exempt_independent_or_ambiguous_state_assertions(recommendation):
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        parse(card(recommendation=recommendation))


@pytest.mark.parametrize("state", ["已经完成", "尚未完成", "没有完成", "并非已经完成"])
def test_both_state_polarities_bind_their_own_subject_and_leave_inference_outside_the_fact(state):
    text = f"日志校验{state}。现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    recommendation = f"日志校验{state}所以可以继续评估重试边界。"
    result = parse(card(evidence_quote=text, recommendation=recommendation), context)
    assert result.recommendation == recommendation
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        parse(card(evidence_quote=text, recommendation=f"订单测试{state}，建议先核对重试边界。"), context)


def test_state_object_and_separate_inference_assertion_are_not_silently_dropped():
    text = "订单已经完成日志校验。现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        parse(card(evidence_quote=text, recommendation="订单已经完成幂等验收所以可以发布。"), context)
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        parse(card(evidence_quote=text, recommendation="订单已经完成日志校验所以可以发布且订单测试已经通过。"), context)


def test_conditional_source_is_not_evidence_of_an_existing_state():
    text = "如果日志校验已经完成，可以考虑再讨论重试边界。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        parse(card(evidence_quote=text, recommendation="日志校验已经完成，建议再讨论重试边界。"), context)


@pytest.mark.parametrize(("source_assertion", "claim"), [
    ("旧订单测试已经完成", "订单测试已经完成"),
    ("旧订单测试已经完成", "当前订单测试已经完成"),
    ("当前订单测试已经完成", "订单测试已经完成"),
    ("当前订单测试尚未完成", "订单测试尚未完成"),
    ("旧系统就绪检查已经完成", "新系统就绪检查已经完成"),
    ("旧系统就绪检查尚未完成", "新系统就绪检查尚未完成"),
    ("报告已经完成", "建议报告已经完成"),
    ("旧订单测试尚未完成", "订单测试尚未完成"),
    ("报告未完成测试", "报告未完成"),
    ("报告已经完成测试", "报告已经完成"),
])
def test_state_assertions_preserve_the_complete_subject_qualifier_and_object(source_assertion, claim):
    text = source_assertion + "。现在计划失败后直接重发请求。"
    context = replace(request(), new_paragraphs=(replace(request().new_paragraphs[0], text=text),))
    with pytest.raises(IntelligenceResponseValidationError, match="state_subject"):
        parse(card(evidence_quote=text, recommendation=claim + "，建议核对异常路径。"), context)
    exact = source_assertion + "所以可以继续评估异常路径。"
    assert parse(card(evidence_quote=text, recommendation=exact), context).recommendation == exact


@pytest.mark.parametrize(("recommendation", "category"), [
    ("建议如果测试已经通过，由张三负责发布。", "owner"),
    ("测试已经完成了吗？建议每日最多重试500次。", "number"),
    ("建议在校验完成后把提示切换为已确认，周五上线。", "deadline"),
])
def test_predicate_exceptions_keep_named_owners_limits_and_dates_strict(recommendation, category):
    with pytest.raises(IntelligenceResponseValidationError, match=category):
        parse(card(recommendation=recommendation))


def test_predicate_exceptions_do_not_change_legacy_or_promote_a_future_target_title_to_fact():
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        parse(card(recommendation="建议如果测试已经完成，可以核对重试边界。"),
              replace(request(), coach_profile="legacy", request_metadata={}))
    with pytest.raises(IntelligenceResponseValidationError, match="state"):
        parse(card(title="建议在校验完成后把提示切换为已确认"))


def test_proposed_named_owner_is_still_an_unverified_assignment():
    with pytest.raises(IntelligenceResponseValidationError, match="owner"):
        parse(card(recommendation="建议由张三负责重试方案。"))


def test_validation_diagnostics_contain_field_categories_without_response_text():
    with pytest.raises(IntelligenceResponseValidationError) as raised:
        parse(card(recommendation="建议由张三负责重试方案。"))
    result = _silent_pi_failure(request=request(), provider_config={"model": "fixture"}, error=raised.value)
    assert result["agent_metrics"]["response_validation_claim_fields"] == {"recommendation": ["owner"]}
    assert "张三" not in json.dumps(result, ensure_ascii=False)


class FakeSidecar:
    def __init__(self, **options):
        self.options = options
        self.available = True
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.closed = False

    async def evaluate(self, payload):
        self.started.set()
        try:
            if payload.get("blocked"):
                await self.release.wait()
            return {"request_id": payload["request_id"], "metrics": {}}
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def close(self):
        self.closed = True


def packet(identifier, *, manual=False, realtime=False, blocked=False):
    return {"request_id": identifier, "blocked": blocked, "context": {
        "priority_mode": "realtime" if realtime else "deep",
        "trigger_type": "user_request" if manual else "discussion_update",
    }}


def test_analysis_does_not_block_realtime_and_user_request_preempts_background():
    async def run():
        pool = PiCoachRuntimePool(sidecar_factory=FakeSidecar)
        assert pool._analysis.options["timeout_seconds"] == 30
        assert "timeout_seconds" not in pool._delegate.options
        auto = asyncio.create_task(pool.evaluate(packet("auto", blocked=True)))
        await pool._analysis.started.wait()
        fast = await asyncio.wait_for(pool.evaluate(packet("fast", realtime=True)), .5)
        assert fast["metrics"]["sidecar_lane"] == "realtime"
        manual = await asyncio.wait_for(pool.evaluate(packet("manual", manual=True)), .5)
        assert manual["request_id"] == "manual"
        assert manual["metrics"]["sidecar_lane"] == "analysis"
        assert pool._analysis.cancelled
        with pytest.raises(PiCoachRuntimeError) as error:
            await auto
        assert error.value.code == "pi_preempted"
        pool.close()
        assert pool._delegate.closed and pool._analysis.closed
    asyncio.run(run())


def test_second_user_request_does_not_cancel_another_user_request():
    async def run():
        pool = PiCoachRuntimePool(sidecar_factory=FakeSidecar)
        first = asyncio.create_task(pool.evaluate(packet("first", manual=True, blocked=True)))
        await pool._analysis.started.wait()
        with pytest.raises(PiCoachRuntimeError) as error:
            await pool.evaluate(packet("second", manual=True))
        assert error.value.code == "pi_busy"
        assert not pool._analysis.cancelled
        pool._analysis.release.set()
        assert (await first)["request_id"] == "first"
        assert pool._manual_waiters == 0
        pool.close()
    asyncio.run(run())


def test_cancelled_analysis_caller_releases_the_lane_for_next_request():
    async def run():
        pool = PiCoachRuntimePool(sidecar_factory=FakeSidecar)
        first = asyncio.create_task(pool.evaluate(packet("first", manual=True, blocked=True)))
        await pool._analysis.started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await pool.evaluate(packet("next", manual=True)))["request_id"] == "next"
        assert not pool._analysis_lock.locked()
        pool.close()
    asyncio.run(run())


def test_nested_trace_and_pool_preserve_host_validated_material_sources():
    async def run():
        source = {"source_type": "meeting_attachment", "source_id": "material_span_fixture", "set_id": "material_set_fixture",
            "meeting_id": "isolated-participation", "attachment_id": "attachment_fixture", "title": "需求",
            "version_id": "version_fixture", "version": 1, "content_hash": "fixture", "locator": {},
            "context_revision": 1, "quote": "重复请求不能重复扣款。"}
        class HostSidecar(PiCoachSidecar):
            async def evaluate(self, payload):
                return {"action": "intervention", "host_source_evidence": [source], "metrics": {},
                    "intervention": card(supporting_facts=[{"text": "不能重复扣款", "source_ref": source["source_id"]}])}
        class Wrapper:
            def __init__(self, delegate):
                self._delegate = delegate
            async def evaluate(self, payload):
                return await self._delegate.evaluate(payload)
        context = replace(request(), document_evidence=(source,))
        runtime = Wrapper(Wrapper(HostSidecar(command=["fixture-only"])))
        result = await run_realtime_coach_via_pi(request=context, pi_runtime=runtime, priority_mode="deep",
            provider_config={"base_url": "https://example.invalid", "api_key": "fixture-only", "model": "fixture", "timeout_seconds": 25})
        assert result["intervention"].source_evidence == (source,)
    asyncio.run(run())


@pytest.mark.parametrize("borrow_unselected_sentence", [False, True],
                         ids=["adjacent_selected_facts_preserved", "registered_but_unselected_fact_rejected"])
def test_trusted_pool_uses_normalized_adjacent_document_quote_scope(borrow_unselected_sentence):
    async def run():
        sentences = ("付款结果待确认时禁止再次付款。", "确认未扣款后可讨论重新付款。")
        unselected = "短信发送失败不得重新发放权益。"
        selected_quote = " ".join(sentences)
        registered_quote = f"{selected_quote} {unselected}"
        registered = {
            "source_type": "meeting_attachment", "source_id": "material_span_adjacent",
            "set_id": "material_set_adjacent", "meeting_id": "isolated-participation",
            "attachment_id": "attachment_adjacent", "title": "付款需求",
            "version_id": "version_adjacent", "version": 2,
            "content_hash": hashlib.sha256(registered_quote.encode("utf-8")).hexdigest(),
            "locator": {"type": "converted_text", "paragraph": 1, "offset": 0},
            "context_revision": 3, "quote": registered_quote,
        }
        normalized = {**registered, "quote": selected_quote}
        facts = [{"text": sentence, "source_ref": registered["source_id"]} for sentence in sentences]
        if borrow_unselected_sentence:
            facts.append({"text": unselected, "source_ref": registered["source_id"]})

        class HostSidecar(PiCoachSidecar):
            calls = 0

            async def evaluate(self, payload):
                self.calls += 1
                # The input registry really has the extra sentence. The final
                # Node-shaped reference authorizes only the selected slice.
                assert payload["context"]["document_evidence"] == [registered]
                return {"action": "intervention", "host_source_evidence": [normalized], "metrics": {},
                        "intervention": card(supporting_facts=facts)}

        class TraceWrapper:
            def __init__(self, delegate):
                self._delegate = delegate

            async def evaluate(self, payload):
                return await self._delegate.evaluate(payload)

        context = replace(request(), document_evidence=(registered,))
        pool = PiCoachRuntimePool(sidecar_factory=HostSidecar, command=["fixture-only"])
        runtime = TraceWrapper(pool)
        try:
            options = dict(request=context, pi_runtime=runtime, priority_mode="deep",
                           provider_config={"base_url": "https://example.invalid", "api_key": "fixture-only",
                                            "model": "fixture", "timeout_seconds": 25})
            if borrow_unselected_sentence:
                with pytest.raises(IntelligenceResponseValidationError, match="quote a registered source verbatim"):
                    await run_realtime_coach_via_pi(**options)
            else:
                result = await run_realtime_coach_via_pi(**options)
                contribution = result["intervention"]
                assert contribution.supporting_facts == tuple(facts)
                assert contribution.source_evidence == (normalized,)
                assert result["source_evidence"] == [normalized]
                assert contribution.to_dict()["source_evidence"] == [normalized]
                assert result["agent_metrics"]["sidecar_lane"] == "analysis"
                assert contribution.topic_id == context.request_metadata["topic_id"]
                assert contribution.analysis_basis_revision == context.request_metadata["analysis_basis_revision"]
            assert pool._analysis.calls == 1
            assert pool._delegate.calls == 0
            assert context.document_evidence == (registered,)
        finally:
            pool.close()

    asyncio.run(run())


def test_untrusted_cycle_cannot_promote_document_evidence():
    async def run():
        class Cycle:
            def __init__(self):
                self._delegate = self
            async def evaluate(self, payload):
                return {"action": "intervention", "host_source_evidence": [{"source_id": "invented", "quote": "看起来是事实"}],
                    "metrics": {}, "intervention": card(supporting_facts=[{"text": "看起来是事实", "source_ref": "invented"}])}
        with pytest.raises(IntelligenceResponseValidationError):
            await run_realtime_coach_via_pi(request=request(), pi_runtime=Cycle(), priority_mode="deep",
                provider_config={"base_url": "https://example.invalid", "api_key": "fixture-only", "model": "fixture"})
    asyncio.run(run())


def test_state_only_call_can_disable_a_second_paid_structure_repair():
    async def run():
        class Provider:
            calls = 0
            async def complete(self, *args, **kwargs):
                self.calls += 1
                return SimpleNamespace(content="{invalid", usage=None)
        provider = Provider()
        with pytest.raises(IntelligenceResponseValidationError):
            await run_realtime_intelligence(request=request(), provider=provider, allow_repair=False)
        assert provider.calls == 1
    asyncio.run(run())
