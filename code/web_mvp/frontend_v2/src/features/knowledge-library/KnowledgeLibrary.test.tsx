import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeApi, KnowledgeDocument, KnowledgeFolder, KnowledgeJob, KnowledgeVersion } from "../../api/knowledge";
import { KnowledgeLibrary } from "./KnowledgeLibrary";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";

const project = { id: "project-1", name: "产品项目", description: "产品会议背景", revision: 1, archivedAtMs: null };
const document: KnowledgeDocument = { id: "doc-1", projectId: project.id, folderId: null, kind: "uploaded_file", title: "预算方案", sourceMeetingId: null, filename: "预算.pdf", activeVersionId: null, status: "draft", importStatus: "extracted", errorCode: null, errorMessage: null, revision: 2 };
const version: KnowledgeVersion = { id: "version-1", documentId: document.id, number: 1, content: "预算为三万元，尚未验收。", contentHash: "hash-1", origin: "extracted", confirmedAtMs: null, spans: [{ id: "span-1", versionId: "version-1", ordinal: 0, text: "预算为三万元，尚未验收。", locator: { type: "pdf_page", page: 2 } }] };
const job: KnowledgeJob = { id: "job-1", documentId: document.id, projectId: project.id, type: "import", status: "queued", progress: "等待转换", attempt: 0, errorCode: null, errorMessage: null, resultVersionId: null };
const empty = { items: [], nextCursor: null, hasMore: false };
function apiFixture(): KnowledgeApi {
  return {
    getMeetingContext: vi.fn(), saveMeetingContext: vi.fn(), getMeetingSource: vi.fn(),
    listProjects: vi.fn().mockResolvedValue({ ...empty, items: [project] }), createProject: vi.fn().mockResolvedValue(project), updateProject: vi.fn().mockResolvedValue(project),
    listFolders: vi.fn().mockResolvedValue(empty), createFolder: vi.fn(), updateFolder: vi.fn(),
    listDocuments: vi.fn().mockResolvedValue({ ...empty, items: [document] }), importDocument: vi.fn().mockResolvedValue({ document, job }), createDocument: vi.fn().mockResolvedValue({ document, version }),
    getDocument: vi.fn().mockResolvedValue({ document, version, latestDraftVersionId: version.id }), listVersions: vi.fn().mockResolvedValue({ ...empty, items: [version] }), getVersion: vi.fn().mockResolvedValue(version),
    saveVersion: vi.fn().mockResolvedValue({ document: { ...document, revision: 3 }, version: { ...version, id: "version-2", number: 2 } }),
    confirmVersion: vi.fn().mockResolvedValue({ ...document, revision: 4, activeVersionId: "version-2", status: "active" }), updateDocument: vi.fn().mockResolvedValue({ ...document, revision: 3 }),
    listJobs: vi.fn().mockResolvedValue({ ...empty, items: [job] }), getJob: vi.fn().mockResolvedValue(job), retryJob: vi.fn().mockResolvedValue(job), cancelJob: vi.fn().mockResolvedValue({ ...job, status: "cancelled" }),
    getSource: vi.fn().mockResolvedValue({ sourceType: "document", sourceId: "span-1", projectId: project.id, documentId: document.id, title: document.title, versionId: version.id, version: 1, quote: version.content, locator: { type: "pdf_page", page: 2 } }),
  };
}
function show(api = apiFixture(), props: Partial<Parameters<typeof KnowledgeLibrary>[0]> = {}) {
  const callbacks = { onOpenMeetings: vi.fn(), onOpenNotes: vi.fn(), onOpenCapabilities: vi.fn(), onOpenMeeting: vi.fn() };
  render(<KnowledgeLibrary api={api} {...callbacks} {...props} />); return { api, ...callbacks };
}
async function selectDocument(user: ReturnType<typeof userEvent.setup>) { await user.click(await screen.findByRole("button", { name: /预算方案.*待检查确认/ })); await screen.findByRole("textbox", { name: "转换文本" }); }
afterEach(() => { vi.useRealTimers(); });

