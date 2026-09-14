import { parseMeetingSnapshot } from "./schema";

function formalMetadata(segmentId: string) {
  return {
    source: "llm_first",
    job_id: `job-${segmentId}`,
    batch_id: "batch-1",
    provider: "test-provider",
    model: "test-model",
    llm_called: true,
    formal_evidence: {
      segment_ids: [segmentId],
      quote: `quote-${segmentId}`,
    },
  };
}

it("parses bounded coach and recent-context history from a snapshot", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-1",
    last_seq: 4,
    segments: [],
    suggestions: [],
    coach_history: [{
      history_id: "coach-event-1",
      created_at_ms: 1_000,
      question: "请先确认回滚负责人。",
      say_this: "请先确认回滚负责人。",
      reason: "负责人还没有明确。",
      why_now: "负责人还没有明确。",
      evidence_segment_ids: ["segment-1"],
      evidence_quote: "负责人还没有定",
      urgency: "high",
      coach_event_type: "discovery_gap",
      provenance_version: "realtime_coach_provenance.v1",
      origin: "pi",
      status: "intervention",
      status_reason: "intervention_submitted",
      decision_reason: "当前证据显示需要先确认责任人。",
      run_id: "coach-run-1",
      decision_id: "coach-decision-1",
      evidence_revision: "coach-evidence:4:abc123",
      valid_until_ms: 9_000,
      lifecycle_action: "retain",
      supersedes_decision_id: null,
      superseded_by: null,
      ...formalMetadata("segment-1"),
    }],
    recent_context_history: [{
      context_id: "topic-event-1",
      kind: "topic",
      title: "发布方案",
      summary: "正在确认发布窗口和回滚安排。",
      updated_at_ms: 2_000,
      evidence_segment_ids: ["segment-2"],
      ...formalMetadata("segment-2"),
    }],
  });

  expect(snapshot.coachHistory[0]).toMatchObject({
    historyId: "coach-event-1",
    createdAtMs: 1_000,
    coachEventType: "discovery_gap",
    origin: "pi",
    status: "intervention",
    runId: "coach-run-1",
    decisionId: "coach-decision-1",
    sayThis: "请先确认回滚负责人。",
    whyNow: "负责人还没有明确。",
    evidenceRevision: "coach-evidence:4:abc123",
    validUntil: 9_000,
    lifecycleAction: "retain",
    supersedesDecisionId: null,
    supersededBy: null,
    formalAi: { source: "llm_first", jobId: "job-segment-1" },
  });
  expect(snapshot.recentContextHistory[0]).toMatchObject({
    contextId: "topic-event-1",
    kind: "topic",
    title: "发布方案",
    summary: "正在确认发布窗口和回滚安排。",
    evidenceSegmentIds: ["segment-2"],
  });
});

it("keeps semantic follow-up and coach decision lanes separate", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-1",
    last_seq: 8,
    segments: [],
    suggestions: [],
    semantic_follow_up: {
      question: "可以补一个真实使用场景吗？",
      reason: "普通语义追问，不应冒充 Pi 介入。",
      evidence_segment_ids: ["segment-8"],
      evidence_quote: "想再了解场景",
      urgency: "medium",
      ...formalMetadata("segment-8"),
    },
    coach_intervention: null,
    coach_decision: {
      provenance_version: "realtime_coach_provenance.v1",
      origin: "pi",
      status: "protected_silent",
      status_reason: "no_actionable_intervention",
      decision_reason: "本轮保持静默。",
      run_id: "coach-run-8",
      decision_id: "coach-decision-8",
      evidence_revision: "coach-evidence:8:xyz",
      valid_until_ms: 20_000,
      lifecycle_action: "deprioritize",
      supersedes_decision_id: "coach-decision-7",
      superseded_by: "coach-decision-9",
      agent_metrics: {
        decision_latency_ms: 2_450,
        ttft_ms: 920,
        within_latency_budget: true,
        coach_skill_id: "general",
        coach_skill_version: 1,
        tool_names: ["submit_intervention"],
        timings: { clock: "unix_epoch_ms", started_at_ms: 1_000, completed_at_ms: 3_450 },
        usage: { prompt_tokens: 80, completion_tokens: 20, total_tokens: 100 },
        api_key: "must-not-enter-typed-state",
      },
    },
  });

  expect(snapshot.followUp).toBeNull();
  expect(snapshot.semanticFollowUp).toMatchObject({
    question: "可以补一个真实使用场景吗？",
    formalAi: { source: "llm_first" },
  });
  expect(snapshot.coachDecision).toMatchObject({
    origin: "pi",
    status: "protected_silent",
    decisionId: "coach-decision-8",
    validUntil: 20_000,
    lifecycleAction: "deprioritize",
    supersedesDecisionId: "coach-decision-7",
    supersededBy: "coach-decision-9",
    agentMetrics: {
      decisionLatencyMs: 2_450,
      ttftMs: 920,
      withinLatencyBudget: true,
      coachSkillId: "general",
      coachSkillVersion: 1,
      toolNames: ["submit_intervention"],
      timings: { clock: "unix_epoch_ms", startedAtMs: 1_000, completedAtMs: 3_450 },
      usage: { promptTokens: 80, completionTokens: 20, totalTokens: 100 },
    },
  });
  expect(snapshot.coachDecision?.agentMetrics).not.toHaveProperty("apiKey");
});

