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
  normalizeInterventionCardFields,
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

function boundDeepRequest(requestId = "deep-contract") {
  const value = fullRequest(requestId);
  value.context.trigger_type = "answer_ready";
  value.context.work_item_id = "answer:contract";
  value.context.priority_mode = "deep";
  value.context.rolling_state.current_answer = {
    answer_id: "answer:contract",
    evidence_segment_id: "remote-1",
    context_evidence_segment_ids: ["local-1", "remote-1"],
    question: "发布前需要满足什么条件？",
    answer: "先确认发布条件。",
    status: "committed",
  };
  return value;
}

function promptPayload(context) {
  const message = context.messages.findLast((item) => item.role === "user");
  const text = typeof message.content === "string"
    ? message.content
    : message.content.find((block) => block.type === "text").text;
  return JSON.parse(text);
}

function harness({ modelId } = {}) {
  const faux = fauxProvider({ provider: `talktrace-test-${Math.random()}` });
  const models = createModels();
  models.setProvider(faux.provider);
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: "faux-test",
      model: modelId ? { ...faux.getModel(), id: modelId } : faux.getModel(),
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
      assert.match(context.systemPrompt, /current_answer is a prior AI answer.*never an authoritative source/i);
      assert.match(context.systemPrompt, /meeting facts only from bound_evidence or exact evidence/i);
      assert.match(context.systemPrompt, /Reviewed evidence can support a direct response/i);
      assert.doesNotMatch(context.systemPrompt, /starting with 先确认/i);
      assert.match(context.systemPrompt, /speakable addition is the primary result/i);
      assert.deepEqual(
        context.tools.map((tool) => tool.name),
        ["search_prior_evidence", "read_transcript_span", "submit_intervention", "keep_silent"],
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
    ["search_prior_evidence", "read_transcript_span", "submit_intervention", "keep_silent"],
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
    ["search_prior_evidence", "read_transcript_span", "submit_intervention", "keep_silent"],
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

test("single full model fields mechanically produce product aliases", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先回答上线条件",
        recommendation: "我先确认压测结果，再给出准确的上线日期。",
        reason: "对方刚询问日期，条件不清会造成错误承诺。",
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
      assert.match(payload.output_language_contract, /title, recommendation, and reason/);
      assert.equal(payload.host_checklist_reviewed, true);
      assert.equal(payload.checklist, undefined);
      assert.equal(payload.candidate_events[0].evidence_text, undefined);
      assert.equal(payload.new_paragraphs[0].text, "周五一定上线吗");
      assert.deepEqual(payload.candidate_events[0].preferred_event_types, ["question_to_user"]);
      assert.equal(payload.new_paragraphs[0].correction_status, "failed_preserved_original");
      assert.equal(payload.new_paragraphs[0].evidence_quality, "provisional");
      assert.deepEqual(context.tools.map((tool) => tool.name), ["submit_intervention", "keep_silent"]);
      assert.match(context.systemPrompt, /工具拒绝引文.*第二轮复制准确原文重试/);
      assert.match(context.systemPrompt, /已补条件、已解决.*静默/);
      assert.match(context.systemPrompt, /不重复追问或提醒/);
      assert.match(context.systemPrompt, /已有可运行实验、指标与下一决定/);
      assert.match(context.systemPrompt, /不追问非必要细节/);
      assert.match(context.systemPrompt, /条件式验证计划不当作无条件承诺/);
      assert.match(context.systemPrompt, /有据状态更新不当作矛盾/);
      assert.match(context.systemPrompt, /provisional.*只可澄清或静默/);
      assert.match(context.systemPrompt, /澄清不新增来源没有的数字\/假设/);
      assert.match(context.systemPrompt, /owner\/deadline只属于同主题同语句/);
      assert.match(context.systemPrompt, /只缺负责人则只问谁/);
      assert.match(context.systemPrompt, /confidence只衡量证据支持.*>=0\.80/);
      assert.match(context.systemPrompt, /保留原意及否定极性/);
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
      assert.match(context.systemPrompt, /当前直接相关recent_context可支撑增量/);
      assert.match(context.systemPrompt, /目标和rolling_state仅供方向，不当作引文/);
      assert.match(context.systemPrompt, /不复述刚听到的问题/);
      assert.equal(payload.skill.objective, compactRequest.context.coach_skill.objective);
      assert.equal(
        payload.skill.intervention_style,
        compactRequest.context.coach_skill.intervention_style,
      );
      return fauxAssistantMessage(
        fauxToolCall("submit_intervention", {
          event_type: "question_to_user",
          title: "先回答上线条件",
          say_this: "我先确认压测结果，再给出准确的上线日期。",
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
        say_this: "监控阈值具体由谁来改？需要在下周一前定下来。",
        urgency: "medium",
        confidence: 0.85,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先落下负责人",
        say_this: "先把监控阈值负责人写进行动项，再进入下一个议题。",
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
        say_this: recommendation,
        urgency: "medium",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "先形成执行项",
        say_this: "先把负责人和完成期限写进行动项，再继续讨论。",
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
        say_this: "先确认压测结果，再给出上线日期。",
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
          say_this: "下一句先给结论，再补一个直接依据。",
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
  // R01 recorded the unchanged default-role compact input at 3484 characters.
  // This bound measures compression without imposing a cap on custom roles.
  assert.ok(compactCandidate.metrics.system_prompt_characters <= 3484 * 0.70);
  assert.ok(compactCandidate.metrics.tool_schema_characters < fullCandidate.metrics.tool_schema_characters);
  assert.ok(compactCandidate.metrics.request_characters < fullCandidate.metrics.request_characters);
  assert.ok(compactCandidate.metrics.request_characters <= fullCandidate.metrics.request_characters - 500);
});

test("compact candidate path rejects empty arguments without synthesizing a card", async () => {
  const { faux, runtime } = harness();
  const candidateRequest = request("empty-argument-candidate");
  candidateRequest.context.compact_terminal_contract = true;
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {}),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("keep_silent", { reason: "没有完整有效的建议内容。" }),
      { stopReason: "toolUse" },
    ),
  ]);

  const result = await runtime.evaluate(candidateRequest);

  assert.equal(result.action, "silent");
  assert.equal(result.intervention, null);
  assert.equal(result.metrics.turns, 2);
});

for (const invalid of [
  { title: "回应发布条件", event_type: "question_to_user", urgency: "medium", confidence: 0.8 },
  { title: "回应发布条件", event_type: "question_to_user", urgency: "medium", confidence: 0.8, say_this: "        " },
  { title: "回应发布条件", event_type: "question_to_user", urgency: "medium", say_this: "先核实压测结果，再确认可执行的发布条件。" },
]) {
  test(`compact candidate rejects missing sentence or confidence: ${Object.keys(invalid).join(",")}:${Boolean(invalid.say_this?.trim())}`, async () => {
    const { faux, runtime } = harness();
    const value = request("partial-compact");
    value.context.compact_terminal_contract = true;
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall("submit_intervention", invalid), { stopReason: "toolUse" }),
      fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "建议字段不完整，保持静默。" }), { stopReason: "toolUse" }),
    ]);
    const result = await runtime.evaluate(value);
    assert.equal(result.action, "silent");
    assert.equal(result.intervention, null);
    assert.equal(result.metrics.turns, 2);
  });
}

function documentSource(overrides = {}) {
  return { source_type: "document", source_id: "span:legal", document_id: "doc:legal", project_id: "project:release",
    title: "发布规则", version_id: "version:legal:1", version: 1, content_hash: "confirmed-hash", locator: { line_start: 1 },
    quote: "发布需要法务批准，预算五万元。", context_revision: 2, ...overrides };
}

function withDocuments(value, sources = []) {
  value.context.knowledge_context = { project_id: "project:release", selection_mode: "project", context_revision: 2 };
  value.context.document_evidence = sources;
  return value;
}

