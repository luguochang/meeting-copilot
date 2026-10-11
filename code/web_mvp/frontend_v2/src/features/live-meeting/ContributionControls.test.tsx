import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContributionControls } from "./ContributionControls";
import { NowRail } from "./NowRail";
import { ContributionBasis } from "./ContributionBasis";
import { ApiError, HttpMeetingApi, type CoachRequestOutcome, type DiscussionWorkItem } from "../../api/client";

afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });
describe("deliberate live contributions", () => {
  it("does not call an old title current when an earlier segment was corrected without a new sequence", () => {
    const view = render(<ContributionControls viewStateKey="equal-seq-revised" currentTopic={{ id: "old", text: "先前通知讨论", status: "active", updatedAtMs: null, lastUpdatedSeq: 3, evidenceSegmentIds: [] }}
      discussionRuntime={{ state: "idle", label: "不可信标签", level: null, detail: null, decision: "not_started", understandingStatus: "waiting_reanalysis", inputTranscriptSeq: 3, lastSemanticSeq: 0 }} onContribute={vi.fn()} />);
    expect(screen.getByText("上次记录的话题：先前通知讨论")).toBeVisible();
    expect(screen.queryByText("正在讨论：先前通知讨论")).not.toBeInTheDocument();
    view.rerender(<ContributionControls viewStateKey="equal-seq-revised" currentTopic={{ id: "old", text: "先前通知讨论", status: "active", updatedAtMs: null, lastUpdatedSeq: 3, evidenceSegmentIds: [] }}
      discussionRuntime={{ state: "active", label: "安全宿主状态", level: null, detail: null, decision: "succeeded", understandingStatus: "partial_review", inputTranscriptSeq: 3, lastSemanticSeq: 3 }} onContribute={vi.fn()} />);
    expect(screen.getByText("正在讨论：先前通知讨论")).toBeVisible();
    expect(screen.queryByText("上次记录的话题：先前通知讨论")).not.toBeInTheDocument();
  });
  it.each(["running", "failed", "pending"])("labels an existing topic historical after its reliable cursor is invalidated (%s)", (decision) => {
    const contribute = vi.fn();
    render(<ContributionControls viewStateKey="revised-cursor" currentTopic={{ id: "old", text: "先前餐饮讨论", status: "active", updatedAtMs: null, lastUpdatedSeq: 8, evidenceSegmentIds: [] }}
      discussionRuntime={{ state: "busy", label: "正在理解", level: null, detail: null, decision, inputTranscriptSeq: 324, lastSemanticSeq: 0 }} onContribute={contribute} />);
    expect(screen.getByText("上次记录的话题：先前餐饮讨论")).toBeVisible();
    expect(screen.queryByText("正在讨论：先前餐饮讨论")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "我可以问什么" })).toBeEnabled();
    expect(contribute).not.toHaveBeenCalled();
  });
  it.each([0, 8, undefined, -1, 8.5])("does not guess historical understanding from initial/equal/missing/invalid topic cursor %s", (lastUpdatedSeq) => {
    render(<ContributionControls viewStateKey="unknown-cursor" currentTopic={{ id: "old", text: "已设置讨论", status: "active", updatedAtMs: null, lastUpdatedSeq, evidenceSegmentIds: [] }}
      discussionRuntime={{ state: "busy", label: "正在理解", level: null, detail: null, decision: "running", inputTranscriptSeq: 8, lastSemanticSeq: 0 }} onContribute={vi.fn()} />);
    expect(screen.getByText("正在讨论：已设置讨论")).toBeVisible();
  });
  it("does not apply an old topic cursor to a different deliberately edited discussion title", async () => {
    render(<ContributionControls viewStateKey="edited-title" currentTopic={{ id: "old", text: "旧模型话题", status: "active", updatedAtMs: null, lastUpdatedSeq: 8, evidenceSegmentIds: [] }}
      discussionRuntime={{ state: "busy", label: "正在理解", level: null, detail: null, decision: "running", inputTranscriptSeq: 324, lastSemanticSeq: 0 }}
      onReadDiscussion={vi.fn().mockResolvedValue({ id: "edited", title: "用户新设置的话题", state: "ready", version: 2, context: {} })} onContribute={vi.fn()} />);
    expect(await screen.findByText("正在讨论：用户新设置的话题")).toBeVisible();
    expect(screen.queryByText("上次记录的话题：用户新设置的话题")).not.toBeInTheDocument();
  });
  it("offers all three intents without a Fast answer or preset goal", async () => {
    const contribute = vi.fn().mockResolvedValue("job-1");
    const status = vi.fn().mockResolvedValue("no_change");
    render(<NowRail viewStateKey="meeting-empty" currentTopic={null} suggestions={[]} followUp={null} openQuestions={[]} decisionCandidates={[]} actionItems={[]} risks={[]} onEvidence={vi.fn()} onFeedback={vi.fn()} onFactStatus={vi.fn()} onMessage={vi.fn()} onContribute={contribute} onCoachRequestStatus={status} />);
    expect(screen.getByRole("button", { name: "我可以问什么" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "帮我组织回应" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "还有什么方案" }));
    await waitFor(() => expect(contribute).toHaveBeenCalledWith("compare_options"));
    await screen.findByText(/当前没有新的有用贡献/);
    expect(contribute).toHaveBeenCalledTimes(1);
  });
  it("restores only a job status on remount and never resubmits a paid request", async () => {
    sessionStorage.setItem("talktrace-contribution:restore", JSON.stringify({ jobId: "saved-job", label: "我可以问什么" }));
    const contribute = vi.fn(); const status = vi.fn().mockResolvedValue("succeeded");
    render(<ContributionControls viewStateKey="restore" currentTopic={null} onContribute={contribute} onCoachRequestStatus={status} />);
    await screen.findByText(/现场建议已生成/);
    expect(status).toHaveBeenCalledWith("saved-job", expect.any(AbortSignal));
    expect(contribute).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("talktrace-contribution:restore")).toBeNull();
  });
  it("shows a timeout as retryable rather than claiming a suggestion was generated", async () => {
    render(<ContributionControls viewStateKey="timeout" currentTopic={null} onContribute={vi.fn().mockResolvedValue("timeout-job")} onCoachRequestStatus={vi.fn().mockResolvedValue("timed_out")} />);
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    await screen.findByText(/本次建议未及时生成，可重试/);
    expect(screen.queryByText(/现场建议已生成/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "我可以问什么" })).toBeEnabled();
  });
  it("consolidates an empty meeting's timeout into a single notice rather than repeating raw Pi errors", () => {
    render(<NowRail viewStateKey="empty-timeout" currentTopic={null} suggestions={[]} followUp={null} coachDecision={{ status: "timed_out" }} coachRuntime={{ state: "error", label: "Pi 补充未完成", detail: "Pi 补充暂不可用", level: null }} openQuestions={[]} decisionCandidates={[]} actionItems={[]} risks={[]} onEvidence={vi.fn()} onFeedback={vi.fn()} onFactStatus={vi.fn()} onMessage={vi.fn()} />);
    expect(screen.getAllByText(/本次建议未及时生成，可重试/)).toHaveLength(1);
    expect(screen.queryByText(/已有回答保留|Pi 补充暂不可用|Pi 补充未完成/)).not.toBeInTheDocument();
  });
  it("keeps a late submission attached to its originating meeting", async () => {
    let finish!: (id: string) => void;
    const contribute = vi.fn(() => new Promise<string>((resolve) => { finish = resolve; }));
    const view = render(<ContributionControls viewStateKey="old" currentTopic={null} onContribute={contribute} />);
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    view.rerender(<ContributionControls viewStateKey="new" currentTopic={null} onContribute={contribute} />);
    finish("old-job");
    await waitFor(() => expect(sessionStorage.getItem("talktrace-contribution:old")).toContain("old-job"));
    expect(sessionStorage.getItem("talktrace-contribution:new")).toBeNull();
    expect(screen.getByRole("button", { name: "我可以问什么" })).toBeEnabled();
  });
  it("shows professional proposals separately from sourced facts and assumptions", () => {
    render(<ContributionBasis value={{ question: "可以独立重试吗？", reason: "失败边界不同", evidenceSegmentIds: ["s"], evidenceQuote: "通知允许延后", urgency: "low", analysisKind: "participation", proposalType: "professional_proposal", supportingFacts: [{ text: "通知允许延后", sourceRef: "s" }], assumptions: ["通知没有同步成功要求"] }} />);
    expect(screen.getByText("专业提议 · 供讨论")).toBeVisible();
    fireEvent.click(screen.getByText("已知条件与待核实假设"));
    expect(screen.getByText("通知允许延后")).toBeVisible();
    expect(screen.getByText("通知没有同步成功要求")).toBeVisible();
  });
  it("corrects an inferred topic and records an answered point without closing the discussion", async () => {
    const discussion: DiscussionWorkItem = { id: "discussion-1", title: "通知同步成功", state: "investigating", version: 2, context: { analysis_basis_revision: 4, responded_points: ["已明确通知可以延后"] } };
    const update = vi.fn().mockImplementation(async (_item, changes) => ({ ...discussion, title: changes.title ?? discussion.title, version: 3, context: { ...discussion.context, ...changes.context } }));
    render(<ContributionControls viewStateKey="discussion" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onUpdateDiscussion={update} followUp={{ question: "通知重试如何处理？", sayThis: "通知能否用异步队列并独立重试？", topicId: discussion.id, reason: "隔离失败", evidenceQuote: "允许延后", evidenceSegmentIds: [], urgency: "low" }} />);
    fireEvent.click(await screen.findByRole("button", { name: "不是当前话题？纠正" }));
    fireEvent.change(screen.getByRole("textbox", { name: "纠正当前议题" }), { target: { value: "通知失败与订单成功的边界" } });
    fireEvent.click(screen.getByRole("button", { name: "保存议题" }));
    await screen.findByText(/当前议题已纠正/);
    expect(update).toHaveBeenNthCalledWith(1, discussion, { title: "通知失败与订单成功的边界", context: { current_problem: "通知失败与订单成功的边界", inferred: false } });
    fireEvent.click(screen.getByRole("button", { name: "这个已经讨论过" }));
    await screen.findByText(/已记录这个点已经讨论过/);
    expect(update.mock.calls[1][0].version).toBe(3);
    expect(update.mock.calls[1][1]).toEqual({ context: { responded_points: ["已明确通知可以延后", "通知能否用异步队列并独立重试？"] } });
    expect(update.mock.calls[1][1]).not.toHaveProperty("state");
  });
  it("refreshes a changed discussion after a version conflict without losing the user's correction", async () => {
    const old: DiscussionWorkItem = { id: "topic", title: "旧议题", state: "investigating", version: 1, context: {} };
    const read = vi.fn().mockResolvedValueOnce(old).mockResolvedValue({ ...old, title: "会议新进展", version: 2 });
    const update = vi.fn().mockRejectedValue(new ApiError(409, "version mismatch", null));
    render(<ContributionControls viewStateKey="conflict" currentTopic={null} onReadDiscussion={read} onUpdateDiscussion={update} />);
    fireEvent.click(await screen.findByRole("button", { name: "不是当前话题？纠正" }));
    fireEvent.change(screen.getByRole("textbox", { name: "纠正当前议题" }), { target: { value: "我的纠正" } });
    fireEvent.click(screen.getByRole("button", { name: "保存议题" }));
    await screen.findByText(/讨论已有新进展，已刷新/);
    expect(screen.getByText("正在讨论：会议新进展")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "纠正当前议题" })).toHaveValue("我的纠正");
    expect(update).toHaveBeenCalledTimes(1);
  });
});