it("accepts canonical coach aliases when legacy card names are absent", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-canonical-card",
    last_seq: 1,
    segments: [],
    suggestions: [],
    coach_history: [{
      history_id: "coach-canonical-1",
      created_at_ms: 1_000,
      say_this: "我先确认验收条件，再给出日期。",
      why_now: "现在需要避免在条件不清时作出承诺。",
      evidence_segment_ids: ["segment-1"],
      evidence_quote: "验收条件还没定",
      urgency: "high",
      coach_event_type: "commitment_risk",
      ...formalMetadata("segment-1"),
    }],
  });

  expect(snapshot.coachHistory[0]).toMatchObject({
    question: "我先确认验收条件，再给出日期。",
    reason: "现在需要避免在条件不清时作出承诺。",
    sayThis: "我先确认验收条件，再给出日期。",
    whyNow: "现在需要避免在条件不清时作出承诺。",
  });
});

it("preserves the soft-cutoff audit envelope without creating a follow-up card", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-soft-cutoff",
    last_seq: 3,
    segments: [],
    suggestions: [],
    coach_intervention: null,
    coach_decision: {
      origin: "pi",
      status: "timed_out",
      status_reason: "soft_deadline_exceeded",
      delivery_status: "too_late",
      soft_deadline_at_ms: 3_500,
      soft_cutoff_elapsed_ms: 3_000,
      soft_timeout_projection_at_ms: 4_000,
      soft_cutoff_triggered: true,
      late_result_discarded: true,
      agent_metrics: {
        soft_deadline_at_ms: 3_500,
        soft_cutoff_elapsed_ms: 3_000,
        soft_cutoff_triggered: true,
        late_result_discarded: true,
      },
    },
  });

  expect(snapshot.followUp).toBeNull();
  expect(snapshot.coachDecision).toMatchObject({
    status: "timed_out",
    statusReason: "soft_deadline_exceeded",
    deliveryStatus: "too_late",
    softDeadlineAtMs: 3_500,
    softCutoffElapsedMs: 3_000,
    softTimeoutProjectionAtMs: 4_000,
    softCutoffTriggered: true,
    lateResultDiscarded: true,
    agentMetrics: {
      softDeadlineAtMs: 3_500,
      softCutoffElapsedMs: 3_000,
      softCutoffTriggered: true,
      lateResultDiscarded: true,
    },
  });
});