function reconciliationRequest(requestId = "project-reconcile") {
  const source = { source_type: "review_document", source_id: "review:final", project_id: "project:release", meeting_id: "meeting-1",
    document_kind: "minutes", version_kind: "user_final", version: 2, content_hash: "human-final-hash",
    locator: { type: "review_document", document_kind: "minutes" }, quote: "本场发布讨论。" };
  const value = withDocuments(fullRequest(requestId));
  value.task_mode = value.context.task_mode = "project_reconcile";
  value.context.new_paragraphs = [];
  value.context.context_paragraphs = [];
  value.context.retrieval_paragraphs = [];
  value.context.knowledge_context.project_revision = 9;
  value.context.knowledge_context.project_state = { project_id: source.project_id, project_revision: 9, items: [], truncated: false };
  value.context.reconciliation_evidence = [source];
  value.context.review_final_content = "本场发布讨论。" + "前文讨论。".repeat(900) + "王工明天核对发布条件。";
  return value;
}

function memoryRequest(requestId = "memory-reconcile") {
  const source = documentSource({ quote: '术语纠正：将“托克吹斯”改为“Talktrace”。', context_revision: 0 });
  const clue = { kind: "term", memory_key: "talktrace", value: "Talktrace", aliases: ["托克吹斯"], source_refs: [source] };
  return { request_id: requestId, session_id: "memory:project:release", task_mode: "memory_reconcile", provider: {},
    context: { task_mode: "memory_reconcile", document_evidence: [source], knowledge_context: { project_id: source.project_id, project_revision: 9 },
      memory_reconciliation: { input_hash: "a".repeat(64), clues: [clue], existing_memories: [] } } };
}

function minimalMemoryTerminal(value) {
  return { input_hash: value.context.memory_reconciliation.input_hash, clue_indices: [0] };
}

