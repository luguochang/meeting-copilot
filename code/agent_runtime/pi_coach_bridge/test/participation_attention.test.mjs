// Scripted SDK replies prove transport/terminal compatibility, not model value.
import assert from "node:assert/strict";
import test from "node:test";
import { createModels, fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime } from "../src/runtime.mjs";

function snapshot(status = "uncertain") {
  return { contract: "discussion_attention.v1", control_revision: 2,
    opportunity_id: status === "present" ? "host-opportunity" : null, covered_seq: 8,
    current_focus: { observations: [{ evidence_segment_ids: ["current"], evidence_quote: "慢点，重新转转。" }],
      relation: "uncertain", object: null, setting: null, purpose: null },
    contribution_opportunity: { status, intent: status === "present" ? "ask_question" : null,
      reason: "当前回应需求尚不确定。", evidence_segment_ids: status === "present" ? ["current"] : [],
      evidence_quote: status === "present" ? "慢点，重新转转。" : "", opportunity_id: null },
    source_bindings: [{ segment_id: "current", revision: 2, evidence_hash: "host-hash" }] };
}

function request(trigger, attention) {
  return { request_id: `attention-${trigger}`, session_id: "attention-meeting", provider: {}, context: {
    coach_profile: "participation", priority_mode: "deep", state_revision: 8, trigger_type: trigger,
    user_request: trigger === "user_request" ? "回看之前预算方案，帮我组织一句建议。" : null,
    request_metadata: { profile: "participation", intent: "freeform", topic_id: "history",
      analysis_basis_revision: 3, current_attention: attention },
    context_paragraphs: [
      { id: "history-source", revision: 2, start_ms: 0, end_ms: 1000, text: "方案预算还需要核对。" },
      { id: "current", revision: 2, start_ms: 2000, end_ms: 3000, text: "慢点，重新转转。" },
    ], new_paragraphs: [], rolling_state: { discussion: { current_problem: "历史预算讨论",
      current_attention: { ...attention, covered_seq: 999, opportunity_id: "stale-rolling" } } },
  } };
}

function harness(suffix) {
  const faux = fauxProvider({ provider: `attention-${suffix}` });
  const models = createModels(); models.setProvider(faux.provider);
  return { faux, runtime: new PiCoachRuntime({ backendFactory: () => ({ identity: suffix,
    model: faux.getModel(), streamFn: models.streamSimple.bind(models) }) }) };
}

function payload(context) {
  return JSON.parse(context.messages.findLast(m => m.role === "user").content.find(b => b.type === "text").text);
}

function meaning(value) {
  return { current_focus: value.current_focus, contribution_opportunity: value.contribution_opportunity,
    opportunity_id: value.opportunity_id };
}

test("automatic SDK receives the exact frozen host attention before history, without guessing scene", async () => {
  const { faux, runtime } = harness("quiet");
  const value = snapshot();
  faux.setResponses([(context) => {
    const p = payload(context);
    assert.deepEqual(p.current_attention, meaning(value));
    assert.equal(p.current_attention.source_bindings, undefined);
    assert.equal(p.discussion.current_attention, undefined);
    assert.equal(p.current_attention.current_focus.setting, null);
    assert.ok(Object.keys(p).indexOf("current_attention") < Object.keys(p).indexOf("discussion"));
    assert.equal(p.recent_context.at(-1).quotes[0].text, "慢点，重新转转。");
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "当前没有明确的待回应判断。" }), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(request("discussion_update", value));
  assert.equal(result.action, "silent");
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
});

test("explicit manual historical request remains usable when automatic attention is none", async () => {
  const { faux, runtime } = harness("manual");
  const value = snapshot("none");
  faux.setResponses([(context) => {
    const p = payload(context);
    assert.deepEqual(p.current_attention, meaning(value));
    assert.equal(p.trigger_type, "user_request");
    assert.equal(p.user_request, "回看之前预算方案，帮我组织一句建议。");
    return fauxAssistantMessage(fauxToolCall("submit_intervention", {
      event_type: "question_to_user", title: "核对历史预算约束", urgency: "low", confidence: .8,
      recommendation: "可以先核对之前方案的预算约束，再比较备选方案的代价？",
      reason: "用户明确要求回看历史方案。", citation_quote_ids: [p.recent_context[0].quotes[0].quote_id], assumptions: [],
    }), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(request("user_request", value));
  assert.equal(result.action, "intervention");
  assert.deepEqual(result.intervention.evidence_segment_ids, ["history-source"]);
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
});

test("present attention can finish silent and does not require the latest citation", async () => {
  const { faux, runtime } = harness("available-quiet");
  const value = snapshot("present");
  faux.setResponses([(context) => {
    assert.deepEqual(payload(context).current_attention, meaning(value));
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "已知条件不足以形成新增建议。" }), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(request("discussion_update", value));
  assert.equal(result.action, "silent");
  assert.equal(result.metrics.turns, 1);
});

test("missing legacy attention is explicitly unassessed rather than copied from rolling history", async () => {
  const { faux, runtime } = harness("legacy");
  const r = request("user_request", snapshot()); delete r.context.request_metadata.current_attention;
  faux.setResponses([(context) => {
    assert.equal(payload(context).current_attention, null);
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "历史问题暂无新增观点。" }), { stopReason: "toolUse" });
  }]);
  assert.equal((await runtime.evaluate(r)).action, "silent");
});
