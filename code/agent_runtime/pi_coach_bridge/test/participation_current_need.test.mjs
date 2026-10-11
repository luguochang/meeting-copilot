import assert from "node:assert/strict";
import test from "node:test";
import { createModels, fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime, validateEvaluationRequest } from "../src/runtime.mjs";
import { createParticipationQuoteCatalog, renderTranscriptQuotes } from "../src/participation_quotes.mjs";

const old = "这款双人餐包含照烧鸡和牛肉。";
const latest = "来三百六十度原地转转，我看什么向圈转。";

function request({ manual = false, text = latest } = {}) {
  return { request_id: "need-request", session_id: "need-session", provider: {}, context: {
    state_revision: 1, coach_profile: "participation", priority_mode: "deep",
    trigger_type: manual ? "user_request" : "discussion_update", user_request: "请结合讨论提出增量观点。",
    request_metadata: { profile: "participation", intent: "freeform", topic_id: "discussion:meal",
      analysis_basis_revision: 1, ...(manual ? {} : { automatic_attention: true,
        current_need_contract: "participation_need.v1", current_need_basis: {
          attention_covered_seq: 8, packet_anchor_seq: 9, focus_segment_ids: ["s8", "s9"],
          unassessed_segment_ids: ["s9"] } }),
      current_attention: { covered_seq: 8, control_revision: 1, opportunity_id: "opportunity:meal",
        current_focus: { observations: [{ evidence_segment_ids: ["s8"], evidence_quote: old }], relation: "same_topic" },
        contribution_opportunity: { status: "present", intent: "ask_question", reason: "核对可替换条件。" } } },
    new_paragraphs: [], context_paragraphs: [
      { id: "s8", text: old, revision: 1, start_ms: 8000, end_ms: 8800 },
      { id: "s9", text, revision: 2, start_ms: 9000, end_ms: 9800 }],
    rolling_state: { discussion: { current_problem: "套餐的选择和展示" } },
  } };
}

function payload(context) {
  return JSON.parse(context.messages.findLast(message => message.role === "user").content.find(block => block.type === "text").text);
}

function quote(body, id) { return body.recent_context.find(item => item.id === id).quotes[0].quote_id; }

function card(body) {
  return { event_type: "question_to_user", title: "核对替换条件", urgency: "low", confidence: .9,
    recommendation: "可以先明确套餐菜品能否替换及替换条件，再决定怎么展示选择，避免承诺不确定的搭配？",
    reason: "菜品搭配影响选择，需要核对替换条件。", citation_quote_ids: [quote(body, "s8")], assumptions: [] };
}

function assessment(body, status = "has_current_need", id = "s9") {
  return { status, focus_quote_ids: [quote(body, id)], reason: "最新原话说明本次机会是否仍需要回应。" };
}

async function execute(value, choose) {
  const faux = fauxProvider({ provider: "current-need-fixture" });
  const models = createModels(); models.setProvider(faux.provider);
  let count = 0;
  const runtime = new PiCoachRuntime({ backendFactory: () => ({ identity: "need-fixture",
    model: faux.getModel(), streamFn: models.streamSimple.bind(models) }) });
  faux.setResponses([context => { count += 1; return choose(payload(context), context); }]);
  try { return { result: await runtime.evaluate(value), count }; }
  catch (error) { return { result: { ok: false, error: { code: error.code }, metrics: error.metrics }, count }; }
}

test("automatic payload explicitly distinguishes attention coverage from latest unassessed focus", async () => {
  const { result, count } = await execute(request(), (body, context) => {
    assert.deepEqual(body.current_need_basis, request().context.request_metadata.current_need_basis);
    assert.equal(body.current_need_contract, "participation_need.v1");
    for (const name of ["submit_intervention", "keep_silent"]) {
      const schema = context.tools.find(tool => tool.name === name).parameters;
      assert.ok(schema.required.includes("current_need"));
      assert.equal(schema.properties.current_need.properties.focus_quote_ids.maxItems, 2);
    }
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "交流已离开套餐。",
      current_need: assessment(body, "moved_on") }), { stopReason: "toolUse" });
  });
  assert.equal(result.action, "silent");
  assert.equal(result.current_need.status, "moved_on");
  assert.deepEqual(result.current_need.focus_evidence, [{ segment_id: "s9", quote: latest }]);
  assert.equal(count, 1);
});

