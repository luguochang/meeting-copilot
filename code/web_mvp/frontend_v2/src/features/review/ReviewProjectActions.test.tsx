import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { KnowledgeApi } from "../../api/knowledge";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";
import type { ProjectReconcileApi, ReconcileJob } from "../../api/projectReconcile";
import type { ReviewDocument } from "../../domain/events";
import { ReviewProjectActions } from "./ReviewProjectActions";
import { ProjectReconcileJobs } from "../project-workspace/ProjectReconcileJobs";
const empty = { items: [], nextCursor: null, hasMore: false };
const final: ReviewDocument = { documentId: "final", meetingId: "meeting", kind: "minutes", revision: 4, sourceRevision: 1, contentJson: { markdown: "人工终稿" }, aiVersion: 1, userVersion: 2, source: "user_final", dirtyState: "saved", updatedAtMs: 2 };
const job: ReconcileJob = { id: "job", projectId: "project", meetingId: "meeting", documentKind: "minutes", status: "pending", baseRevision: 7, attempts: 0, resultProposalId: null, errorClass: null, errorMessage: null, sourceVersion: 2, contextTruncated: false };
function fixture() {
  const knowledgeApi = { getMeetingContext: vi.fn().mockResolvedValue({ projectId: "project", folderId: null, revision: 1 }), importFinal: vi.fn(), listProjects: vi.fn().mockResolvedValue({ ...empty, items: [{ id: "project", name: "产品项目", description: "", revision: 7, archivedAtMs: null }] }), listFolders: vi.fn().mockResolvedValue(empty), listDocuments: vi.fn().mockResolvedValue(empty) } as unknown as KnowledgeApi;
  const projectApi = { listState: vi.fn().mockResolvedValue({ ...empty, revision: 7 }) } as unknown as ProjectOutcomesApi;
  const reconcileApi: ProjectReconcileApi = { list: vi.fn().mockResolvedValue(empty), get: vi.fn().mockResolvedValue(job), create: vi.fn().mockResolvedValue(job), cancel: vi.fn().mockResolvedValue({ ...job, status: "cancelled" }), retry: vi.fn().mockResolvedValue({ ...job, sourceVersion: 3 }), getCurrentFinal: vi.fn().mockResolvedValue(final) };
  return { knowledgeApi, projectApi, reconcileApi };
}
afterEach(() => vi.useRealTimers());
it("explains independent document and Wiki confirmations without starting paid work", async () => {
  const api = fixture(); render(<ReviewProjectActions {...api} meetingId="meeting" documents={{ minutes: final }} saveStates={{ minutes: "saved" }} />);
  await screen.findByText(/使用已保存的第 2 版/);
  const guide = within(screen.getByRole("region", { name: "会后内容复用步骤" }));
  expect(guide.getByText(/资料文本与项目事项分别确认/)).toBeVisible();
  expect(guide.getByText(/明确点击 Pi 核对才调用模型/)).toBeVisible();
  expect(guide.getByText(/下次会议选择同项目与范围/)).toBeVisible();
  expect(api.reconcileApi.create).not.toHaveBeenCalled(); expect(api.reconcileApi.retry).not.toHaveBeenCalled();
  expect(api.knowledgeApi.importFinal).not.toHaveBeenCalled();
});
it("only creates a guarded job for the exact saved manual final and supports local unsaved gating", async () => {
  const api = fixture(); const view = render(<ReviewProjectActions {...api} meetingId="meeting" documents={{ minutes: final }} saveStates={{ minutes: "unsaved" }} />); await screen.findByText(/所选文稿尚未形成/); expect(screen.getByRole("button", { name: "让 Pi 核对项目变化" })).toBeDisabled(); expect(api.reconcileApi.create).not.toHaveBeenCalled(); view.rerender(<ReviewProjectActions {...api} meetingId="meeting" documents={{ minutes: final }} saveStates={{ minutes: "saved" }} />); fireEvent.click(screen.getByRole("button", { name: "让 Pi 核对项目变化" })); await waitFor(() => expect(api.reconcileApi.create).toHaveBeenCalledWith("meeting", "minutes", 7, 2)); await screen.findByRole("button", { name: "取消本次核对" });
});
it("refuses a source that changed rather than auto-substituting its new version", async () => {
  const api = fixture(); vi.mocked(api.reconcileApi.getCurrentFinal).mockResolvedValue({ ...final, userVersion: 3 }); render(<ReviewProjectActions {...api} meetingId="meeting" documents={{ minutes: final }} saveStates={{ minutes: "saved" }} />); await screen.findByText(/使用已保存的第 2 版/); fireEvent.click(screen.getByRole("button", { name: "让 Pi 核对项目变化" })); await screen.findByText(/人工终稿已更新/); expect(api.reconcileApi.create).not.toHaveBeenCalled();
});
it("opens source import using true review document/version rather than copying displayed fallback", async () => {
  const api = fixture(); render(<ReviewProjectActions {...api} meetingId="meeting" documents={{ minutes: final }} saveStates={{ minutes: "saved" }} />); await screen.findByText(/使用已保存的第 2 版/); fireEvent.click(screen.getByRole("button", { name: "把已保存人工终稿加入资料库" })); await waitFor(() => expect(screen.getByRole("button", { name: "导入当前原文为草稿" })).toBeEnabled()); fireEvent.click(screen.getByRole("button", { name: "导入当前原文为草稿" })); await waitFor(() => expect(api.knowledgeApi.importFinal).toHaveBeenCalledWith("meeting", { documentKind: "minutes", expectedSourceVersion: 2, folderId: null }));
});
it("polls only a selected unfinished job and never creates or retries during metadata reads", async () => {
  const api = fixture(); vi.mocked(api.reconcileApi.list).mockResolvedValue({ ...empty, items: [job] }); render(<ProjectReconcileJobs api={api.reconcileApi} projectApi={api.projectApi} projectId="project" />); const select = await screen.findByRole("button", { name: /会议纪要 · 等待后台核对/ }); expect(api.reconcileApi.get).not.toHaveBeenCalled(); vi.useFakeTimers(); fireEvent.click(select); await act(async () => { await vi.advanceTimersByTimeAsync(2_100); }); expect(api.reconcileApi.get).toHaveBeenCalledWith("job", expect.any(AbortSignal)); expect(api.reconcileApi.create).not.toHaveBeenCalled(); expect(api.reconcileApi.retry).not.toHaveBeenCalled(); vi.useRealTimers(); fireEvent.click(screen.getByRole("button", { name: "取消本次核对" })); await waitFor(() => expect(api.reconcileApi.cancel).toHaveBeenCalledWith("job"));
});
it("retries explicitly from latest saved human source and exposes candidate vs no-change completion", async () => {
  const api = fixture(); vi.mocked(api.reconcileApi.list).mockResolvedValue({ ...empty, items: [{ ...job, status: "failed" }] }); vi.mocked(api.reconcileApi.getCurrentFinal).mockResolvedValue({ ...final, userVersion: 3 }); const open = vi.fn(); const view = render(<ProjectReconcileJobs api={api.reconcileApi} projectApi={api.projectApi} projectId="project" onOpenProposals={open} />); fireEvent.click(await screen.findByRole("button", { name: /会议纪要 · 核对未完成/ })); fireEvent.click(screen.getByRole("button", { name: "按最新已保存终稿手动重试" })); await waitFor(() => expect(api.reconcileApi.retry).toHaveBeenCalledWith("job", 7, 3));
  vi.mocked(api.reconcileApi.get).mockResolvedValue({ ...job, status: "succeeded", resultProposalId: "proposal" }); view.rerender(<ProjectReconcileJobs api={api.reconcileApi} projectApi={api.projectApi} projectId="project" onOpenProposals={open} initialJobId="job" />); fireEvent.click(await screen.findByRole("button", { name: "查看并审核项目候选" })); expect(open).toHaveBeenCalledWith("project");
  vi.mocked(api.reconcileApi.get).mockResolvedValue({ ...job, id: "job2", status: "succeeded", resultProposalId: null }); view.rerender(<ProjectReconcileJobs api={api.reconcileApi} projectApi={api.projectApi} projectId="project" onOpenProposals={open} initialJobId="job2" />); await screen.findByText(/未发现需要确认的项目变更/);
});