test("memory background exposes only source-bound candidate proposal and waits for host acceptance", async () => {
  const { faux, runtime } = harness();
  const value = memoryRequest();
  let calls = 0;
  faux.setResponses([(context) => {
    assert.deepEqual(context.tools.map((tool) => tool.name), ["propose_memory_update"]);
    assert.equal(outputTokenLimitForContext(context), 256);
    assert.deepEqual(Object.keys(context.tools[0].parameters.properties).sort(), ["clue_indices", "input_hash"]);
    assert.ok(context.systemPrompt.includes("他人发言"));
    return fauxAssistantMessage(fauxToolCall("propose_memory_update", minimalMemoryTerminal(value)), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(value, { proposeMemoryUpdate: async (terminal) => {
    calls += 1;
    assert.deepEqual(terminal.candidates, value.context.memory_reconciliation.clues);
    return terminal;
  } });
  assert.equal(result.action, "memory_update");
  assert.equal(result.metrics.turns, 1);
  assert.equal(result.metrics.tool_calls, 1);
  assert.equal(result.metrics.decision_timeout_ms, 25000);
  assert.equal(result.metrics.checklist_reviewed, false);
  assert.equal(calls, 1);
});

for (const mutation of ["hash", "negative", "out_of_range", "duplicate", "fraction", "string_index", "extra", "legacy_candidates", "too_many"]) {
  test(`memory rejects ${mutation} and never asks host to accept an unregistered candidate`, async () => {
    const { faux, runtime } = harness();
    const value = memoryRequest(`bad-memory-${mutation}`);
    const bad = minimalMemoryTerminal(value);
    if (mutation === "hash") bad.input_hash = "b".repeat(64);
    if (mutation === "negative") bad.clue_indices = [-1];
    if (mutation === "out_of_range") bad.clue_indices = [1];
    if (mutation === "duplicate") bad.clue_indices = [0, 0];
    if (mutation === "fraction") bad.clue_indices = [0.5];
    if (mutation === "string_index") bad.clue_indices = ["0"];
    if (mutation === "extra") bad.value = "AnotherProduct";
    if (mutation === "legacy_candidates") { delete bad.clue_indices; bad.candidates = value.context.memory_reconciliation.clues; }
    if (mutation === "too_many") bad.clue_indices = Array(7).fill(0);
    const empty = { input_hash: value.context.memory_reconciliation.input_hash, clue_indices: [] };
    const capturedEmpty = { input_hash: empty.input_hash, candidates: [] };
    const accepted = [];
    faux.setResponses([fauxAssistantMessage(fauxToolCall("propose_memory_update", bad), { stopReason: "toolUse" }),
      fauxAssistantMessage(fauxToolCall("propose_memory_update", empty), { stopReason: "toolUse" })]);
    const result = await runtime.evaluate(value, { proposeMemoryUpdate: async (terminal) => { accepted.push(terminal); return terminal; } });
    assert.deepEqual(accepted, [capturedEmpty]);
    assert.equal(result.metrics.turns, 2);
    assert.ok(result.metrics.tool_calls <= 3);
    assert.deepEqual(result.memory_update, capturedEmpty);
  });
}

test("memory selections expand independent clones of all six exact registered clues", async () => {
  const { faux, runtime } = harness();
  const value = memoryRequest("six-memory-clones");
  const declarations = Array.from({ length: 6 }, (_, index) => `术语纠正：将“错误${index}”改为“Term${index}”。`);
  const source = { ...value.context.document_evidence[0], quote: declarations.join("\n") };
  value.context.document_evidence = [source];
  value.context.memory_reconciliation.clues = declarations.map((quote, index) => ({
    kind: "term", memory_key: `term${index}`, value: `Term${index}`, aliases: [`错误${index}`],
    source_refs: [{ ...source, quote }],
  }));
  const original = structuredClone(value.context.memory_reconciliation.clues);
  const selected = { input_hash: value.context.memory_reconciliation.input_hash, clue_indices: [5, 4, 3, 2, 1, 0] };
  assert.ok(JSON.stringify(selected).length < 128);
  faux.setResponses([fauxAssistantMessage(fauxToolCall("propose_memory_update", selected), { stopReason: "toolUse" })]);
  let captured;
  const result = await runtime.evaluate(value, { proposeMemoryUpdate: async (terminal) => {
    captured = terminal;
    return terminal;
  } });
  assert.deepEqual(Object.keys(captured).sort(), ["candidates", "input_hash"]);
  assert.deepEqual(captured.candidates, [...original].reverse());
  for (const [index, candidate] of captured.candidates.entries()) {
    const originalSource = value.context.memory_reconciliation.clues[5 - index];
    assert.notEqual(candidate.aliases, originalSource.aliases);
    assert.notEqual(candidate.source_refs[0].locator, originalSource.source_refs[0].locator);
  }
  assert.deepEqual(value.context.memory_reconciliation.clues, original);
  assert.deepEqual(result.memory_update.candidates, [...original].reverse());
  assert.equal(result.metrics.turns, 1);
});

test("memory cannot expand a clue from a different project", async () => {
  const { runtime } = harness();
  const value = memoryRequest("cross-project-memory-clue");
  value.context.memory_reconciliation.clues[0].source_refs[0] = { ...value.context.memory_reconciliation.clues[0].source_refs[0], project_id: "different-project" };
  let called = false;
  await assert.rejects(runtime.evaluate(value, { proposeMemoryUpdate: async (terminal) => { called = true; return terminal; } }), /memory source|not registered/);
  assert.equal(called, false);
});

test("memory host rejection cannot be bypassed by the final bridge response", async () => {
  const { faux, runtime } = harness();
  const value = memoryRequest("host-reject-memory");
  faux.setResponses(Array.from({ length: 2 }, () => fauxAssistantMessage(fauxToolCall("propose_memory_update", minimalMemoryTerminal(value)), { stopReason: "toolUse" })));
  await assert.rejects(() => runtime.evaluate(value, { proposeMemoryUpdate: async (terminal) => ({ ...terminal, candidates: [] }) }), (error) => {
    assert.equal(error.code, "missing_terminal_action");
    assert.equal(error.metrics.turns, 2);
    assert.equal(error.metrics.tool_calls, 2);
    return true;
  });
});

test("memory terminal is absent from realtime and project-background SDK schemas", async () => {
  for (const value of [fullRequest("realtime-no-memory-write"), reconciliationRequest("project-no-memory-write")]) {
    const { faux, runtime } = harness();
    faux.setResponses([(context) => {
      assert.equal(context.tools.some((tool) => tool.name === "propose_memory_update"), false);
      return fauxAssistantMessage(fauxToolCall(value.context.task_mode === "project_reconcile" ? "propose_project_changes" : "keep_silent",
        value.context.task_mode === "project_reconcile" ? { base_project_revision: 9, changes: [] } : { reason: "没有增量。" }), { stopReason: "toolUse" });
    }]);
    await runtime.evaluate(value, { proposeProjectChanges: async (terminal) => terminal });
  }
});

function projectProposal(value) {
  return { base_project_revision: 9, changes: [{ action: "create", type: "task", text: "核对发布条件", status: "active", owner: "王工", due: "明天",
    source_refs: [{ ...value.context.reconciliation_evidence[0], quote: "王工明天核对发布条件。" }] }] };
}

test("background reconciliation validates the full human final and calls the host before producing pending changes", async () => {
  const { faux, runtime } = harness();
  const value = reconciliationRequest();
  const proposal = projectProposal(value);
  let hostCalls = 0;
  faux.setResponses([(context) => {
    assert.deepEqual(context.tools.map((tool) => tool.name), ["propose_project_changes"]);
    assert.equal(promptPayload(context).review_final_content, value.context.review_final_content);
    assert.equal(outputTokenLimitForContext(context), 4096);
    return fauxAssistantMessage(fauxToolCall("propose_project_changes", proposal), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(value, { proposeProjectChanges: async (params) => { hostCalls += 1; return params; } });
  assert.equal(hostCalls, 1);
  assert.equal(result.action, "project_changes");
  assert.deepEqual(result.project_changes, proposal);
  assert.equal(result.intervention, null);
  assert.equal(result.metrics.decision_timeout_ms, 60000);
  assert.equal(result.metrics.prompt_profile, "project_reconcile");
  assert.equal(result.metrics.checklist_reviewed, false);
});

test("an explicit zero-change background terminal is successful and still requires host acceptance", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([fauxAssistantMessage(fauxToolCall("propose_project_changes", { base_project_revision: 9, changes: [] }), { stopReason: "toolUse" })]);
  const result = await runtime.evaluate(reconciliationRequest("no-project-changes"), { proposeProjectChanges: async (params) => params });
  assert.deepEqual(result.project_changes.changes, []);
  assert.equal(result.action, "project_changes");
});

test("background reference schema resolves typed IDs and quote to exact registered source metadata", async () => {
  const { faux, runtime } = harness();
  const value = reconciliationRequest("minimal-project-source");
  const proposal = projectProposal(value);
  const expected = structuredClone(proposal);
  proposal.changes[0].source_refs = proposal.changes[0].source_refs.map(({ source_type, source_id, quote }) => ({ source_type, source_id, quote }));
  faux.setResponses([fauxAssistantMessage(fauxToolCall("propose_project_changes", proposal), { stopReason: "toolUse" })]);
  const result = await runtime.evaluate(value, { proposeProjectChanges: async (params) => {
    assert.deepEqual(params, expected);
    return params;
  } });
  assert.deepEqual(result.project_changes, expected);
  assert.equal(result.metrics.tool_errors.length, 0);
});

test("background reference resolver rejects any explicitly forged metadata", async () => {
  const { faux, runtime } = harness();
  const value = reconciliationRequest("forged-project-source-version");
  const proposal = projectProposal(value);
  proposal.changes[0].source_refs[0].version += 1;
  let calls = 0;
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("propose_project_changes", proposal), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("propose_project_changes", { base_project_revision: 9, changes: [] }), { stopReason: "toolUse" }),
  ]);
  const result = await runtime.evaluate(value, { proposeProjectChanges: async (params) => { calls += 1; return params; } });
  assert.equal(calls, 1);
  assert.equal(result.metrics.tool_errors[0].code, "project_source_mismatch");
});

for (const damage of ["quote", "owner", "revision"]) {
  test(`background rejects unregistered project ${damage} before asking the host to accept`, async () => {
    const { faux, runtime } = harness();
    const value = reconciliationRequest(`forged-project-${damage}`);
    const proposal = projectProposal(value);
    if (damage === "quote") proposal.changes[0].source_refs[0].quote = "未经讨论决定直接上线。";
    if (damage === "owner") proposal.changes[0].owner = "李工";
    if (damage === "revision") proposal.base_project_revision = 8;
    let calls = 0;
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall("propose_project_changes", proposal), { stopReason: "toolUse" }),
      fauxAssistantMessage(fauxToolCall("propose_project_changes", { base_project_revision: 9, changes: [] }), { stopReason: "toolUse" }),
    ]);
    const result = await runtime.evaluate(value, { proposeProjectChanges: async (params) => { calls += 1; return params; } });
    assert.equal(calls, 1);
    assert.equal(result.metrics.tool_errors[0].tool, "propose_project_changes");
    assert.deepEqual(result.project_changes.changes, []);
  });
}

test("background evidence reads stop at four model turns and six executed tools", async () => {
  for (const manyTools of [false, true]) {
    const { faux, runtime } = harness();
    let reads = 0;
    const state = { source_type: "project_state", project_id: "project:release", project_revision: 9, snapshot_hash: "a".repeat(64), items: [], truncated: false };
    faux.setResponses(manyTools
      ? [fauxAssistantMessage(Array.from({ length: 7 }, () => fauxToolCall("read_project_state", {})), { stopReason: "toolUse" })]
      : Array.from({ length: 5 }, () => fauxAssistantMessage(fauxToolCall("read_project_state", {}), { stopReason: "toolUse" })));
    await assert.rejects(() => runtime.evaluate(reconciliationRequest(`background-budget-${manyTools}`),
      { readProjectState: async () => { reads += 1; return [state]; }, proposeProjectChanges: async (params) => params }),
    (error) => error.code === "missing_terminal_action" && error.metrics.turns <= 4 && error.metrics.tool_calls <= 6);
    assert.equal(reads, manyTools ? 6 : 4);
  }
});

test("only explicit project mode admits a human final without transcript and expands Provider deadline", () => {
  const background = reconciliationRequest();
  assert.equal(validateEvaluationRequest(background).context.task_mode, "project_reconcile");
  background.task_mode = "realtime_coach";
  assert.throws(() => validateEvaluationRequest(background), PiCoachProtocolError);
  const config = { base_url: "https://gateway.example", api_key: "test-key", model: "gpt-6-sol", timeout_ms: 60000, decision_timeout_ms: 60000 };
  assert.equal(createOpenAICompatibleBackend({ ...config, task_mode: "project_reconcile" }).decisionTimeoutMs, 60000);
  assert.equal(createOpenAICompatibleBackend(config).decisionTimeoutMs, 25000);
});

test("deep document lookup is typed and finishes with verified citations in two model turns", async () => {
  const { faux, runtime } = harness();
  const source = documentSource();
  const value = withDocuments(boundDeepRequest("deep-document-search"));
  let searches = 0;
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("search_project_documents", { query: "发布法务", max_results: 1 }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("submit_intervention", { headline: "补充资料条件",
        say_this_addition: "我先核对法务批准，再确定发布安排。", key_points: ["资料要求法务批准", "核对批准后确定发布安排"], confidence: 0.9,
        document_evidence_refs: [{ source_id: source.source_id, quote: "发布需要法务批准" }] }), { stopReason: "toolUse" }),
  ]);
  const result = await runtime.evaluate(value, { searchDocuments: async () => { searches += 1; return [source]; } });
  assert.equal(searches, 1);
  assert.equal(result.metrics.turns, 2);
  assert.deepEqual(result.metrics.tool_names, ["search_project_documents", "submit_intervention"]);
  assert.deepEqual(result.intervention.source_evidence, [{ ...source, quote: "发布需要法务批准" }]);
  assert.ok(!result.intervention.evidence_segment_ids.includes(source.source_id));
});