it("parses local-reflex provenance without attributing it to formal AI or Pi", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-local-reflex",
    last_seq: 5,
    segments: [],
    suggestions: [],
    coach_intervention: {
      origin: "local_reflex",
      local_reflex_kind: "communication_clarity",
      status: "intervention",
      coach_event_type: "communication_clarity",
      say_this: "我先收束一下：当前只确认目标，细节稍后逐项核对。",
      why_now: "同一观点已经连续重复，需要先收束表达。",
      evidence_segment_ids: ["segment-5"],
      evidence_quote: "我们先把这个目标说清楚，我再重复一下",
      urgency: "medium",
      valid_until_ms: 35_000,
      lifecycle_action: "retain",
      decision_id: "local-reflex-decision-5",
    },
    coach_decision: {
      origin: "local_reflex",
      status: "intervention",
      decision_id: "local-reflex-decision-5",
      valid_until_ms: 35_000,
      lifecycle_action: "retain",
    },
    coach_history: [{
      history_id: "local-reflex-history-5",
      created_at_ms: 5_000,
      origin: "local_reflex",
      local_reflex_kind: "communication_clarity",
      status: "intervention",
      coach_event_type: "communication_clarity",
      say_this: "我先收束一下：当前只确认目标，细节稍后逐项核对。",
      why_now: "同一观点已经连续重复，需要先收束表达。",
      evidence_segment_ids: ["segment-5"],
      evidence_quote: "我们先把这个目标说清楚，我再重复一下",
      urgency: "medium",
      valid_until_ms: 35_000,
      lifecycle_action: "retain",
      decision_id: "local-reflex-decision-5",
    }],
  });

  expect(snapshot.followUp).toMatchObject({
    origin: "local_reflex",
    localReflexKind: "communication_clarity",
    status: "intervention",
    coachEventType: "communication_clarity",
    sayThis: "我先收束一下：当前只确认目标，细节稍后逐项核对。",
    whyNow: "同一观点已经连续重复，需要先收束表达。",
    validUntil: 35_000,
  });
  expect(snapshot.followUp?.formalAi).toBeNull();
  expect(snapshot.coachDecision).toMatchObject({
    origin: "local_reflex",
    status: "intervention",
    decisionId: "local-reflex-decision-5",
  });
  expect(snapshot.coachHistory[0]).toMatchObject({
    historyId: "local-reflex-history-5",
    origin: "local_reflex",
    decisionId: "local-reflex-decision-5",
  });
  expect(snapshot.coachHistory[0].formalAi).toBeNull();
});

it("hydrates a trusted local-reflex card from the production legacy follow-up lane", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-local-reflex-production-shape",
    last_seq: 5,
    segments: [],
    suggestions: [],
    follow_up: {
      origin: "local_reflex",
      local_reflex_kind: "missing_next_step",
      status: "intervention",
      coach_event_type: "execution_gap",
      say_this: "先确认一下：下一步是什么、谁来负责、什么时候回看？",
      why_now: "对话正在收尾，但还没有明确下一步。",
      evidence_segment_ids: ["segment-close"],
      evidence_quote: "我们已经把方案讨论完了今天先到这",
      urgency: "high",
      valid_until_ms: 100_000,
      lifecycle_action: "retain",
      decision_id: "local-reflex-decision-close",
    },
    // The current backend snapshot keeps the canonical lane explicit while
    // persisting a local reflex in the legacy follow_up field.
    coach_intervention: null,
    semantic_follow_up: null,
    coach_decision: {
      origin: "local_reflex",
      status: "intervention",
      decision_id: "local-reflex-decision-close",
      valid_until_ms: 100_000,
      lifecycle_action: "retain",
    },
    coach_history: [],
  });

  expect(snapshot.followUp).toMatchObject({
    origin: "local_reflex",
    localReflexKind: "missing_next_step",
    status: "intervention",
    coachEventType: "execution_gap",
    lifecycleAction: "retain",
    decisionId: "local-reflex-decision-close",
    validUntil: 100_000,
  });
  expect(snapshot.coachDecision).toMatchObject({
    origin: "local_reflex",
    status: "intervention",
    decisionId: "local-reflex-decision-close",
  });
});

it("does not let a non-local legacy follow-up bypass explicit provenance lanes", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-untrusted-legacy-follow-up",
    last_seq: 6,
    segments: [],
    suggestions: [],
    follow_up: {
      origin: "pi",
      status: "intervention",
      coach_event_type: "commitment_risk",
      question: "这条旧字段建议不应绕过新通道。",
      reason: "它缺少可验证的正式来源。",
      evidence_segment_ids: ["segment-untrusted"],
      evidence_quote: "请直接给出日期",
      valid_until_ms: 100_000,
      lifecycle_action: "retain",
    },
    coach_intervention: null,
    semantic_follow_up: null,
    coach_decision: {
      origin: "pi",
      status: "intervention",
      decision_id: "untrusted-legacy-decision",
      valid_until_ms: 100_000,
      lifecycle_action: "retain",
    },
  });

  expect(snapshot.followUp).toBeNull();
  expect(snapshot.coachDecision).toMatchObject({
    origin: "pi",
    status: "intervention",
  });
});

