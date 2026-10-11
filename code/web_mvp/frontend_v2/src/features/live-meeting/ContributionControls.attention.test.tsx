import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DiscussionWorkItem } from "../../api/client";
import type { RuntimeIndicator } from "../../domain/events";
import { ContributionControls } from "./ContributionControls";

afterEach(() => { sessionStorage.clear(); });

const runtime: RuntimeIndicator = {
  state: "active", label: "宿主状态", level: null, detail: null, decision: "succeeded",
  topicId: "topic-one", analysisBasisRevision: 8, inputTranscriptSeq: 10, lastSemanticSeq: 10,
};
function snapshot() {
  return {
    current_focus: {
      observations: [
        { evidence_segment_ids: ["s9"], evidence_quote: "来三百六十度原地转转" },
        { evidence_segment_ids: ["s10"], evidence_quote: "慢点，重新转转" },
      ],
      relation: "interlude", object: null, setting: null, purpose: null,
    },
    contribution_opportunity: {
      status: "none", intent: null as string | null, reason: "最新交流未提出需要讨论的判断或澄清问题。",
      evidence_segment_ids: ["s10"], evidence_quote: "慢点，重新转转", opportunity_id: null as string | null,
    },
    contract: "discussion_attention.v1", control_revision: 1, opportunity_id: null as string | null,
    covered_seq: 10,
    source_bindings: [
      { segment_id: "s9", revision: 2, evidence_hash: "a".repeat(64) },
      { segment_id: "s10", revision: 2, evidence_hash: "b".repeat(64) },
    ],
  };
}
function discussion(attention: unknown = snapshot(), overrides: Record<string, unknown> = {}): DiscussionWorkItem {
  return {
    id: "topic-one", title: "IOI店铺经营与评价情况", state: "investigating", version: 4,
    context: { analysis_basis_revision: 8, last_semantic_seq: 10, last_checked_seq: 10,
      phase: "discussing", current_attention: attention },
    ...overrides,
  };
}
function show(item = discussion(), currentRuntime: RuntimeIndicator = runtime) {
  const contribute = vi.fn().mockResolvedValue("manual-job");
  const read = vi.fn().mockResolvedValue(item);
  const view = render(<ContributionControls viewStateKey="meeting-one" currentTopic={null}
    discussionRuntime={currentRuntime} onReadDiscussion={read} onContribute={contribute} />);
  fireEvent.click(view.container.querySelector(".contribution-topic-details > summary")!);
  return { ...view, contribute, read };
}