for (const documentAsTranscript of [false, true]) {
  test(`full manual document lookup keeps transcript and document quote provenance distinct: ${documentAsTranscript}`, async () => {
    const { faux, runtime } = harness();
    const source = documentSource();
    const value = withDocuments(fullRequest(`full-document-quote-${documentAsTranscript}`));
    value.context.trigger_type = "user_request";
    value.context.user_request = "请读取发布资料后给一句可直接说出口的建议。";
    value.context.priority_mode = "deep";
    value.context.document_evidence = [];
    value.context.new_paragraphs = [{ id: "current-question", text: "发布还需什么条件？", revision: 1, source_track: "system_audio" }];
    faux.setResponses([
      (context) => {
        assert.ok(context.systemPrompt.includes("those fields refer only to the current meeting transcript"));
        return fauxAssistantMessage(fauxToolCall("search_project_documents", { query: "发布" }), { stopReason: "toolUse" });
      },
      fauxAssistantMessage(fauxToolCall("submit_intervention", {
        event_type: "question_to_user", title: "核对发布条件", recommendation: "请先核对法务批准，再讨论具体发布安排。",
        reason: "资料要求核对发布的法务批准条件。", evidence_segment_ids: ["current-question"],
        evidence_quote: documentAsTranscript ? "发布需要法务批准" : "发布还需什么条件？",
        urgency: "medium", confidence: 0.9, document_evidence_refs: [{ source_id: source.source_id, quote: "发布需要法务批准" }],
      }), { stopReason: "toolUse" }),
    ]);
    const execute = () => runtime.evaluate(value, { searchDocuments: async () => [source] });
    if (documentAsTranscript) {
      await assert.rejects(execute, (error) => error.code === "missing_terminal_action"
        && error.metrics.turns === 2 && error.metrics.tool_errors[0].code === "evidence_quote_not_verbatim");
    } else {
      const result = await execute();
      assert.equal(result.metrics.turns, 2);
      assert.equal(result.intervention.source_evidence[0].source_id, source.source_id);
      assert.equal(result.intervention.evidence_quote, "发布还需什么条件？");
    }
  });
}

test("advertised full and compact terminals expose one required field per meaning", async () => {
  for (const compact of [false, true]) {
    const { faux, runtime } = harness();
    const value = compact ? request("canonical-compact") : fullRequest("canonical-full");
    value.context.compact_terminal_contract = compact;
    faux.setResponses([(context) => {
      const schema = context.tools.find((tool) => tool.name === "submit_intervention").parameters;
      assert.equal(schema.additionalProperties, false);
      assert.ok(schema.required.includes(compact ? "say_this" : "recommendation"));
      assert.equal(Object.hasOwn(schema.properties, compact ? "recommendation" : "say_this"), false);
      assert.equal(Object.hasOwn(schema.properties, "why_now"), false);
      assert.equal(Object.hasOwn(schema.properties, "reason"), !compact);
      if (!compact) {
        assert.ok(schema.required.includes("reason"));
        assert.ok(context.systemPrompt.includes("once in recommendation"));
        assert.equal(promptPayload(context).output_language_contract.includes("accepted aliases"), false);
      }
      return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "没有新增可用信息。" }), { stopReason: "toolUse" });
    }]);
    const result = await runtime.evaluate(value);
    assert.equal(result.metrics.turns, 1);
    assert.equal(result.action, "silent");
  }
});

test("compatibility parser accepts equal aliases but rejects legacy sentence and reason mismatches", () => {
  const canonical = { recommendation: "请核对法务批准和预算是否落实。", reason: "原文列出了发布条件。" };
  const fields = normalizeInterventionCardFields(canonical);
  assert.equal(fields.say_this, canonical.recommendation);
  assert.equal(fields.why_now, canonical.reason);
  assert.deepEqual(normalizeInterventionCardFields({ ...canonical, say_this: canonical.recommendation, why_now: canonical.reason }), fields);
  assert.throws(() => normalizeInterventionCardFields({ ...canonical, say_this: "我先确认发布条件，再讨论执行。" }), /recommendation and say_this must match/);
  assert.throws(() => normalizeInterventionCardFields({ ...canonical, why_now: "当前是另一句解释。" }), /reason and why_now must match/);
});

test("preloaded document citations cannot invent quotes or masquerade as transcript sources", async () => {
  const { faux, runtime } = harness();
  const source = documentSource();
  const value = withDocuments(fullRequest("forged-document-ref"), [source]);
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("submit_intervention", { event_type: "commitment_risk", title: "发布条件",
      recommendation: "我先核对批准状态。", reason: "应核对发布条件。", evidence_segment_ids: ["remote-1"],
      evidence_quote: value.context.new_paragraphs[0].text, urgency: "medium", confidence: 0.9,
      document_evidence_refs: [{ source_id: source.source_id, quote: "法务已经批准" }] }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "引用内容不匹配，保留原文。" }), { stopReason: "toolUse" }),
  ]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "silent");
  assert.equal(result.metrics.tool_errors[0].tool, "submit_intervention");
});

test("document lookup cannot consume a second lookup turn or exceed the existing tool budget", async () => {
  const { faux, runtime } = harness();
  let calls = 0;
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("search_project_documents", { query: "发布条件" }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("search_project_documents", { query: "预算" }), { stopReason: "toolUse" }),
  ]);
  await assert.rejects(() => runtime.evaluate(withDocuments(boundDeepRequest("document-budget")),
    { searchDocuments: async () => { calls += 1; return []; } }), (error) => error.code === "missing_terminal_action" && error.metrics.turns === 2);
  assert.equal(calls, 1);
});

test("switching document scope clears retained Pi messages instead of replaying earlier sources", async () => {
  const { faux, runtime } = harness();
  const first = withDocuments(fullRequest("document-scope-first"), [documentSource()]);
  faux.setResponses([fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "暂不打断。" }), { stopReason: "toolUse" })]);
  await runtime.evaluate(first);
  const second = withDocuments(fullRequest("document-scope-second"));
  second.context.knowledge_context.project_id = "different-project";
  second.context.knowledge_context.context_revision = 3;
  faux.setResponses([(context) => {
    assert.equal(context.messages.filter((message) => message.role === "user").length, 1);
    assert.ok(!JSON.stringify(context.messages).includes("confirmed-hash"));
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "新资料范围未发现相关条件。" }), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(second);
  assert.equal(result.metrics.session_message_count_before, 0);
});

test("project state reads remain readonly and consume the shared two-turn budget", async () => {
  const { faux, runtime } = harness();
  const value = withDocuments(boundDeepRequest("read-project-state"));
  value.context.knowledge_context.project_revision = 9;
  const state = { source_type: "project_state", project_id: "project:release", project_revision: 9, snapshot_hash: "a".repeat(64),
    items: [{ id: "task:1", type: "task", text: "补齐法务批准", status: "active", revision: 1, origin: "user", source_refs: [] }], truncated: false };
  faux.setResponses([
    (context) => {
      assert.ok(context.tools.some((tool) => tool.name === "read_project_state"));
      assert.ok(!context.tools.some((tool) => tool.name === "propose_project_changes"));
      return fauxAssistantMessage(fauxToolCall("read_project_state", {}), { stopReason: "toolUse" });
    },
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "已确认事项当前没有新进展。" }), { stopReason: "toolUse" }),
  ]);
  const result = await runtime.evaluate(value, { readProjectState: async () => [state] });
  assert.equal(result.metrics.turns, 2);
  assert.deepEqual(result.metrics.tool_names, ["read_project_state", "keep_silent"]);
});

