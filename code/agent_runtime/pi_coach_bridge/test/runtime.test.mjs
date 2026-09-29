import assert from "node:assert/strict";
import test from "node:test";

import {
  createAssistantMessageEventStream,
  createModels,
  fauxAssistantMessage,
  fauxProvider,
  fauxToolCall,
} from "@earendil-works/pi-ai";

import {
  PiCoachProtocolError,
  PiCoachRuntime,
  createOpenAICompatibleBackend,
  terminalToolChoiceForContext,
  outputTokenLimitForContext,
  requiresTerminalToolChoice,
  validateEvaluationRequest,
} from "../src/runtime.mjs";

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
          correction_status: "failed_preserved_original",
          evidence_quality: "provisional",
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
      candidate_events: [
        {
          event_type: "question_pending",
          evidence_segment_ids: ["remote-1"],
          reason: "新证据包含仍可能需要回应的问题。",
          candidate_key: "coach-candidate:question_pending:remote-1",
        },
        {
          event_type: "commitment_without_condition",
          evidence_segment_ids: ["remote-1"],
          reason: "日期、结果或责任承诺缺少明确前提。",
          candidate_key: "coach-candidate:commitment_without_condition:remote-1",
        },
      ],
      priority_mode: "realtime",
      rolling_state: { topic: "上线窗口" },
      meeting_goal: "不要无条件承诺上线日期",
    },
  };
}

function fullRequest(requestId = "request-1") {
  const value = request(requestId);
  value.context.candidate_events = [];
  value.context.priority_mode = null;
  return value;
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
    fauxAssistantMessage(fauxToolCall("read_realtime_context", { scope: "meeting_goal" }), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "commitment_risk",
        title: "对方正在等你回答",
        recommendation: "我先确认压测结果，再给出准确的上线日期。",
        reason: "对方直接询问了上线时间。",
        evidence_segment_ids: ["local-1", "remote-1"],
        evidence_quote: "周五一定上线吗\n压测通过后才能确定日期",
        urgency: "high",
        confidence: 0.93,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(fullRequest());

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.event_type, "commitment_risk");
  assert.equal(result.metrics.turns, 2);
  assert.equal(result.metrics.context_reads, 1);
  assert.equal(result.metrics.tool_calls, 2);
  assert.equal(result.metrics.checklist_reviewed, true);
  assert.equal(typeof result.metrics.ttft_ms, "number");
  assert.ok(result.metrics.ttft_ms >= 0);
  assert.ok(result.metrics.ttft_ms <= result.metrics.decision_latency_ms);
  assert.equal(result.metrics.decision_latency_ms, result.metrics.elapsed_ms);
  assert.equal(result.metrics.decision_timeout_ms, 10000);
  assert.ok(result.metrics.prompt_characters > 0);
  assert.equal(result.metrics.session_message_count_before, 0);
  assert.equal(result.metrics.timings.clock, "unix_epoch_ms");
  assert.ok(result.metrics.timings.first_token_at_ms >= result.metrics.timings.started_at_ms);
  assert.ok(result.metrics.timings.completed_at_ms >= result.metrics.timings.first_token_at_ms);
  assert.deepEqual(result.metrics.checklist_item_ids, [
    "unanswered_question",
    "unsafe_commitment",
    "goal_coverage",
    "position_conflict",
    "expression_clarity",
    "intervention_value",
  ]);
});

test("Pi leaves TTFT null when a provider exposes no streamed token event", async () => {
  const faux = fauxProvider({ provider: `talktrace-no-delta-${Math.random()}` });
  const finalMessage = fauxAssistantMessage(
    fauxToolCall("keep_silent", { reason: "No actionable moment." }),
    { stopReason: "toolUse" },
  );
  const monotonicValues = [100, 180];
  const wallClockValues = [1_000, 1_080];
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: "no-delta-test",
      model: faux.getModel(),
      streamFn: () => {
        const stream = createAssistantMessageEventStream();
        queueMicrotask(() => {
          stream.push({ type: "done", reason: "toolUse", message: finalMessage });
          stream.end(finalMessage);
        });
        return stream;
      },
    }),
    clock: () => monotonicValues.shift(),
    wallClock: () => wallClockValues.shift(),
  });

  const result = await runtime.evaluate(request("no-delta"));

  assert.equal(result.action, "silent");
  assert.equal(result.metrics.ttft_ms, null);
  assert.equal(result.metrics.decision_latency_ms, 80);
  assert.deepEqual(result.metrics.timings, {
    clock: "unix_epoch_ms",
    started_at_ms: 1_000,
    first_token_at_ms: null,
    completed_at_ms: 1_080,
  });
});

test("Pi can deliberately keep silent after the host checklist", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "No actionable moment." }), {
      stopReason: "toolUse",
    }),
  ]);

  const result = await runtime.evaluate(fullRequest());

  assert.equal(result.action, "silent");
  assert.equal(result.intervention, null);
  assert.equal(result.decision_reason, "No actionable moment.");
  assert.equal(result.metrics.turns, 1);
  assert.equal(result.metrics.checklist_reviews, 1);
  assert.equal(faux.state.callCount, 1);
});

test("deep answer coaching sees the Fast Answer and submits an incremental card in one turn", async () => {
  const { faux, runtime } = harness();
  const deep = fullRequest("deep-answer");
  deep.context.trigger_type = "answer_ready";
  deep.context.work_item_id = "answer:job-1";
  deep.context.priority_mode = "deep";
  deep.context.rolling_state.current_answer = {
    answer_id: "answer:job-1",
    evidence_segment_id: "remote-1",
    context_evidence_segment_ids: ["local-1", "remote-1"],
    question: "为什么选择 Redis Stream 而不是 Kafka？",
    answer: "因为已有 Redis 集群且当前吞吐规模可控，先用 Stream 降低接入和运维成本。",
    status: "committed",
  };
  faux.setResponses([
    (context) => {
      const userMessage = context.messages.findLast((message) => message.role === "user");
      const text = typeof userMessage.content === "string"
        ? userMessage.content
        : userMessage.content.find((block) => block.type === "text").text;
      const payload = JSON.parse(text);
      assert.equal(payload.task, "deepen_current_answer");
      assert.deepEqual(payload.current_answer, {
        question: deep.context.rolling_state.current_answer.question,
        answer: deep.context.rolling_state.current_answer.answer,
        status: "committed",
      });
      assert.equal(payload.user_request, null);
      assert.deepEqual(
        payload.bound_evidence.map((paragraph) => paragraph.id),
        ["local-1", "remote-1"],
      );
      assert.match(context.systemPrompt, /do not rewrite or summarize current_answer/i);
      assert.match(context.systemPrompt, /facts only from current_answer and bound_evidence/i);
      assert.match(context.systemPrompt, /speakable addition is the primary result/i);
      assert.deepEqual(
        context.tools.map((tool) => tool.name),
        ["submit_intervention", "keep_silent"],
      );
      assert.ok(!context.tools.some((tool) => tool.name === "read_realtime_context"));
      const terminalSchema = context.tools.find((tool) => tool.name === "submit_intervention").parameters;
      assert.ok(terminalSchema.properties.headline);
      assert.ok(terminalSchema.properties.say_this_addition);
      assert.ok(terminalSchema.properties.key_points);
      assert.equal(terminalSchema.properties.key_points.minItems, 2);
      assert.equal(terminalSchema.properties.key_points.maxItems, 2);
      assert.equal(terminalSchema.properties.core_judgement, undefined);
      assert.equal(terminalSchema.properties.next_action, undefined);
      assert.equal(terminalSchema.properties.recommendation, undefined);
      assert.equal(terminalSchema.properties.say_this, undefined);
      assert.equal(terminalSchema.properties.evidence_segment_ids, undefined);
      assert.equal(terminalSchema.properties.evidence_quote, undefined);
      return fauxAssistantMessage(
        fauxToolCall("submit_intervention", {
          headline: "补充异常恢复边界",
          say_this_addition: "我会再补充异常恢复和重新评估选型的触发条件，避免只回答接入成本。",
          key_points: [
            "当前回答没有覆盖异常恢复和重新选型条件",
            "补充选型切换条件和验证方式",
          ],
          confidence: 0.88,
        }),
        { stopReason: "toolUse" },
      );
    },
  ]);

  const result = await runtime.evaluate(deep);

  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.prompt_profile, "deep_answer");
  assert.deepEqual(
    result.metrics.available_tool_names,
    ["submit_intervention", "keep_silent"],
  );
  assert.equal(result.metrics.turns, 1);
  assert.equal(result.metrics.tool_calls, 1);
  assert.deepEqual(result.metrics.tool_names, ["submit_intervention"]);
  assert.equal(result.intervention.title, "补充异常恢复边界");
  assert.equal(result.intervention.recommendation, "我会再补充异常恢复和重新评估选型的触发条件，避免只回答接入成本。");
  assert.equal(result.intervention.coaching_package.question_intent, "需要回答：为什么选择 Redis Stream 而不是 Kafka？");
  assert.deepEqual(result.intervention.coaching_package.likely_follow_ups, []);
  assert.deepEqual(result.intervention.coaching_package.evidence_refs.map((item) => item.segment_id), ["local-1", "remote-1"]);
  assert.deepEqual(result.intervention.evidence_segment_ids, ["local-1", "remote-1"]);
  assert.equal(
    result.intervention.evidence_quote,
    "压测通过后才能确定日期\n周五一定上线吗",
  );
});

