import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProjectBriefPreview, ProjectOutcomesApi, ProjectSourceRef, ProjectStateItem } from "../../api/projectOutcomes";
import { MeetingProjectBrief } from "./MeetingProjectBrief";

const selection = { projectId: "project", folderId: null, documentIds: null };
const source: ProjectSourceRef = { sourceType: "document", sourceId: "span", projectId: "project", meetingId: null, documentId: "doc", title: "预算", versionId: "version", version: 2, contentHash: "hash", quote: "预算五万元，仍需法务审核。", locator: { type: "converted_text", paragraph: 1 } };
const item: ProjectStateItem = { id: "decision", projectId: "project", type: "decision", text: "预算五万元", status: "active", owner: null, due: null, sourceMeetingId: null, sourceRefs: [source], origin: "confirmed_proposal", revision: 2, supersedesId: null };
const brief: ProjectBriefPreview = { projectId: "project", projectName: "项目甲", revision: 7, folderId: null, documentIds: null, generatedAtMs: 1000, decisions: [item], unfinishedTasks: [{ ...item, id: "task", type: "task", text: "法务审核", owner: "李明", due: "周五" }], openQuestions: [{ ...item, id: "question", type: "question", text: "合同何时生效？" }], needsReview: [], needsReviewDocuments: [], truncated: false };
function apiFixture() { return { previewBrief: vi.fn().mockResolvedValue(brief), getSource: vi.fn().mockResolvedValue(source) } as unknown as ProjectOutcomesApi; }
afterEach(() => vi.useRealTimers());

describe("automatic pre-meeting project brief", () => {
  it("automatically shows confirmed conclusions, open work and verified citations for the chosen scope", async () => {
    const api = apiFixture(); render(<MeetingProjectBrief api={api} selection={selection} open />);
    expect(await screen.findByText("预算五万元")).toBeVisible(); expect(screen.getByText("法务审核 · 负责人：李明 · 期限：周五")).toBeVisible(); expect(screen.getByText("合同何时生效？")).toBeVisible(); expect(screen.getByText(/项目第 7 版/)).toBeVisible();
    expect(api.previewBrief).toHaveBeenCalledWith(selection, expect.any(AbortSignal));
    fireEvent.click(screen.getAllByRole("button", { name: /查看来源/ })[0]);
    expect(await screen.findByText(source.quote)).toBeVisible(); expect(api.getSource).toHaveBeenCalledWith(source);
  });
  it("keeps invalidated statements in the review section and cannot substitute current sources", async () => {
    const api = apiFixture(); vi.mocked(api.previewBrief!).mockResolvedValue({ ...brief, decisions: [], unfinishedTasks: [], openQuestions: [], needsReview: [{ ...item, status: "needs_review" }], needsReviewDocuments: [{ id: "doc", title: "关联旧文稿", revision: 3, sourceStatus: "needs_review", activeVersion: 2 }] });
    render(<MeetingProjectBrief api={api} selection={selection} open />);
    await screen.findByText(/关联旧文稿.*待复核/); expect(screen.getByText("所选范围暂无已确认结论。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /查看来源/ })); expect(await screen.findByRole("alert")).toHaveTextContent("来源已变化"); expect(api.getSource).not.toHaveBeenCalled();
  });
  it("aborts the previous scope and ignores late responses after switching folders", async () => {
    const api = apiFixture(); let resolveOld!: (value: ProjectBriefPreview) => void;
    vi.mocked(api.previewBrief!).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; })).mockResolvedValueOnce({ ...brief, folderId: "new-folder", decisions: [{ ...item, text: "新目录结论" }] });
    const view = render(<MeetingProjectBrief api={api} selection={selection} open />);
    await waitFor(() => expect(api.previewBrief).toHaveBeenCalledOnce()); const oldSignal = vi.mocked(api.previewBrief!).mock.calls[0][1]!;
    view.rerender(<MeetingProjectBrief api={api} selection={{ ...selection, folderId: "new-folder" }} open />);
    expect(await screen.findByText("新目录结论")).toBeVisible(); expect(oldSignal.aborted).toBe(true);
    await act(async () => resolveOld(brief)); expect(screen.queryByText("预算五万元")).not.toBeInTheDocument(); expect(screen.getByText("新目录结论")).toBeVisible();
  });
  it("clears a previous snapshot on explicit empty scope or on project errors", async () => {
    const api = apiFixture(); const view = render(<MeetingProjectBrief api={api} selection={selection} open />); await screen.findByText("预算五万元");
    vi.mocked(api.previewBrief!).mockResolvedValueOnce({ ...brief, documentIds: [], decisions: [], unfinishedTasks: [], openQuestions: [] });
    view.rerender(<MeetingProjectBrief api={api} selection={{ ...selection, documentIds: [] }} open />); expect(screen.queryByText("预算五万元")).not.toBeInTheDocument(); expect(await screen.findByText(/明确不使用项目资料/)).toBeVisible();
    vi.mocked(api.previewBrief!).mockRejectedValueOnce(new Error("项目已归档，请重新选择。"));
    view.rerender(<MeetingProjectBrief api={api} selection={{ ...selection, projectId: "archived" }} open />); expect(await screen.findByRole("alert")).toHaveTextContent("项目已归档"); expect(screen.queryByText(/项目第 7 版/)).not.toBeInTheDocument();
  });
  it("does not request a brief before choosing a project or when the dialog is closed", () => {
    const api = apiFixture(); const view = render(<MeetingProjectBrief api={api} selection={{ ...selection, projectId: null }} open />); expect(api.previewBrief).not.toHaveBeenCalled();
    view.rerender(<MeetingProjectBrief api={api} selection={selection} open={false} />); expect(api.previewBrief).not.toHaveBeenCalled(); expect(screen.queryByLabelText("会前项目简报")).not.toBeInTheDocument();
  });
  it("times out without accepting a late snapshot and allows explicit refresh", async () => {
    vi.useFakeTimers(); const api = apiFixture(); let resolveLate!: (value: ProjectBriefPreview) => void;
    vi.mocked(api.previewBrief!).mockImplementationOnce(() => new Promise((resolve) => { resolveLate = resolve; })).mockResolvedValueOnce({ ...brief, truncated: true });
    render(<MeetingProjectBrief api={api} selection={selection} open />); await act(async () => vi.advanceTimersByTimeAsync(6_000));
    expect(screen.getByRole("alert")).toHaveTextContent("读取超时"); expect(vi.mocked(api.previewBrief!).mock.calls[0][1]?.aborted).toBe(true);
    await act(async () => resolveLate(brief)); expect(screen.queryByText("预算五万元")).not.toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "刷新会前简报" }))); expect(screen.getByText("预算五万元")).toBeVisible(); expect(screen.getByText(/这里只展示有限的项目事项/)).toBeVisible();
  });
});
