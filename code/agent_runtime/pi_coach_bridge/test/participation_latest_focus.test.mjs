// Faux SDK calls verify request and terminal mechanics, not real model semantics.
import assert from "node:assert/strict";
import test from "node:test";
import { createModels, fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime } from "../src/runtime.mjs";

function request(trigger = "discussion_update", userRequest = null) {
  return { request_id: `focus-${trigger}`, session_id: "focus-meeting", provider: {}, context: {
    state_revision: 3, coach_profile: "participation", priority_mode: "deep", trigger_type: trigger,
    user_request: userRequest,
    request_metadata: { profile: "participation", intent: "freeform", topic_id: "prior-topic", analysis_basis_revision: 2 },
    context_paragraphs: [
      { id: "prior", revision: 2, start_ms: 0, end_ms: 1000, text: "采用同步处理方案，预算需要进一步核对。" },
      { id: "latest-1", revision: 2, start_ms: 2000, end_ms: 3000, text: "先等一下，我们把手头的资料摆好。" },
      { id: "latest-2", revision: 2, start_ms: 4000, end_ms: 5000, text: "好的，稍等。" },
    ],
    new_paragraphs: [], rolling_state: { discussion: { current_problem: "比较同步与异步方案", open_points: [] } },
  } };
}

function harness(suffix) {
  const faux = fauxProvider({ provider: `latest-focus-${suffix}` });
  const models = createModels(); models.setProvider(faux.provider);
  const runtime = new PiCoachRuntime({ backendFactory: () => ({ identity: suffix,
    model: faux.getModel(), streamFn: models.streamSimple.bind(models) }) });
  return { faux, runtime };
}

function payload(context) {
  return JSON.parse(context.messages.findLast(m => m.role === "user").content.find(b => b.type === "text").text);
}

test("SDK gets current quote references before prior memory and can finish silently in one turn", async () => {
  const { faux, runtime } = harness("quiet");
  faux.setResponses([(context) => {
    const p = payload(context); const fields = Object.keys(p);
    assert.deepEqual(p.latest_focus_paragraph_ids, ["latest-1", "latest-2"]);
    assert.equal(p.trigger_type, "discussion_update");
    assert.ok(fields.indexOf("recent_context") < fields.indexOf("discussion"));
    assert.deepEqual(p.recent_context.map(s => s.id), ["prior", "latest-1", "latest-2"]);
    assert.equal(p.discussion.current_problem, "比较同步与异步方案");
    assert.equal(p.recent_context.at(-1).quotes[0].text, "好的，稍等。");
    assert.equal(p.latest_focus_paragraph_ids.every(id => p.recent_context.some(s => s.id === id)), true);
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "当前没有需要当场提出的新判断。" }), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(request());
  assert.equal(result.action, "silent");
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.tool_names, ["keep_silent"]);
  assert.deepEqual(result.metrics.tool_errors, []);
  assert.equal(result.metrics.context_reads, 0);
  assert.equal(result.metrics.history_searches, 0);
});

for (const [trigger, userRequest, latest] of [
  ["discussion_update", null, "异步方案也可以，继续看预算取舍。"],
  ["user_request", "回看刚才同步方案的预算风险，帮我组织一句建议。", "好的，稍等。"],
]) {
  test(`current short reply or manual historical task may still cite prior conditions: ${trigger}`, async () => {
    const { faux, runtime } = harness(trigger);
    const value = request(trigger, userRequest);
    value.context.context_paragraphs.at(-1).text = latest;
    faux.setResponses([(context) => {
      const p = payload(context);
      assert.equal(p.trigger_type, trigger);
      assert.equal(p.user_request, userRequest);
      assert.deepEqual(p.latest_focus_paragraph_ids, ["latest-1", "latest-2"]);
      const citation = p.recent_context[0].quotes[0].quote_id;
      return fauxAssistantMessage(fauxToolCall("submit_intervention", {
        event_type: "question_to_user", title: "明确方案预算边界", urgency: "low", confidence: .8,
        recommendation: "可以先核对预算约束，再比较同步和异步方案的代价？",
        reason: "先前方案提出了预算待核对条件。", citation_quote_ids: [citation], assumptions: [],
      }), { stopReason: "toolUse" });
    }]);
    const result = await runtime.evaluate(value);
    assert.equal(result.action, "intervention");
    assert.equal(result.metrics.turns, 1);
    assert.deepEqual(result.metrics.tool_names, ["submit_intervention"]);
    assert.deepEqual(result.intervention.evidence_segment_ids, ["prior"]);
    assert.equal(result.intervention.evidence_quote, value.context.context_paragraphs[0].text);
    assert.deepEqual(result.metrics.tool_errors, []);
  });
}

test("one paragraph focus refers to the existing quote catalog without duplicated text", async () => {
  const { faux, runtime } = harness("one-source");
  const value = request(); value.context.context_paragraphs = [];
  value.context.new_paragraphs = [{ id: "single", revision: 1, text: "这个条件还需要核对。" }];
  faux.setResponses([(context) => {
    const p = payload(context);
    assert.deepEqual(p.latest_focus_paragraph_ids, ["single"]);
    assert.equal(p.recent_context.length, 1);
    assert.equal(JSON.stringify(p).split("这个条件还需要核对。").length - 1, 1);
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "暂时没有新增观点。" }), { stopReason: "toolUse" });
  }]);
  assert.equal((await runtime.evaluate(value)).action, "silent");
});

test("an earlier new group does not override later timed context as the latest focus", async () => {
  const { faux, runtime } = harness("group-order");
  const value = request();
  value.context.new_paragraphs = [value.context.context_paragraphs.shift()];
  faux.setResponses([(context) => {
    const p = payload(context);
    assert.deepEqual(p.recent_context.map(s => s.id), ["latest-1", "latest-2", "prior"]);
    assert.deepEqual(p.latest_focus_paragraph_ids, ["latest-1", "latest-2"]);
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "当前没有新的待回应事项。" }), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "silent");
  assert.equal(result.metrics.turns, 1);
});

for (const uncertainty of ["missing-time", "same-time", "different-track"]) {
test(`several paragraphs with uncertain audio order do not claim group order is latest: ${uncertainty}`, async () => {
  const { faux, runtime } = harness(uncertainty);
  const value = request("user_request", "回看之前方案，帮我比较预算风险。");
  if (uncertainty === "missing-time") {
    for (const source of value.context.context_paragraphs) { delete source.start_ms; delete source.end_ms; }
  } else if (uncertainty === "same-time") {
    value.context.context_paragraphs[0].start_ms = value.context.context_paragraphs[1].start_ms;
    value.context.context_paragraphs[0].end_ms = value.context.context_paragraphs[1].end_ms;
  } else {
    value.context.context_paragraphs[0].source_track = "microphone";
    value.context.context_paragraphs[1].source_track = "system_audio";
  }
  faux.setResponses([(context) => {
    const p = payload(context);
    assert.deepEqual(p.latest_focus_paragraph_ids, []);
    assert.equal(p.recent_context.length, 3);
    assert.equal(p.user_request, value.context.user_request);
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "没有新增的可靠建议。" }), { stopReason: "toolUse" });
  }]);
  assert.equal((await runtime.evaluate(value)).action, "silent");
});
}