test("changed project revision cannot replace the original bound state during a tool read", async () => {
  const { faux, runtime } = harness();
  const value = withDocuments(boundDeepRequest("stale-project-state"));
  value.context.knowledge_context.project_revision = 9;
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("read_project_state", {}), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "项目状态版本已变化，等待重新取证。" }), { stopReason: "toolUse" }),
  ]);
  const result = await runtime.evaluate(value, { readProjectState: async () => [{ source_type: "project_state", project_id: "project:release", project_revision: 10,
    snapshot_hash: "a".repeat(64), items: [], truncated: false }] });
  assert.equal(result.action, "silent");
  assert.equal(result.metrics.tool_errors[0].tool, "read_project_state");
});

test("explicit memory terms and brief preference enter every prompt without becoming evidence", async () => {
  for (const profile of ["candidate", "full", "deep"]) {
    const { faux, runtime } = harness();
    const value = profile === "candidate" ? request(`memory-${profile}`)
      : profile === "deep" ? boundDeepRequest(`memory-${profile}`) : fullRequest(`memory-${profile}`);
    value.context.user_memory = { terms: [{ canonical: "法务", aliases: ["legal"] }], preferences: { response_length: "brief" } };
    faux.setResponses([(context) => {
      assert.deepEqual(promptPayload(context).user_memory, value.context.user_memory);
      assert.ok(context.systemPrompt.includes("通常不超过80"));
      return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "术语只辅助表达，没有新增事实。" }), { stopReason: "toolUse" });
    }]);
    const result = await runtime.evaluate(value);
    assert.equal(result.action, "silent");
  }
});

test("compact safety repair never fabricates a replacement for a fused cross-topic deadline", async () => {
  const { faux, runtime } = harness();
  const value = request("fused-owner-deadline");
  value.context.compact_terminal_contract = true;
  value.context.context_paragraphs = [];
  value.context.new_paragraphs[0].text = "复盘报告我们下周一发，但监控阈值谁来改？还没定。";
  value.context.new_paragraphs[0].correction_status = "no_change";
  value.context.new_paragraphs[0].evidence_quality = "reviewed";
  value.context.candidate_events = [value.context.candidate_events[0]];
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("submit_intervention", {
      event_type: "question_to_user", title: "监控阈值负责人待确认",
      say_this: "请明确下周一前负责监控阈值修改的人。", urgency: "medium", confidence: 0.85,
    }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "日期不属于当前行动。" }), { stopReason: "toolUse" }),
  ]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "silent");
  assert.equal(result.intervention, null);
  assert.deepEqual(result.metrics.tool_errors, [{ tool: "submit_intervention", code: "cross_topic_deadline" }]);
});

for (const profile of ["full", "candidate", "spark", "deep"]) {
  test(`shared host role, scene context and balanced unresolved items reach ${profile}`, async () => {
    const { faux, runtime } = harness({ modelId: profile === "spark" ? "gpt-5.3-codex-spark" : undefined });
    const value = profile === "deep" ? boundDeepRequest(`role-${profile}`)
      : profile === "full" ? fullRequest(`role-${profile}`) : request(`role-${profile}`);
    value.context.compact_terminal_contract = profile === "candidate" || profile === "spark";
    value.context.role_contract = "你是用户的私人对话副驾；本次以中立访谈追问帮助主持者。";
    value.context.role_contract_version = "fixture.role.v1";
    value.context.participant_role = "用户研究访谈者";
    value.context.focus_points = ["真实使用场景", "已确认条件"];
    value.context.glossary = ["Talktrace", "SeACo"];
    value.context.meeting_goal = { goal: "了解实际工作行为", participant_role: "用户研究访谈者" };
    value.context.rolling_state.open_items = [
      { id: "decision", type: "decision", status: "pending", text: "决定尚未确认" },
      { id: "action", type: "action_item", status: "open", text: "后续行动" },
      { id: "risk", type: "risk", status: "open", text: "关键前提缺失" },
      ...Array.from({ length: 9 }, (_, i) => ({ id: `q-${i}`, type: "open_question", status: "open", text: "仍待了解的问题" })),
      { id: "closed", type: "risk", status: "resolved", text: "已解决风险" },
    ];
    faux.setResponses([(context) => {
      assert.ok(context.systemPrompt.includes(value.context.role_contract));
      assert.match(context.systemPrompt, /fixture\.role\.v1/);
      const payload = promptPayload(context);
      assert.equal(payload.participant_role, value.context.participant_role);
      assert.deepEqual(payload.focus_points, value.context.focus_points);
      assert.deepEqual(payload.glossary, value.context.glossary);
      const goal = profile === "full" ? payload.context_signals.meeting_goal
        : profile === "spark" ? payload.goal : payload.meeting_goal;
      assert.deepEqual(goal, value.context.meeting_goal);
      const items = profile === "full" ? payload.context_signals.rolling_state.open_items
        : profile === "candidate" ? payload.rolling_state.open_items : payload.open_items;
      assert.equal(items.length, 6);
      assert.ok(["decision", "action", "risk"].every((id) => items.some((item) => item.id === id)));
      assert.ok(!items.some((item) => item.id === "closed"));
      if (profile === "deep") assert.equal(payload.current_answer_authority, "prior_ai_output_for_comparison_only");
      return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "本轮没有需要补充的新信息。" }), { stopReason: "toolUse" });
    }]);
    const result = await runtime.evaluate(value);
    assert.equal(result.action, "silent");
    assert.equal(result.metrics.turns, 1);
  });
}

test("legacy and blank host contracts retain the conservative Chinese shared role", async () => {
  for (const roleContract of [undefined, ""]) {
    const { faux, runtime } = harness();
    const value = fullRequest("legacy-role-contract");
    value.context.role_contract = roleContract;
    faux.setResponses([(context) => {
      assert.match(context.systemPrompt, /你是当前用户的私人工作与对话副驾/);
      assert.match(context.systemPrompt, /旧AI答案仅用于比较，不是事实来源/);
      return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "没有增量。" }), { stopReason: "toolUse" });
    }]);
    assert.equal((await runtime.evaluate(value)).action, "silent");
  }
});

test("shared context rejects non-text contracts and unbounded focus lists", () => {
  const invalidRole = request("invalid-role-contract");
  invalidRole.context.role_contract = { guessed: "role" };
  assert.throws(() => validateEvaluationRequest(invalidRole), /context\.role_contract must be text/);
  const invalidFocus = request("invalid-focus");
  invalidFocus.context.focus_points = Array(13).fill("范围");
  assert.throws(() => validateEvaluationRequest(invalidFocus), /context\.focus_points must be a bounded array/);
});

test("deep coach completes one host search with exact neighbors and a cited terminal action in two turns", async () => {
  const { faux, runtime } = harness();
  const value = boundDeepRequest("deep-bounded-search");
  value.context.retrieval_paragraphs = [];
  let searches = 0;
  const prior = [
    { id: "prior-condition", text: "压测通过后才能发布。", revision: 3, source_track: "system_audio", role_hint: "remote_mix", correction_status: "no_change", evidence_quality: "reviewed" },
    { id: "condition-neighbor", text: "发布前需要提供压测报告。", revision: 2, source_track: "system_audio", role_hint: "remote_mix", correction_status: "no_change", evidence_quality: "reviewed" },
  ];
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("search_prior_evidence", { query: "发布压测条件", max_results: 2 }), { stopReason: "toolUse" }),
    (context) => {
      const toolMessage = context.messages.findLast((item) => item.role === "toolResult");
      const results = JSON.parse(toolMessage.content[0].text).results;
      assert.deepEqual(results.map(({ id, text, revision, correction_status, evidence_quality }) =>
        ({ id, text, revision, correction_status, evidence_quality })),
      prior.map(({ id, text, revision, correction_status, evidence_quality }) =>
        ({ id, text, revision, correction_status, evidence_quality })));
      return fauxAssistantMessage(fauxToolCall("submit_intervention", {
        headline: "补充发布前提",
        say_this_addition: "发布前应补齐压测通过的依据和报告，再据此确定可执行的发布安排。",
        key_points: ["旧条件要求压测通过才发布", "补齐压测报告并核对发布条件"], confidence: 0.9,
      }), { stopReason: "toolUse" });
    },
  ]);
  const result = await runtime.evaluate(value, {
    searchEvidence: async (params) => {
      searches += 1;
      assert.equal(params.include_neighbors, true);
      assert.equal(params.max_results, 2);
      return prior;
    },
  });
  assert.equal(searches, 1);
  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.turns, 2);
  assert.equal(result.metrics.history_searches, 1);
  assert.deepEqual(result.metrics.tool_names, ["search_prior_evidence", "submit_intervention"]);
  assert.ok(result.intervention.evidence_segment_ids.includes("prior-condition"));
  assert.ok(result.intervention.evidence_segment_ids.includes("condition-neighbor"));
  assert.ok(result.intervention.evidence_quote.includes(prior[0].text));
  assert.ok(result.intervention.evidence_quote.includes(prior[1].text));
});