test("deep answer coaching preserves a structured user-request revision", async () => {
  const { faux, runtime } = harness();
  const deep = fullRequest("deep-answer-labels");
  deep.context.trigger_type = "user_request";
  deep.context.priority_mode = "deep";
  deep.context.user_request = "补充当前回答。";
  deep.context.rolling_state.current_answer = {
    evidence_segment_id: "local-1",
    question: "为什么选择 Redis Stream？",
    answer: "因为当前规模可控。",
    status: "committed",
  };
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        headline: "补充可靠性边界",
        say_this_addition: "可以补充当前规模之外的重新评估条件，以及失败消费的处理方式。",
        key_points: ["当前回答只说明了规模条件", "补充失败消费验证方式"],
        confidence: 0.8,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(deep);

  assert.equal(result.intervention.coaching_package.missing_points[0], "当前回答只说明了规模条件");
  assert.equal(result.intervention.coaching_package.next_actions[0], "补充失败消费验证方式");
  assert.deepEqual(
    result.metrics.available_tool_names,
    ["submit_intervention", "keep_silent"],
  );
  assert.equal(result.metrics.history_searches, 0);
});

test("bound user-request revisions fail before Provider when Answer evidence is absent", async () => {
  const { faux, runtime } = harness();
  const deep = fullRequest("deep-answer-missing-bound-evidence");
  deep.context.trigger_type = "user_request";
  deep.context.work_item_id = "answer:older-question";
  deep.context.priority_mode = "deep";
  deep.context.user_request = "没说中重点：请围绕原问题补充判断边界。";
  deep.context.rolling_state.current_answer = {
    answer_id: "answer:older-question",
    evidence_segment_id: "older-question-evidence",
    context_evidence_segment_ids: ["older-question-evidence"],
    question: "两个指标是否冲突？",
    answer: "两个指标采用了不同统计口径。",
    status: "committed",
  };

  await assert.rejects(
    runtime.evaluate(deep),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "invalid_request"
      && error.message === "deep answer is missing its host-bound evidence paragraph",
  );
  assert.equal(faux.state.callCount, 0);
});

test("the host checklist lets Pi reach a terminal decision in one provider turn", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("keep_silent", { reason: "No actionable moment." }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(request());

  assert.equal(result.action, "silent");
  assert.equal(result.metrics.turns, 1);
  assert.equal(result.metrics.tool_calls, 1);
  assert.deepEqual(result.metrics.tool_names, ["keep_silent"]);
  assert.equal(result.metrics.checklist_reviewed, true);
  assert.equal(faux.state.callCount, 1);
});

test("the host checklist exposes bounded routing signals in the initial request", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    (context) => {
      const userMessage = context.messages.findLast((message) => message.role === "user");
      assert.ok(userMessage);
      const text = typeof userMessage.content === "string"
        ? userMessage.content
        : userMessage.content.find((block) => block.type === "text").text;
      const payload = JSON.parse(text);
      assert.equal(payload.output_language, "zh-CN");
      assert.match(payload.output_language_contract, /title, recommendation, and reason/);
      assert.equal(payload.checklist_reviewed, true);
      assert.equal(payload.checklist.length, 6);
      assert.equal(payload.priority_mode, null);
      assert.deepEqual(payload.candidate_events, []);
      assert.match(payload.reminder, /Use exactly one terminal tool now when evidence is sufficient/);
      assert.match(payload.reminder, /Copy evidence_quote verbatim from the text of paragraphs named in evidence_segment_ids/);
      assert.match(context.systemPrompt, /evaluate those host-detected candidates first and in the supplied order/);
      assert.match(context.systemPrompt, /copy evidence_quote verbatim from the text of paragraphs named in evidence_segment_ids/i);
      assert.equal(payload.context_signals.meeting_goal, request().context.meeting_goal);
      assert.deepEqual(payload.context_signals.rolling_state, request().context.rolling_state);
      assert.deepEqual(
        payload.context_signals.recent_context_paragraphs.map((paragraph) => ({
          id: paragraph.id,
          text: paragraph.text,
          revision: paragraph.revision,
          source_track: paragraph.source_track,
          role_hint: paragraph.role_hint,
        })),
        request().context.context_paragraphs,
      );
      return fauxAssistantMessage(
        fauxToolCall("keep_silent", { reason: "The bounded context does not require intervention." }),
        { stopReason: "toolUse" },
      );
    },
  ]);

  const result = await runtime.evaluate(fullRequest());

  assert.equal(result.action, "silent");
  assert.equal(result.metrics.checklist_reviews, 1);
});

test("realtime candidates produce a grounded intervention in the first provider turn", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先回答上线条件",
        recommendation: "我先确认压测结果，再给出准确的上线日期。",
        reason: "候选问题仍在等待回答，且可以立刻澄清承诺条件。",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "周五一定上线吗",
        urgency: "high",
        confidence: 0.94,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(request("candidate-first-turn"));

  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.turns, 1);
  assert.equal(result.metrics.tool_calls, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
  assert.equal(faux.state.callCount, 1);
});

test("accepts canonical why_now and say_this intervention fields", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先回答上线条件",
        say_this: "我先确认压测结果，再给出准确的上线日期。",
        why_now: "对方刚询问日期，条件不清会造成错误承诺。",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "周五一定上线吗",
        urgency: "high",
        confidence: 0.94,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(request("canonical-card-fields"));

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.say_this, "我先确认压测结果，再给出准确的上线日期。");
  assert.equal(result.intervention.why_now, "对方刚询问日期，条件不清会造成错误承诺。");
  assert.equal(result.intervention.recommendation, result.intervention.say_this);
  assert.equal(result.intervention.reason, result.intervention.why_now);
});

