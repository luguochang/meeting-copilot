import { act, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { parseMeetingSnapshot } from "../../api/schema";
import { createInitialMeetingState, meetingReducer } from "../../domain/reducer";
import type { FollowUpProjection } from "../../domain/events";
import { NowRail } from "./NowRail";

function formalAi(segmentId = "segment-1") {
  return {
    source: "llm_first" as const,
    jobId: `job-${segmentId}`,
    batchId: "batch-1",
    provider: "test-provider",
    model: "test-model",
    llmCalled: true as const,
    evidence: {
      segmentIds: [segmentId],
      quote: "原文依据",
      evidenceHash: null,
      stateRevision: null,
    },
  };
}

function followUp(overrides: Partial<FollowUpProjection> = {}): FollowUpProjection {
  return {
    question: "先确认这次承诺的验收条件。",
    reason: "当前承诺还没有可核验的条件。",
    evidenceSegmentIds: ["segment-1"],
    evidenceQuote: "周五上线",
    urgency: "high",
    coachEventType: "commitment_risk",
    formalAi: formalAi(),
    ...overrides,
  };
}

function renderRail(nextFollowUp: FollowUpProjection | null) {
  const props: ComponentProps<typeof NowRail> = {
    currentTopic: null,
    followUp: nextFollowUp,
    coachHistory: [],
    openQuestions: [],
    suggestions: [],
    decisionCandidates: [],
    actionItems: [],
    risks: [],
    coachRuntime: {
      state: "active",
      label: "Pi 教练监听中",
      level: null,
      detail: null,
      decision: null,
    },
    onEvidence: vi.fn(),
    onFeedback: vi.fn(async () => undefined),
    onFactStatus: vi.fn(async () => undefined),
    onMessage: vi.fn(),
  };
  return render(<NowRail {...props} />);
}

it("shows the provenance badge on a current Pi intervention", () => {
  renderRail(followUp({
    origin: "pi",
    status: "intervention",
    runId: "coach-run-1",
    decisionId: "coach-decision-1",
    evidenceRevision: "coach-evidence:1:abc",
    validUntil: Date.now() + 60_000,
  }));

  expect(screen.getByTestId("follow-up-card")).toBeVisible();
  expect(screen.getByText("Pi Agent")).toBeVisible();
  expect(screen.queryByTestId("semantic-follow-up-card")).toBeNull();
});

it("shows a local clarity reflex without labeling it as Pi or formal AI", () => {
  renderRail(followUp({
    origin: "local_reflex",
    localReflexKind: "communication_clarity",
    status: "intervention",
    coachEventType: "communication_clarity",
    sayThis: "我先收束一下：当前只确认目标，细节稍后逐项核对。",
    whyNow: "同一观点已经连续重复，需要先收束表达。",
    evidenceQuote: "这个目标我再重复一下，我们先把目标说清楚",
    validUntil: Date.now() + 30_000,
    formalAi: null,
  }));

  const card = screen.getByTestId("follow-up-card");
  expect(card).toBeVisible();
  expect(card).toHaveTextContent("本地实时提示");
  expect(card).toHaveTextContent("表达需要收束");
  expect(card).toHaveTextContent("我先收束一下：当前只确认目标，细节稍后逐项核对。");
  expect(screen.queryByText("Pi Agent")).toBeNull();
});

it("keeps a retained local reflex current until its validity boundary, then moves it to history", async () => {
  vi.useFakeTimers();
  try {
    const nowMs = 100_000;
    vi.setSystemTime(nowMs);
    const rawIntervention = {
      origin: "local_reflex",
      local_reflex_kind: "missing_next_step",
      status: "intervention",
      lifecycle_action: "retain",
      coach_event_type: "execution_gap",
      decision_id: "local-reflex-decision-close",
      title: "不应显示的动态标题",
      say_this: "先确认一下：下一步是什么、谁来负责、什么时候回看？",
      why_now: "对话正在收尾，但还没有明确下一步。",
      evidence_segment_ids: ["segment-close"],
      evidence_quote: "我们已经把方案讨论完了今天先到这",
      urgency: "high",
      valid_until_ms: nowMs + 1_000,
    };
    const snapshot = parseMeetingSnapshot({
      meeting_id: "meeting-local-reflex-boundary",
      last_seq: 5,
      segments: [],
      suggestions: [],
      follow_up: rawIntervention,
      coach_intervention: null,
      semantic_follow_up: null,
      coach_decision: {
        origin: "local_reflex",
        status: "intervention",
        lifecycle_action: "retain",
        decision_id: "local-reflex-decision-close",
        valid_until_ms: nowMs + 1_000,
      },
      coach_history: [{
        ...rawIntervention,
        history_id: "local-reflex-history-close",
        created_at_ms: nowMs - 1_000,
      }],
      runtime: { phase: "live" },
    });
    const state = meetingReducer(createInitialMeetingState(snapshot.meetingId), {
      type: "snapshot.received",
      snapshot,
      receivedAtMs: nowMs,
    });

    render(
      <NowRail
        currentTopic={state.currentTopic}
        followUp={state.followUp}
        coachDecision={state.coachDecision}
        coachHistory={state.coachHistory}
        openQuestions={[]}
        suggestions={[]}
        decisionCandidates={[]}
        actionItems={[]}
        risks={[]}
        onEvidence={vi.fn()}
        onFeedback={vi.fn(async () => undefined)}
        onFactStatus={vi.fn(async () => undefined)}
        onMessage={vi.fn()}
      />,
    );

    const currentCard = screen.getByTestId("follow-up-card");
    expect(currentCard).toHaveTextContent("收尾前明确下一步");
    expect(currentCard).toHaveTextContent("本地实时提示");
    expect(screen.queryByRole("list", { name: "过去的教练建议" })).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_025);
    });

    expect(screen.queryByTestId("follow-up-card")).toBeNull();
    expect(screen.getByText("建议已过期")).toBeVisible();
    const history = screen.getByRole("list", { name: "过去的教练建议" });
    expect(history).toHaveTextContent("收尾前明确下一步");
    expect(history).toHaveTextContent("本地实时提示");
    expect(history).toHaveTextContent("已过期");
  } finally {
    vi.useRealTimers();
  }
});

