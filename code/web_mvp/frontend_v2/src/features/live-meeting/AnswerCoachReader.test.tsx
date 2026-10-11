import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import type { CoachHistoryEntry, Suggestion } from "../../domain/events";
import { NowRail } from "./NowRail";
import type { NowRailProps } from "./nowRailTypes";
import { MeetingSplitPane } from "./MeetingSplitPane";

afterEach(() => { vi.useRealTimers(); sessionStorage.clear(); localStorage.clear(); });
const answer = (index: number): Suggestion => ({
  suggestionId: `answer-${index}`, meetingId: "focus-test", jobId: `answer-job-${index}`, generationId: `generation-${index}`,
  kind: "answer", questionText: `问题 ${index}`, evidenceSegmentId: "segment-1", evidenceTranscriptSeq: index,
  evidenceHash: "hash", stateRevision: index, status: "committed", draftText: "", draftSeq: 1,
  text: `回答 ${index}：这是原始完整正文。`, finalDraftSeq: 1, feedback: null,
  createdAtMs: index * 1000, updatedAtMs: index * 1000, committedAtMs: index * 1000,
});
const props = (): NowRailProps => ({
  viewStateKey: "focus-test", suggestions: [answer(1)], followUp: null, coachHistory: [], currentTopic: null,
  openQuestions: [], decisionCandidates: [], actionItems: [], risks: [], onEvidence: vi.fn(),
  onFeedback: vi.fn(), onFactStatus: vi.fn(), onMessage: vi.fn(),
  onCoachRefine: vi.fn().mockResolvedValue("refine-1"), onCoachRequestStatus: vi.fn().mockResolvedValue("pending"),
});
const revision = (jobId: string, index = 1): CoachHistoryEntry => ({
  historyId: `history-${index}`, decisionId: `decision-${index}`, answerId: "answer-1", promptProfile: "deep_answer",
  origin: "pi", status: "intervention", triggerType: "user_request", userRequest: "补充风险", revision: index,
  question: `补充 ${index}`, sayThis: `Pi 的新增内容 ${index}`, reason: "需要明确风险", urgency: "medium",
  evidenceSegmentIds: ["segment-1"], evidenceQuote: "原话", createdAtMs: Date.now(),
  formalAi: { source: "llm_first", jobId, batchId: "batch", provider: "test", model: "test", llmCalled: true,
    evidence: { segmentIds: ["segment-1"], quote: "原话", evidenceHash: null, stateRevision: null } },
});

it("keeps a typed adjustment attached to the original question as new answers arrive", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "补充风险");
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 1");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  expect(p.onCoachRefine).toHaveBeenCalledWith("answer-1", "补充风险");
  await user.dblClick(screen.getByRole("button", { name: "提交补充" }));
  expect(p.onCoachRefine).toHaveBeenCalledTimes(1);
});

it("keeps a quick adjustment attached to its question as new answers arrive", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} />);
  await user.click(screen.getByRole("button", { name: "常用调整" }));
  await user.click(screen.getByRole("button", { name: "补充关键风险与适用边界" }));
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 1");
  expect(screen.getByRole("textbox", { name: "调整当前回答" })).toHaveValue("补充关键风险与适用边界");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  expect(p.onCoachRefine).toHaveBeenCalledWith("answer-1", "补充关键风险与适用边界");
});

it("keeps a new participation proposal visible while preserving a Fast answer being read", async () => {
  const p = props(); const view = render(<NowRail {...p} />);
  const reader = screen.getByRole("region", { name: "建议阅读区" }); reader.scrollTop = 180; fireEvent.scroll(reader);
  fireEvent.change(screen.getByRole("textbox", { name: "调整当前回答" }), { target: { value: "保留正在编辑的回应" } });
  const proposal: CoachHistoryEntry = { ...revision("participation-job"), answerId: undefined, promptProfile: "participation", analysisKind: "participation", proposalType: "professional_proposal", title: "独立通知链路", question: "通知能否异步化？", sayThis: "我建议订单与通知分开确认成功。" };
  view.rerender(<NowRail {...p} followUp={proposal} coachHistory={[proposal]} />);
  expect(screen.getByText("我建议订单与通知分开确认成功。")).toBeVisible();
  expect(screen.getByText("专业提议 · 供讨论")).toBeVisible();
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("原始完整正文");
  expect(screen.getByRole("textbox", { name: "调整当前回答" })).toHaveValue("保留正在编辑的回应");
  expect(reader.scrollTop).toBe(180);
  view.unmount();
});

