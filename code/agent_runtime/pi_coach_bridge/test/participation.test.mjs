import assert from "node:assert/strict";
import test from "node:test";
import { createModels, fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime, validateEvaluationRequest } from "../src/runtime.mjs";
import { participationCard, participationMetadata } from "../src/participation.mjs";

function request(id = "participation-1") {
  return { request_id: id, session_id: "isolated-meeting", provider: {}, context: {
    state_revision: 4, coach_profile: "participation", priority_mode: "deep", trigger_type: "user_request",
    user_request: "帮我提出一个值得讨论的问题", request_metadata: { profile: "participation",
      intent: "ask_question", topic_id: "orders", analysis_basis_revision: 2 },
    new_paragraphs: [{ id: "current", text: "现在计划失败后直接重发请求。", revision: 1 }],
    context_paragraphs: [], rolling_state: {},
  } };
}

function card(extra = {}) {
  return { event_type: "question_to_user", title: "核对重复请求处理", urgency: "low",
    recommendation: "是否可以先明确重复请求的识别和结果复用方式，避免重试产生重复订单？",
    reason: "当前在讨论失败后的重试，先明确异常路径有助于评估方案。",
    evidence_segment_ids: ["current"], evidence_quote: "现在计划失败后直接重发请求。", confidence: .9,
    supporting_facts: [{ text: "失败后直接重发请求", source_ref: "current" }],
    assumptions: ["需要核对重发时是否有重复提交的可能。"], ...extra };
}

function selectedCard(payload, extra = {}) {
  const { evidence_segment_ids: _ids, evidence_quote: _quote, supporting_facts: _facts,
    document_evidence_refs: _documents, ...fields } = card();
  const transcript = payload.recent_context.flatMap((source) => source.quotes.map((quote) => quote.quote_id));
  const documents = payload.document_evidence.flatMap((source) => source.quotes.slice(0, 1).map((quote) => quote.quote_id));
  return { ...fields, citation_quote_ids: [...transcript, ...documents].slice(0, 4), ...extra };
}

function promptPayload(context) {
  return JSON.parse(context.messages.findLast((message) => message.role === "user").content.find((block) => block.type === "text").text);
}

function legacyQuoteHarness(value, contribution) {
  value.context.coach_profile = "legacy"; value.context.request_metadata = {};
  const { supporting_facts: _facts, assumptions: _assumptions, ...raw } = contribution;
  return raw;
}