it("keeps a deprioritized intervention out of the current card and in history", () => {
  const deprioritized = followUp({
    origin: "pi",
    status: "intervention",
    lifecycleAction: "deprioritize",
    supersededBy: "coach-decision-next",
    decisionId: "coach-decision-prior",
    validUntil: Date.now() + 60_000,
  });

  render(
    <NowRail
      currentTopic={null}
      followUp={deprioritized}
      coachDecision={{
        origin: "pi",
        status: "intervention",
        lifecycleAction: "deprioritize",
        decisionId: "coach-decision-prior",
        supersededBy: "coach-decision-next",
        decisionReason: "已有更新的高优先级建议。",
      }}
      coachHistory={[{
        ...deprioritized,
        historyId: "coach-history-prior",
        createdAtMs: Date.now() - 5_000,
      }]}
      openQuestions={[]}
      suggestions={[]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn(async () => undefined)}
      onFactStatus={vi.fn(async () => undefined)}
      onMessage={vi.fn()}
    />,
  );

  expect(screen.queryByTestId("follow-up-card")).toBeNull();
  expect(screen.getByText("建议已替换")).toBeVisible();
  expect(screen.getByText("已有更新的高优先级建议。")).toBeVisible();
  const history = screen.getByRole("list", { name: "过去的教练建议" });
  expect(history).toHaveTextContent("已替换");
});

it.each([
  ["missing_next_step", "execution_gap", "收尾前明确下一步"],
  ["communication_clarity", "communication_clarity", "表达需要收束"],
  ["strong_objection", "discovery_gap", "先澄清反对条件"],
] as const)("shows the %s local reflex with its semantic title", (localReflexKind, coachEventType, title) => {
  renderRail(followUp({
    origin: "local_reflex",
    localReflexKind,
    status: "intervention",
    coachEventType,
    title: "Pi 生成的标题",
    sayThis: "请先确认当前需要补充的信息。",
    whyNow: "原话触发了严格的本地规则。",
    evidenceQuote: "就这样，先到这里",
    validUntil: Date.now() + 30_000,
    formalAi: null,
  }));

  const card = screen.getByTestId("follow-up-card");
  expect(card).toHaveTextContent("本地实时提示");
  expect(card).toHaveTextContent(title);
  expect(card).not.toHaveTextContent("Pi 生成的标题");
  expect(screen.queryByText("Pi Agent")).toBeNull();
});

it.each([
  [undefined, "execution_gap"],
  ["missing_next_step", "communication_clarity"],
  ["communication_clarity", "discovery_gap"],
  ["strong_objection", "execution_gap"],
] as const)("rejects an unknown or mismatched local reflex (%s/%s)", (localReflexKind, coachEventType) => {
  renderRail(followUp({
    origin: "local_reflex",
    localReflexKind,
    status: "intervention",
    coachEventType,
    sayThis: "不应显示。",
    whyNow: "来源合同不匹配。",
    evidenceQuote: "测试原话",
    validUntil: Date.now() + 30_000,
    formalAi: null,
  }));

  expect(screen.queryByTestId("follow-up-card")).toBeNull();
  expect(screen.queryByText("本地实时提示")).toBeNull();
});