describe("KnowledgeLibrary", () => {
  it("opens the existing Wiki after confirming a document without confirming any project candidate", async () => {
    const api = apiFixture();
    vi.mocked(api.confirmVersion).mockResolvedValue({ ...document, status: "active", activeVersionId: version.id, revision: 3 });
    vi.mocked(api.getVersion).mockResolvedValue({ ...version, confirmedAtMs: 1000 });
    const projectApi = { listState: vi.fn().mockResolvedValue({ ...empty, revision: 1 }), listProposals: vi.fn().mockResolvedValue(empty),
      getWiki: vi.fn().mockResolvedValue({ projectId: project.id, revision: 1, markdown: "# 已确认项目状态", items: [], truncated: false }), confirmProposal: vi.fn(), createProposal: vi.fn() } as unknown as ProjectOutcomesApi;
    const user = userEvent.setup(); show(api, { projectApi }); await selectDocument(user);
    expect(projectApi.getWiki).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认这一版可用" }));
    await screen.findByRole("region", { name: "已确认资料的下一步" });
    expect(screen.getByText(/项目决策与待办仍需单独确认/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "查看本项目 Wiki" }));
    expect(await screen.findByRole("heading", { name: "已确认项目状态" })).toBeVisible();
    expect(projectApi.confirmProposal).not.toHaveBeenCalled(); expect(projectApi.createProposal).not.toHaveBeenCalled();
    expect(api.confirmVersion).toHaveBeenCalledExactlyOnceWith(document.id, version.id, document.revision);
  });
  it("exports the whole selected project on explicit click, locking repeats until download is ready", async () => {
    const api = apiFixture(); let finish!: () => void;
    api.exportProject = vi.fn().mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    const user = userEvent.setup(); show(api);
    const button = await screen.findByRole("button", { name: "导出项目资料" });
    expect(api.exportProject).not.toHaveBeenCalled();
    await user.click(button);
    expect(api.exportProject).toHaveBeenCalledExactlyOnceWith(project.id);
    expect(screen.getByRole("button", { name: "正在导出…" })).toBeDisabled();
    await act(async () => finish());
    await screen.findByText(/已交给浏览器下载/);
    expect(screen.getByRole("button", { name: "导出项目资料" })).toBeEnabled();
    expect(api.confirmVersion).not.toHaveBeenCalled(); expect(api.createDocument).not.toHaveBeenCalled();
  });
  it("blocks exporting unsaved edits and keeps failed downloads retryable", async () => {
    const api = apiFixture(); api.exportProject = vi.fn().mockRejectedValue(new Error("项目资料超出导出上限。"));
    const user = userEvent.setup(); show(api); await selectDocument(user);
    await user.type(screen.getByLabelText("转换文本"), " 尚未保存");
    expect(screen.getByRole("button", { name: "导出项目资料" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存修改为草稿" }));
    await waitFor(() => expect(api.saveVersion).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "导出项目资料" }));
    await screen.findByText("项目资料超出导出上限。");
    expect(screen.getByRole("button", { name: "导出项目资料" })).toBeEnabled();
    expect(api.confirmVersion).not.toHaveBeenCalled();
  });
  it("blocks confirmation of an old source snapshot while keeping its text readable", async () => {
    const api = apiFixture(); vi.mocked(api.getDocument).mockResolvedValue({ document: { ...document, sourceStatus: "needs_review" }, version: { ...version, sourceStatus: "needs_review" }, latestDraftVersionId: version.id }); const user = userEvent.setup(); show(api); await selectDocument(user); expect(screen.getByRole("button", { name: "确认这一版可用" })).toBeDisabled(); expect(screen.getByRole("textbox", { name: "转换文本" })).toHaveValue(version.content); expect(screen.getByText(/原来源已修改或删除/)).toBeInTheDocument(); expect(api.confirmVersion).not.toHaveBeenCalled();
  });
  it("edits extracted text then confirms the saved revision, retaining source versions", async () => {
    const user = userEvent.setup(); const { api } = show(); await selectDocument(user);
    expect(screen.getByText(/待确认版本。检查人名/)).toBeInTheDocument();
    const editor = screen.getByRole("textbox", { name: "转换文本" }); await user.clear(editor); await user.type(editor, "预算为四万元，仍未验收。");
    await user.click(screen.getByRole("button", { name: "确认这一版可用" }));
    await waitFor(() => expect(api.confirmVersion).toHaveBeenCalledWith("doc-1", "version-2", 3));
    expect(api.saveVersion).toHaveBeenCalledWith("doc-1", "预算为四万元，仍未验收。", 2, "version-1");
    await screen.findByText(/已确认。这一版可用于/);
  });
  it("uses opaque document paging and fully loads multi-page folder trees", async () => {
    const api = apiFixture(); const root: KnowledgeFolder = { id: "folder-root", projectId: project.id, parentId: null, name: "发布", revision: 1 };
    vi.mocked(api.listFolders).mockResolvedValueOnce({ items: [root], nextCursor: "folder-next", hasMore: true }).mockResolvedValue({ ...empty, items: [{ ...root, id: "folder-child", name: "条款", parentId: root.id }] });
    vi.mocked(api.listDocuments).mockResolvedValueOnce({ items: [document], nextCursor: "opaque+next=", hasMore: true }).mockResolvedValue({ ...empty, items: [{ ...document, id: "doc-301", title: "第301份资料" }] });
    const user = userEvent.setup(); show(api); await screen.findByRole("button", { name: "条款" });
    expect(api.listFolders).toHaveBeenCalledWith(project.id, "folder-next", expect.any(AbortSignal));
    await user.click(await screen.findByRole("button", { name: "下一页" })); await screen.findByRole("button", { name: /第301份资料/ });
    expect(api.listDocuments).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "opaque+next=", limit: 25 }), expect.any(AbortSignal));
    expect(screen.getByText("第 2 页")).toBeInTheDocument(); await user.click(screen.getByRole("button", { name: "上一页" }));
    await waitFor(() => expect(api.listDocuments).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: null }), expect.any(AbortSignal)));
  });
  it.each(["方案.txt", "方案.md", "方案.docx", "方案.pdf"])("imports %s into the chosen folder without treating it as confirmed", async (filename) => {
    const user = userEvent.setup(); const api = apiFixture(); show(api);
    await user.click(await screen.findByRole("button", { name: "导入文件" }));
    const modal = screen.getByRole("dialog", { name: "导入文件" });
    await user.upload(within(modal).getByLabelText("选择资料文件"), new File(["本地内容"], filename));
    await user.click(within(modal).getByRole("button", { name: "开始导入" }));
    await waitFor(() => expect(api.importDocument).toHaveBeenCalledWith(expect.any(File), project.id, null, undefined));
    expect(api.confirmVersion).not.toHaveBeenCalled(); await screen.findByText(/已加入转换队列/);
  });
  it("pastes a corrected meeting document with its optional source meeting", async () => {
    const user = userEvent.setup(); const api = apiFixture(); show(api);
    await user.click(await screen.findByRole("button", { name: "添加文本" })); const modal = screen.getByRole("dialog", { name: "添加文本资料" });
    await user.type(within(modal).getByRole("textbox", { name: "名称" }), "已修正发布会议");
    await user.selectOptions(within(modal).getByLabelText("文本资料类型"), "meeting_document");
    await user.type(within(modal).getByLabelText("来源会议编号（可选）"), "meeting-7"); await user.type(within(modal).getByLabelText("资料正文"), "发布前仍需验收。");
    await user.click(within(modal).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.createDocument).toHaveBeenCalledWith({ projectId: project.id, folderId: null, title: "已修正发布会议", content: "发布前仍需验收。", kind: "meeting_document", sourceMeetingId: "meeting-7" }));
    expect(api.confirmVersion).not.toHaveBeenCalled();
  });
  it("reports scanned PDF limits and actual job failure, with retry/cancel", async () => {
    const user = userEvent.setup(); const api = apiFixture();
    vi.mocked(api.getDocument).mockResolvedValueOnce({ document: { ...document, importStatus: "needs_ocr", errorCode: "pdf_ocr_required" }, version: null, latestDraftVersionId: null }).mockResolvedValue({ document: { ...document, importStatus: "pending" }, version: null, latestDraftVersionId: null });
    vi.mocked(api.listJobs).mockResolvedValueOnce({ ...empty, items: [{ ...job, status: "failed", errorMessage: "没有可提取的文字。" }] }).mockResolvedValue({ ...empty, items: [job] }); show(api);
    await user.click(await screen.findByRole("button", { name: /预算方案/ })); await screen.findByText(/这份 PDF 没有可提取的文字/);
    expect(screen.queryByLabelText("转换文本")).not.toBeInTheDocument(); expect(api.listJobs).toHaveBeenCalledWith(project.id, document.id, expect.any(AbortSignal));
    await user.click(screen.getByRole("button", { name: "重试转换" })); await waitFor(() => expect(api.retryJob).toHaveBeenCalledWith("job-1"));
    await user.click(await screen.findByRole("button", { name: "取消转换" })); await waitFor(() => expect(api.cancelJob).toHaveBeenCalledWith("job-1"));
  });
  it("keeps unsaved content on revision conflict and requires explicit discard to navigate", async () => {
    const user = userEvent.setup(); const api = apiFixture(); vi.mocked(api.saveVersion).mockRejectedValue(new Error("资料已被更新，请刷新后再操作。")); const callbacks = show(api); await selectDocument(user);
    await user.type(screen.getByLabelText("转换文本"), " 需要复核"); await user.click(screen.getByRole("button", { name: "保存修改为草稿" }));
    await screen.findByRole("alert"); expect(screen.getByLabelText("转换文本")).toHaveValue(`${version.content} 需要复核`);
    await user.click(screen.getByRole("button", { name: "会议记录" })); expect(callbacks.onOpenMeetings).not.toHaveBeenCalled();
    await user.click(within(screen.getByRole("dialog", { name: "确认操作" })).getByRole("button", { name: "取消" }));
    expect(screen.getByLabelText("转换文本")).toHaveValue(`${version.content} 需要复核`);
    await user.click(screen.getByRole("button", { name: "会议记录" })); await user.click(within(screen.getByRole("dialog", { name: "确认操作" })).getByRole("button", { name: "继续" })); expect(callbacks.onOpenMeetings).toHaveBeenCalledOnce();
  });
  it("disables confirmed sources immediately and explicitly confirms deletion", async () => {
    const user = userEvent.setup(); const api = apiFixture(); const active = { ...document, activeVersionId: version.id, status: "active" as const };
    vi.mocked(api.getDocument).mockResolvedValue({ document: active, version: { ...version, confirmedAtMs: 1000 }, latestDraftVersionId: null });
    vi.mocked(api.updateDocument).mockResolvedValueOnce({ ...active, status: "disabled", revision: 3 }).mockResolvedValue({ ...active, status: "deleted", revision: 4 }); show(api);
    await user.click(await screen.findByRole("button", { name: /预算方案/ })); await user.click(await screen.findByRole("button", { name: "禁用资料" }));
    await waitFor(() => expect(api.updateDocument).toHaveBeenCalledWith(document.id, 2, { status: "disabled" }));
    expect(await screen.findByRole("button", { name: "重新启用" })).toBeInTheDocument(); expect(screen.getByRole("button", { name: "确认这一版可用" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "删除资料" })); expect(api.updateDocument).toHaveBeenCalledTimes(1);
    await user.click(within(screen.getByRole("dialog", { name: "确认操作" })).getByRole("button", { name: "继续" }));
    await waitFor(() => expect(api.updateDocument).toHaveBeenLastCalledWith(document.id, 3, { status: "deleted" })); await screen.findByText(/资料已删除，后续不会再使用它/);
  });
  it("locates draft/old source text as preview without impersonating an active citation", async () => {
    const user = userEvent.setup(); const { api } = show(); await selectDocument(user);
    await user.click(screen.getByText(/原文定位/)); await user.click(screen.getByRole("button", { name: /第 2 页 · 段落 1/ }));
    expect(await screen.findByRole("region", { name: "原文来源" })).toHaveTextContent("历史或待确认来源"); expect(api.getSource).not.toHaveBeenCalled();
  });
  it("opens a typed source deep link at the cited version and page", async () => {
    const api = apiFixture(); vi.mocked(api.getDocument).mockResolvedValue({ document: { ...document, activeVersionId: version.id, status: "active" }, version: { ...version, confirmedAtMs: 1000 }, latestDraftVersionId: "draft-new" });
    show(api, { initialSourceId: "span-1", initialProjectId: project.id });
    const source = await screen.findByRole("region", { name: "原文来源" }); expect(source).toHaveTextContent("第 2 页");
    expect(screen.getByLabelText("转换文本")).toHaveValue(version.content); expect(api.getVersion).not.toHaveBeenCalledWith(document.id, "draft-new", expect.anything());
  });
  it("creates a project and a child folder, excluding descendants from folder moves", async () => {
    const user = userEvent.setup(); const api = apiFixture();
    const root: KnowledgeFolder = { id: "folder-root", projectId: project.id, parentId: null, name: "发布", revision: 1 };
    const child = { ...root, id: "folder-child", parentId: root.id, name: "条款" };
    vi.mocked(api.listFolders).mockResolvedValue({ ...empty, items: [root, child] }); vi.mocked(api.createFolder).mockResolvedValue({ ...child, id: "folder-new", name: "复核" });
    vi.mocked(api.createProject).mockResolvedValue({ ...project, id: "project-new", name: "新项目" }); show(api);
    await user.click(await screen.findByRole("button", { name: "新建文件夹" }));
    let modal = screen.getByRole("dialog", { name: "新建文件夹" }); await user.type(within(modal).getByRole("textbox", { name: "名称" }), "复核"); await user.selectOptions(within(modal).getByLabelText("上级文件夹"), root.id); await user.click(within(modal).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.createFolder).toHaveBeenCalledWith(project.id, "复核", root.id));
    await user.click(screen.getByRole("button", { name: "发布" })); await user.click(screen.getByRole("button", { name: "改名或移动当前文件夹" })); modal = screen.getByRole("dialog", { name: "编辑文件夹" });
    expect(within(modal).queryByRole("option", { name: "条款" })).not.toBeInTheDocument(); expect(within(modal).queryByRole("option", { name: "发布" })).not.toBeInTheDocument(); await user.click(within(modal).getByRole("button", { name: "取消" }));
    await user.click(screen.getByRole("button", { name: "新建项目" })); modal = screen.getByRole("dialog", { name: "新建项目" }); await user.type(within(modal).getByRole("textbox", { name: "名称" }), "新项目"); await user.click(within(modal).getByRole("button", { name: "保存" })); await waitFor(() => expect(api.createProject).toHaveBeenCalledWith("新项目", "")); expect(screen.getByLabelText("选择项目")).toHaveValue("project-new");
  });
  it("moves/renames a document with stable ID and reads older version pages", async () => {
    const user = userEvent.setup(); const api = apiFixture();
    vi.mocked(api.listFolders).mockResolvedValue({ ...empty, items: [{ id: "folder-2", projectId: project.id, parentId: null, name: "验收", revision: 1 }] });
    vi.mocked(api.listVersions).mockResolvedValueOnce({ items: [{ ...version, number: 5 }], nextCursor: "5", hasMore: true }).mockResolvedValue({ ...empty, items: [{ ...version, id: "old-version", number: 4, confirmedAtMs: 100 }] });
    vi.mocked(api.getVersion).mockResolvedValue({ ...version, id: "old-version", number: 4, content: "历史预算", confirmedAtMs: 100 }); show(api); await selectDocument(user);
    await user.click(screen.getByRole("button", { name: "改名 / 移动" })); const modal = screen.getByRole("dialog", { name: "编辑资料信息" }); await user.clear(within(modal).getByRole("textbox", { name: "名称" })); await user.type(within(modal).getByRole("textbox", { name: "名称" }), "校对后预算"); await user.selectOptions(within(modal).getByLabelText("存放文件夹"), "folder-2"); await user.click(within(modal).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.updateDocument).toHaveBeenCalledWith(document.id, 2, { title: "校对后预算", folderId: "folder-2" }));
    await user.click(screen.getByRole("button", { name: "更早版本" })); await waitFor(() => expect(api.listVersions).toHaveBeenLastCalledWith(document.id, 5)); await user.selectOptions(screen.getByLabelText("查看资料版本"), "old-version"); await waitFor(() => expect(screen.getByLabelText("转换文本")).toHaveValue("历史预算"));
  });
  it("polls only an unfinished selected import and shows its completed conversion", async () => {
    vi.useFakeTimers(); const api = apiFixture();
    vi.mocked(api.getDocument).mockResolvedValueOnce({ document: { ...document, importStatus: "pending" }, version: null, latestDraftVersionId: null }).mockResolvedValue({ document, version, latestDraftVersionId: version.id });
    vi.mocked(api.getJob).mockResolvedValue({ ...job, status: "succeeded", resultVersionId: version.id, progress: "文本已提取" }); show(api);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); }); fireEvent.click(screen.getByRole("button", { name: /预算方案/ }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); }); expect(api.getJob).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); }); expect(api.getJob).toHaveBeenCalledOnce(); expect(screen.getByLabelText("转换文本")).toHaveValue(version.content);
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); }); expect(api.getJob).toHaveBeenCalledOnce(); expect(api.confirmVersion).not.toHaveBeenCalled();
  });
});
