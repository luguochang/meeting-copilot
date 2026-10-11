import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import type { KnowledgeApi } from "../../api/knowledge";
import { afterEach } from "vitest";
import { parseMeetingSnapshot } from "../../api/schema";
import { createInitialMeetingState, meetingReducer } from "../../domain/reducer";
import type { CoachHistoryEntry, FollowUpProjection, MeetingAttachmentSourceEvidence, Suggestion } from "../../domain/events";
import { NowRail } from "./NowRail";

afterEach(() => {
  window.sessionStorage.clear();
});

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

function coachingPackage(headline: string, sayThisAddition: string) {
  return {
    headline,
    questionIntent: "判断选型是否有清晰的适用边界",
    coreJudgement: "当前回答解释了成本，但异常恢复仍需补充",
    whyItMatters: "缺少异常路径会让选型依据显得不完整",
    sayThisAddition,
    missingPoints: ["异常消费后的恢复策略"],
    constraints: ["当前吞吐规模可控"],
    risks: ["重复消费和积压处理尚未说明"],
    nextActions: ["补充迁移条件和验证方式"],
    likelyFollowUps: [{ question: "如何处理失败消费？", answerAngle: "说明重试、幂等和人工介入边界" }],
    evidenceRefs: [{ segmentId: "segment-1", quote: "为什么选择 Redis Stream？" }],
    confidence: 0.88,
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

function readingProps(): ComponentProps<typeof NowRail> {
  return {
    viewStateKey: "reading-flow", currentTopic: null, followUp: null, coachHistory: [],
    suggestions: [], openQuestions: [], decisionCandidates: [], actionItems: [], risks: [],
    onEvidence: vi.fn(), onFeedback: vi.fn(), onFactStatus: vi.fn(), onMessage: vi.fn(),
  };
}

function readingAnswer(index: number): Suggestion {
  return {
    suggestionId: `answer-${index}`, meetingId: "reading-flow", jobId: `job-${index}`,
    generationId: `generation-${index}`, kind: "answer", questionText: `问题 ${index}`,
    evidenceSegmentId: `segment-${index}`, evidenceTranscriptSeq: index, evidenceHash: `hash-${index}`,
    stateRevision: index, status: "committed", draftText: "", draftSeq: 1,
    text: `回答 ${index}`, finalDraftSeq: 1, feedback: null,
    createdAtMs: index * 1000, updatedAtMs: index * 1000, committedAtMs: index * 1000,
  };
}

function participationRevision(id: string, overrides: Partial<CoachHistoryEntry> = {}): CoachHistoryEntry {
  return { ...followUp({ origin: "pi", status: "intervention", analysisKind: "participation", promptProfile: "participation", proposalType: "professional_proposal", topicId: "orders", analysisBasisRevision: 3,
    decisionId: id, sayThis: `建议正文 ${id}`, supportingFacts: [{ text: `条件 ${id}`, sourceRef: "segment-1" }], formalAi: formalAi(id) }), historyId: `history-${id}`, createdAtMs: id === "first" ? 1000 : 2000, ...overrides };
}

it("prioritizes participation actions and readable advice while preserving expandable diagnostics and history", async () => {
  const user = userEvent.setup();
  const p = readingProps();
  const onOpenAsk = vi.fn();
  const advice = participationRevision("current", { whyNow: "还没有讨论通知失败后的补偿。" });
  const past = participationRevision("past", { status: "stale", lifecycleStatus: "retracted" });
  render(<NowRail {...p} followUp={advice} coachHistory={[advice, past]} onOpenAsk={onOpenAsk}
    onContribute={vi.fn()} discussionRuntime={{ state: "error", label: "理解失败", level: null,
      detail: null, decision: "failed", errorClass: "provider_unavailable" }} />);
  expect(screen.getByRole("group", { name: "请求现场建议" })).toBeVisible();
  for (const label of ["我可以问什么", "还有什么方案", "帮我组织回应"]) {
    expect(screen.getByRole("button", { name: label })).toBeEnabled();
  }
  expect(screen.getByTestId("follow-up-card")).toHaveTextContent(advice.sayThis!);
  expect(screen.getByText(advice.sayThis!)).toBeVisible();
  expect(screen.getByText(advice.whyNow!, { selector: "p" })).not.toBeVisible();
  expect(screen.getByText("部分分析未完成，可展开核对")).toBeVisible();
  expect(screen.getByRole("list", { name: "过去的教练建议" })).not.toBeVisible();
  await user.click(screen.getByText("运行详情"));
  expect(screen.getByRole("status", { name: "当前讨论的理解状态" })).toHaveTextContent("模型服务暂不可用");
  await user.click(screen.getByText("为什么现在 · 依据原话"));
  expect(screen.getByText(advice.whyNow!, { selector: "p" })).toBeVisible();
  await user.click(screen.getByText("过去建议"));
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent("已撤回");
  await user.click(screen.getByRole("button", { name: "查看依据" }));
  expect(p.onEvidence).toHaveBeenCalledWith("segment-1");
  await user.click(screen.getByRole("button", { name: "自己提问" }));
  expect(onOpenAsk).toHaveBeenCalledOnce();
});

it("keeps the frozen discussion time through later transcripts, refresh and history", () => {
  const meetingId = "frozen-discussion";
  const frozenAtMs = new Date(2026, 9, 11, 13, 24, 35).getTime();
  const completedAtMs = frozenAtMs + 20_000;
  const provenance = { source: "llm_first", job_id: "job-frozen", batch_id: "batch-frozen",
    provider: "test-provider", model: "test-model", llm_called: true, llm_call_status: "called",
    evidence: { segment_ids: ["segment-1"], quote: "验收条件还没定" } };
  const intervention = { ...provenance, origin: "pi", status: "intervention", decision_id: "decision-frozen",
    say_this: "先明确这次上线的验收条件。", why_now: "验收条件还没有确定。",
    evidence_segment_ids: ["segment-1"], evidence_quote: "验收条件还没定", frozen_at_ms: frozenAtMs };
  const decision = { origin: "pi", status: "intervention", decision_id: "decision-frozen",
    frozen_at_ms: frozenAtMs, completed_at_ms: completedAtMs };
  const snapshotData = { meeting_id: meetingId, last_seq: 0, segments: [], suggestions: [], runtime: { phase: "live" } };
  let state = meetingReducer(createInitialMeetingState(meetingId), { type: "snapshot.received",
    snapshot: parseMeetingSnapshot(snapshotData), receivedAtMs: completedAtMs });
  state = meetingReducer(state, { type: "events.received", receivedAtMs: completedAtMs, events: [{
    meetingId, seq: 1, eventId: "event-frozen", type: "meeting.intelligence.applied",
    aggregateType: "meeting", aggregateId: meetingId, occurredAtMs: completedAtMs,
    correlationId: null, causationId: null, idempotencyKey: "frozen", publishedAtMs: null,
    payload: { ...provenance, coach_intervention: intervention, coach_decision: decision },
  }] });
  expect(state.followUp?.frozenAtMs).toBe(frozenAtMs);
  expect(state.coachDecision?.frozenAtMs).toBe(frozenAtMs);
  const props = { ...readingProps(), viewStateKey: meetingId, sourceMeetingId: meetingId };
  const view = render(<NowRail {...props} followUp={state.followUp} coachDecision={state.coachDecision} coachHistory={state.coachHistory} />);
  const label = "基于截至 13:24:35 的讨论";
  expect(screen.getByTestId("follow-up-card")).toHaveTextContent(label);
  state = meetingReducer(state, { type: "events.received", receivedAtMs: completedAtMs + 10_000, events: [{
    meetingId, seq: 2, eventId: "later-transcript", type: "transcript.segment.finalized",
    aggregateType: "transcript_segment", aggregateId: "segment-later", occurredAtMs: completedAtMs + 10_000,
    correlationId: null, causationId: null, idempotencyKey: "later", publishedAtMs: null,
    payload: { meeting_id: meetingId, segment_id: "segment-later", final_id: "later", transcript_seq: 2,
      text: "新增讨论", normalized_text: "新增讨论", revision: 1 },
  }] });
  view.rerender(<NowRail {...props} followUp={state.followUp} coachDecision={state.coachDecision} coachHistory={state.coachHistory} />);
  expect(screen.getByTestId("follow-up-card")).toHaveTextContent(label);
  const refreshed = parseMeetingSnapshot({ ...snapshotData, last_seq: 2, coach_intervention: intervention,
    coach_decision: decision, coach_history: [{ ...intervention, history_id: "event-frozen", created_at_ms: completedAtMs }] });
  expect(refreshed.followUp?.frozenAtMs).toBe(frozenAtMs);
  expect(refreshed.coachDecision?.frozenAtMs).toBe(frozenAtMs);
  view.rerender(<NowRail {...props} followUp={null} coachDecision={null} coachHistory={refreshed.coachHistory} />);
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent(label);
  view.rerender(<NowRail {...props} followUp={followUp()} coachHistory={[]} />);
  expect(screen.queryByText(/基于截至/)).not.toBeInTheDocument();
});

it("shows a new rejected understanding without replacing an older readable participation card", async () => {
  const previous = participationRevision("first");
  const props = readingProps();
  const view = render(<NowRail {...props} followUp={previous} coachHistory={[previous]} coachDecision={{ decisionId: "first", status: "intervention" }}
    coachRuntime={{ state: "idle", label: "Pi 教练监听中", detail: null, level: null }}
    discussionRuntime={{ state: "paused", decision: "failed", errorClass: "evidence_rejected", label: "后台失败", detail: null, level: null }} />);
  expect(screen.getByRole("status", { name: "当前讨论的理解状态" })).toHaveTextContent("未通过依据核对");
  expect(screen.getByTestId("follow-up-card")).toHaveTextContent("建议正文 first");
  expect(screen.queryByText("正在积累讨论内容")).not.toBeInTheDocument();
  expect(screen.queryByText(/正在结合本场讨论分析/)).not.toBeInTheDocument();
  view.rerender(<NowRail {...props} followUp={previous} coachHistory={[previous]} coachDecision={{ decisionId: "first", status: "intervention" }}
    coachRuntime={{ state: "busy", label: "主动请求处理中", detail: null, level: null }}
    discussionRuntime={{ state: "paused", decision: "failed", errorClass: "evidence_rejected", label: "后台失败", detail: null, level: null }} />);
  expect(screen.getByTestId("follow-up-card")).toHaveTextContent("建议正文 first");
  expect(screen.getByRole("status", { name: "当前会议的 Pi 状态" })).toHaveTextContent("正在结合本场讨论分析");
  view.rerender(<NowRail {...props} followUp={null} coachHistory={[{ ...previous, status: "stale", lifecycleAction: "retract" }]}
    coachDecision={{ decisionId: "first:stale", status: "stale", lifecycleAction: "retract", supersedesDecisionId: "first" }}
    discussionRuntime={{ state: "idle", decision: "not_started", label: "新议题等待理解", detail: null, level: null }} />);
  expect(screen.queryByTestId("follow-up-card")).not.toBeInTheDocument();
  expect(screen.getByRole("status", { name: "当前讨论的理解状态" })).not.toHaveTextContent("未通过依据核对");
  expect(props.onFeedback).not.toHaveBeenCalled();
});

it("distinguishes normal suggestion silence from independently rejected understanding", async () => {
  render(<NowRail {...readingProps()} coachDecision={{ status: "protected_silent", decisionReason: "当前议题没有新的贡献机会" }}
    discussionRuntime={{ state: "paused", decision: "failed", errorClass: "invalid_output", label: "失败", detail: null, level: null }} />);
  expect(screen.getByRole("status", { name: "当前讨论的理解状态" })).toHaveTextContent("未通过格式校验");
  expect(screen.getByRole("status", { name: "当前会议的 Pi 状态" })).toHaveTextContent("本轮保持静默");
  await userEvent.setup().click(screen.getByText("运行详情"));
  expect(screen.getByText("当前议题没有新的贡献机会")).toBeVisible();
});

it("hides only generic empty expectations when understanding is waiting, preserving legacy behavior", () => {
  const props = readingProps();
  const view = render(<NowRail {...props} discussionRuntime={{ state: "idle", decision: "retry_wait", label: "等待", detail: null, level: null }} />);
  expect(screen.getByRole("status", { name: "当前讨论的理解状态" })).toHaveTextContent("当前未在执行");
  expect(screen.queryByText("正在积累讨论内容")).not.toBeInTheDocument();
  expect(screen.queryByText(/明确问题会立即生成回应/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "我可以问什么" })).toBeVisible();
  view.rerender(<NowRail {...props} />);
  expect(screen.getByText("可随时主动请求建议")).toBeVisible();
  expect(screen.queryByRole("status", { name: "当前讨论的理解状态" })).not.toBeInTheDocument();
});

it.each([false, true])("holds an expanded participation decision through new results with Fast=%s", async (withFast) => {
  const user = userEvent.setup();
  const props = { ...readingProps(), suggestions: withFast ? [readingAnswer(1)] : [] };
  const first = participationRevision("first");
  const second = participationRevision("second");
  const view = render(<NowRail {...props} followUp={first} coachHistory={[first]} coachDecision={{ decisionId: "first", jobId: "job-first", status: "intervention" }} />);
  await user.click(within(screen.getByTestId("follow-up-card")).getByText("已知条件与待核实假设"));
  view.rerender(<NowRail {...props} followUp={second} coachHistory={[first, second]} coachDecision={{ decisionId: "second", jobId: "job-second", status: "intervention" }} />);
  const held = screen.getByTestId("follow-up-card");
  expect(held).toHaveTextContent("建议正文 first");
  expect(within(held).getByText("条件 first")).toBeVisible();
  expect(held).toHaveAttribute("data-ui-render-job-id", "job-first");
  await user.click(screen.getByRole("button", { name: "有新建议，查看最新" }));
  expect(screen.getByTestId("follow-up-card")).toHaveTextContent("建议正文 second");
  expect(screen.getByTestId("follow-up-card")).toHaveAttribute("data-ui-render-job-id", "job-second");
});

it("retains historical reading after material invalidation and restores it without claiming a new card", async () => {
  const user = userEvent.setup();
  const props = { ...readingProps(), suggestions: [readingAnswer(1)] };
  const first = participationRevision("first");
  const stale = { ...first, status: "stale" as const, lifecycleAction: "retract" as const, lifecycleStatus: "retracted" as const };
  const view = render(<NowRail {...props} followUp={first} coachHistory={[first]} />);
  await user.click(within(screen.getByTestId("follow-up-card")).getByRole("button", { name: "查看依据" }));
  expect(props.onEvidence).toHaveBeenCalledWith("segment-1");
  const staleProps = { ...props, followUp: null, coachHistory: [stale], coachDecision: { decisionId: "retract", status: "stale" as const, lifecycleAction: "retract" as const, supersedesDecisionId: "first" } };
  view.rerender(<NowRail {...staleProps} />);
  expect(screen.getByTestId("historical-contribution-card")).toHaveTextContent("建议正文 first");
  expect(screen.getByText("这条建议已失效，仅保留供核对。")).toBeVisible();
  expect(screen.getByTestId("historical-contribution-card")).not.toHaveAttribute("data-ui-render-card");
  expect(screen.queryByRole("button", { name: "有新建议，查看最新" })).not.toBeInTheDocument();
  view.unmount();
  render(<NowRail {...staleProps} />);
  expect(screen.getByTestId("historical-contribution-card")).toHaveTextContent("建议正文 first");
});

it("does not treat a failed new job as a new contribution while reading", async () => {
  const user = userEvent.setup();
  const first = participationRevision("first");
  const props = readingProps();
  const view = render(<NowRail {...props} followUp={first} coachHistory={[first]} />);
  await user.click(screen.getByText("已知条件与待核实假设"));
  view.rerender(<NowRail {...props} followUp={null} coachHistory={[first]} coachDecision={{ decisionId: "failed", status: "timed_out", jobId: "failed-job" }} />);
  expect(screen.getByTestId("follow-up-card")).toHaveTextContent("建议正文 first");
  expect(screen.getByTestId("follow-up-card")).toHaveAttribute("data-ui-render-job-id", "job-first");
  expect(screen.queryByRole("button", { name: "有新建议，查看最新" })).not.toBeInTheDocument();
});

it("marks a held contribution historical on topic change and keeps expired text for verification", async () => {
  const user = userEvent.setup();
  const first = participationRevision("first");
  const second = participationRevision("second", { topicId: "billing" });
  const props = readingProps();
  const view = render(<NowRail {...props} followUp={first} coachHistory={[first]} />);
  await user.click(screen.getByText("已知条件与待核实假设"));
  view.rerender(<NowRail {...props} followUp={second} coachHistory={[first, second]} />);
  expect(screen.getByTestId("historical-contribution-card")).toHaveTextContent("建议正文 first");
  await user.click(screen.getByRole("button", { name: "有新建议，查看最新" }));
  expect(screen.getByTestId("follow-up-card")).toHaveTextContent("建议正文 second");
  view.unmount();
  vi.useFakeTimers();
  try {
    vi.setSystemTime(100000);
    const expiring = participationRevision("expiring", { validUntil: 101000 });
    render(<NowRail {...props} followUp={expiring} coachHistory={[expiring]} />);
    act(() => screen.getByText("已知条件与待核实假设").focus());
    await act(async () => vi.advanceTimersByTime(1100));
    expect(screen.getByTestId("historical-contribution-card")).toHaveTextContent("建议正文 expiring");
    expect(screen.getByTestId("historical-contribution-card")).not.toHaveAttribute("data-ui-render-card");
  } finally { vi.useRealTimers(); }
});

it.each([false, true])("reviews independent participation history and its exact attachment source with Fast=%s", async (withFast) => {
  const user = userEvent.setup();
  const source: MeetingAttachmentSourceEvidence = { sourceType: "meeting_attachment", sourceId: "material-span", setId: "material-set", meetingId: "reading-flow", attachmentId: "attachment", title: "下单需求", versionId: "attachment-v1", version: 1, contentHash: "attachment-hash", locator: { page: 2 }, quote: "订单成功与通知成功绑定。", contextRevision: 2 };
  const old: CoachHistoryEntry = { ...followUp({ origin: "pi", status: "stale", analysisKind: "participation", proposalType: "professional_proposal", promptProfile: "participation", lifecycleAction: "retract", title: "确认通知失败边界", question: "通知失败如何影响下单？", sayThis: "建议将下单与通知的失败边界拆开确认。", supportingFacts: [{ text: "原需求把订单与通知成功绑定", sourceRef: source.sourceId }], assumptions: ["通知渠道可能暂时不可用"], sourceEvidence: [source] }), historyId: "participation-history", createdAtMs: 1000 };
  const failed: CoachHistoryEntry = { ...followUp({ origin: "pi", status: "timed_out", question: "失败轮不能算新建议" }), historyId: "failed-round", createdAtMs: 2000 };
  const historicalSource = { ...source, sourceState: "stale" as const, staleReason: "attachment_scope_or_version_changed", ordinal: 3 };
  const knowledgeApi = { getMeetingAttachmentSource: vi.fn().mockResolvedValue(historicalSource) } as unknown as KnowledgeApi;
  const onKnowledgeSource = vi.fn();
  render(<NowRail {...readingProps()} historyOnly suggestions={withFast ? [readingAnswer(1)] : []} coachHistory={[old, failed]} sourceMeetingId="reading-flow" knowledgeApi={knowledgeApi} onKnowledgeSource={onKnowledgeSource} />);
  expect(screen.queryByText(/本场会议还没有问答/)).not.toBeInTheDocument();
  expect(screen.queryByTestId("follow-up-card")).not.toBeInTheDocument();
  if (withFast) await user.click(screen.getByText("其他会中提示"));
  await user.click(screen.getByText("过去建议"));
  const history = screen.getByRole("list", { name: "过去的教练建议" });
  expect(history).toHaveTextContent("已撤回");
  expect(history).not.toHaveTextContent("失败轮不能算新建议");
  await user.click(within(history).getByText("查看当时建议与依据"));
  expect(within(history).getByText("建议将下单与通知的失败边界拆开确认。")).toBeVisible();
  expect(within(history).getByText("专业提议 · 供讨论")).toBeVisible();
  await user.click(within(history).getByText("已知条件与待核实假设"));
  expect(within(history).getByText("原需求把订单与通知成功绑定")).toBeVisible();
  expect(within(history).getByText("通知渠道可能暂时不可用")).toBeVisible();
  await user.click(within(history).getByText("查看全部依据 · 1 条"));
  await user.click(within(history).getByRole("button", { name: /本场附件 · 下单需求 · 第 1 版 · 第 2 页/ }));
  await waitFor(() => expect(onKnowledgeSource).toHaveBeenCalledWith(historicalSource, "reading-flow"));
  expect(knowledgeApi.getMeetingAttachmentSource).toHaveBeenCalledWith("reading-flow", "material-span");
  expect(screen.getByText("这一版附件的使用范围或版本已变化；当前打开的是历史原文，仅供核对，不作为当前建议的依据。")).toBeVisible();
});

it("keeps a completed answer during streaming and returns from history to live", async () => {
  const user = userEvent.setup();
  const props = readingProps();
  const first = readingAnswer(1);
  const draft = { ...readingAnswer(2), status: "draft" as const, text: null, draftText: "未完成" };
  const view = render(<NowRail {...props} suggestions={[first, draft]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("回答 1");
  expect(screen.getByText(/下一条回应正在生成/)).toBeVisible();
  view.rerender(<NowRail {...props} suggestions={[first, readingAnswer(2)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("回答 2");
  await user.click(screen.getByRole("button", { name: "历史 2" }));
  await user.click(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getByRole("button", { name: /问题 1/ }));
  view.rerender(<NowRail {...props} suggestions={[first, readingAnswer(2), readingAnswer(3)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("回答 1");
  await user.click(screen.getByRole("button", { name: /2 条新建议.*查看最新/ }));
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("回答 3");
});

it("follows live answers by default, retains explicit historical reading, and restores it in review", async () => {
  const user = userEvent.setup();
  const props = readingProps();
  const view = render(<NowRail {...props} suggestions={[readingAnswer(1)]} />);
  const allAnswers = Array.from({ length: 25 }, (_, index) => readingAnswer(index + 1));
  view.rerender(<NowRail {...props} suggestions={allAnswers} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 25");
  await user.click(screen.getByRole("button", { name: "历史 25" }));
  expect(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getAllByRole("button")).toHaveLength(25);
  await user.type(screen.getByRole("textbox", { name: "搜索问答记录" }), "问题 2");
  await user.click(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getByRole("button", { name: /问题 2 0 次/ }));
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 2");
  view.rerender(<NowRail {...props} suggestions={[...allAnswers, readingAnswer(26)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 2");
  expect(screen.getByRole("button", { name: /24 条新建议.*查看最新/ })).toBeVisible();
  await waitFor(() => expect(sessionStorage.getItem("meeting-copilot-coach-reader:reading-flow")).toContain('"answer-2"'));
  view.unmount();
  render(<NowRail {...props} historyOnly suggestions={allAnswers} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 2");
  expect(screen.getByText("历史记录")).toBeVisible();
  expect(screen.getByRole("heading", { name: "会中问答与 Pi 补充" })).toBeVisible();
});

it("appends Pi revisions without removing prior content and copies every supplement", async () => {
  const user = userEvent.setup();
  const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  const props = { ...readingProps(), suggestions: [readingAnswer(1)] };
  const revision = (index: number): CoachHistoryEntry => ({
    ...followUp({ origin: "pi", status: "intervention", promptProfile: "deep_answer", answerId: "answer-1", decisionId: `pi-${index}`, revision: index }),
    historyId: `history-${index}`, createdAtMs: index * 1000,
    coachingPackage: coachingPackage(`判断 ${index}`, `补充说法 ${index}`),
  });
  const view = render(<NowRail {...props} coachHistory={[revision(1)]} />);
  view.rerender(<NowRail {...props} coachHistory={[revision(1), revision(2)]} />);
  const card = screen.getByTestId("answer-copilot-card");
  expect(card).toHaveTextContent("补充说法 1");
  expect(card).toHaveTextContent("补充说法 2");
  expect(screen.getByRole("button", { name: /本题有新补充/ })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "复制完整建议" }));
  expect(clipboard).toHaveBeenLastCalledWith(expect.stringContaining("补充说法 1"));
  expect(clipboard).toHaveBeenLastCalledWith(expect.stringContaining("关键风险"));
  await user.click(screen.getByRole("button", { name: /本题有新补充/ }));
  await user.click(screen.getByRole("button", { name: "复制完整建议" }));
  expect(clipboard).toHaveBeenLastCalledWith(expect.stringContaining("补充说法 2"));
  expect(clipboard).toHaveBeenLastCalledWith(expect.stringContaining("依据原话"));
});

it("restores a pending refinement after navigation and retries the original answer after failure", async () => {
  const user = userEvent.setup();
  const request = vi.fn().mockResolvedValue("pi-job-1");
  const status = vi.fn().mockResolvedValue("pending");
  const props = { ...readingProps(), suggestions: [readingAnswer(1)], onCoachRefine: request, onCoachRequestStatus: status };
  const first = render(<NowRail {...props} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "更具体");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  await waitFor(() => expect(status).toHaveBeenCalledWith("pi-job-1", expect.any(AbortSignal)));
  expect(screen.getByRole("button", { name: "提交补充" })).toBeDisabled();
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("回答 1");
  first.unmount();
  status.mockResolvedValue("failed");
  const second = render(<NowRail {...props} suggestions={[readingAnswer(1), readingAnswer(2)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("回答 2");
  await user.click(await screen.findByRole("button", { name: /另一问题的补充未完成/ }));
  await screen.findByRole("button", { name: "重试这次补充" });
  second.unmount();
  render(<NowRail {...props} suggestions={[readingAnswer(1), readingAnswer(2)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("回答 2");
  await user.click(await screen.findByRole("button", { name: /另一问题的补充未完成/ }));
  await user.click(await screen.findByRole("button", { name: "重试这次补充" }));
  expect(request).toHaveBeenLastCalledWith("answer-1", expect.any(String));
  expect(request).toHaveBeenCalledTimes(2);
});

it("retains every Pi revision when loading a long meeting snapshot", () => {
  const state = createInitialMeetingState("meeting-1");
  const history = Array.from({ length: 25 }, (_, index) => ({
    ...followUp({ origin: "pi", status: "intervention", decisionId: `pi-${index}` }),
    historyId: `history-${index}`, createdAtMs: index * 1000,
  }));
  const next = meetingReducer(state, { type: "snapshot.received", snapshot: { ...state, coachHistory: history }, receivedAtMs: 30_000 });
  expect(next.coachHistory).toHaveLength(25);
});

it("keeps the empty coach heading stable while runtime state remains secondary", () => {
  renderRail(null);

  expect(screen.getByRole("heading", { name: "AI 实时教练" })).toBeVisible();
  expect(screen.getByText("Pi 教练监听中")).toBeVisible();
  expect(screen.getByRole("button", { name: "我可以问什么" })).toBeVisible();
});

it("keeps the current streamed answer primary while Pi remains a supplement", () => {
  const answer: Suggestion = {
    suggestionId: "answer-1",
    meetingId: "meeting-1",
    jobId: "answer-job-1",
    generationId: "answer-generation-1",
    kind: "answer",
    questionText: "为什么选择 Redis Stream，而不是 Kafka？",
    evidenceSegmentId: "segment-1",
    evidenceTranscriptSeq: 3,
    evidenceHash: "hash-1",
    stateRevision: 1,
    status: "draft",
    draftText: "我们当时更看重现有 Redis 的复用和较低的运维成本。",
    draftSeq: 1,
    text: null,
    finalDraftSeq: null,
    feedback: null,
    createdAtMs: 100,
    updatedAtMs: 110,
    committedAtMs: null,
  };

  render(
    <NowRail
      currentTopic={null}
      followUp={followUp({
        origin: "pi",
        status: "intervention",
        promptProfile: "deep_answer",
        answerId: "answer-1",
        sayThis: "准备说明消息不丢失的保障，以及迁移 Kafka 的触发条件。",
      })}
      coachHistory={[]}
      openQuestions={[]}
      suggestions={[answer]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn(async () => undefined)}
      onFactStatus={vi.fn(async () => undefined)}
      onMessage={vi.fn()}
    />,
  );

  const card = screen.getByTestId("answer-copilot-card");
  expect(card).toHaveTextContent("为什么选择 Redis Stream，而不是 Kafka？");
  expect(card).toHaveTextContent("我们当时更看重现有 Redis 的复用");
  expect(card).toHaveTextContent("Pi 补充");
  expect(card).toHaveTextContent("迁移 Kafka 的触发条件");
  expect(screen.queryByTestId("follow-up-card")).toBeNull();
  expect(screen.getByRole("button", { name: "复制完整建议" })).toBeVisible();
});

it("shows complete Pi packages in chronological order with evidence available", async () => {
  const user = userEvent.setup();
  const onEvidence = vi.fn();
  const answer: Suggestion = {
    suggestionId: "answer-structured",
    meetingId: "meeting-1",
    jobId: "answer-job-structured",
    generationId: "answer-generation-structured",
    kind: "answer",
    questionText: "为什么选择 Redis Stream？",
    evidenceSegmentId: "segment-1",
    evidenceTranscriptSeq: 3,
    evidenceHash: "hash-1",
    stateRevision: 1,
    status: "committed",
    draftText: "",
    draftSeq: 1,
    text: "因为当前规模可控且可以复用现有 Redis 运维体系。",
    finalDraftSeq: 1,
    feedback: null,
    createdAtMs: 100,
    updatedAtMs: 110,
    committedAtMs: 110,
  };
  const revisions: CoachHistoryEntry[] = [
    {
      ...followUp({
        origin: "pi",
        status: "intervention",
        promptProfile: "deep_answer",
        answerId: answer.suggestionId,
        decisionId: "pi-v1",
        revision: 1,
        triggerType: "answer_ready",
        coachingPackage: coachingPackage("先补齐可靠性", "第一版：补充失败消费和积压处理。"),
      }),
      historyId: "history-v1",
      createdAtMs: 120,
    },
    {
      ...followUp({
        origin: "pi",
        status: "intervention",
        promptProfile: "deep_answer",
        answerId: answer.suggestionId,
        decisionId: "pi-v2",
        revision: 2,
        triggerType: "user_request",
        userRequest: "请更具体",
        supersedesDecisionId: "pi-v1",
        coachingPackage: coachingPackage("给出迁移判断", "第二版：补充吞吐、留存和运维成本的迁移判断。"),
      }),
      historyId: "history-v2",
      createdAtMs: 140,
    },
  ];

  const props: ComponentProps<typeof NowRail> = {
    viewStateKey: "meeting-1",
    currentTopic: null,
    followUp: null,
    coachHistory: revisions,
    openQuestions: [],
    suggestions: [answer],
    decisionCandidates: [],
    actionItems: [],
    risks: [],
    onEvidence,
    onFeedback: vi.fn(async () => undefined),
    onFactStatus: vi.fn(async () => undefined),
    onMessage: vi.fn(),
  };
  const first = render(<NowRail {...props} />);

  const card = screen.getByTestId("answer-copilot-card");
  expect(card).toHaveTextContent("根据你的调整补充");
  expect(card).toHaveTextContent("给出迁移判断");
  expect(card).toHaveTextContent("第二版：补充吞吐、留存和运维成本的迁移判断。");
  expect(card).toHaveTextContent("可能追问");
  expect(card).toHaveTextContent("先补齐可靠性");
  expect(card).toHaveTextContent("第一版：补充失败消费和积压处理。");
  await user.click(within(card).getAllByText("查看分析依据")[0]);
  await user.click(within(card).getAllByRole("button", { name: "原话 1" })[0]);
  expect(onEvidence).toHaveBeenCalledWith("segment-1");

  first.unmount();
  render(<NowRail {...props} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("第一版：补充失败消费和积压处理。");
});

it("separates prior answers and prior coach cards without duplicating the current deep supplement", async () => {
  const current: Suggestion = {
    suggestionId: "answer-current",
    meetingId: "meeting-1",
    jobId: "answer-job-current",
    generationId: "answer-generation-current",
    kind: "answer",
    questionText: "为什么选择 Redis Stream，而不是 Kafka？",
    evidenceSegmentId: "segment-current",
    evidenceTranscriptSeq: 8,
    evidenceHash: "hash-current",
    stateRevision: 3,
    status: "committed",
    draftText: "",
    draftSeq: 2,
    text: "当前规模优先复用 Redis，并给出迁移 Kafka 的量化边界。",
    finalDraftSeq: 2,
    feedback: null,
    createdAtMs: 300,
    updatedAtMs: 320,
    committedAtMs: 320,
  };
  const prior: Suggestion = {
    ...current,
    suggestionId: "answer-prior",
    jobId: "answer-job-prior",
    generationId: "answer-generation-prior",
    questionText: "如何保证消息不会丢失？",
    evidenceSegmentId: "segment-prior",
    evidenceTranscriptSeq: 4,
    evidenceHash: "hash-prior",
    stateRevision: 2,
    status: "superseded",
    draftText: "通过消费确认、幂等键和失败重试共同保护处理结果。",
    text: "通过消费确认、幂等键和失败重试共同保护处理结果。",
    createdAtMs: 100,
    updatedAtMs: 150,
    committedAtMs: 150,
  };
  const currentDeep = {
    ...followUp({
      origin: "pi" as const,
      status: "intervention" as const,
      promptProfile: "deep_answer",
      answerId: "answer-current",
      decisionId: "coach-deep-current",
      sayThis: "补充压测阈值、监控指标和回滚条件。",
    }),
    historyId: "coach-history-deep-current",
    createdAtMs: 330,
  };
  const priorCoach = {
    ...followUp({
      origin: "pi" as const,
      status: "intervention" as const,
      promptProfile: "candidate_fast",
      decisionId: "coach-prior",
      question: "先确认失败恢复的验收条件。",
    }),
    historyId: "coach-history-prior",
    createdAtMs: 200,
  };

  render(
    <NowRail
      currentTopic={null}
      followUp={null}
      coachHistory={[priorCoach, currentDeep]}
      openQuestions={[]}
      suggestions={[prior, current]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn(async () => undefined)}
      onFactStatus={vi.fn(async () => undefined)}
      onMessage={vi.fn()}
    />,
  );

  const currentCard = screen.getByTestId("answer-copilot-card");
  expect(currentCard).toHaveTextContent("当前规模优先复用 Redis");
  expect(currentCard).toHaveTextContent("补充压测阈值、监控指标和回滚条件");

  await userEvent.setup().click(screen.getByRole("button", { name: "历史 2" }));
  const answerHistory = screen.getByRole("list", { name: "回答与 Pi 历史" });
  expect(answerHistory).toHaveTextContent("如何保证消息不会丢失？");
  expect(answerHistory).toHaveTextContent("已保存");
  expect(answerHistory).toHaveTextContent("为什么选择 Redis Stream，而不是 Kafka？");

  await userEvent.setup().click(screen.getByText("其他会中提示"));
  const coachHistory = screen.getByRole("list", { name: "过去的教练建议" });
  expect(coachHistory).toHaveTextContent("先确认失败恢复的验收条件。");
  expect(within(coachHistory).queryByText("补充压测阈值、监控指标和回滚条件。")).toBeNull();
});

it("requests a Pi revision against the selected answer without removing the current content", async () => {
  const user = userEvent.setup();
  const onCoachRefine = vi.fn(async () => "refine-job");
  const answer: Suggestion = {
    suggestionId: "answer-feedback",
    meetingId: "meeting-1",
    jobId: "answer-job-feedback",
    generationId: "answer-generation-feedback",
    kind: "answer",
    questionText: "上线前最重要的风险是什么？",
    evidenceSegmentId: "segment-1",
    evidenceTranscriptSeq: 3,
    evidenceHash: "hash-1",
    stateRevision: 1,
    status: "committed",
    draftText: "",
    draftSeq: 1,
    text: "最重要的是先确认回滚触发条件和负责人。",
    finalDraftSeq: 1,
    feedback: null,
    createdAtMs: 100,
    updatedAtMs: 110,
    committedAtMs: 110,
  };

  render(
    <NowRail
      currentTopic={null}
      followUp={null}
      coachHistory={[]}
      openQuestions={[]}
      suggestions={[answer]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn(async () => undefined)}
      onFactStatus={vi.fn(async () => undefined)}
      onMessage={vi.fn()}
      onCoachRefine={onCoachRefine}
    />,
  );

  await user.click(screen.getByRole("button", { name: "常用调整" }));
  await user.click(screen.getByRole("button", { name: "补充关键风险与适用边界" }));
  expect(onCoachRefine).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "提交补充" }));

  expect(onCoachRefine).toHaveBeenCalledWith(
    "answer-feedback",
    expect.stringContaining("关键风险"),
  );
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("最重要的是先确认回滚触发条件和负责人");
});

it("never labels an unrelated realtime Pi intervention as a deep answer supplement", () => {
  const answer: Suggestion = {
    suggestionId: "answer-1",
    meetingId: "meeting-1",
    jobId: "answer-job-1",
    generationId: "answer-generation-1",
    kind: "answer",
    questionText: "为什么选择 Redis Stream？",
    evidenceSegmentId: "segment-1",
    evidenceTranscriptSeq: 3,
    evidenceHash: "hash-1",
    stateRevision: 1,
    status: "committed",
    draftText: "",
    draftSeq: 1,
    text: "因为当前规模可控且可以复用现有 Redis 运维体系。",
    finalDraftSeq: 1,
    feedback: null,
    createdAtMs: 100,
    updatedAtMs: 110,
    committedAtMs: 110,
  };

  render(
    <NowRail
      currentTopic={null}
      followUp={followUp({
        origin: "pi",
        status: "intervention",
        promptProfile: "candidate_fast",
        sayThis: "你说的是观澜湖新城吗？",
      })}
      coachHistory={[]}
      openQuestions={[]}
      suggestions={[answer]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn(async () => undefined)}
      onFactStatus={vi.fn(async () => undefined)}
      onMessage={vi.fn()}
    />,
  );

  const card = screen.getByTestId("answer-copilot-card");
  expect(card).not.toHaveTextContent("Pi 深度补充");
  expect(card).not.toHaveTextContent("观澜湖");
});

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

it("keeps a deprioritized intervention out of the current card and in history", async () => {
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
  await userEvent.setup().click(screen.getByText("运行详情"));
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
  expect(card).toHaveTextContent("可以这样说");
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

it("clears silent and expired cards while exposing the decision lifecycle", async () => {
  const { rerender } = renderRail(followUp({
    origin: "pi",
    status: "protected_silent",
    decisionReason: "本轮没有高价值、可立即执行的介入建议。",
    decisionId: "coach-decision-silent",
  }));

  expect(screen.getByText("本轮保持静默")).toBeVisible();
  await userEvent.setup().click(screen.getByText("运行详情"));
  expect(screen.getByText("本轮没有高价值、可立即执行的介入建议。")).toBeVisible();
  expect(screen.queryByTestId("follow-up-card")).toBeNull();
  expect(screen.getByRole("button", { name: "我可以问什么" })).toBeVisible();

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

it("shows a retract decision without restoring the prior intervention card", async () => {
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
  await userEvent.setup().click(screen.getByText("运行详情"));
  expect(screen.getByText("负责人已经确认，旧建议不再成立。")).toBeVisible();
  await userEvent.setup().click(screen.getByText("过去建议"));
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent("先限定承诺条件");
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent("已撤回");
  expect(screen.getByRole("button", { name: "先确认这次承诺的验收条件。" })).toBeVisible();
});