for (const status of ["already_answered", "moved_on", "uncertain"]) {
  test(`latest ${status} cannot revive the old opportunity through submit`, async () => {
    const { result, count } = await execute(request(), body => fauxAssistantMessage(fauxToolCall("submit_intervention",
      { ...card(body), current_need: assessment(body, status) }), { stopReason: "toolUse" }));
    assert.equal(result.ok, false);
    assert.equal(result.error.code, "invalid_agent_action");
    assert.equal(result.metrics.turns, 1);
    assert.equal(count, 1);
  });
}

test("same-topic new constraint can contribute with earlier card evidence in one SDK turn", async () => {
  const text = "那如果套餐里有人不吃牛肉，该怎么处理？";
  const { result, count } = await execute(request({ text }), body => fauxAssistantMessage(fauxToolCall("submit_intervention",
    { ...card(body), current_need: assessment(body) }), { stopReason: "toolUse" }));
  assert.equal(result.ok, true);
  assert.equal(result.action, "intervention");
  assert.deepEqual(result.intervention.evidence_segment_ids, ["s8"]);
  assert.deepEqual(result.current_need.focus_evidence, [{ segment_id: "s9", quote: text }]);
  assert.equal(result.metrics.turns, 1);
  assert.equal(count, 1);
});

for (const kind of ["missing", "old_only", "forged", "document"]) {
  test(`automatic current-need ${kind} selection fails once without a repair turn`, async () => {
    const { result, count } = await execute(request(), body => {
      const current_need = kind === "missing" ? undefined : assessment(body, "has_current_need", kind === "old_only" ? "s8" : "s9");
      if (kind === "forged" || kind === "document") current_need.focus_quote_ids = [kind === "document" ? "d-unregistered" : "t-forged"];
      return fauxAssistantMessage(fauxToolCall("submit_intervention", { ...card(body), ...(current_need ? { current_need } : {}) }), { stopReason: "toolUse" });
    });
    assert.equal(result.ok, false);
    assert.equal(result.metrics.turns, 1);
    assert.equal(count, 1);
  });
}

test("manual historical participation retains its existing schema", async () => {
  const { result, count } = await execute(request({ manual: true }), (body, context) => {
    assert.equal(context.tools.find(tool => tool.name === "submit_intervention").parameters.properties.current_need, undefined);
    return fauxAssistantMessage(fauxToolCall("submit_intervention", card(body)), { stopReason: "toolUse" });
  });
  assert.equal(result.action, "intervention");
  assert.equal(result.current_need, undefined);
  assert.equal(count, 1);
});

function directRequest() {
  const value = request({ text: "付款已扣款但接口超时，查询暂时不可用时页面应该如何提示？" });
  const metadata = value.context.request_metadata;
  delete metadata.automatic_attention;
  delete metadata.current_attention;
  metadata.automatic_direct = true;
  metadata.current_need_basis.attention_covered_seq = 0;
  metadata.current_need_basis.unassessed_segment_ids = ["s8", "s9"];
  return value;
}

test("direct automatic Pi assesses fresh exchange without a State opportunity in one SDK turn", async () => {
  const { result, count } = await execute(directRequest(), (body, context) => {
    assert.ok(context.systemPrompt.includes("宿主没有预先判定贡献机会"));
    assert.ok(!context.systemPrompt.includes("自动discussion_update只考虑宿主确认的present机会"));
    return fauxAssistantMessage(fauxToolCall("submit_intervention", {
      ...card(body), title: "核对超时页面", citation_quote_ids: [quote(body, "s9")],
      recommendation: "可以先把查询不可用显示为待确认，再明确用户如何核实结果，避免把超时误当成支付失败？",
      current_need: assessment(body),
    }), { stopReason: "toolUse" });
  });
  assert.equal(result.ok, true);
  assert.equal(result.action, "intervention");
  assert.equal(result.current_need.status, "has_current_need");
  assert.equal(count, 1);
});

test("direct automatic Pi must still assess latest focus and may keep silent after an answer", async () => {
  const { result, count } = await execute(directRequest(), body => fauxAssistantMessage(fauxToolCall("keep_silent", {
    reason: "当前回答已经覆盖该点，暂不重复。", current_need: assessment(body, "already_answered"),
  }), { stopReason: "toolUse" }));
  assert.equal(result.ok, true);
  assert.equal(result.action, "silent");
  assert.equal(result.current_need.status, "already_answered");
  assert.equal(count, 1);
});

