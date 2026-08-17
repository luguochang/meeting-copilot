import assert from "node:assert/strict";
import test from "node:test";

import {
  createModels,
  fauxAssistantMessage,
  fauxProvider,
  fauxToolCall,
} from "@earendil-works/pi-ai";

import { PiCoachProtocolError, PiCoachRuntime } from "../src/runtime.mjs";

function request(requestId = "request-1") {
  return {
    request_id: requestId,
    session_id: "meeting-1",
    provider: {},
    context: {
      state_revision: 7,
      new_paragraphs: [
        {
          id: "remote-1",
          text: "周五一定上线吗",
          revision: 1,
          source_track: "system_audio",
          role_hint: "remote_mix",
        },
      ],
      context_paragraphs: [
        {
          id: "local-1",
          text: "压测通过后才能确定日期",
          revision: 1,
          source_track: "microphone",
          role_hint: "self_or_room",
        },
      ],
      retrieval_paragraphs: [
        {
          id: "remote-history-1",
          text: "五百并发只是测试目标，还没有确认达标",
          revision: 1,
          source_track: "system_audio",
          role_hint: "remote_mix",
        },
      ],
      semantic_windows: [{ id: "window-1", segment_ids: ["local-1", "remote-1"] }],
      rolling_state: { topic: "上线窗口" },
      meeting_goal: "不要无条件承诺上线日期",
    },
  };
}

function harness() {
  const faux = fauxProvider({ provider: `talktrace-test-${Math.random()}` });
  const models = createModels();
  models.setProvider(faux.provider);
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: "faux-test",
      model: faux.getModel(),
      streamFn: models.streamSimple.bind(models),
    }),
  });
  return { faux, runtime };
}

test("Pi executes a context tool loop and returns only a validated intervention", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("review_coaching_checklist", {}), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(fauxToolCall("read_realtime_context", { scope: "meeting_goal" }), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "对方正在等你回答",
        recommendation: "我先确认压测结果，再给出准确的上线日期。",
        reason: "对方直接询问了上线时间。",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "周五一定上线吗",
        urgency: "high",
        confidence: 0.93,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(request());

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.event_type, "question_to_user");
  assert.equal(result.metrics.turns, 3);
  assert.equal(result.metrics.context_reads, 1);
  assert.equal(result.metrics.tool_calls, 3);
  assert.equal(result.metrics.checklist_reviewed, true);
  assert.deepEqual(result.metrics.checklist_item_ids, [
    "unanswered_question",
    "unsafe_commitment",
    "goal_coverage",
    "position_conflict",
    "intervention_value",
  ]);
});

test("Pi can deliberately keep silent after the mandatory checklist", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("review_coaching_checklist", {}), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "No actionable moment." }), {
      stopReason: "toolUse",
    }),
  ]);

  const result = await runtime.evaluate(request());

  assert.equal(result.action, "silent");
  assert.equal(result.intervention, null);
  assert.equal(result.metrics.turns, 2);
  assert.equal(faux.state.callCount, 2);
});

test("the tool boundary rejects invented evidence", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("review_coaching_checklist", {}), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "commitment_risk",
        title: "虚构证据",
        recommendation: "先确认条件，再给出准确日期。",
        reason: "证据不存在。",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "客户要求本周无条件交付",
        urgency: "high",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "Evidence was not valid." }), {
      stopReason: "toolUse",
    }),
  ]);

  const result = await runtime.evaluate(request());

  assert.equal(result.action, "silent");
  assert.equal(result.metrics.turns, 3);
});

test("the same Pi session retains bounded history across evaluations", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("review_coaching_checklist", {}), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "First moment is not actionable." }), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(fauxToolCall("review_coaching_checklist", {}), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "Second moment is not actionable." }), {
      stopReason: "toolUse",
    }),
  ]);

  const first = await runtime.evaluate(request("request-1"));
  const second = await runtime.evaluate(request("request-2"));

  assert.equal(first.metrics.session_reused, false);
  assert.equal(second.metrics.session_reused, true);
  assert.equal(runtime.sessionCount, 1);
});

test("Pi can search bounded prior transcript evidence before deciding", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("review_coaching_checklist", {}), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(fauxToolCall("search_prior_evidence", {
      query: "五百并发确认达标",
      max_results: 3,
    }), { stopReason: "toolUse" }),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "contradiction",
        title: "前后口径需要确认",
        recommendation: "先确认五百并发是否已经完成验收，再据此确定方案。",
        reason: "较早表述仍是测试目标，尚未确认达标。",
        evidence_segment_ids: ["remote-history-1"],
        evidence_quote: "五百并发只是测试目标",
        urgency: "high",
        confidence: 0.91,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(request());

  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.history_searches, 1);
  assert.equal(result.metrics.history_results, 1);
});

test("ordinary assistant text is not accepted as a business result", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([fauxAssistantMessage("You should answer the question.")]);

  await assert.rejects(
    runtime.evaluate(request()),
    (error) => error instanceof PiCoachProtocolError && error.code === "missing_terminal_action",
  );
  assert.equal(runtime.sessionCount, 0);
});