test("deep coach rejects changed revisions from a host lookup without replacing bound evidence", async () => {
  const { faux, runtime } = harness();
  const value = boundDeepRequest("deep-stale-search");
  value.context.retrieval_paragraphs = [];
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("search_prior_evidence", { query: "上线条件" }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "原文版本发生变化，不能继续提交。" }), { stopReason: "toolUse" }),
  ]);
  const result = await runtime.evaluate(value, {
    searchEvidence: async () => [{ ...value.context.new_paragraphs[0], text: "改过的另一版本", revision: 2 }],
  });
  assert.equal(result.action, "silent");
  assert.deepEqual(result.metrics.tool_errors, [{ tool: "search_prior_evidence", code: "evidence_revision_changed" }]);
});

test("deep coach cannot spend its second turn on another evidence lookup", async () => {
  const { faux, runtime } = harness();
  const value = boundDeepRequest("deep-lookup-budget");
  let searches = 0;
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("search_prior_evidence", { query: "发布条件" }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("search_prior_evidence", { query: "另查上线条件" }), { stopReason: "toolUse" }),
  ]);
  await assert.rejects(() => runtime.evaluate(value, {
    searchEvidence: async () => { searches += 1; return []; },
  }), (error) => error.code === "missing_terminal_action"
    && error.metrics.turns === 2
    && Number.isFinite(error.metrics.timings.first_token_at_ms)
    && error.metrics.timings.started_at_ms <= error.metrics.timings.first_token_at_ms
    && error.metrics.timings.first_token_at_ms <= error.metrics.timings.completed_at_ms);
  assert.equal(searches, 1);
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
        say_this: "你最近一次遇到这个问题是什么时候？",
        urgency: "medium",
        confidence: 0.9,
      }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "追问具体场景",
        say_this: "请说明这个问题出现在哪个环节，主要影响是什么？",
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
    "请说明这个问题出现在哪个环节，主要影响是什么？",
  );
  assert.equal(result.metrics.turns, 2);
  assert.equal(result.metrics.tool_errors.length, 1);
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

for (const profile of ["candidate", "full", "deep"]) {
test(`the same Pi session retains history only for full and deep: ${profile}`, async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "First moment is not actionable." }), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "Second moment is not actionable." }), {
      stopReason: "toolUse",
    }),
  ]);

  const makeRequest = profile === "candidate" ? request : profile === "full" ? fullRequest : boundDeepRequest;
  const first = await runtime.evaluate(makeRequest("request-1"));
  const second = await runtime.evaluate(makeRequest("request-2"));

  assert.equal(first.metrics.session_reused, false);
  assert.equal(second.metrics.session_reused, true);
  assert.equal(first.metrics.session_message_count_before, 0);
  if (profile === "candidate") assert.equal(second.metrics.session_message_count_before, 0);
  else assert.ok(second.metrics.session_message_count_before > 0);
  assert.equal(runtime.sessionCount, 1);
});
}

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
  assert.equal(second.metrics.session_message_count_before, 0);
  assert.equal(third.metrics.session_reused, false);
  assert.deepEqual(third.metrics.available_tool_names, ["submit_intervention", "keep_silent"]);
  assert.equal(third.metrics.session_message_count_before, 0);
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
      && error.metrics.provider_connect_ms >= 0
      && error.metrics.timings.clock === "unix_epoch_ms"
      && error.metrics.timings.first_token_at_ms === null
      && error.metrics.timings.completed_at_ms >= error.metrics.timings.started_at_ms,
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

test("gpt-6-sol selects the relay low reasoning effort", () => {
  const backend = createOpenAICompatibleBackend({
    base_url: "https://codexai.club",
    api_key: "test-only-key",
    model: "gpt-6-sol",
    api_style: "responses",
    timeout_ms: 2250,
    decision_timeout_ms: 2250,
  });

  assert.equal(backend.model.reasoning, true);
  assert.equal(backend.model.thinkingLevelMap.off, "low");
});

test("Provider requests cannot exceed the asynchronous deep bridge ceiling", () => {
  const backend = createOpenAICompatibleBackend({
    base_url: "https://provider.example.test/v1",
    api_key: "test-only-key",
    model: "gpt-5.5",
    api_style: "responses",
    timeout_ms: 60_000,
    decision_timeout_ms: 60_000,
  });

  assert.equal(backend.providerTimeoutMs, 25_000);
  assert.equal(backend.decisionTimeoutMs, 25_000);
});

test("deep Answer revision permits evidence lookup through non-streaming Responses", async () => {
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
  assert.equal(requestBody.tool_choice, undefined);
  assert.ok(requestBody.tools.some((tool) => tool.name === "search_prior_evidence"));
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
      assert.match(context.systemPrompt, /表达问题可用两段原话中性帮助/);
      assert.match(context.systemPrompt, /不因身份未知单独拒绝/);
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

for (const profile of ["candidate", "compact", "spark"]) {
  test(`candidate evaluations replace old revisions and tool output: ${profile}`, async () => {
    const { faux, runtime } = harness({ modelId: profile === "spark" ? "gpt-5.3-codex-spark" : undefined });
    const first = request("revision-first");
    first.context.compact_terminal_contract = profile !== "candidate";
    first.context.new_paragraphs[0].text = "旧版本把演示安排在早上。";
    first.context.context_paragraphs = [];
    const second = structuredClone(first);
    second.request_id = "revision-second";
    second.context.state_revision = 8;
    second.context.new_paragraphs[0].revision = 2;
    second.context.new_paragraphs[0].text = "校对后原话是演示安排仍待讨论。";
    second.context.new_paragraphs[0].correction_status = "changed";
    second.context.new_paragraphs[0].evidence_quality = "reviewed";
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "旧轮工具结果，不是本次事实。" }), { stopReason: "toolUse" }),
      (context) => {
        assert.equal(context.messages.filter((item) => item.role === "user").length, 1);
        assert.ok(!JSON.stringify(context.messages).includes("旧版本把演示安排在早上"));
        assert.ok(!JSON.stringify(context.messages).includes("旧轮工具结果"));
        const payload = promptPayload(context);
        const paragraph = (payload.new_paragraphs ?? payload.paragraphs).find((item) => item.id === "remote-1");
        assert.equal(payload.state_revision, 8);
        assert.equal(paragraph.revision, 2);
        assert.equal(paragraph.text, second.context.new_paragraphs[0].text);
        assert.equal(paragraph.correction_status, "changed");
        assert.equal(paragraph.evidence_quality, "reviewed");
        return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "只判断当前校对版本。" }), { stopReason: "toolUse" });
      },
    ]);
    await runtime.evaluate(first);
    const result = await runtime.evaluate(second);
    assert.equal(result.metrics.session_reused, true);
    assert.equal(result.metrics.session_message_count_before, 0);
  });
}