it("shows meeting-level soft timeout and due progress without stealing a typed answer or reading position", async () => {
  const user = userEvent.setup(); const p = props(); const view = render(<NowRail {...p} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "保留我的调整");
  const reader = screen.getByRole("region", { name: "建议阅读区" }); reader.scrollTop = 180; fireEvent.scroll(reader);
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} coachDecision={{ status: "timed_out", answerId: "answer-2" }}
    coachDueItems={[{ itemId: "due-2", status: "due", nextCheckAtMs: 123, evidenceSegmentIds: [], evidenceQuote: "", createdAtMs: 100 }]} />);
  expect(screen.getByRole("status", { name: "当前会议的 Pi 状态" })).toHaveTextContent("本轮未及时完成");
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 1");
  expect(screen.getByRole("textbox", { name: "调整当前回答" })).toHaveValue("保留我的调整");
  expect(reader.scrollTop).toBe(180); expect(p.onCoachRefine).not.toHaveBeenCalled();
});

it("restores reading position after opening history and never replaces the current answer", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} />);
  const reader = screen.getByRole("region", { name: "建议阅读区" });
  reader.scrollTop = 240;
  fireEvent.scroll(reader);
  await user.click(screen.getByRole("button", { name: "历史 1" }));
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} />);
  await user.keyboard("{Escape}");
  expect(reader.scrollTop).toBe(240);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 1");
  expect(screen.getByRole("button", { name: "历史 2" })).toHaveFocus();
  await user.click(screen.getByRole("button", { name: /1 条新建议.*查看最新/ }));
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 2");
  expect(reader.scrollTop).toBe(0);
});

it("replaces the pending placeholder in place, preserving the original answer and each adjustment", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "补充风险");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  const pending = await screen.findByRole("region", { name: "调整记录" });
  expect(pending).toHaveTextContent("正在补充这条回答");
  view.rerender(<NowRail {...p} coachHistory={[revision("refine-1")]} />);
  expect(screen.getByRole("region", { name: "调整记录" })).toBe(pending);
  expect(pending).toHaveTextContent("Pi 的新增内容 1");
  expect(pending).not.toHaveTextContent("正在补充这条回答");
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("原始完整正文");
});

it("stops following a pending result when the reader scrolls upward", async () => {
  const user = userEvent.setup();
  const p = props();
  const scroll = vi.fn();
  const view = render(<NowRail {...p} />);
  const reader = screen.getByRole("region", { name: "建议阅读区" });
  reader.scrollTo = scroll;
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "补充风险");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  await waitFor(() => expect(p.onCoachRequestStatus).toHaveBeenCalled());
  expect(scroll).toHaveBeenCalled();
  fireEvent.wheel(reader, { deltaY: -90 });
  scroll.mockClear();
  view.rerender(<NowRail {...p} coachHistory={[revision("refine-1")]} />);
  expect(scroll).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /本题有新补充/ })).toBeVisible();
});

it("keeps an in-flight submission across navigation without enabling a duplicate request", async () => {
  const user = userEvent.setup();
  let resolve!: (jobId: string) => void;
  const p = { ...props(), onCoachRefine: vi.fn(() => new Promise<string>((done) => { resolve = done; })) };
  const first = render(<NowRail {...p} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "补充风险");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  first.unmount();
  render(<NowRail {...p} />);
  expect(screen.getByRole("button", { name: "提交补充" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "重试这次补充" })).not.toBeInTheDocument();
  await act(async () => resolve("job-after-navigation"));
  await waitFor(() => expect(p.onCoachRequestStatus).toHaveBeenCalledWith("job-after-navigation", expect.any(AbortSignal)));
  expect(p.onCoachRefine).toHaveBeenCalledTimes(1);
});

it("keeps a failed adjustment on its question when the user is reading another question", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "补充风险");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} />);
  await user.click(screen.getByRole("button", { name: /1 条新建议.*查看最新/ }));
  await user.click(screen.getByRole("button", { name: /另一问题正在补充/ }));
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 1");
  const fail = vi.fn().mockResolvedValue("failed");
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} onCoachRequestStatus={fail} />);
  await user.click(await screen.findByRole("button", { name: "重试这次补充" }));
  expect(p.onCoachRefine).toHaveBeenLastCalledWith("answer-1", "补充风险");
  expect(within(screen.getByTestId("answer-copilot-card")).getByText("回答 1：这是原始完整正文。")).toBeVisible();
});