test("participation uses Chinese professional role and first-turn SDK terminal without Fast", async () => {
  const faux = fauxProvider({ provider: "participation-fixture" });
  const models = createModels(); models.setProvider(faux.provider);
  const runtime = new PiCoachRuntime({ backendFactory: () => ({ identity: "fixture",
    model: faux.getModel(), streamFn: models.streamSimple.bind(models) }) });
  faux.setResponses([(context) => {
    assert.match(context.systemPrompt, /私人评审伙伴/);
    assert.match(context.systemPrompt, /原话没出现过/);
    const user = context.messages.findLast((message) => message.role === "user");
    const payload = JSON.parse(user.content.find((block) => block.type === "text").text);
    assert.equal(payload.task, "participation");
    assert.equal(payload.intent, "ask_question");
    assert.equal(payload.current_answer, undefined);
    const parameters = context.tools.find((tool) => tool.name === "submit_intervention").parameters.properties;
    assert.equal(parameters.recommendation.maxLength, 300);
    assert.match(context.systemPrompt, /一个citation_quote_ids列表/);
    assert.equal(parameters.evidence_quote, undefined);
    assert.equal(parameters.supporting_facts, undefined);
    assert.equal(parameters.citation_quote_ids.maxItems, 4);
    assert.match(parameters.citation_quote_ids.description, /相邻连续句.*300 UTF16/u);
    assert.ok(!parameters.citation_quote_ids.description.includes("最多一句"));
    assert.equal(parameters.transcript_quote_ids, undefined);
    assert.equal(parameters.document_quote_ids, undefined);
    assert.equal(parameters.supporting_fact_quote_ids, undefined);
    assert.equal(payload.recent_context[0].text, undefined);
    return fauxAssistantMessage(fauxToolCall("submit_intervention", selectedCard(payload)), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(request());
  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.prompt_profile, "participation");
  assert.equal(result.metrics.turns, 1);
  assert.equal(result.intervention.analysis_kind, "participation");
  assert.equal(result.intervention.proposal_type, "professional_proposal");
  assert.equal(result.intervention.topic_id, "orders");
  assert.deepEqual(result.intervention.supporting_facts, [{ source_ref: "current", text: request().context.new_paragraphs[0].text }]);
});

test("participation facts must be exact cited evidence and hypotheses cannot be asserted as project state", () => {
  const context = request().context;
  const evidence = new Map([["current", context.new_paragraphs[0].text], ["unselected", "订单已完成幂等处理"]]);
  assert.throws(() => participationCard(card({ supporting_facts: [{ text: "已完成幂等处理", source_ref: "unselected" }] }), context, evidence));
  assert.throws(() => participationCard(card({ recommendation: "我们的订单服务已经完成幂等处理，重试不会重复。" }), context, evidence));
  assert.throws(() => participationCard(card({ supporting_facts: [{ text: "应该重发请求", source_ref: "current" }] }), context, evidence));
});

test("participation requires frozen host intent and keeps metadata inside total request budget", () => {
  assert.throws(() => participationMetadata("participation", { intent: "ask_question" }));
  const value = request(); value.context.request_metadata.topic_id = "";
  assert.throws(() => validateEvaluationRequest(value));
});

test("a document used by a contribution must expose one exact supporting constraint", () => {
  const context = request().context;
  const evidence = new Map([["current", context.new_paragraphs[0].text]]);
  const source = { source_id: "reference", quote: "超时不能判断为失败，必须先查询处理结果。" };
  assert.throws(() => participationCard(card({ source_evidence: [source] }), context, evidence), /cited document/);
  const result = participationCard(card({ source_evidence: [source], supporting_facts: [
    ...card().supporting_facts,
    { source_ref: "reference", text: "超时不能判断为失败" },
  ] }), context, evidence);
  assert.equal(result.supporting_facts[1].source_ref, "reference");
  assert.throws(() => participationCard(card({ source_evidence: [source], supporting_facts: [
    { source_ref: "reference", text: "超时必定失败" },
  ] }), context, evidence), /verbatim/);
});

test("bounded small-material prefetch is limited to host participation and preserves legacy budgets", () => {
  const value = request();
  const source = (id, quote = "字".repeat(700)) => ({ source_type: "meeting_attachment", source_id: id,
    set_id: "set", meeting_id: value.session_id, attachment_id: "attachment", version_id: "version",
    version: 1, context_revision: 1, title: "模拟材料", content_hash: "fixture-hash", quote, locator: { paragraph: 1 } });
  value.context.document_evidence = Array.from({ length: 7 }, (_, index) => source(`span-${index}`));
  assert.equal(validateEvaluationRequest(value).context.document_evidence.length, 7);
  const longSpan = { ...value, context: { ...value.context, document_evidence: [source("long-span", "字".repeat(1500))] } };
  assert.equal(validateEvaluationRequest(longSpan).context.document_evidence[0].quote.length, 1500);
  assert.throws(() => validateEvaluationRequest({ ...longSpan, context: { ...longSpan.context, coach_profile: "legacy", request_metadata: {} } }));
  assert.throws(() => validateEvaluationRequest({ ...value, context: { ...value.context, coach_profile: "legacy", request_metadata: {} } }));
  assert.throws(() => validateEvaluationRequest({ ...value, context: { ...value.context,
    document_evidence: Array.from({ length: 13 }, (_, index) => source(`span-${index}`, "短文")) } }));
  assert.throws(() => validateEvaluationRequest({ ...value, context: { ...value.context,
    document_evidence: Array.from({ length: 9 }, (_, index) => source(`span-${index}`, "字".repeat(900))) } }));
});

function quoteContractHarness(id) {
  const faux = fauxProvider({ provider: `participation-quote-${id}` });
  const models = createModels(); models.setProvider(faux.provider);
  const runtime = new PiCoachRuntime({ backendFactory: () => ({ identity: `quote-${id}`,
    model: faux.getModel(), streamFn: models.streamSimple.bind(models) }) });
  const value = request(`quote-${id}`);
  const source = { source_type: "meeting_attachment", source_id: "attachment-span",
    set_id: "set", meeting_id: value.session_id, attachment_id: "attachment", version_id: "version",
    version: 1, context_revision: 1, title: "模拟超时约束", content_hash: "fixture-hash",
    quote: "超时不能判断为失败，必须先查询处理结果。", locator: { paragraph: 1 } };
  value.context.document_evidence = [source];
  const contribution = card({
    recommendation: "建议先查询处理结果，再判断是否需要重试，避免把超时直接当成失败。",
    reason: "资料要求先查询，当前候选是失败后直接重发。",
    document_evidence_refs: [{ source_id: source.source_id, quote: "超时不能判断为失败" }],
    supporting_facts: [...card().supporting_facts,
      { source_ref: source.source_id, text: "超时不能判断为失败" }],
  });
  return { faux, runtime, value, source, contribution };
}

test("real Pi SDK participation system and tool schema require the same quote selection contract", async () => {
  const { faux, runtime, value, source } = quoteContractHarness("system-schema-contract");
  faux.setResponses([(context) => {
    assert.match(context.systemPrompt, /citation_quote_ids选择已提供的quote_id/u);
    assert.match(context.systemPrompt, /宿主判定来源并恢复所选逐字原文及版本/u);
    assert.doesNotMatch(context.systemPrompt, /document_evidence_refs|evidence_segment_ids|evidence_quote|transcript_quote_ids|document_quote_ids|supporting_fact_quote_ids/u);
    const schema = context.tools.find((tool) => tool.name === "submit_intervention").parameters;
    assert.ok(schema.required.includes("citation_quote_ids"));
    for (const field of ["document_evidence_refs", "evidence_segment_ids", "evidence_quote", "supporting_facts"]) {
      assert.equal(schema.properties[field], undefined);
    }
    return fauxAssistantMessage(fauxToolCall("submit_intervention", selectedCard(promptPayload(context))), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.turns, 1);
  assert.equal(result.metrics.tool_calls, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
  assert.deepEqual(result.intervention.source_evidence, [source]);
});

for (const multipleFragments of [false, true]) {
  test(`participation exact meeting quote and separate attachment quote finish first turn: ${multipleFragments}`, async () => {
    const { faux, runtime, value, source, contribution } = quoteContractHarness(`exact-${multipleFragments}`);
    if (multipleFragments) {
      value.context.context_paragraphs = [{ id: "prior", text: "我们还要核对查询结果如何显示。", revision: 1 }];
      contribution.evidence_segment_ids.push("prior");
      contribution.evidence_quote = "失败后直接重发请求\n核对查询结果如何显示";
    } else {
      contribution.evidence_quote = "失败后直接重发请求";
    }
    faux.setResponses([(context) => fauxAssistantMessage(fauxToolCall("submit_intervention", selectedCard(promptPayload(context), {
      recommendation: contribution.recommendation, reason: contribution.reason,
    })), { stopReason: "toolUse" })]);
    const result = await runtime.evaluate(value);
    assert.equal(result.action, "intervention");
    assert.equal(result.metrics.turns, 1);
    assert.deepEqual(result.metrics.tool_names, ["submit_intervention"]);
    assert.deepEqual(result.metrics.tool_errors, []);
    assert.equal(result.metrics.context_reads, 0);
    assert.equal(result.metrics.history_searches, 0);
    assert.equal(result.intervention.evidence_quote, [...value.context.context_paragraphs, ...value.context.new_paragraphs].map((item) => item.text).join("\n"));
    assert.deepEqual(result.intervention.evidence_segment_ids, [...value.context.context_paragraphs, ...value.context.new_paragraphs].map((item) => item.id));
    assert.deepEqual(result.intervention.source_evidence, [source]);
    assert.ok(result.intervention.supporting_facts.some((fact) => fact.source_ref === source.source_id && fact.text === source.quote));
    assert.ok(!result.intervention.evidence_segment_ids.includes(source.source_id));
  });
}

for (const [label, quote] of [
  ["paraphrased", "计划失败后再次发送请求"],
  ["attachment-as-meeting", "超时不能判断为失败"],
  ["mixed-meeting-and-attachment", "失败后直接重发请求\n超时不能判断为失败"],
]) {
  test(`legacy preserves strict quote rejection without silently repairing: ${label}`, async () => {
    const { faux, runtime, value, contribution } = quoteContractHarness(label);
    contribution.evidence_quote = quote;
    const raw = legacyQuoteHarness(value, contribution);
    faux.setResponses(Array.from({ length: 2 }, () => fauxAssistantMessage(
      fauxToolCall("submit_intervention", raw), { stopReason: "toolUse" })));
    await assert.rejects(() => runtime.evaluate(value), (error) => {
      assert.equal(error.code, "missing_terminal_action");
      assert.equal(error.metrics.turns, 2);
      assert.deepEqual(error.metrics.tool_names, ["submit_intervention", "submit_intervention"]);
      assert.deepEqual(error.metrics.tool_errors, Array.from({ length: 2 }, () => ({
        tool: "submit_intervention", code: "evidence_quote_not_verbatim",
      })));
      return true;
    });
  });
}

test("legacy keeps bounded model repair after a rejected meeting quote", async () => {
  const { faux, runtime, value, contribution } = quoteContractHarness("model-repair");
  const raw = legacyQuoteHarness(value, contribution);
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("submit_intervention", {
      ...raw, evidence_quote: "计划失败后再次发送请求",
    }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("submit_intervention", raw), { stopReason: "toolUse" }),
  ]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.turns, 2);
  assert.deepEqual(result.metrics.tool_names, ["submit_intervention", "submit_intervention"]);
  assert.deepEqual(result.metrics.tool_errors, [{ tool: "submit_intervention", code: "evidence_quote_not_verbatim" }]);
  assert.equal(result.intervention.evidence_quote, contribution.evidence_quote);
  assert.equal(result.metrics.history_searches, 0);
});