it("does not render an untrusted local-reflex card outside communication clarity", () => {
  renderRail(followUp({
    origin: "local_reflex",
    status: "intervention",
    coachEventType: "commitment_risk",
    validUntil: Date.now() + 30_000,
    formalAi: null,
  }));

  expect(screen.queryByTestId("follow-up-card")).toBeNull();
  expect(screen.queryByText("本地实时提示")).toBeNull();
});

it("renders the explicit coach card contract and validity window", () => {
  renderRail(followUp({
    origin: "pi",
    status: "intervention",
    whyNow: "对方刚要求日期，但验收条件还没有说清。",
    sayThis: "我可以先给目标日期，但需要先确认验收条件。",
    evidenceQuote: "周五上线，但验收条件还没定",
    validUntil: Date.now() + 25_000,
  }));

  const card = screen.getByTestId("follow-up-card");
  expect(card).toHaveAttribute("data-valid-until-ms");
  expect(card).toHaveTextContent("为什么现在");
  expect(card).toHaveTextContent("对方刚要求日期，但验收条件还没有说清。");
  expect(card).toHaveTextContent("建议说");
  expect(card).toHaveTextContent("我可以先给目标日期，但需要先确认验收条件。");
  expect(screen.getByLabelText("依据原话")).toHaveTextContent("周五上线，但验收条件还没定");
  expect(card).toHaveTextContent(/当前窗口剩余 \d+ 秒/);
});

it("renders direct semantic follow-up separately instead of calling it a Pi card", () => {
  renderRail(followUp({
    origin: "direct_intelligence",
    status: undefined,
    coachEventType: undefined,
  }));

  expect(screen.getByTestId("semantic-follow-up-card")).toBeVisible();
  expect(screen.getByText("普通智能追问")).toBeVisible();
  expect(screen.getByText("直连智能")).toBeVisible();
  expect(screen.queryByTestId("follow-up-card")).toBeNull();
});

it("clears silent and expired cards while exposing the decision lifecycle", () => {
  const { rerender } = renderRail(followUp({
    origin: "pi",
    status: "protected_silent",
    decisionReason: "本轮没有高价值、可立即执行的介入建议。",
    decisionId: "coach-decision-silent",
  }));

  expect(screen.getByText("本轮保持静默")).toBeVisible();
  expect(screen.getByText("本轮没有高价值、可立即执行的介入建议。")).toBeVisible();
  expect(screen.queryByTestId("follow-up-card")).toBeNull();
  expect(screen.getByText("Pi Agent")).toBeVisible();

  rerender(
    <NowRail
      currentTopic={null}
      followUp={followUp({
        origin: "pi",
        status: "intervention",
        validUntil: Date.now() - 1,
        decisionReason: "新证据已经让上一条建议失效。",
        decisionId: "coach-decision-expired",
      })}
      coachHistory={[]}
      openQuestions={[]}
      suggestions={[]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn(async () => undefined)}
      onFactStatus={vi.fn(async () => undefined)}
      onMessage={vi.fn()}
    />,
  );
  expect(screen.getByText("建议已过期")).toBeVisible();
  expect(screen.getByText("新证据已经让上一条建议失效。")).toBeVisible();
  expect(screen.queryByTestId("follow-up-card")).toBeNull();
});

it("shows a retract decision without restoring the prior intervention card", () => {
  const prior = {
    ...followUp({
      origin: "pi" as const,
      status: "intervention" as const,
      decisionId: "coach-decision-prior",
      lifecycleAction: "retract" as const,
      supersededBy: "coach-decision-retract",
    }),
    historyId: "coach-history-prior",
    createdAtMs: Date.now() - 5_000,
  };

  render(
    <NowRail
      currentTopic={null}
      followUp={null}
      coachDecision={{
        origin: "pi",
        status: "stale",
        decisionId: "coach-decision-retract",
        decisionReason: "负责人已经确认，旧建议不再成立。",
        lifecycleAction: "retract",
        supersedesDecisionId: "coach-decision-prior",
      }}
      coachHistory={[prior]}
      openQuestions={[]}
      suggestions={[]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn(async () => undefined)}
      onFactStatus={vi.fn(async () => undefined)}
      onMessage={vi.fn()}
    />,
  );

  expect(screen.queryByTestId("follow-up-card")).toBeNull();
  expect(screen.getByText("建议已撤回")).toBeVisible();
  expect(screen.getByText("负责人已经确认，旧建议不再成立。")).toBeVisible();
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent("先限定承诺条件");
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent("已撤回");
  expect(screen.getByRole("button", { name: "先确认这次承诺的验收条件。" })).toBeVisible();
});