test("realtime candidate fast path sends exact evidence and only terminal tools", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    (context) => {
      const userMessage = context.messages.findLast((message) => message.role === "user");
      const text = typeof userMessage.content === "string"
        ? userMessage.content
        : userMessage.content.find((block) => block.type === "text").text;
      const payload = JSON.parse(text);
      assert.equal(payload.task, "decide_realtime_candidate");
      assert.equal(payload.output_language, "zh-CN");
      assert.match(payload.output_language_contract, /title,recommendation,reason/);
      assert.equal(payload.host_checklist_reviewed, true);
      assert.equal(payload.checklist, undefined);
      assert.equal(payload.candidate_events[0].evidence_text, undefined);
      assert.equal(payload.new_paragraphs[0].text, "周五一定上线吗");
      assert.deepEqual(payload.candidate_events[0].preferred_event_types, ["question_to_user"]);
      assert.equal(payload.new_paragraphs[0].correction_status, "failed_preserved_original");
      assert.equal(payload.new_paragraphs[0].evidence_quality, "provisional");
      assert.deepEqual(context.tools.map((tool) => tool.name), ["submit_intervention", "keep_silent"]);
      assert.match(context.systemPrompt, /retry submit_intervention with the exact paragraph text/i);
      assert.match(context.systemPrompt, /latest explicit resolution as authoritative/i);
      assert.match(context.systemPrompt, /do not intervene merely to repeat, reconfirm, or compress/i);
      assert.match(context.systemPrompt, /for experiment_gap, keep silent.*bounded prototype/i);
      assert.match(context.systemPrompt, /do not ask for optional experiment refinements/i);
      assert.match(context.systemPrompt, /conditional or tentative plan.*is not commitment_risk/i);
      assert.match(context.systemPrompt, /newer evidence-backed status update.*is not contradiction/i);
      assert.match(context.systemPrompt, /provisional.*ask only for clarification/i);
      assert.match(context.systemPrompt, /do not add a digit.*最近一次.*多久.*几次/i);
      assert.match(context.systemPrompt, /scope every owner and deadline to the same action or topic/i);
      assert.match(context.systemPrompt, /when the evidence grounds only an owner gap, ask only who owns it/i);
      assert.match(context.systemPrompt, /confidence measures whether.*evidence supports.*at least 0\.80/i);
      assert.match(context.systemPrompt, /preserve the wording and polarity supported by the evidence/i);
      return fauxAssistantMessage(
        fauxToolCall("keep_silent", { reason: "The candidate is not actionable enough." }),
        { stopReason: "toolUse" },
      );
    },
  ]);

  const result = await runtime.evaluate(request("candidate-fast-contract"));

  assert.equal(result.action, "silent");
  assert.equal(result.metrics.prompt_profile, "candidate_fast");
  assert.deepEqual(result.metrics.available_tool_names, ["submit_intervention", "keep_silent"]);
  assert.ok(result.metrics.tool_schema_characters < 2_000);
  assert.ok(result.metrics.prompt_characters < 1_400);
});

test("compact candidate contract lets the host reconstruct auditable evidence", async () => {
  const { faux, runtime } = harness();
  const compactRequest = request("compact-candidate");
  compactRequest.context.compact_terminal_contract = true;
  compactRequest.context.coach_skill = {
    id: "project",
    version: 1,
    name: "Project execution coach",
    objective: "Turn discussion into executable next steps.",
    intervention_style: "Name one missing execution field and the delivery risk.",
    checklist: [{
      id: "execution_readiness",
      event_type: "execution_gap",
      question: "Is an execution field missing?",
    }],
  };
  faux.setResponses([
    (context) => {
      const userMessage = context.messages.findLast((message) => message.role === "user");
      const text = typeof userMessage.content === "string"
        ? userMessage.content
        : userMessage.content.find((block) => block.type === "text").text;
      const payload = JSON.parse(text);
      assert.equal(context.tools.find((tool) => tool.name === "submit_intervention").parameters.properties.reason, undefined);
      assert.equal(payload.task, "decide_realtime_candidate");
      assert.match(context.systemPrompt, /directly relevant recent context paragraph may support the card/i);
      assert.match(context.systemPrompt, /meeting goals and rolling state.*never quote evidence/i);
      assert.match(context.systemPrompt, /do not repeat or paraphrase the question/i);
      assert.equal(payload.skill.objective, compactRequest.context.coach_skill.objective);
      assert.equal(
        payload.skill.intervention_style,
        compactRequest.context.coach_skill.intervention_style,
      );
      return fauxAssistantMessage(
        fauxToolCall("submit_intervention", {
          event_type: "question_to_user",
          title: "先回答上线条件",
          recommendation: "我先确认压测结果，再给出准确的上线日期。",
          urgency: "high",
          confidence: 0.94,
        }),
        { stopReason: "toolUse" },
      );
    },
  ]);

  const result = await runtime.evaluate(compactRequest);

  assert.equal(result.action, "intervention");
  assert.deepEqual(result.intervention.evidence_segment_ids, ["remote-1", "local-1"]);
  assert.equal(result.intervention.evidence_quote, "周五一定上线吗\n压测通过后才能确定日期");
  assert.equal(result.intervention.reason, "新证据包含仍可能需要回应的问题。");
  assert.equal(result.metrics.compact_terminal_tools, true);
  assert.ok(result.metrics.tool_schema_characters < 1_500);
});

test("compact candidate contract rejects an echoed question and accepts a closure move", async () => {
  const { faux, runtime } = harness();
  const compactRequest = request("compact-question-anti-echo");
  compactRequest.context.compact_terminal_contract = true;
  compactRequest.context.new_paragraphs = [{
    id: "remote-owner",
    text: "复盘报告我们下周一发，但监控阈值谁来改？还没定。",
    revision: 1,
    source_track: "system_audio",
    role_hint: "remote_mix",
    correction_status: "no_change",
    evidence_quality: "reviewed",
  }];
  compactRequest.context.context_paragraphs = [];
  compactRequest.context.candidate_events = [{
    event_type: "question_pending",
    evidence_segment_ids: ["remote-owner"],
    reason: "新证据包含仍可能需要回应的问题。",
    candidate_key: "coach-candidate:question_pending:remote-owner",
  }];
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "监控阈值负责人未定",
        say_this: "监控阈值具体由谁来改，什么时候能改完？",
        urgency: "high",
        confidence: 0.91,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先保留为未决事项",
        say_this: "先不要结束这个议题，把负责人补齐后再继续。",
        urgency: "high",
        confidence: 0.91,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(compactRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.say_this, "先不要结束这个议题，把负责人补齐后再继续。");
  assert.equal(result.metrics.turns, 2);
  assert.deepEqual(result.metrics.tool_errors, [
    { tool: "submit_intervention", code: "recommendation_restates_evidence" },
  ]);
});

test("compact candidate contract accepts say_this as the canonical sentence field", async () => {
  const { faux, runtime } = harness();
  const compactRequest = request("compact-canonical-card");
  compactRequest.context.compact_terminal_contract = true;
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先回答上线条件",
        say_this: "我先确认压测结果，再给出准确的上线日期。",
        urgency: "high",
        confidence: 0.94,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(compactRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.say_this, "我先确认压测结果，再给出准确的上线日期。");
  assert.equal(result.intervention.recommendation, result.intervention.say_this);
  assert.equal(result.intervention.why_now, "新证据包含仍可能需要回应的问题。");
});