for (const change of ["role", "question", "document", "memory"]) {
  test(`candidate current host input replaces previous ${change}`, async () => {
    const { faux, runtime } = harness();
    const first = request("old-host-input");
    first.context.compact_terminal_contract = true;
    first.context.role_contract = "只帮助当前用户，旧角色标记不可跨作业重放。";
    first.context.rolling_state.current_answer = { question: "旧问题标记", answer: "只供比较。" };
    first.context.user_memory = { terms: [{ canonical: "旧术语标记", aliases: ["old-term"] }], preferences: {} };
    withDocuments(first, [documentSource({ quote: "旧文档标记，仅第一场范围有效。" })]);
    const second = structuredClone(first);
    second.request_id = "new-host-input";
    const oldMarker = { role: "旧角色标记", question: "旧问题标记", document: "旧文档标记", memory: "旧术语标记" }[change];
    if (change === "role") second.context.role_contract = "新角色为中立主持者，不代答。";
    if (change === "question") second.context.rolling_state.current_answer = { question: "新的问题", answer: "当前比较项。" };
    if (change === "document") {
      second.context.document_evidence = [];
      second.context.knowledge_context.context_revision = 3;
    }
    if (change === "memory") second.context.user_memory = { terms: [], preferences: {} };
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "第一轮不介入。" }), { stopReason: "toolUse" }),
      (context) => {
        assert.ok(!(context.systemPrompt + JSON.stringify(context.messages)).includes(oldMarker));
        assert.equal(context.messages.filter((item) => item.role === "user").length, 1);
        const payload = promptPayload(context);
        if (change === "role") assert.ok(context.systemPrompt.includes(second.context.role_contract));
        if (change === "question") assert.equal(payload.rolling_state.current_answer.question, "新的问题");
        if (change === "document") assert.equal(payload.document_evidence, undefined);
        if (change === "memory") assert.equal(payload.user_memory, undefined);
        return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "当前输入没有新增介入价值。" }), { stopReason: "toolUse" });
      },
    ]);
    await runtime.evaluate(first);
    const result = await runtime.evaluate(second);
    assert.equal(result.metrics.session_message_count_before, 0);
  });
}

test("candidate scoped current evidence keeps revisions and recent resolution", async () => {
  const { faux, runtime } = harness();
  const value = request("cross-batch-current");
  value.context.compact_terminal_contract = true;
  value.context.candidate_evidence_paragraphs = [{ id: "cross-batch", text: "原先尚未明确验收条件。", revision: 4, source_track: "system_audio", correction_status: "changed", evidence_quality: "reviewed" }];
  value.context.candidate_events[0].evidence_segment_ids = ["cross-batch", "remote-1"];
  value.context.context_paragraphs = [
    { id: "outside-last-two", text: "更早的话不直接进入本次上下文。", revision: 1 },
    { id: "resolution-1", text: "现在条件已经明确，先核对测试结果。", revision: 3 },
    { id: "resolution-2", text: "测试结果已提供，接下来按确认条件演示。", revision: 5 },
  ];
  faux.setResponses([(context) => {
    const payload = promptPayload(context);
    assert.deepEqual(payload.candidate_evidence_paragraphs.map(({ id, revision, text }) => ({ id, revision, text })), value.context.candidate_evidence_paragraphs.map(({ id, revision, text }) => ({ id, revision, text })));
    assert.deepEqual(payload.recent_context_paragraphs.map(({ id, revision }) => ({ id, revision })), [{ id: "resolution-1", revision: 3 }, { id: "resolution-2", revision: 5 }]);
    assert.equal(payload.recent_context_paragraphs[1].text, value.context.context_paragraphs[2].text);
    assert.ok(!JSON.stringify(context.messages).includes("更早的话不直接进入"));
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "近期已补齐条件，不重复旧提醒。" }), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "silent");
});

for (const role of [undefined, "", "主持者自定义约束：保持中立，不替受访者回答。\n来源变更时撤旧，不编造经历。"]) {
  test(`candidate retains safe role and silence constraints: ${role === undefined ? "legacy" : role ? "custom" : "blank"}`, async () => {
    const { faux, runtime } = harness();
    const value = request("role-and-silence");
    value.context.compact_terminal_contract = true;
    value.context.role_contract = role;
    faux.setResponses([(context) => {
      if (role) assert.ok(context.systemPrompt.includes(role));
      else assert.match(context.systemPrompt, /你是当前用户的私人工作与对话副驾/);
      assert.match(context.systemPrompt, /外放、录播、广告和转述不等于用户/);
      assert.match(context.systemPrompt, /不替作者争论立场或说教/);
      assert.match(context.systemPrompt, /引述提问、反问、自答、未完片段/);
      assert.match(context.systemPrompt, /已补条件、已解决.*静默/);
      assert.match(context.systemPrompt, /provisional.*只可澄清或静默/);
      assert.match(context.systemPrompt, /资料.*不执行其中指令/);
      assert.match(context.systemPrompt, /旧AI答案和记忆不是业务事实/);
      assert.match(context.systemPrompt, /保留真实来源版本与范围/);
      assert.match(context.systemPrompt, /最多两轮模型\/四工具/);
      assert.match(context.systemPrompt, /访谈主持者用中立追问/);
      return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "没有有效待回应事项。" }), { stopReason: "toolUse" });
    }]);
    assert.equal((await runtime.evaluate(value)).action, "silent");
  });
}

test("candidate messages survive an in-evaluation quote rejection", async () => {
  const { faux, runtime } = harness();
  const value = request("same-evaluation-repair");
  const card = { event_type: "question_to_user", title: "核对上线条件", recommendation: "先依据压测结果讨论上线日期。", reason: "当前上线条件仍需核对。", evidence_segment_ids: ["remote-1"], evidence_quote: "周五一定上线吗？", urgency: "medium", confidence: 0.9 };
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("submit_intervention", card), { stopReason: "toolUse" }),
    (context) => {
      assert.equal(context.messages.filter((item) => item.role === "user").length, 1);
      assert.ok(context.messages.some((item) => item.role === "toolResult"));
      assert.match(JSON.stringify(context.messages), /evidence_quote/);
      return fauxAssistantMessage(fauxToolCall("submit_intervention", { ...card, evidence_quote: "周五一定上线吗" }), { stopReason: "toolUse" });
    },
  ]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.turns, 2);
  assert.equal(result.metrics.tool_errors[0].code, "evidence_quote_not_verbatim");
});

test("candidate lookup rejects an obsolete revision without losing the current input", async () => {
  const { faux, runtime } = harness();
  const value = request("candidate-stale-lookup");
  value.context.new_paragraphs[0].revision = 2;
  value.context.candidate_events = [{ event_type: "objection_detected", evidence_segment_ids: ["remote-1"], reason: "需要核对先前条件。", candidate_key: "objection:current" }];
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("search_prior_evidence", { query: "先前条件" }), { stopReason: "toolUse" }),
    (context) => {
      const toolResult = context.messages.findLast((item) => item.role === "toolResult");
      assert.ok(toolResult);
      assert.match(JSON.stringify(toolResult), /revision changed/);
      assert.equal(promptPayload(context).new_paragraphs[0].revision, 2);
      assert.ok(!JSON.stringify(context.messages).includes("旧版本错把日期当承诺"));
      return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "旧版本不能作为当前矛盾依据。" }), { stopReason: "toolUse" });
    },
  ]);
  const result = await runtime.evaluate(value, { searchEvidence: async () => [{ ...value.context.new_paragraphs[0], revision: 1, text: "旧版本错把日期当承诺。" }] });
  assert.equal(result.action, "silent");
  assert.equal(result.metrics.turns, 2);
  assert.equal(result.metrics.tool_errors[0].code, "evidence_revision_changed");
});

