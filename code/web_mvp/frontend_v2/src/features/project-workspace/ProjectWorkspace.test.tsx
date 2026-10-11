import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ProjectOutcomesApi, ProjectProposal, ProjectSourceRef, ProjectStateItem } from "../../api/projectOutcomes";
import { ProjectWorkspace } from "./ProjectWorkspace";
import { ProjectSources } from "./ProjectSources";
import { MeetingProjectState } from "./MeetingProjectState";

const empty = { items: [], nextCursor: null, hasMore: false };
const source: ProjectSourceRef = { sourceType: "document", sourceId: "span", documentId: "doc", projectId: "p", meetingId: null, title: "预算资料", versionId: "v1", version: 1, contentHash: "h1", ordinal: 0, quote: "预算已确认。", locator: { type: "pdf_page", page: 1 } };
const item: ProjectStateItem = { id: "item", projectId: "p", type: "decision", text: "预算不超过五万元。", status: "active", owner: null, due: null, sourceMeetingId: null, sourceRefs: [source], origin: "user", revision: 2, supersedesId: null };
const proposal: ProjectProposal = { id: "proposal", projectId: "p", meetingId: "meeting", origin: "ai", baseRevision: 7, changes: [{ action: "create", type: "task", text: "核对预算", owner: null, due: null, sourceRefs: [source] }], status: "pending", revision: 1, isStale: false };
export function projectApiFixture(): ProjectOutcomesApi {
  return { listState: vi.fn().mockResolvedValue({ ...empty, items: [item], revision: 7 }), createState: vi.fn().mockResolvedValue({ item, revision: 8 }), updateState: vi.fn().mockResolvedValue({ item, revision: 8 }), history: vi.fn().mockResolvedValue({ items: [{ snapshot: item, reason: "updated" }], nextBeforeRevision: null }), listProposals: vi.fn().mockResolvedValue({ ...empty, items: [proposal] }), createProposal: vi.fn().mockResolvedValue(proposal), confirmProposal: vi.fn().mockResolvedValue({ proposal: { ...proposal, status: "confirmed" }, items: [item], revision: 8 }), rejectProposal: vi.fn().mockResolvedValue({ ...proposal, status: "rejected" }), getWiki: vi.fn().mockResolvedValue({ projectId: "p", revision: 7, markdown: "# 项目状态\n- 已确认预算", items: [item], truncated: false }), getMeetingState: vi.fn().mockResolvedValue({ projectId: "p", revision: 7, decisions: [item], unfinishedTasks: [], openQuestions: [], truncated: false }), getSource: vi.fn().mockResolvedValue(source) };
}
describe("project confirmation workspace", () => {
  it("requires explicit confirm/reject and keeps candidates out of current Wiki", async () => {
    const api = projectApiFixture(); const user = userEvent.setup(); render(<ProjectWorkspace api={api} projectId="p" />);
    await screen.findByText(item.text); expect(api.confirmProposal).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "待审核候选" })); await screen.findByText("核对预算");
    await user.click(screen.getByRole("button", { name: "确认这些变更" })); await waitFor(() => expect(api.confirmProposal).toHaveBeenCalledWith("proposal", 1, 7));
    await user.click(screen.getByRole("button", { name: "拒绝候选" })); await waitFor(() => expect(api.rejectProposal).toHaveBeenCalledWith("proposal", 1));
    await user.click(screen.getByRole("button", { name: "项目 Wiki" })); expect(screen.getByText("已确认预算")).toBeInTheDocument(); expect(screen.queryByText("核对预算")).not.toBeInTheDocument();
  });
  it("blocks stale confirmation while allowing explicit rejection", async () => {
    const api = projectApiFixture(); vi.mocked(api.listProposals).mockResolvedValue({ ...empty, items: [{ ...proposal, isStale: true, status: "needs_review" }] }); render(<ProjectWorkspace api={api} projectId="p" />);
    await screen.findByText(item.text); fireEvent.click(screen.getByRole("button", { name: "待审核候选" })); expect(screen.getByRole("button", { name: "确认这些变更" })).toBeDisabled(); expect(screen.getByRole("button", { name: "拒绝候选" })).toBeEnabled(); expect(api.confirmProposal).not.toHaveBeenCalled();
  });
  it("does not ask to reject an already confirmed proposal when confirmation advanced the project revision", async () => {
    const api = projectApiFixture(); const user = userEvent.setup();
    vi.mocked(api.listProposals).mockResolvedValueOnce({ ...empty, items: [proposal] })
      .mockResolvedValue({ ...empty, items: [{ ...proposal, status: "confirmed", isStale: true }] });
    render(<ProjectWorkspace api={api} projectId="p" initialView="proposals" />);
    await user.click(await screen.findByRole("button", { name: "确认这些变更" }));
    await screen.findByText("候选已确认，项目状态与 Wiki 已更新。");
    await screen.findByText("已确认");
    expect(screen.queryByText(/请拒绝旧候选/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认这些变更" })).not.toBeInTheDocument();
  });
  it("preserves drafts on revision conflicts, reloads latest revisions only on demand", async () => {
    const api = projectApiFixture(); vi.mocked(api.updateState).mockRejectedValueOnce(new Error("事项已更新，请刷新。")); const user = userEvent.setup(); render(<ProjectWorkspace api={api} projectId="p" />);
    await user.click(await screen.findByRole("button", { name: "修改事项" })); const field = screen.getByRole("textbox", { name: "项目事项内容" }); await user.clear(field); await user.type(field, "我的修正"); await user.click(screen.getByRole("button", { name: "确认并保存事项" }));
    expect(api.updateState).toHaveBeenCalledWith("item", expect.objectContaining({ text: "我的修正" }), 2, 7); expect(field).toHaveValue("我的修正");
    vi.mocked(api.listState).mockResolvedValue({ ...empty, items: [{ ...item, revision: 3, text: "别人修改" }], revision: 8 }); vi.mocked(api.history).mockResolvedValue({ items: [{ snapshot: { ...item, revision: 3, text: "别人修改" }, reason: "updated" }], nextBeforeRevision: null });
    await user.click(screen.getByRole("button", { name: "重新载入最新版本（保留我的草稿）" })); await screen.findByText("最新已保存内容：别人修改"); expect(field).toHaveValue("我的修正");
    await user.click(screen.getByRole("button", { name: "确认并保存事项" })); await waitFor(() => expect(api.updateState).toHaveBeenLastCalledWith("item", expect.objectContaining({ text: "我的修正" }), 3, 8));
  });
  it("requires independent manual reconfirmation after a source changed", async () => {
    const api = projectApiFixture(); vi.mocked(api.listState).mockResolvedValue({ ...empty, items: [{ ...item, status: "needs_review" }], revision: 7 }); const user = userEvent.setup(); render(<ProjectWorkspace api={api} projectId="p" />);
    await user.click(await screen.findByRole("button", { name: "修改事项" })); expect(screen.getByRole("button", { name: "确认并保存事项" })).toBeDisabled(); await user.click(screen.getByRole("checkbox", { name: /我已独立核实/ })); await user.click(screen.getByRole("button", { name: "确认并保存事项" })); await waitFor(() => expect(api.updateState).toHaveBeenCalledWith("item", expect.objectContaining({ sourceRefs: [], status: "active" }), 2, 7));
  });
  it("adds explicit user tasks without inferred owner or due and uses opaque pagination", async () => {
    const api = projectApiFixture(); vi.mocked(api.listState).mockResolvedValue({ items: [], revision: 7, nextCursor: "opaque+/=", hasMore: true }); const user = userEvent.setup(); render(<ProjectWorkspace api={api} projectId="p" />);
    await user.click(await screen.findByRole("button", { name: "添加已确认事项" })); await user.selectOptions(screen.getByRole("combobox", { name: "项目事项类型" }), "task"); await user.type(screen.getByRole("textbox", { name: "项目事项内容" }), "复核开销"); await user.click(screen.getByRole("button", { name: "确认并保存事项" })); await waitFor(() => expect(api.createState).toHaveBeenCalledWith("p", { type: "task", text: "复核开销", status: "active", owner: null, due: null }, 7)); await user.click(screen.getByRole("button", { name: "下一页事项" })); await waitFor(() => expect(api.listState).toHaveBeenLastCalledWith("p", null, "opaque+/=", expect.any(AbortSignal)));
  });
  it("paginates item history and renders real snapshots", async () => {
    const api = projectApiFixture(); vi.mocked(api.history).mockResolvedValueOnce({ items: [{ snapshot: item, reason: "updated" }], nextBeforeRevision: 2 }).mockResolvedValue({ items: [{ snapshot: { ...item, revision: 1, text: "先前预算" }, reason: "created" }], nextBeforeRevision: null }); const user = userEvent.setup(); render(<ProjectWorkspace api={api} projectId="p" />);
    await user.click(await screen.findByRole("button", { name: "修改历史" })); const dialog = screen.getByRole("dialog", { name: "项目事项修改历史" }); await user.click(within(dialog).getByRole("button", { name: "更早修改" })); await within(dialog).findByText("先前预算"); expect(api.history).toHaveBeenLastCalledWith("item", 2);
  });
});
describe("project source and meeting scope", () => {
  it.each(["new_version", "backend_error"])("never substitutes the newest source after %s", async (kind) => {
    const api = projectApiFixture(); if (kind === "new_version") vi.mocked(api.getSource).mockResolvedValue({ ...source, versionId: "v2", version: 2, contentHash: "h2", quote: "新版" }); else vi.mocked(api.getSource).mockRejectedValue(new Error("来源已失效。")); const open = vi.fn(); const user = userEvent.setup(); render(<ProjectSources api={api} sources={[source]} onDocument={open} />); await user.click(screen.getByRole("button", { name: /查看来源/ })); await screen.findByRole("alert"); expect(open).not.toHaveBeenCalled(); expect(screen.queryByText("新版")).not.toBeInTheDocument();
  });
  it("shows inherited scope and refuses a source if this meeting scope changed", async () => {
    const api = projectApiFixture(); const user = userEvent.setup(); render(<MeetingProjectState api={api} meetingId="meeting" />); await screen.findByText(item.text); vi.mocked(api.getMeetingState).mockResolvedValue({ projectId: "p", revision: 8, decisions: [], unfinishedTasks: [], openQuestions: [], truncated: false }); await user.click(screen.getByRole("button", { name: /查看来源/ })); await screen.findByText(/本场范围或项目事项已变化/); expect(api.getSource).not.toHaveBeenCalled(); await user.click(screen.getByRole("button", { name: "刷新继承事项" })); await screen.findByText(/当前范围没有可继承事项/);
  });
  it("shows valid original quote before enabling source navigation", async () => {
    const api = projectApiFixture(); const open = vi.fn(); const user = userEvent.setup(); render(<ProjectSources api={api} sources={[source]} onDocument={open} />); expect(screen.queryByRole("button", { name: "在资料库定位原文" })).not.toBeInTheDocument(); await user.click(screen.getByRole("button", { name: /查看来源/ })); await user.click(await screen.findByRole("button", { name: "在资料库定位原文" })); expect(open).toHaveBeenCalledWith(source);
  });
});
