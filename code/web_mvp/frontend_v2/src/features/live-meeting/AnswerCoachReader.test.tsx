import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import type { CoachHistoryEntry, Suggestion } from "../../domain/events";
import { NowRail } from "./NowRail";
import type { NowRailProps } from "./nowRailTypes";
import { MeetingSplitPane } from "./MeetingSplitPane";

afterEach(() => { sessionStorage.clear(); localStorage.clear(); });
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