test("compact candidate contract removes a deadline borrowed from another topic", async () => {
  const { faux, runtime } = harness();
  const scopedRequest = request("compact-cross-topic-deadline");
  scopedRequest.context.compact_terminal_contract = true;
  scopedRequest.context.context_paragraphs = [];
  scopedRequest.context.new_paragraphs = [{
    id: "reviewed-1",
    text: "复盘报告我们下周一发，但监控阈值谁来改？还没定。",
    revision: 1,
    source_track: "system_audio",
    role_hint: "remote_mix",
    correction_status: "no_change",
    evidence_quality: "reviewed",
  }];
  scopedRequest.context.candidate_events = [{
    event_type: "question_pending",
    evidence_segment_ids: ["reviewed-1"],
    reason: "新证据包含仍可能需要回应的问题。",
    candidate_key: "coach-candidate:question_pending:reviewed-1",
  }];
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "监控阈值负责人待确认",
        recommendation: "监控阈值具体由谁来改？需要在下周一前定下来。",
        urgency: "medium",
        confidence: 0.85,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先落下负责人",
        recommendation: "先把监控阈值负责人写进行动项，再进入下一个议题。",
        urgency: "medium",
        confidence: 0.85,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(scopedRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.recommendation, "先把监控阈值负责人写进行动项，再进入下一个议题。");
  assert.equal(result.intervention.say_this, result.intervention.recommendation);
  assert.deepEqual(result.metrics.tool_errors, [
    { tool: "submit_intervention", code: "recommendation_restates_evidence" },
  ]);
});

test("compact candidate contract keeps a deadline scoped to the same owner gap", async () => {
  const { faux, runtime } = harness();
  const scopedRequest = request("compact-same-topic-deadline");
  scopedRequest.context.compact_terminal_contract = true;
  scopedRequest.context.context_paragraphs = [];
  scopedRequest.context.new_paragraphs = [{
    id: "reviewed-1",
    text: "监控阈值由谁来改，需要在下周一前定下来？",
    revision: 1,
    source_track: "system_audio",
    role_hint: "remote_mix",
    correction_status: "no_change",
    evidence_quality: "reviewed",
  }];
  scopedRequest.context.candidate_events = [{
    event_type: "question_pending",
    evidence_segment_ids: ["reviewed-1"],
    reason: "新证据包含仍可能需要回应的问题。",
    candidate_key: "coach-candidate:question_pending:reviewed-1",
  }];
  const recommendation = "监控阈值具体由谁来改，需要在下周一前定下来？";
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "确认监控阈值负责人和期限",
        recommendation,
        urgency: "medium",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先形成执行项",
        recommendation: "先把负责人和完成期限写进行动项，再继续讨论。",
        urgency: "medium",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(scopedRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.recommendation, "先把负责人和完成期限写进行动项，再继续讨论。");
  assert.deepEqual(result.metrics.tool_errors, [
    { tool: "submit_intervention", code: "recommendation_restates_evidence" },
  ]);
});

test("compact candidate contract fail-closes an ungrounded host reason", async () => {
  const { faux, runtime } = harness();
  const decisionRequest = request("compact-ungrounded-host-reason");
  decisionRequest.context.coach_skill = {
    id: "decision",
    version: 1,
    name: "Decision coach",
    objective: "Keep unresolved decision conditions visible.",
    intervention_style: "Ask one bounded question.",
    checklist: [{
      id: "decision_readiness",
      event_type: "decision_readiness",
      question: "Is a material decision condition still open?",
    }],
  };
  decisionRequest.context.candidate_events = [{
    event_type: "commitment_without_condition",
    evidence_segment_ids: ["remote-1"],
    reason: "决策正在收口，但仍有未关闭的决策条件。",
    candidate_key: "coach-candidate:commitment_without_condition:decision",
  }];
  decisionRequest.context.compact_terminal_contract = true;
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "decision_readiness",
        title: "先确认上线条件",
        recommendation: "先确认压测结果，再给出上线日期。",
        urgency: "high",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(decisionRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.reason, "当前候选仍有可立即核实的具体缺口。");
  assert.equal(result.intervention.why_now, result.intervention.reason);
  assert.deepEqual(result.intervention.evidence_segment_ids, ["remote-1", "local-1"]);
});

test("cross-batch clarity prompt exposes exact prior evidence and quotes both paragraphs", async () => {
  const { faux, runtime } = harness();
  const crossBatchRequest = request("compact-cross-batch-clarity");
  crossBatchRequest.context.compact_terminal_contract = true;
  crossBatchRequest.context.context_paragraphs = [];
  crossBatchRequest.context.retrieval_paragraphs = [{
    id: "clarity-prior",
    text: "结论先放到后面。",
    revision: 1,
    source_track: "microphone",
    role_hint: "self_or_room",
  }];
  crossBatchRequest.context.new_paragraphs = [{
    id: "clarity-fresh",
    text: "下一步仍然稍后再说。",
    revision: 1,
    source_track: "microphone",
    role_hint: "self_or_room",
  }];
  crossBatchRequest.context.candidate_events = [{
    event_type: "monologue_duration",
    evidence_segment_ids: ["clarity-prior", "clarity-fresh"],
    reason: "连续表达较长且仍有未收束信号。",
    candidate_key: "coach-candidate:monologue_duration:episode-1",
  }];
  faux.setResponses([
    (context) => {
      const userMessage = context.messages.findLast((message) => message.role === "user");
      const text = typeof userMessage.content === "string"
        ? userMessage.content
        : userMessage.content.find((block) => block.type === "text").text;
      const payload = JSON.parse(text);
      assert.deepEqual(
        payload.candidate_evidence_paragraphs.map((paragraph) => paragraph.id),
        ["clarity-prior"],
      );
      assert.deepEqual(
        payload.new_paragraphs.map((paragraph) => paragraph.id),
        ["clarity-fresh"],
      );
      return fauxAssistantMessage(
        fauxToolCall("submit_intervention", {
          event_type: "communication_clarity",
          title: "先说结论",
          recommendation: "下一句先给结论，再补一个直接依据。",
          urgency: "medium",
          confidence: 0.9,
        }),
        { stopReason: "toolUse" },
      );
    },
  ]);

  const result = await runtime.evaluate(crossBatchRequest);

  assert.equal(result.action, "intervention");
  assert.deepEqual(result.intervention.evidence_segment_ids, [
    "clarity-prior",
    "clarity-fresh",
  ]);
  assert.equal(
    result.intervention.evidence_quote,
    "结论先放到后面。\n下一步仍然稍后再说。",
  );
});

test("candidate fast path identifies when a terminal provider tool is required", () => {
  assert.equal(requiresTerminalToolChoice({
    tools: [{ name: "submit_intervention" }, { name: "keep_silent" }],
  }), true);
  assert.equal(requiresTerminalToolChoice({
    tools: [
      { name: "search_prior_evidence" },
      { name: "submit_intervention" },
      { name: "keep_silent" },
    ],
  }), false);
});

test("explicit deep-answer corrections force the submit terminal tool", () => {
  const deepTools = [
    {
      name: "submit_intervention",
      parameters: {
        properties: {
          key_points: { type: "array" },
          say_this_addition: { type: "string" },
        },
      },
    },
    { name: "keep_silent", parameters: { properties: {} } },
  ];
  assert.deepEqual(terminalToolChoiceForContext({
    tools: deepTools,
    messages: [{
      role: "user",
      content: JSON.stringify({
        task: "deepen_current_answer",
        user_request: "没说中重点：补充判断边界。",
      }),
    }],
  }), { type: "function", name: "submit_intervention" });
  assert.equal(terminalToolChoiceForContext({
    tools: deepTools,
    messages: [{
      role: "user",
      content: JSON.stringify({ task: "deepen_current_answer", user_request: null }),
    }],
  }), "required");
  assert.equal(terminalToolChoiceForContext({
    tools: [{ name: "search_prior_evidence" }, ...deepTools],
    messages: [],
  }), undefined);
});