test("candidate timeout and late callback cannot enter the next evaluation", async () => {
  const faux = fauxProvider({ provider: `r01-late-${Math.random()}` });
  const models = createModels();
  models.setProvider(faux.provider);
  let evaluations = 0;
  const runtime = new PiCoachRuntime({ backendFactory: () => {
    evaluations += 1;
    return { identity: "r01-late", model: faux.getModel(), decisionTimeoutMs: evaluations === 1 ? 15 : 1000,
      streamFn: evaluations === 1 ? () => {
        const stream = createAssistantMessageEventStream();
        setTimeout(() => {
          const message = fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "过期作业的迟到标记。" }), { stopReason: "toolUse" });
          stream.push({ type: "done", reason: "toolUse", message });
          stream.end(message);
        }, 45);
        return stream;
      } : models.streamSimple.bind(models) };
  } });
  const old = request("timeout-before-new-input");
  old.context.new_paragraphs[0].text = "过期作业的原文标记。";
  await assert.rejects(runtime.evaluate(old), (error) => error.code === "agent_deadline_exceeded");
  faux.setResponses([(context) => {
    assert.equal(context.messages.filter((item) => item.role === "user").length, 1);
    assert.ok(!JSON.stringify(context.messages).includes("过期作业"));
    return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "当前问题无需重复提示。" }), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(request("fresh-after-timeout"));
  assert.equal(result.metrics.session_message_count_before, 0);
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(runtime.sessionCount, 1);
  assert.ok(!JSON.stringify(runtime.sessions.get("meeting-1").agent.state.messages).includes("过期作业"));
});

function r01bReleaseRequest(requestId) {
  const value = request(requestId);
  value.context.compact_terminal_contract = true;
  value.context.new_paragraphs = [{ id: "release-question", text: "发布前还需要检查哪些条件？",
    revision: 1, source_track: "system_audio", role_hint: "remote_mix", correction_status: "no_change",
    evidence_quality: "reviewed" }];
  value.context.context_paragraphs = [{ id: "release-condition", text: "压测还没通过，回滚方案也没有验收。",
    revision: 1, source_track: "microphone", role_hint: "self_or_room", correction_status: "no_change",
    evidence_quality: "reviewed" }];
  value.context.candidate_events = [{ event_type: "question_pending", evidence_segment_ids: ["release-question"],
    reason: "核对当前问题是否仍需回应。", candidate_key: "release-condition:question" }];
  value.context.retrieval_paragraphs = [];
  value.context.semantic_windows = [];
  value.context.rolling_state = { topic: "发布前置条件" };
  value.context.meeting_goal = "明确当前发布的前置条件，不承诺未经确认的结果。";
  return value;
}

function r01bReleaseCard(sayThis) {
  return { event_type: "question_to_user", title: "核对发布条件", say_this: sayThis,
    urgency: "medium", confidence: 0.9 };
}

const R01B_GROUNDED_RELEASE_SENTENCE = "压测通过且回滚方案验收后，再确认发布条件。";

test("R01b compact candidate accepts original release conditions on the first turn", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([(context) => {
    assert.match(context.systemPrompt, /直接列举原文条件，勿添加未引述数量、日期或阈值/);
    assert.equal(promptPayload(context).recent_context_paragraphs[0].text, "压测还没通过，回滚方案也没有验收。");
    return fauxAssistantMessage(fauxToolCall("submit_intervention", r01bReleaseCard(R01B_GROUNDED_RELEASE_SENTENCE)), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(r01bReleaseRequest("r01b-original-release"));
  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.say_this, R01B_GROUNDED_RELEASE_SENTENCE);
  assert.deepEqual(result.intervention.evidence_segment_ids, ["release-question", "release-condition"]);
  assert.equal(result.intervention.evidence_quote, "发布前还需要检查哪些条件？\n压测还没通过，回滚方案也没有验收。");
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
});

test("R01b compact candidate accepts the exact next-step navigation cue", async () => {
  const { faux, runtime } = harness();
  const sentence = `下一步，${R01B_GROUNDED_RELEASE_SENTENCE}`;
  faux.setResponses([fauxAssistantMessage(fauxToolCall("submit_intervention", r01bReleaseCard(sentence)), { stopReason: "toolUse" })]);
  const result = await runtime.evaluate(r01bReleaseRequest("r01b-next-step"));
  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.say_this, sentence);
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
});

for (const [name, sentence] of [
  ["next-step-with-unquoted-repetitions", "下一步，压测复测3次且回滚方案验收后，再确认发布条件。"],
  ["unquoted-derived-count", "这两项都通过后，再确认发布条件。"],
  ["unquoted-date", "明天压测通过且回滚方案验收后，再确认发布条件。"],
  ["unquoted-threshold", "压测通过率达到95%且回滚方案验收后，再确认发布条件。"],
  ["unquoted-single-item", "先确认一项发布条件，再检查回滚方案。"],
  ["unquoted-step-count", "先完成一步验收，再确认发布条件。"],
  ["unquoted-remaining-step", "只剩下一步即可发布。"],
  ["unquoted-residual-step", "余下一步即可发布。"],
]) {
  test(`R01b compact candidate still rejects ${name} and repairs from original conditions`, async () => {
    const { faux, runtime } = harness();
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall("submit_intervention", r01bReleaseCard(sentence)), { stopReason: "toolUse" }),
      (context) => {
        const failure = context.messages.findLast((message) => message.role === "toolResult");
        assert.ok(failure);
        assert.match(JSON.stringify(failure), /unsupported date or number/);
        assert.equal(promptPayload(context).recent_context_paragraphs[0].text, "压测还没通过，回滚方案也没有验收。");
        return fauxAssistantMessage(fauxToolCall("submit_intervention", r01bReleaseCard(R01B_GROUNDED_RELEASE_SENTENCE)), { stopReason: "toolUse" });
      },
    ]);
    const result = await runtime.evaluate(r01bReleaseRequest(`r01b-${name}`));
    assert.equal(result.action, "intervention");
    assert.equal(result.intervention.say_this, R01B_GROUNDED_RELEASE_SENTENCE);
    assert.equal(result.metrics.turns, 2);
    assert.deepEqual(result.metrics.tool_errors, [{ tool: "submit_intervention", code: "ungrounded_discovery" }]);
  });
}

test("R01b next-step navigation preserves the original quoted quantity", async () => {
  const { faux, runtime } = harness();
  const value = r01bReleaseRequest("r01b-quoted-repetitions");
  value.context.context_paragraphs[0].text = "压测还没通过，需要复测3次，回滚方案也没有验收。";
  const sentence = "下一步，压测复测3次且回滚方案验收后，再确认发布条件。";
  faux.setResponses([fauxAssistantMessage(fauxToolCall("submit_intervention", r01bReleaseCard(sentence)), { stopReason: "toolUse" })]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "intervention");
  assert.equal(result.intervention.say_this, sentence);
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
});

test("R01b resolved release conditions replace the old revision and allow silence", async () => {
  const { faux, runtime } = harness();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("submit_intervention", r01bReleaseCard(R01B_GROUNDED_RELEASE_SENTENCE)), { stopReason: "toolUse" }),
    (context) => {
      const payload = promptPayload(context);
      assert.equal(payload.state_revision, 8);
      assert.equal(payload.recent_context_paragraphs[0].revision, 2);
      assert.equal(payload.recent_context_paragraphs[0].text, "压测通过、回滚方案验收均已核对，这个问题已经回答，继续其他议题。");
      assert.ok(!JSON.stringify(context.messages).includes("压测还没通过"));
      assert.match(context.systemPrompt, /已补条件、已解决.*静默/);
      return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "发布前置条件已核对，当前无需重复提醒。" }), { stopReason: "toolUse" });
    },
  ]);
  const value = r01bReleaseRequest("r01b-before-resolution");
  assert.equal((await runtime.evaluate(value)).action, "intervention");
  value.request_id = "r01b-after-resolution";
  value.context.state_revision = 8;
  value.context.context_paragraphs[0].revision = 2;
  value.context.context_paragraphs[0].text = "压测通过、回滚方案验收均已核对，这个问题已经回答，继续其他议题。";
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "silent");
  assert.equal(result.intervention, null);
  assert.equal(result.metrics.turns, 1);
  assert.equal(result.metrics.session_message_count_before, 0);
  assert.deepEqual(result.metrics.tool_errors, []);
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