it("allows keyboard resizing and keeps the last chosen column ratio", async () => {
  const user = userEvent.setup();
  const view = render(<MeetingSplitPane><section>文字稿</section><section>教练</section></MeetingSplitPane>);
  const divider = screen.getByRole("separator", { name: "调整文字稿与教练宽度" });
  divider.focus();
  await user.keyboard("{ArrowRight}{ArrowRight}");
  expect(divider).toHaveAttribute("aria-valuenow", "46");
  view.unmount();
  render(<MeetingSplitPane>正文</MeetingSplitPane>);
  expect(screen.getByRole("separator")).toHaveAttribute("aria-valuenow", "46");
});

it("offers a direct path to a result that completes while another question is being read", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "补充风险");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} />);
  await user.click(screen.getByRole("button", { name: /1 条新建议.*查看最新/ }));
  const complete = vi.fn().mockResolvedValue("succeeded");
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} coachHistory={[revision("refine-1")]} onCoachRequestStatus={complete} />);
  const notice = await screen.findByRole("button", { name: /另一问题的补充已完成/ });
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 2");
  await user.click(notice);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 1");
  expect(screen.getByRole("region", { name: "调整记录" })).toHaveTextContent("Pi 的新增内容 1");
});

it("appends a live Pi projection after stored revisions before history catches up", () => {
  const p = props();
  const first = revision("auto-1");
  const live = { ...revision("auto-2", 2), createdAtMs: undefined };
  const view = render(<NowRail {...p} coachHistory={[first]} />);
  view.rerender(<NowRail {...p} coachHistory={[first]} followUp={live} />);
  const supplements = screen.getAllByRole("region", { name: "Pi 补充" });
  expect(supplements[0]).toHaveTextContent("Pi 的新增内容 1");
  expect(supplements[1]).toHaveTextContent("Pi 的新增内容 2");
});

it("switches a revised live answer to its valid replacement without submitting the old adjustment", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "原回答的调整");
  const reader = screen.getByRole("region", { name: "建议阅读区" });
  reader.scrollTop = 240;
  fireEvent.scroll(reader);
  const old = { ...answer(1), status: "superseded" as const, errorClass: "transcript_revised" };
  const update = { ...answer(2), questionText: old.questionText, evidenceTranscriptSeq: old.evidenceTranscriptSeq,
    supersedesSuggestionId: old.suggestionId, text: "依据修正为未验收，应先完成验收。" };
  view.rerender(<NowRail {...p} suggestions={[old, update]} />);
  expect(screen.queryByRole("region", { name: "回答修订状态" })).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "调整当前回答" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "提交补充" })).toBeDisabled();
  expect(p.onCoachRefine).not.toHaveBeenCalled();
  expect(screen.queryByText(/本次回答未完成/)).not.toBeInTheDocument();
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent(update.text!);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("修订后的回答");
  expect(reader.scrollTop).toBe(0);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "补充验收依据");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  expect(p.onCoachRefine).toHaveBeenCalledWith(update.suggestionId, "补充验收依据");
});