test("candidate fast path bounds provider output and request shape", async () => {
  const evaluateOnce = async (compactTerminal) => {
    const { faux, runtime } = harness();
    const candidateRequest = request(`request-shape-${compactTerminal}`);
    candidateRequest.context.compact_terminal_contract = compactTerminal;
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall("keep_silent", { reason: "The candidate is not actionable enough." }),
        { stopReason: "toolUse" },
      ),
    ]);
    return runtime.evaluate(candidateRequest);
  };

  const fullCandidate = await evaluateOnce(false);
  const compactCandidate = await evaluateOnce(true);

  assert.equal(outputTokenLimitForContext({
    tools: [{ name: "submit_intervention" }, { name: "keep_silent" }],
  }), 256);
  assert.equal(outputTokenLimitForContext({
    tools: [{ name: "read_realtime_context" }, { name: "keep_silent" }],
  }), 512);
  assert.ok(compactCandidate.metrics.system_prompt_characters < fullCandidate.metrics.system_prompt_characters);
  assert.ok(compactCandidate.metrics.tool_schema_characters < fullCandidate.metrics.tool_schema_characters);
  assert.ok(compactCandidate.metrics.request_characters < fullCandidate.metrics.request_characters);
  assert.ok(compactCandidate.metrics.request_characters <= fullCandidate.metrics.request_characters - 500);
});

test("compact candidate path recovers a provider empty-argument terminal call safely", async () => {
  const { faux, runtime } = harness();
  const candidateRequest = request("empty-argument-candidate");
  candidateRequest.context.compact_terminal_contract = true;
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {}),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(candidateRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.event_type, "question_to_user");
  assert.equal(result.intervention.recommendation, "请确认这件事具体由谁负责？");
  assert.equal(result.intervention.evidence_segment_ids[0], "remote-1");
});

test("candidate fast path prefers the scene skill event over a generic question", async () => {
  const { faux, runtime } = harness();
  const interviewRequest = request("interview-candidate");
  interviewRequest.context.coach_skill = {
    id: "interview",
    version: 1,
    name: "User interview coach",
    objective: "Uncover concrete user context.",
    intervention_style: "Ask one neutral follow-up question.",
    checklist: [{
      id: "discovery_depth",
      event_type: "discovery_gap",
      question: "Is the pain still missing context, frequency, or impact?",
    }],
  };
  faux.setResponses([
    (context) => {
      const userMessage = context.messages.findLast((message) => message.role === "user");
      const text = typeof userMessage.content === "string"
        ? userMessage.content
        : userMessage.content.find((block) => block.type === "text").text;
      const payload = JSON.parse(text);
      assert.deepEqual(
        payload.candidate_events[0].preferred_event_types,
        ["discovery_gap", "question_to_user"],
      );
      return fauxAssistantMessage(
        fauxToolCall("submit_intervention", {
          event_type: "discovery_gap",
          title: "追问具体场景",
          recommendation: "能举一个最近遇到的具体导出场景吗？",
          reason: "当前痛点描述还缺少具体场景。",
          evidence_segment_ids: ["remote-1"],
          evidence_quote: "周五一定上线吗",
          urgency: "medium",
          confidence: 0.9,
        }),
        { stopReason: "toolUse" },
      );
    },
  ]);

  const result = await runtime.evaluate(interviewRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.event_type, "discovery_gap");
});

test("compact candidate contract enforces the first preferred scene event", async () => {
  const { faux, runtime } = harness();
  const interviewRequest = request("compact-preferred-event");
  interviewRequest.context.compact_terminal_contract = true;
  interviewRequest.context.coach_skill = {
    id: "interview",
    version: 1,
    name: "User interview coach",
    objective: "Uncover concrete behavior without leading the interviewee.",
    intervention_style: "Ask one neutral follow-up question.",
    checklist: [{
      id: "discovery_depth",
      event_type: "discovery_gap",
      question: "Is a concrete example or impact still missing?",
    }],
  };
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        // ``question_to_user`` is allowed for this candidate, but the host
        // must enforce the scene-specific ``discovery_gap`` preference.
        event_type: "question_to_user",
        title: "先追问具体场景",
        recommendation: "你最近一次遇到这个问题是什么时候？",
        urgency: "medium",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(interviewRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.event_type, "discovery_gap");
  assert.equal(
    result.intervention.recommendation,
    "请具体说明这个问题发生在哪个环节、带来什么影响？",
  );
});

test("candidate fast path retries exact evidence after a rejected quote", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先回答上线条件",
        recommendation: "先确认压测结果，再确认上线日期。",
        reason: "当前问题仍未被回答。",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "周五一定上线吗？",
        urgency: "high",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先回答上线条件",
        recommendation: "先确认压测结果，再确认上线日期。",
        reason: "当前问题仍未被回答。",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "周五一定上线吗",
        urgency: "high",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(request("candidate-quote-retry"));

  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.turns, 2);
  assert.deepEqual(result.metrics.tool_errors, [
    { tool: "submit_intervention", code: "evidence_quote_not_verbatim" },
  ]);
});

test("candidate fast path rejects an English-only card for Chinese dialogue", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "Confirm the owner",
        recommendation: "Ask who owns the release threshold and when it will be ready.",
        reason: "The latest question still has no accountable owner.",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "周五一定上线吗",
        urgency: "high",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先确认负责人",
        recommendation: "先问清谁负责发布阈值，以及最晚何时完成。",
        reason: "当前问题仍缺少明确负责人和完成时间。",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "周五一定上线吗",
        urgency: "high",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(request("candidate-language-retry"));

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.recommendation, "先问清谁负责发布阈值，以及最晚何时完成。");
  assert.equal(result.metrics.turns, 2);
  assert.deepEqual(result.metrics.tool_errors, [
    { tool: "submit_intervention", code: "output_language_mismatch" },
  ]);
});

test("candidate events must use supported types and reference fresh context evidence", () => {
  const unsupported = request("unsupported-candidate");
  unsupported.context.candidate_events[0].event_type = "freeform_agent_guess";
  assert.throws(
    () => validateEvaluationRequest(unsupported),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "invalid_request"
      && error.message === "candidate event type is unsupported",
  );

  const unknownEvidence = request("candidate-unknown-evidence");
  unknownEvidence.context.candidate_events[0].evidence_segment_ids = ["missing-segment"];
  assert.throws(
    () => validateEvaluationRequest(unknownEvidence),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "invalid_request"
      && error.message === "delta candidate events must reference known evidence and include fresh paragraph evidence",
  );

  const crossBatch = request("candidate-cross-batch-evidence");
  crossBatch.context.candidate_events = [{
    event_type: "monologue_duration",
    evidence_segment_ids: ["local-1", "remote-1"],
    reason: "连续表达较长且仍有未收束信号。",
    candidate_key: "coach-candidate:monologue_duration:cross-batch",
  }];
  assert.doesNotThrow(() => validateEvaluationRequest(crossBatch));

  const staleOnly = request("candidate-stale-only-evidence");
  staleOnly.context.candidate_events = [{
    event_type: "monologue_duration",
    evidence_segment_ids: ["local-1"],
    reason: "旧证据不应单独触发实时建议。",
    candidate_key: "coach-candidate:monologue_duration:stale-only",
  }];
  assert.throws(
    () => validateEvaluationRequest(staleOnly),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "invalid_request"
      && error.message === "delta candidate events must reference known evidence and include fresh paragraph evidence",
  );
});