it("keeps a pending-question local reflex visible after a Pi timeout", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-pending-question",
    last_seq: 9,
    segments: [],
    suggestions: [],
    coach_history: [{
      history_id: "local-question-1",
      created_at_ms: 9_000,
      question: "先直接回应这个问题，并说明当前结论。",
      say_this: "先直接回应这个问题，并说明当前结论。",
      reason: "原文包含待回应的问题，先给出明确回应。",
      why_now: "原文包含待回应的问题，先给出明确回应。",
      evidence_segment_ids: ["segment-question"],
      evidence_quote: "为什么年轻人不愿意干这个？",
      urgency: "high",
      coach_event_type: "question_to_user",
      local_reflex_kind: "pending_question",
      origin: "local_reflex",
      status: "intervention",
      valid_until_ms: 20_000,
      lifecycle_action: "retain",
    }],
  });

  expect(snapshot.coachHistory[0]).toMatchObject({
    origin: "local_reflex",
    localReflexKind: "pending_question",
    coachEventType: "question_to_user",
    status: "intervention",
  });
});

it.each([
  ["missing_next_step", "execution_gap"],
  ["communication_clarity", "communication_clarity"],
  ["strong_objection", "discovery_gap"],
] as const)("parses the valid %s/%s local reflex in snapshot and history", (localReflexKind, coachEventType) => {
  const intervention = {
    origin: "local_reflex",
    local_reflex_kind: localReflexKind,
    status: "intervention",
    coach_event_type: coachEventType,
    say_this: "请先确认当前需要补充的信息。",
    why_now: "原话触发了严格的本地规则。",
    evidence_segment_ids: ["segment-local"],
    evidence_quote: "就这样，先到这里",
    urgency: "medium",
    valid_until_ms: 35_000,
    decision_id: `decision-${localReflexKind}`,
  };
  const snapshot = parseMeetingSnapshot({
    meeting_id: `meeting-${localReflexKind}`,
    last_seq: 6,
    segments: [],
    suggestions: [],
    coach_intervention: intervention,
    coach_decision: {
      origin: "local_reflex",
      status: "intervention",
      decision_id: `decision-${localReflexKind}`,
      valid_until_ms: 35_000,
    },
    coach_history: [{
      ...intervention,
      history_id: `history-${localReflexKind}`,
      created_at_ms: 6_000,
    }],
  });

  expect(snapshot.followUp).toMatchObject({ localReflexKind, coachEventType });
  expect(snapshot.coachHistory[0]).toMatchObject({ localReflexKind, coachEventType });
});

it.each([
  ["missing_next_step", "communication_clarity"],
  ["communication_clarity", "discovery_gap"],
  ["strong_objection", "execution_gap"],
  ["unknown_reflex", "execution_gap"],
])("drops the invalid %s/%s local reflex from snapshot, decision, and history", (localReflexKind, coachEventType) => {
  const invalid = {
    origin: "local_reflex",
    local_reflex_kind: localReflexKind,
    status: "intervention",
    coach_event_type: coachEventType,
    say_this: "不应显示。",
    why_now: "来源合同不匹配。",
    evidence_segment_ids: ["segment-local"],
    evidence_quote: "测试原话",
    urgency: "medium",
    valid_until_ms: 35_000,
    decision_id: "invalid-local-decision",
  };
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-invalid-local",
    last_seq: 7,
    segments: [],
    suggestions: [],
    coach_intervention: invalid,
    coach_decision: {
      origin: "local_reflex",
      status: "intervention",
      decision_id: "invalid-local-decision",
      valid_until_ms: 35_000,
    },
    coach_history: [{ ...invalid, history_id: "invalid-history", created_at_ms: 7_000 }],
  });

  expect(snapshot.followUp).toBeNull();
  expect(snapshot.coachDecision).toBeNull();
  expect(snapshot.coachHistory).toEqual([]);
});