describe("current exchange and participation judgment", () => {
  it("separates a confirmed interlude from the retained historical topic and normal waiting", async () => {
    const { contribute } = show();
    await screen.findByText("已记录的议题：IOI店铺经营与评价情况");
    expect(screen.getByRole("region", { name: "最近交流与参与判断" })).toBeVisible();
    expect(screen.getByText("来三百六十度原地转转")).toBeVisible();
    expect(screen.getByText("慢点，重新转转")).toBeVisible();
    expect(screen.getByText("暂时无需补充，继续聆听。" )).toBeVisible();
    expect(screen.getByText("最新交流未提出需要讨论的判断或澄清问题。" )).toBeVisible();
    expect(screen.queryByText(/正在讨论：IOI/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/已结束|建议已生成/)).not.toBeInTheDocument();
    expect(contribute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    await waitFor(() => expect(contribute).toHaveBeenCalledWith("ask_question"));
  });

  it.each(["unknown", "uncertain"])("keeps an unconfirmed relationship historical while allowing an explicit opportunity (%s)", async (relation) => {
    const attention = snapshot();
    attention.current_focus.relation = relation;
    attention.current_focus.observations = [{ evidence_segment_ids: ["s10"], evidence_quote: "这个操作会删除历史吗？" }];
    attention.contribution_opportunity = { status: "present", intent: "ask_question", reason: "有一个尚未确认的删除风险，可以当场澄清。",
      evidence_segment_ids: ["s10"], evidence_quote: "这个操作会删除历史吗？", opportunity_id: null };
    attention.opportunity_id = "opportunity:risk-one";
    const { contribute } = show(discussion(attention));
    await screen.findByText("已记录的议题：IOI店铺经营与评价情况");
    expect(screen.getByText("这个操作会删除历史吗？")).toBeVisible();
    expect(screen.getByText("有可参与的讨论点，可主动请求建议。" )).toBeVisible();
    expect(screen.getByText("有一个尚未确认的删除风险，可以当场澄清。" )).toBeVisible();
    expect(screen.queryByText(/建议已生成/)).not.toBeInTheDocument();
    expect(contribute).not.toHaveBeenCalled();
  });

  it("shows uncertainty as an ordinary pending judgment, never as a failure", async () => {
    const attention = snapshot(); attention.contribution_opportunity.status = "uncertain";
    attention.contribution_opportunity.reason = "还需要确认这句是否在回应刚才的问题。";
    show(discussion(attention));
    await screen.findByText("参与时机尚不确定，继续聆听。" );
    expect(screen.getByText("还需要确认这句是否在回应刚才的问题。" )).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each(["same_topic", "changed"])("still presents a supported current topic for %s", async (relation) => {
    const attention = snapshot(); attention.current_focus.relation = relation;
    show(discussion(attention));
    await screen.findByText("正在讨论：IOI店铺经营与评价情况");
    expect(screen.getByRole("region", { name: "最近交流与参与判断" })).toBeVisible();
  });

  it.each([
    ["unregistered contract", (a: ReturnType<typeof snapshot>) => { a.contract = "unknown-contract"; }],
    ["zero control revision", (a: ReturnType<typeof snapshot>) => { a.control_revision = 0; }],
    ["fractional coverage", (a: ReturnType<typeof snapshot>) => { a.covered_seq = 9.5; }],
    ["future coverage", (a: ReturnType<typeof snapshot>) => { a.covered_seq = 11; }],
    ["withdrawn observations", (a: ReturnType<typeof snapshot>) => { a.current_focus.observations = []; }],
    ["withdrawn bindings", (a: ReturnType<typeof snapshot>) => { a.source_bindings = []; }],
    ["unbound quote", (a: ReturnType<typeof snapshot>) => { a.current_focus.observations[0].evidence_segment_ids = ["foreign"]; }],
    ["invalid binding", (a: ReturnType<typeof snapshot>) => { a.source_bindings[0].revision = 0; }],
    ["oversized quote", (a: ReturnType<typeof snapshot>) => { a.current_focus.observations[0].evidence_quote = "长".repeat(301); }],
    ["oversized reason", (a: ReturnType<typeof snapshot>) => { a.contribution_opportunity.reason = "长".repeat(201); }],
    ["unsupported status", (a: ReturnType<typeof snapshot>) => { a.contribution_opportunity.status = "ready"; }],
    ["none with intent", (a: ReturnType<typeof snapshot>) => { a.contribution_opportunity.intent = "ask_question"; }],
    ["present without host identity", (a: ReturnType<typeof snapshot>) => { a.contribution_opportunity.status = "present"; a.contribution_opportunity.intent = "ask_question"; }],
    ["unknown freeform field", (a: ReturnType<typeof snapshot>) => { Object.assign(a.current_focus, { title: "猜测的店内展示" }); }],
    ["unknown host field", (a: ReturnType<typeof snapshot>) => { Object.assign(a, { label: "未经确认的现场" }); }],
    ["invalid hash", (a: ReturnType<typeof snapshot>) => { a.source_bindings[0].evidence_hash = "not-a-hash"; }],
    ["repeated binding", (a: ReturnType<typeof snapshot>) => { a.source_bindings.push(a.source_bindings[0]); }],
    ["freeform setting", (a: ReturnType<typeof snapshot>) => { Object.assign(a.current_focus, { setting: "店内" }); }],
    ["unsafe text", (a: ReturnType<typeof snapshot>) => { a.contribution_opportunity.reason = "原因\u0000"; }],
  ])("does not render invalid or revoked snapshots as current (%s)", async (_name, mutate) => {
    const attention = snapshot(); mutate(attention); show(discussion(attention));
    await screen.findByText("已记录的议题：IOI店铺经营与评价情况");
    expect(screen.queryByRole("region", { name: "最近交流与参与判断" })).not.toBeInTheDocument();
    expect(screen.queryByText("慢点，重新转转")).not.toBeInTheDocument();
    expect(screen.queryByText("有可参与的讨论点，可主动请求建议。" )).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "我可以问什么" })).toBeEnabled();
  });

  it.each([
    { ...runtime, inputTranscriptSeq: 11, decision: "running" },
    { ...runtime, lastSemanticSeq: 0, decision: "not_started", understandingStatus: "waiting_reanalysis" as const },
    { ...runtime, topicId: "different-topic" },
    { ...runtime, analysisBasisRevision: 9 },
  ])("does not call a stale or different discussion snapshot the latest exchange (%j)", async (currentRuntime) => {
    show(discussion(), currentRuntime);
    await screen.findByText("已记录的议题：IOI店铺经营与评价情况");
    expect(screen.queryByRole("region", { name: "最近交流与参与判断" })).not.toBeInTheDocument();
    expect(screen.queryByText("慢点，重新转转")).not.toBeInTheDocument();
    expect(screen.getByText(/等待重新理解/)).toBeVisible();
  });

  it("does not use an attention revoked by material changes even with a preserved semantic cursor", async () => {
    const item = discussion(); item.context.phase = "needs_review";
    show(item);
    await screen.findByText("已记录的议题：IOI店铺经营与评价情况");
    expect(screen.queryByRole("region", { name: "最近交流与参与判断" })).not.toBeInTheDocument();
  });

  it("does not retain old observations while a different meeting is being read", async () => {
    const { rerender, contribute, read } = show();
    await screen.findByText("慢点，重新转转");
    let finish!: (item: DiscussionWorkItem | null) => void;
    const nextRead = vi.fn(() => new Promise<DiscussionWorkItem | null>((resolve) => { finish = resolve; }));
    rerender(<ContributionControls viewStateKey="different-meeting" currentTopic={null}
      discussionRuntime={runtime} onReadDiscussion={nextRead} onContribute={contribute} />);
    await waitFor(() => expect(nextRead).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("慢点，重新转转")).not.toBeInTheDocument();
    finish(discussion(snapshot(), { meeting_id: "meeting-one" }));
    await screen.findByText("已记录的议题：IOI店铺经营与评价情况");
    expect(screen.queryByRole("region", { name: "最近交流与参与判断" })).not.toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(1); expect(contribute).not.toHaveBeenCalled();
  });

  it("keeps the legacy discussion and manual correction flow available", async () => {
    const item = discussion(); delete item.context.current_attention;
    const update = vi.fn().mockResolvedValue(item); const contribute = vi.fn();
    render(<ContributionControls viewStateKey="legacy" currentTopic={null} onReadDiscussion={vi.fn().mockResolvedValue(item)}
      onContribute={contribute} onUpdateDiscussion={update} />);
    await screen.findByText("正在讨论：IOI店铺经营与评价情况");
    expect(screen.queryByRole("region", { name: "最近交流与参与判断" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("议题与判断"));
    fireEvent.click(screen.getByRole("button", { name: "不是当前话题？纠正" }));
    expect(screen.getByRole("textbox", { name: "纠正当前议题" })).toBeVisible();
    expect(update).not.toHaveBeenCalled(); expect(contribute).not.toHaveBeenCalled();
  });
});