test("trigger contract permits due and user requests without fabricated paragraphs", () => {
  const due = request("task-due-no-delta");
  due.context.trigger_type = "task_due";
  due.context.work_item_id = "decision-7";
  due.context.new_paragraphs = [];
  due.context.candidate_events = [{
    event_type: "commitment_without_condition",
    evidence_segment_ids: ["local-1"],
    reason: "持久事项到期，需要复核已有证据。",
    candidate_key: "coach-candidate:task-due:decision-7",
  }];
  const validatedDue = validateEvaluationRequest(due);
  assert.equal(validatedDue.context.trigger_type, "task_due");
  assert.equal(validatedDue.context.work_item_id, "decision-7");
  assert.deepEqual(validatedDue.context.new_paragraphs, []);

  const requested = request("user-request-no-delta");
  requested.context.trigger_type = "user_request";
  requested.context.user_request = "发布前还缺哪些条件？";
  requested.context.new_paragraphs = [];
  requested.context.candidate_events = [];
  const validatedRequest = validateEvaluationRequest(requested);
  assert.equal(validatedRequest.context.trigger_type, "user_request");
  assert.equal(validatedRequest.context.user_request, "发布前还缺哪些条件？");

  const answerReady = request("answer-ready-no-delta");
  answerReady.context.trigger_type = "answer_ready";
  answerReady.context.work_item_id = "answer:job-1";
  answerReady.context.new_paragraphs = [];
  answerReady.context.candidate_events = [];
  answerReady.context.rolling_state.current_answer = {
    question: "为什么选择 Redis Stream？",
    answer: "因为当前规模可控。",
    status: "committed",
  };
  const validatedAnswerReady = validateEvaluationRequest(answerReady);
  assert.equal(validatedAnswerReady.context.trigger_type, "answer_ready");
  assert.equal(validatedAnswerReady.context.work_item_id, "answer:job-1");

  const transcriptDelta = request("transcript-delta-canonical");
  transcriptDelta.context.trigger_type = "transcript_delta";
  assert.equal(validateEvaluationRequest(transcriptDelta).context.trigger_type, "transcript_delta");
});

test("trigger contract rejects incomplete due, user, and delta requests", () => {
  const emptyDelta = request("empty-delta");
  emptyDelta.context.new_paragraphs = [];
  emptyDelta.context.candidate_events = [];
  assert.throws(
    () => validateEvaluationRequest(emptyDelta),
    (error) => error instanceof PiCoachProtocolError
      && error.message === "delta context.new_paragraphs must not be empty",
  );

  const due = request("due-without-item");
  due.context.trigger_type = "task_due";
  assert.throws(
    () => validateEvaluationRequest(due),
    (error) => error instanceof PiCoachProtocolError
      && error.message === "task_due requires context.work_item_id",
  );

  const requested = request("user-without-request");
  requested.context.trigger_type = "user_request";
  assert.throws(
    () => validateEvaluationRequest(requested),
    (error) => error instanceof PiCoachProtocolError
      && error.message === "user_request requires context.user_request",
  );

  const answerReady = request("answer-ready-without-item");
  answerReady.context.trigger_type = "answer_ready";
  assert.throws(
    () => validateEvaluationRequest(answerReady),
    (error) => error instanceof PiCoachProtocolError
      && error.message === "answer_ready requires context.work_item_id",
  );
});

test("paragraph evidence quality fields reject explicit unsupported values", () => {
  const invalidCorrectionStatus = request("invalid-correction-status");
  invalidCorrectionStatus.context.new_paragraphs[0].correction_status = "guessed";
  assert.throws(
    () => validateEvaluationRequest(invalidCorrectionStatus),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "invalid_request"
      && error.message === "context.new_paragraphs[0].correction_status is unsupported",
  );

  const invalidEvidenceQuality = request("invalid-evidence-quality");
  invalidEvidenceQuality.context.new_paragraphs[0].evidence_quality = "certain";
  assert.throws(
    () => validateEvaluationRequest(invalidEvidenceQuality),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "invalid_request"
      && error.message === "context.new_paragraphs[0].evidence_quality is unsupported",
  );
});

test("priority_mode accepts realtime and the explicit deep lane only", () => {
  const deep = request("deep-priority-mode");
  deep.context.priority_mode = "deep";
  assert.equal(validateEvaluationRequest(deep).context.priority_mode, "deep");

  const invalid = request("invalid-priority-mode");
  invalid.context.priority_mode = "batch";

  assert.throws(
    () => validateEvaluationRequest(invalid),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "invalid_request"
      && error.message === "context.priority_mode is unsupported",
  );
});

test("the selected scene skill extends the checklist and accepts its bounded event", async () => {
  const { faux, runtime } = harness();
  const interviewRequest = fullRequest("interview-1");
  interviewRequest.context.coach_skill = {
    id: "interview",
    version: 1,
    name: "User interview coach",
    objective: "Uncover concrete behavior without leading the interviewee.",
    intervention_style: "Offer one neutral follow-up question grounded in exact words.",
    checklist: [{
      id: "discovery_depth",
      event_type: "discovery_gap",
      question: "Is a concrete example or impact still missing?",
    }],
  };
  faux.setResponses([
    (context) => {
      const userMessage = context.messages.findLast((message) => message.role === "user");
      const text = typeof userMessage.content === "string"
        ? userMessage.content
        : userMessage.content.find((block) => block.type === "text").text;
      const payload = JSON.parse(text);
      assert.equal(payload.coach_skill.id, "interview");
      assert.equal(payload.checklist.length, 7);
      return fauxAssistantMessage(
        fauxToolCall("submit_intervention", {
          event_type: "discovery_gap",
          title: "补一个具体例子",
          recommendation: "你最近一次遇到这个问题是什么时候？",
          reason: "当前只有结论，还没有具体行为场景。",
          evidence_segment_ids: ["remote-1"],
          evidence_quote: "周五一定上线吗",
          urgency: "medium",
          confidence: 0.9,
        }),
        { stopReason: "toolUse" },
      );
    },
  ]);

  const result = await runtime.evaluate(interviewRequest);

  assert.equal(result.intervention.event_type, "discovery_gap");
  assert.equal(result.metrics.coach_skill_id, "interview");
  assert.equal(result.metrics.coach_skill_version, 1);
  assert.equal(result.metrics.decision_latency_budget_ms, 10000);
  assert.ok(result.metrics.checklist_item_ids.includes("discovery_depth"));
});

test("the tool boundary rejects events owned by a different scene skill", async () => {
  const { faux, runtime } = harness();
  const interviewRequest = request("interview-wrong-event");
  interviewRequest.context.coach_skill = {
    id: "interview",
    version: 1,
    name: "User interview coach",
    objective: "Uncover concrete behavior without leading the interviewee.",
    intervention_style: "Offer one neutral follow-up question grounded in exact words.",
    checklist: [{
      id: "discovery_depth",
      event_type: "discovery_gap",
      question: "Is a concrete example or impact still missing?",
    }],
  };
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "execution_gap",
        title: "Missing owner",
        recommendation: "Please name an owner and deadline before moving on.",
        reason: "This event belongs to the project skill.",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: "周五一定上线吗",
        urgency: "medium",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("keep_silent", { reason: "The requested event is not allowed by this skill." }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(interviewRequest);

  assert.equal(result.action, "silent");
  assert.equal(result.intervention, null);
  assert.equal(result.metrics.turns, 2);
});

test("the tool boundary rejects invented evidence", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
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
  assert.equal(result.metrics.turns, 2);
  assert.deepEqual(result.metrics.tool_errors, [
    { tool: "submit_intervention", code: "evidence_quote_not_verbatim" },
  ]);
});

test("a contradiction cannot be submitted without prior evidence", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "contradiction",
        title: "Current claim alone is not a contradiction",
        recommendation: "Check the earlier condition before treating this claim as confirmed.",
        reason: "Only the latest claim was cited.",
        evidence_segment_ids: ["remote-1"],
        evidence_quote: request().context.new_paragraphs[0].text,
        urgency: "high",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("keep_silent", { reason: "No prior evidence was established." }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(request());

  assert.equal(result.action, "silent");
  assert.equal(result.metrics.turns, 2);
  assert.deepEqual(result.metrics.tool_errors, [
    { tool: "submit_intervention", code: "prior_evidence_required" },
  ]);
});