it("keeps the live rail quiet when only expired records exist and shows refresh states in explicit history", async () => {
  const user = userEvent.setup();
  const p = props();
  const old = { ...answer(1), status: "superseded" as const, errorClass: "transcript_revised" };
  const view = render(<NowRail {...p} suggestions={[old]} />);
  expect(screen.queryByTestId("answer-copilot-card")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看问答历史 1" }));
  await user.click(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getByRole("button"));
  expect(screen.getByRole("region", { name: "回答修订状态" })).toHaveTextContent("暂无可用的更新回答");
  expect(screen.queryByText(/更新回答正在生成/)).not.toBeInTheDocument();
  const updating = { ...answer(2), text: null, draftText: "", status: "draft" as const,
    supersedesSuggestionId: old.suggestionId };
  view.rerender(<NowRail {...p} suggestions={[old, updating]} />);
  expect(screen.getByRole("region", { name: "回答修订状态" })).toHaveTextContent("更新回答正在生成");
  expect(screen.queryByText(/正在生成可直接说出口的回答/)).not.toBeInTheDocument();
  view.rerender(<NowRail {...p} suggestions={[old, { ...updating, status: "rejected", errorClass: "provider_timeout" }]} />);
  expect(screen.getByRole("region", { name: "回答修订状态" })).toHaveTextContent("更新回答未完成，请核对最新文字稿");
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent(old.text!);
  expect(screen.queryByRole("button", { name: "查看更新回答" })).not.toBeInTheDocument();
});

it("follows a refresh chain through an interrupted draft without opening an unrelated new question", async () => {
  const user = userEvent.setup();
  const p = props();
  const old = { ...answer(1), status: "superseded" as const, errorClass: "transcript_revised" };
  const view = render(<NowRail {...p} suggestions={[old]} />);
  await user.click(screen.getByRole("button", { name: "查看问答历史 1" }));
  await user.click(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getByRole("button"));
  const interrupted = { ...answer(2), status: "superseded" as const, errorClass: "transcript_revised",
    supersedesSuggestionId: old.suggestionId };
  const updated = { ...answer(3), questionText: old.questionText, text: "这是再次修订后有效的回答。",
    supersedesSuggestionId: interrupted.suggestionId };
  view.rerender(<NowRail {...p} suggestions={[old, interrupted, updated, answer(4)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent(old.text!);
  await user.click(screen.getByRole("button", { name: "查看更新回答" }));
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent(updated.text!);
  expect(screen.getByTestId("answer-copilot-card")).not.toHaveTextContent("问题 4");
});

it("never retries a failed adjustment after its answer evidence was revised", async () => {
  const user = userEvent.setup();
  const p = { ...props(), onCoachRequestStatus: vi.fn().mockResolvedValue("failed") };
  const view = render(<NowRail {...p} />);
  await user.type(screen.getByRole("textbox", { name: "调整当前回答" }), "补充风险");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  await screen.findByRole("button", { name: "重试这次补充" });
  view.rerender(<NowRail {...p} suggestions={[{ ...answer(1), status: "superseded", errorClass: "transcript_revised" }]} />);
  expect(screen.queryByRole("button", { name: "重试这次补充" })).not.toBeInTheDocument();
  expect(screen.queryByTestId("answer-copilot-card")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看问答历史 1" }));
  await user.click(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getByRole("button"));
  expect(screen.getByRole("region", { name: "调整记录" })).toHaveTextContent("请在有效的更新回答中重新提交");
  expect(p.onCoachRefine).toHaveBeenCalledTimes(1);
});

it("labels expired records in history and copied content", async () => {
  const user = userEvent.setup();
  const p = props();
  const old = { ...answer(1), status: "superseded" as const, errorClass: "transcript_revised" };
  render(<NowRail {...p} suggestions={[old]} coachHistory={[revision("historical-pi")]} />);
  const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  await user.click(screen.getByRole("button", { name: "查看问答历史 1" }));
  await user.click(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getByRole("button"));
  await user.click(screen.getByRole("button", { name: "复制完整建议" }));
  expect(clipboard).toHaveBeenCalledWith(expect.stringContaining("此回答及其补充已过期"));
  expect(clipboard).toHaveBeenCalledWith(expect.stringContaining(old.text!));
  expect(clipboard).toHaveBeenCalledWith(expect.stringContaining("Pi 的新增内容 1"));
  await user.click(screen.getByRole("button", { name: "历史 1" }));
  expect(screen.getByRole("list", { name: "回答与 Pi 历史" })).toHaveTextContent("已过期 · 原文修订");
});

it("follows new valid answers by default instead of retaining the first card indefinitely", () => {
  const p = props();
  const view = render(<NowRail {...p} />);
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 2");
  expect(screen.getByText("当前建议")).toBeVisible();
  expect(screen.queryByText("1 条新建议")).not.toBeInTheDocument();
});

it("does not restore a historical session selection into the live main card", () => {
  sessionStorage.setItem("meeting-copilot-coach-reader:focus-test", JSON.stringify({ answerId: "answer-1", positions: {}, drafts: { "answer-1": "保留旧题草稿" } }));
  render(<NowRail {...props()} suggestions={[answer(1), answer(2)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 2");
  expect(screen.getByRole("textbox", { name: "调整当前回答" })).toHaveValue("");
});

it("preserves an explicitly selected historical answer, labels it, and can return to live", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} suggestions={[answer(1), answer(2)]} />);
  await user.click(screen.getByRole("button", { name: "历史 2" }));
  await user.click(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getByRole("button", { name: /问题 1/ }));
  view.rerender(<NowRail {...p} suggestions={[answer(1), answer(2), answer(3)]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 1");
  expect(screen.getByText("历史记录")).toBeVisible();
  expect(screen.getByTestId("answer-copilot-card")).not.toHaveAttribute("data-ui-render-card");
  await user.click(screen.getByRole("button", { name: "返回当前状态" }));
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent("问题 3");
  expect(screen.getByText("当前建议")).toBeVisible();
});

it("keeps an explicitly opened history list available when the live answer becomes invalid", async () => {
  const user = userEvent.setup();
  const p = props();
  const view = render(<NowRail {...p} />);
  await user.click(screen.getByRole("button", { name: "历史 1" }));
  view.rerender(<NowRail {...p} suggestions={[{ ...answer(1), status: "superseded", errorClass: "transcript_revised" }]} />);
  expect(screen.getByRole("list", { name: "回答与 Pi 历史" })).toHaveTextContent("已过期 · 原文修订");
  await user.click(screen.getByRole("button", { name: "返回当前状态" }));
  expect(screen.queryByTestId("answer-copilot-card")).not.toBeInTheDocument();
});

it("keeps a valid Fast answer during coach silence but removes the bound old Pi supplement", () => {
  const p = props();
  const pi = revision("pi-original");
  const view = render(<NowRail {...p} coachHistory={[pi]} />);
  expect(screen.getByRole("region", { name: "Pi 补充" })).toHaveTextContent("Pi 的新增内容 1");
  view.rerender(<NowRail {...p} coachHistory={[pi]} coachDecision={{ status: "protected_silent", answerId: "answer-1", lifecycleAction: "deprioritize" }} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent(answer(1).text!);
  expect(screen.queryByRole("region", { name: "Pi 补充" })).not.toBeInTheDocument();
});

it("does not apply another answer's quiet decision to the current answer supplement", () => {
  render(<NowRail {...props()} coachHistory={[revision("pi-current")]}
    coachDecision={{ status: "protected_silent", answerId: "answer-2", lifecycleAction: "deprioritize" }} />);
  expect(screen.getByRole("region", { name: "Pi 补充" })).toHaveTextContent("Pi 的新增内容 1");
});

it.each([
  { status: "stale" as const },
  { lifecycleStatus: "resolved" as const },
  { lifecycleStatus: "retracted" as const },
  { lifecycleStatus: "superseded" as const },
  { lifecycleAction: "retract" as const },
  { lifecycleAction: "deprioritize" as const },
  { validUntil: 1 },
  { supersededBy: "replacement-decision" },
])("keeps an invalid Pi revision out of the live answer (%j)", (invalid) => {
  render(<NowRail {...props()} coachHistory={[{ ...revision("pi-invalid"), ...invalid }]} />);
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent(answer(1).text!);
  expect(screen.queryByRole("region", { name: "Pi 补充" })).not.toBeInTheDocument();
});

it("expires a Pi supplement without waiting for a new transcript or snapshot", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-04T08:00:00Z"));
  render(<NowRail {...props()} coachHistory={[{ ...revision("pi-expiring"), validUntil: Date.now() + 1000 }]} />);
  expect(screen.getByRole("region", { name: "Pi 补充" })).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(1026); });
  expect(screen.queryByRole("region", { name: "Pi 补充" })).not.toBeInTheDocument();
  expect(screen.getByTestId("answer-copilot-card")).toHaveTextContent(answer(1).text!);
});

it("shows a quiet live state when only superseded answers remain, with explicit historical access", async () => {
  const user = userEvent.setup();
  render(<NowRail {...props()} suggestions={[{ ...answer(1), status: "superseded", errorClass: "transcript_revised" }]}
    coachDecision={{ status: "protected_silent" }} />);
  expect(screen.queryByTestId("answer-copilot-card")).not.toBeInTheDocument();
  expect(screen.getByRole("status", { name: "当前会议的 Pi 状态" })).toHaveTextContent("本轮保持静默");
  await user.click(screen.getByRole("button", { name: "查看问答历史 1" }));
  await user.click(within(screen.getByRole("list", { name: "回答与 Pi 历史" })).getByRole("button"));
  expect(screen.getByText("过期记录")).toBeVisible();
  expect(screen.getByTestId("answer-copilot-card")).not.toHaveAttribute("data-ui-render-card");
  await user.click(screen.getByRole("button", { name: "返回当前状态" }));
  expect(screen.queryByTestId("answer-copilot-card")).not.toBeInTheDocument();
  expect(screen.getByRole("status", { name: "当前会议的 Pi 状态" })).toHaveTextContent("本轮保持静默");
});