describe("safe contribution terminal UX", () => {
  const discussion: DiscussionWorkItem = { id: "orders", title: "订单取消边界", state: "investigating", version: 1, context: { analysis_basis_revision: 3 } };
  const outcome = (overrides: Partial<CoachRequestOutcome> = {}): CoachRequestOutcome => ({ meetingId: "meeting", jobId: "own", status: "failed", failureCategory: "validation_rejected", canRetry: false, topicId: "orders", analysisBasisRevision: 3, ...overrides });
  const stored = (overrides: Partial<CoachRequestOutcome> = {}) => JSON.stringify({ schemaVersion: 1, intent: "ask_question", outcome: outcome(overrides) });
  const terminalKey = "talktrace-contribution:meeting:terminal-v1";

  it("uses the formal own failure and safe reason despite a successful durable job and old intervention", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ jobs: [{ id: "own", status: "succeeded", request_context: { topic_id: "orders", analysis_basis_revision: 3 } }], coach_decision: { job_id: "old", status: "stale" }, coach_history: [{ job_id: "old", status: "intervention" }], diagnostics: { coach_runtime_history: [{ job_id: "own", status: "failed", status_reason: "IntelligenceResponseValidationError", rejected_output: "private rejected text" }] } }), { headers: { "Content-Type": "application/json" } })));
    const api = new HttpMeetingApi(); const contribute = vi.fn().mockResolvedValue("own");
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} onCoachRequestOutcome={(jobId, signal) => api.getCoachRequestOutcome("meeting", jobId, signal)} />);
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    expect(await screen.findByRole("alert", { name: "本次现场请求结果" })).toHaveTextContent("生成建议未通过格式或依据核对，未发布");
    expect(screen.queryByText(/现场建议已生成|当前没有新的有用贡献/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/IntelligenceResponseValidationError|private rejected text/);
    expect(sessionStorage.getItem(terminalKey)).not.toMatch(/status_reason|rejected_output|private rejected text/);
    expect(screen.getByRole("button", { name: "我可以问什么" })).toBeEnabled();
    expect(contribute).toHaveBeenCalledTimes(1);
  });

  it("restores only the bounded terminal on refresh without resubmitting a request", async () => {
    sessionStorage.setItem(terminalKey, stored());
    const contribute = vi.fn(); const poll = vi.fn();
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} onCoachRequestStatus={poll} />);
    await screen.findByText(/生成建议未通过格式或依据核对，未发布/);
    expect(contribute).not.toHaveBeenCalled(); expect(poll).not.toHaveBeenCalled();
  });

  it("rechecks a cached success against its own formal result on refresh without posting", async () => {
    sessionStorage.setItem(terminalKey, stored({ status: "succeeded", failureCategory: null }));
    let finish!: (value: CoachRequestOutcome) => void;
    const readOutcome = vi.fn(() => new Promise<CoachRequestOutcome>((resolve) => { finish = resolve; }));
    const contribute = vi.fn();
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} onCoachRequestOutcome={readOutcome} />);
    await waitFor(() => expect(readOutcome).toHaveBeenCalledWith("own", expect.any(AbortSignal)));
    expect(screen.queryByText(/现场建议已生成/)).not.toBeInTheDocument();
    finish(outcome());
    await screen.findByText(/生成建议未通过格式或依据核对，未发布/);
    expect(screen.queryByText(/现场建议已生成/)).not.toBeInTheDocument();
    expect(contribute).not.toHaveBeenCalled(); expect(readOutcome).toHaveBeenCalledTimes(1);
  });

  it("drops a cached terminal when the result belongs to a different job", async () => {
    sessionStorage.setItem(terminalKey, stored()); const contribute = vi.fn();
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} onCoachRequestOutcome={vi.fn().mockResolvedValue(outcome({ jobId: "foreign" }))} />);
    await screen.findByText(/未能确认本次请求的结果身份/);
    expect(screen.queryByRole("alert", { name: "本次现场请求结果" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(terminalKey)).toBeNull(); expect(contribute).not.toHaveBeenCalled();
  });

  it("keeps an unavailable refresh result unconfirmed and never silently repeats analysis", async () => {
    sessionStorage.setItem(terminalKey, stored()); const contribute = vi.fn(); const readOutcome = vi.fn().mockRejectedValue(new Error("network"));
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} onCoachRequestOutcome={readOutcome} />);
    await screen.findByText(/暂时无法恢复本次请求结果/);
    expect(screen.queryByRole("alert", { name: "本次现场请求结果" })).not.toBeInTheDocument();
    expect(readOutcome).toHaveBeenCalledTimes(1); expect(contribute).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(terminalKey)).not.toBeNull();
  });

  it("waits for the canonical discussion before verifying a same-scope cached terminal", async () => {
    sessionStorage.setItem(terminalKey, stored());
    let finishDiscussion!: (value: DiscussionWorkItem) => void;
    const read = vi.fn(() => new Promise<DiscussionWorkItem>((resolve) => { finishDiscussion = resolve; }));
    const getOutcome = vi.fn().mockResolvedValue(outcome()); const contribute = vi.fn();
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={read} onContribute={contribute} onCoachRequestOutcome={getOutcome} />);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    expect(getOutcome).not.toHaveBeenCalled(); expect(sessionStorage.getItem(terminalKey)).not.toBeNull();
    finishDiscussion(discussion);
    await screen.findByRole("alert", { name: "本次现场请求结果" });
    expect(getOutcome).toHaveBeenCalledTimes(1); expect(sessionStorage.getItem(terminalKey)).toContain('"jobId":"own"');
    expect(contribute).not.toHaveBeenCalled();
  });

  it("finishes a restored pending request without carrying its result into a changed topic", async () => {
    sessionStorage.setItem("talktrace-contribution:meeting", JSON.stringify({ jobId: "own", label: "我可以问什么", intent: "ask_question", topicId: "orders", analysisBasisRevision: 3 }));
    let finishOutcome!: (value: CoachRequestOutcome) => void;
    const pendingResult = new Promise<CoachRequestOutcome>((resolve) => { finishOutcome = resolve; });
    const getOutcome = vi.fn(() => pendingResult); const contribute = vi.fn();
    const read = vi.fn().mockResolvedValue(discussion);
    const followUp = { question: "之前", reason: "本地状态用例", evidenceSegmentIds: [], evidenceQuote: "", urgency: "low" as const };
    const view = render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={read} onContribute={contribute} onCoachRequestOutcome={getOutcome} followUp={{ ...followUp, decisionId: "before" }} />);
    await screen.findByText("正在讨论：订单取消边界");
    read.mockResolvedValue({ ...discussion, id: "new-topic", title: "新的导入范围", context: { analysis_basis_revision: 4 } });
    view.rerender(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={read} onContribute={contribute} onCoachRequestOutcome={getOutcome} followUp={{ ...followUp, decisionId: "after" }} />);
    await screen.findByText("正在讨论：新的导入范围");
    finishOutcome(outcome());
    await screen.findByText(/讨论已更新，本次请求已结束/);
    expect(screen.queryByRole("alert", { name: "本次现场请求结果" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(terminalKey)).toBeNull(); expect(sessionStorage.getItem("talktrace-contribution:meeting")).toBeNull();
    expect(contribute).not.toHaveBeenCalled();
  });

  it("clears the previous terminal only on a new explicit request and does not retry itself", async () => {
    sessionStorage.setItem(terminalKey, stored());
    const contribute = vi.fn().mockResolvedValue("own"); const getOutcome = vi.fn().mockResolvedValue(outcome());
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} onCoachRequestOutcome={getOutcome} />);
    await screen.findByText(/生成建议未通过格式或依据核对/);
    expect(contribute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    await waitFor(() => expect(contribute).toHaveBeenCalledTimes(1));
    await screen.findByRole("alert", { name: "本次现场请求结果" });
    expect(contribute).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    await waitFor(() => expect(contribute).toHaveBeenCalledTimes(2));
  });

  it.each([{ id: "new-topic", basis: 3 }, { id: "orders", basis: 4 }])("clears a terminal when the canonical topic or basis changes (%j)", async ({ id, basis }) => {
    sessionStorage.setItem(terminalKey, stored());
    const read = vi.fn().mockResolvedValue(discussion); const contribute = vi.fn();
    const followUp = { question: "之前", reason: "本地状态用例", evidenceSegmentIds: [], evidenceQuote: "", urgency: "low" as const };
    const view = render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={read} onContribute={contribute} followUp={{ ...followUp, decisionId: "before" }} />);
    await screen.findByText(/生成建议未通过格式或依据核对/);
    read.mockResolvedValue({ ...discussion, id, context: { analysis_basis_revision: basis } });
    view.rerender(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={read} onContribute={contribute} followUp={{ ...followUp, decisionId: "after" }} />);
    await waitFor(() => expect(screen.queryByText(/生成建议未通过格式或依据核对/)).not.toBeInTheDocument());
    expect(sessionStorage.getItem(terminalKey)).toBeNull(); expect(contribute).not.toHaveBeenCalled();
  });

  it("does not carry a previous meeting's terminal to another meeting", async () => {
    sessionStorage.setItem(terminalKey, stored());
    const read = vi.fn().mockResolvedValue(discussion); const contribute = vi.fn();
    const view = render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={read} onContribute={contribute} />);
    await screen.findByText(/生成建议未通过格式或依据核对/);
    view.rerender(<ContributionControls viewStateKey="different" currentTopic={null} onReadDiscussion={read} onContribute={contribute} />);
    await waitFor(() => expect(screen.queryByText(/生成建议未通过格式或依据核对/)).not.toBeInTheDocument());
    expect(contribute).not.toHaveBeenCalled();
  });

  it.each(["not json", stored({ meetingId: "foreign" }), stored({ jobId: "bad\u0000id" }), stored({ failureCategory: "private raw reason" as never }), stored({ canRetry: true }), stored({ analysisBasisRevision: null }), JSON.stringify({ schemaVersion: 99 }), "x".repeat(1700)])("ignores malformed or foreign persisted state", async (value) => {
    sessionStorage.setItem(terminalKey, value); const contribute = vi.fn();
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} />);
    await screen.findByText("正在讨论：订单取消边界");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(); expect(contribute).not.toHaveBeenCalled();
  });

  it("does not claim a foreign job's terminal as this request's result", async () => {
    const contribute = vi.fn().mockResolvedValue("own");
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} onCoachRequestOutcome={vi.fn().mockResolvedValue(outcome({ jobId: "foreign", status: "succeeded", failureCategory: null }))} />);
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    await screen.findByText(/暂时无法读取进度/);
    expect(screen.queryByRole("alert", { name: "本次现场请求结果" })).not.toBeInTheDocument();
    expect(screen.queryByText(/现场建议已生成/)).not.toBeInTheDocument(); expect(sessionStorage.getItem(terminalKey)).toBeNull();
    expect(contribute).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["no_change", null, false, "当前没有新的有用贡献"],
    ["timed_out", "timeout", true, "本次建议未及时生成，可重试"],
    ["failed", "evidence_rejected", false, "生成建议未通过依据核对，未发布"],
  ] as const)("distinguishes silence, timeout and evidence refusal (%s)", async (status, failureCategory, canRetry, message) => {
    const contribute = vi.fn().mockResolvedValue("own");
    render(<ContributionControls viewStateKey="meeting" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(discussion)} onContribute={contribute} onCoachRequestOutcome={vi.fn().mockResolvedValue(outcome({ status, failureCategory, canRetry }))} />);
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    await screen.findByText(new RegExp(message));
    expect(screen.queryByText(/现场建议已生成/)).not.toBeInTheDocument(); expect(contribute).toHaveBeenCalledTimes(1);
  });
});