test("the same Pi session retains bounded history across evaluations", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "First moment is not actionable." }), {
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
  assert.equal(first.metrics.session_message_count_before, 0);
  assert.ok(second.metrics.session_message_count_before > 0);
  assert.equal(runtime.sessionCount, 1);
});

test("session tool capability follows the current candidate profile", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "No action." }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "History search is unnecessary." }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "No action." }), { stopReason: "toolUse" }),
  ]);

  const first = await runtime.evaluate(request("profile-1"));
  const contradiction = request("profile-2");
  contradiction.context.candidate_events = [{
    event_type: "objection_detected",
    evidence_segment_ids: ["remote-1"],
    reason: "当前说法可能与历史证据冲突。",
    candidate_key: "coach-candidate:objection_detected:remote-1",
  }];
  const second = await runtime.evaluate(contradiction);
  const third = await runtime.evaluate(request("profile-3"));

  assert.equal(first.metrics.session_reused, false);
  assert.equal(second.metrics.session_reused, false);
  assert.deepEqual(second.metrics.available_tool_names, [
    "search_prior_evidence",
    "read_transcript_span",
    "submit_intervention",
    "keep_silent",
  ]);
  assert.ok(second.metrics.session_message_count_before > 0);
  assert.equal(third.metrics.session_reused, false);
  assert.deepEqual(third.metrics.available_tool_names, ["submit_intervention", "keep_silent"]);
  assert.ok(third.metrics.session_message_count_before > 0);
});

test("a host evidence channel does not disable compact terminal candidates", async () => {
  const { faux, runtime } = harness();
  const candidateRequest = request("host-channel-compact");
  candidateRequest.context.compact_terminal_contract = true;
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "The bounded candidate is not actionable." }), {
      stopReason: "toolUse",
    }),
  ]);

  const result = await runtime.evaluate(candidateRequest, {
    searchEvidence: async () => [],
    readEvidenceSpan: async () => [],
  });

  assert.equal(result.action, "silent");
  assert.equal(result.metrics.compact_terminal_tools, true);
  assert.deepEqual(result.metrics.available_tool_names, ["submit_intervention", "keep_silent"]);
  assert.equal(result.metrics.history_searches, 0);
});

test("the total Agent deadline aborts a slow provider across all turns", async () => {
  const faux = fauxProvider({ provider: `talktrace-deadline-${Math.random()}` });
  const neverEndingStream = createAssistantMessageEventStream();
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: "deadline-test",
      model: faux.getModel(),
      decisionTimeoutMs: 20,
      streamFn: () => neverEndingStream,
    }),
  });

  await assert.rejects(
    runtime.evaluate(request("agent-deadline")),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "agent_deadline_exceeded"
      && error.metrics?.prompt_profile === "candidate_fast"
      && typeof error.metrics?.elapsed_ms === "number"
      && Array.isArray(error.metrics?.available_tool_names),
  );
  assert.equal(runtime.sessionCount, 0);
});

test("Provider 5xx status survives the SDK failure path as a safe numeric metric", async () => {
  const backend = createOpenAICompatibleBackend({
    base_url: "https://provider.example.test/v1",
    api_key: "test-only-key",
    model: "test-model",
    api_style: "chat_completions",
    timeout_ms: 1000,
    decision_timeout_ms: 1000,
  });
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: backend.identity,
      model: backend.model,
      providerTimeoutMs: backend.providerTimeoutMs,
      decisionTimeoutMs: backend.decisionTimeoutMs,
      get lastProviderStatusCode() {
        return backend.lastProviderStatusCode;
      },
      get lastProviderConnectMs() {
        return backend.lastProviderConnectMs;
      },
      streamFn: (model, context, options) => backend.streamFn(model, context, {
        ...options,
        fetch: async () => new Response(
          JSON.stringify({ error: { message: "temporarily unavailable", type: "server_error" } }),
          {
            status: 503,
            headers: { "content-type": "application/json" },
          },
        ),
      }),
    }),
  });

  await assert.rejects(
    runtime.evaluate(request("provider-503")),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "agent_provider_error"
      && error.metrics?.provider_status_code === 503
      && typeof error.metrics?.provider_connect_ms === "number"
      && error.metrics.provider_connect_ms >= 0,
  );
  assert.equal(runtime.sessionCount, 0);
});

test("codex-spark backend selects a bounded low reasoning effort", () => {
  const backend = createOpenAICompatibleBackend({
    base_url: "https://codexai.club",
    api_key: "test-only-key",
    model: "gpt-5.3-codex-spark",
    api_style: "chat_completions",
    timeout_ms: 2250,
    decision_timeout_ms: 2250,
  });

  assert.equal(backend.model.reasoning, true);
  assert.equal(backend.model.thinkingLevelMap.off, "low");
  assert.equal(backend.decisionTimeoutMs, 2250);
});

test("GPT-5 compatible gateways explicitly disable hidden default reasoning", () => {
  const backend = createOpenAICompatibleBackend({
    base_url: "https://codexai.club",
    api_key: "test-only-key",
    model: "gpt-5.5",
    api_style: "chat_completions",
    timeout_ms: 2250,
    decision_timeout_ms: 2250,
  });

  assert.equal(backend.model.reasoning, true);
  assert.equal(backend.model.thinkingLevelMap.off, "none");
});

test("Provider requests cannot exceed the ten-second bridge ceiling", () => {
  const backend = createOpenAICompatibleBackend({
    base_url: "https://provider.example.test/v1",
    api_key: "test-only-key",
    model: "gpt-5.5",
    api_style: "responses",
    timeout_ms: 25_000,
    decision_timeout_ms: 25_000,
  });

  assert.equal(backend.providerTimeoutMs, 10_000);
  assert.equal(backend.decisionTimeoutMs, 10_000);
});

test("explicit deep Answer revision forces the submit tool through non-streaming Responses", async () => {
  const backend = createOpenAICompatibleBackend({
    base_url: "https://provider.example.test/v1",
    api_key: "test-only-key",
    model: "gpt-5.5",
    api_style: "responses",
    timeout_ms: 1000,
    decision_timeout_ms: 1000,
  });
  let requestBody;
  let requestHeaders;
  let streamingCalls = 0;
  const fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    requestBody = await request.clone().json();
    requestHeaders = request.headers;
    return new Response(JSON.stringify({
      id: "resp_deep_non_streaming",
      object: "response",
      created_at: 1,
      status: "completed",
      model: "gpt-5.5",
      output: [{
        type: "function_call",
        id: "fc_deep_non_streaming",
        call_id: "call_deep_non_streaming",
        name: "submit_intervention",
        arguments: JSON.stringify({
          headline: "补充异常恢复边界",
          say_this_addition: "我再补充异常消费后的恢复策略和重新评估选型的触发条件。",
          key_points: ["当前回答没有说明异常恢复条件", "补充异常路径验证方式"],
          confidence: 0.88,
        }),
        status: "completed",
      }],
      usage: {
        input_tokens: 210,
        output_tokens: 70,
        total_tokens: 280,
        input_tokens_details: { cached_tokens: 10 },
        output_tokens_details: { reasoning_tokens: 5 },
      },
    }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: backend.identity,
      model: backend.model,
      decisionTimeoutMs: backend.decisionTimeoutMs,
      streamFn: (...args) => {
        streamingCalls += 1;
        return backend.streamFn(...args);
      },
      deepStreamFn: (model, context, options) => backend.deepStreamFn(model, context, {
        ...options,
        fetch,
      }),
      get lastProviderStatusCode() {
        return backend.lastProviderStatusCode;
      },
      get lastProviderConnectMs() {
        return backend.lastProviderConnectMs;
      },
    }),
  });
  const deep = fullRequest("deep-non-streaming");
  deep.context.trigger_type = "user_request";
  deep.context.user_request = "没说中重点：补充异常恢复边界。";
  deep.context.priority_mode = "deep";
  deep.context.work_item_id = "answer:job-non-streaming";
  deep.context.rolling_state.current_answer = {
    answer_id: "answer:job-non-streaming",
    evidence_segment_id: "remote-1",
    context_evidence_segment_ids: ["local-1", "remote-1"],
    question: "为什么选择 Redis Stream 而不是 Kafka？",
    answer: "当前吞吐规模可控，而且已有 Redis 集群。",
    status: "committed",
  };

  const result = await runtime.evaluate(deep);

  assert.equal(requestBody.stream, false);
  assert.deepEqual(requestBody.tool_choice, { type: "function", name: "submit_intervention" });
  assert.equal(requestBody.max_output_tokens, 280);
  assert.equal(requestHeaders.has("session_id"), false);
  assert.equal(requestHeaders.has("x-client-request-id"), false);
  assert.equal(streamingCalls, 0);
  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.provider_transport, "responses_non_streaming");
  assert.equal(result.metrics.tool_calls, 1);
  assert.equal(result.metrics.provider_status_code, undefined);
  assert.equal(result.metrics.usage.total_tokens, 280);
  assert.equal(result.intervention.title, "补充异常恢复边界");
});