function directWindowRequest(size) {
  const value = directRequest();
  const paragraphs = Array.from({ length: size }, (_, index) => ({ id: `s${index + 1}`, revision: 1,
    text: index === 0 ? "通知超时不代表发送失败，可以凭请求号查询结果。" : `当前讨论恢复边界的补充条件${index + 1}。`,
    start_ms: (index + 1) * 1000, end_ms: (index + 1) * 1000 + 800 }));
  value.request_id = `direct-window-${size}`;
  value.context.state_revision = size;
  value.context.priority_mode = "realtime";
  value.context.context_paragraphs = paragraphs.slice(-3);
  value.context.retrieval_paragraphs = paragraphs.slice(0, -3);
  value.context.new_paragraphs = [];
  value.context.request_metadata.current_need_basis = { attention_covered_seq: 0, packet_anchor_seq: size,
    focus_segment_ids: [`s${size - 1}`, `s${size}`], unassessed_segment_ids: [`s${size - 1}`, `s${size}`] };
  return value;
}

function directWindowCard(body) {
  return { event_type: "question_to_user", title: "核对通知结果恢复方式", urgency: "low", confidence: .9,
    recommendation: "建议先查询原请求结果，再决定是否重发，避免把超时直接当作通知失败？",
    reason: "当前正在讨论异常恢复，通知超时需要区分真实结果。",
    citation_quote_ids: [quote(body, "s1")], assumptions: [] };
}

for (const size of [6, 12]) {
  test(`direct ${size}-paragraph first SDK turn registers every frozen source and may cite earlier evidence`, async () => {
    const value = directWindowRequest(size);
    const originals = [...value.context.retrieval_paragraphs, ...value.context.context_paragraphs];
    const { result, count } = await execute(value, body => {
      assert.deepEqual(body.recent_context.map(source => source.id), originals.map(source => source.id));
      for (const source of originals) {
        assert.equal(body.recent_context.find(item => item.id === source.id).quotes.map(item => item.text).join(""), source.text);
      }
      return fauxAssistantMessage(fauxToolCall("submit_intervention", {
        ...directWindowCard(body),
        current_need: assessment(body, "has_current_need", `s${size}`),
      }), { stopReason: "toolUse" });
    });
    assert.equal(result.ok, true);
    assert.equal(result.action, "intervention");
    assert.deepEqual(result.intervention.evidence_segment_ids, ["s1"]);
    assert.equal(result.intervention.evidence_quote, originals[0].text);
    assert.deepEqual(result.current_need.focus_evidence, [{ segment_id: `s${size}`, quote: originals.at(-1).text }]);
    assert.equal(result.metrics.turns, 1);
    assert.equal(count, 1);
  });

  test(`direct ${size}-paragraph catalog does not allow old evidence to replace latest current-need assessment`, async () => {
    const { result, count } = await execute(directWindowRequest(size), body => fauxAssistantMessage(fauxToolCall("submit_intervention", {
      ...directWindowCard(body), current_need: assessment(body, "has_current_need", "s1"),
    }), { stopReason: "toolUse" }));
    assert.equal(result.ok, false);
    assert.equal(result.error.code, "invalid_agent_action");
    assert.equal(result.metrics.turns, 1);
    assert.equal(count, 1);
  });
}

test("non-direct participation keeps historical retrieval outside the initial quote catalog and prompt", async () => {
  const value = request({ manual: true });
  value.context.retrieval_paragraphs = [{ id: "unselected-history", revision: 1,
    text: "这个历史片段应在受控检索之后才进入目录。", start_ms: 1000, end_ms: 1800 }];
  const context = validateEvaluationRequest(value).context;
  const catalog = createParticipationQuoteCatalog(context, value.session_id);
  assert.deepEqual([...catalog.sources.keys()], ["transcript:s8", "transcript:s9"]);
  assert.throws(() => renderTranscriptQuotes(catalog, context.retrieval_paragraphs), /quote source was not registered/u);
  const { result, count } = await execute(value, body => {
    assert.deepEqual(body.recent_context.map(source => source.id), ["s8", "s9"]);
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "本轮不提出新的观点。" }), { stopReason: "toolUse" });
  });
  assert.equal(result.ok, true);
  assert.equal(result.action, "silent");
  assert.equal(count, 1);
});