test("realtime candidates keep the Pi SDK streaming transport", async () => {
  const faux = fauxProvider({ provider: `talktrace-streaming-lane-${Math.random()}` });
  const models = createModels();
  models.setProvider(faux.provider);
  let deepCalls = 0;
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "No intervention needed." }), {
      stopReason: "toolUse",
    }),
  ]);
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: "streaming-lane-test",
      model: faux.getModel(),
      streamFn: models.streamSimple.bind(models),
      deepStreamFn: () => {
        deepCalls += 1;
        return createAssistantMessageEventStream();
      },
    }),
  });

  const result = await runtime.evaluate(request("streaming-lane"));

  assert.equal(result.action, "silent");
  assert.equal(result.metrics.provider_transport, "sdk_streaming");
  assert.equal(deepCalls, 0);
});

test("a provider that ignores abort cannot crash on a late tool callback", async () => {
  const faux = fauxProvider({ provider: `talktrace-late-callback-${Math.random()}` });
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: "late-callback-test",
      model: faux.getModel(),
      decisionTimeoutMs: 15,
      streamFn: () => {
        const stream = createAssistantMessageEventStream();
        setTimeout(() => {
          const message = fauxAssistantMessage(
            fauxToolCall("keep_silent", { reason: "The callback arrived after the deadline." }),
            { stopReason: "toolUse" },
          );
          stream.push({ type: "done", reason: "toolUse", message });
          stream.end(message);
        }, 35);
        return stream;
      },
    }),
  });

  await assert.rejects(
    runtime.evaluate(request("late-callback")),
    (error) => error instanceof PiCoachProtocolError
      && error.code === "agent_deadline_exceeded"
      && typeof error.metrics?.tool_calls === "number",
  );
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(runtime.sessionCount, 0);
});

for (const hostSearch of [false, true]) {
test(`Pi can search ${hostSearch ? "host-only" : "bounded prior"} transcript evidence before deciding`, async () => {
  const { faux, runtime } = harness();
  const contradictionRequest = request("prior-search");
  const history = contradictionRequest.context.retrieval_paragraphs;
  if (hostSearch) contradictionRequest.context.retrieval_paragraphs = [];
  contradictionRequest.context.candidate_events = [{
    event_type: "objection_detected",
    evidence_segment_ids: ["remote-1"],
    reason: "当前说法可能与历史验收条件冲突，需要先查找旧证据。",
    candidate_key: "coach-candidate:objection_detected:remote-1",
  }];
  faux.setResponses([
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

  let hostCalls = 0;
  const result = await runtime.evaluate(contradictionRequest, hostSearch ? {
    searchEvidence: async (params) => {
      hostCalls += 1;
      assert.equal(params.query, "五百并发确认达标");
      return history.filter((item) => item.id === "remote-history-1");
    },
  } : {});

  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.history_searches, 1);
  assert.equal(result.metrics.history_results, 1);
  assert.equal(hostCalls, hostSearch ? 1 : 0);
});
}

test("Pi can read an exact transcript span after locating prior evidence", async () => {
  const { faux, runtime } = harness();
  const spanRequest = fullRequest("span-read");
  spanRequest.context.retrieval_paragraphs = [];
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("read_transcript_span", {
      segment_id: "remote-history-1", before: 1, after: 1,
    }), { stopReason: "toolUse" }),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "contradiction",
        title: "前后条件需要确认",
        recommendation: "先确认五百并发是否已经完成验收，再据此确定方案。",
        reason: "原文仍显示五百并发只是测试目标，当前条件尚未闭合。",
        evidence_segment_ids: ["remote-history-1"],
        evidence_quote: "五百并发只是测试目标，还没有确认达标",
        urgency: "high",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
  ]);
  let calls = 0;
  const result = await runtime.evaluate(spanRequest, {
    readEvidenceSpan: async (params) => {
      calls += 1;
      assert.deepEqual(params, { segment_id: "remote-history-1", before: 1, after: 1 });
      return [
        { id: "remote-history-1", text: "五百并发只是测试目标，还没有确认达标", revision: 1,
          source_track: "system_audio", role_hint: "remote_mix" },
      ];
    },
  });
  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.history_searches, 1);
  assert.equal(result.metrics.history_results, 1);
  assert.equal(calls, 1);
});

test("Pi accepts grounded communication clarity coaching over sustained speech", async () => {
  const { faux, runtime } = harness();
  const clarityRequest = request();
  clarityRequest.context.new_paragraphs = [
    {
      id: "local-expression-1",
      text: "说白了直播很重要，大家说对不对。",
      revision: 1,
      source_track: "microphone",
      role_hint: "self_or_room",
    },
    {
      id: "local-expression-2",
      text: "所以说直播真的很重要，你们觉得我说得对吗。",
      revision: 1,
      source_track: "microphone",
      role_hint: "self_or_room",
    },
  ];
  clarityRequest.context.candidate_events = [{
    event_type: "repetition",
    evidence_segment_ids: ["local-expression-1", "local-expression-2"],
    reason: "新证据包含重复表达，需要判断是否仍值得打断。",
    candidate_key: "coach-candidate:repetition:local-expression",
  }];
  faux.setResponses([
    (context) => {
      assert.match(context.systemPrompt, /sustained microphone\/self_or_room speech is sufficient evidence/);
      assert.match(context.systemPrompt, /never keep silent only because microphone speaker identity is unconfirmed/);
      return fauxAssistantMessage(
        fauxToolCall("submit_intervention", {
          event_type: "communication_clarity",
          title: "先收束核心观点",
          recommendation: "下一句先给结论：直播的价值是持续交流；然后只补一个例子。",
          reason: "连续两段重复强调重要性，但还没有形成清晰结论。",
          evidence_segment_ids: ["local-expression-1", "local-expression-2"],
          evidence_quote: "说白了直播很重要\n所以说直播真的很重要",
          urgency: "medium",
          confidence: 0.9,
        }),
        { stopReason: "toolUse" },
      );
    },
  ]);

  const result = await runtime.evaluate(clarityRequest);

  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.event_type, "communication_clarity");
  assert.deepEqual(result.intervention.evidence_segment_ids, ["local-expression-1", "local-expression-2"]);
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
