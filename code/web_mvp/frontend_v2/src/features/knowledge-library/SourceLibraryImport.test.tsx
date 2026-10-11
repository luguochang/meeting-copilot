import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { KnowledgeApi, KnowledgeDocument, KnowledgeVersion } from "../../api/knowledge";
import { SourceLibraryImport } from "./SourceLibraryImport";
const empty = { items: [], nextCursor: null, hasMore: false };
const document: KnowledgeDocument = { id: "doc", projectId: "project", folderId: null, kind: "note", title: "保存的笔记", sourceMeetingId: "meeting", filename: null, activeVersionId: "active", status: "active", importStatus: "extracted", errorCode: null, errorMessage: null, revision: 6, sourceBinding: { sourceKind: "note", sourceId: "note" }, sourceStatus: "needs_review" };
const version: KnowledgeVersion = { id: "draft", documentId: "doc", number: 4, content: "最新原文", contentHash: "hash", origin: "note", confirmedAtMs: null, spans: [], sourceStatus: "current" };
function apiFixture() { return { listProjects: vi.fn().mockResolvedValue({ ...empty, items: [{ id: "project", name: "发布项目", description: "", revision: 7, archivedAtMs: null }] }), listFolders: vi.fn().mockResolvedValue({ ...empty, items: [{ id: "folder", projectId: "project", parentId: null, name: "结论", revision: 1 }] }), listDocuments: vi.fn().mockResolvedValue({ ...empty, items: [document] }), getMeetingContext: vi.fn().mockResolvedValue({ projectId: "project", folderId: null, revision: 2 }), importNote: vi.fn().mockResolvedValue({ document, version, created: false, alreadyImported: false }), importFinal: vi.fn().mockResolvedValue({ document, version, created: true, alreadyImported: false }) } as unknown as KnowledgeApi; }
it("imports the real saved note version using current linked document revision and keeps it draft", async () => {
  const api = apiFixture(); const open = vi.fn(); render(<SourceLibraryImport api={api} source={{ kind: "note", id: "note", version: 3 }} onClose={vi.fn()} onOpenDocument={open} />); fireEvent.change(await screen.findByRole("combobox", { name: "入库项目" }), { target: { value: "project" } }); await waitFor(() => expect(screen.getByRole("button", { name: "导入当前原文为草稿" })).toBeEnabled()); fireEvent.change(screen.getByRole("combobox", { name: "入库文件夹" }), { target: { value: "folder" } }); fireEvent.click(screen.getByRole("button", { name: "导入当前原文为草稿" })); await waitFor(() => expect(api.importNote).toHaveBeenCalledWith("note", { projectId: "project", expectedSourceVersion: 3, expectedDocumentRevision: 6, folderId: "folder" })); await screen.findByText(/已保存为资料草稿，尚未用于会议/); fireEvent.click(screen.getByRole("button", { name: "到资料库检查并确认" })); expect(open).toHaveBeenCalledWith({ projectId: "project", documentId: "doc" });
});
it("uses the meeting bound project for saved human final and shows repeat-import preservation", async () => {
  const api = apiFixture(); vi.mocked(api.listDocuments).mockResolvedValue(empty); vi.mocked(api.importFinal!).mockResolvedValue({ document, version, created: false, alreadyImported: true }); render(<SourceLibraryImport api={api} source={{ kind: "final", id: "final-doc", meetingId: "meeting", documentKind: "minutes", version: 2 }} onClose={vi.fn()} />); await waitFor(() => expect(screen.getByRole("button", { name: "导入当前原文为草稿" })).toBeEnabled()); expect(screen.getByRole("combobox", { name: "入库项目" })).toBeDisabled(); fireEvent.click(screen.getByRole("button", { name: "导入当前原文为草稿" })); await waitFor(() => expect(api.importFinal).toHaveBeenCalledWith("meeting", { documentKind: "minutes", expectedSourceVersion: 2, folderId: null })); await screen.findByText(/保留已有修正内容/);
});
it("never rebuilds a disabled linked document or silently advances a source on conflict", async () => {
  const api = apiFixture(); vi.mocked(api.listDocuments).mockResolvedValue({ ...empty, items: [{ ...document, status: "disabled" }] }); render(<SourceLibraryImport api={api} source={{ kind: "note", id: "note", version: 3 }} onClose={vi.fn()} />); fireEvent.change(await screen.findByRole("combobox", { name: "入库项目" }), { target: { value: "project" } }); await screen.findByText(/已有资料/); expect(screen.getByRole("button", { name: "导入当前原文为草稿" })).toBeDisabled(); expect(api.importNote).not.toHaveBeenCalled();
});
it("leaves an imported source draft and makes the next confirmation explicit", async () => {
  const api = apiFixture(); vi.mocked(api.listDocuments).mockResolvedValue(empty); const open = vi.fn();
  render(<SourceLibraryImport api={api} source={{ kind: "final", id: "final-doc", meetingId: "meeting", documentKind: "minutes", version: 2 }} onClose={vi.fn()} onOpenDocument={open} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "导入当前原文为草稿" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "导入当前原文为草稿" }));
  await screen.findByText(/项目事项与 Wiki 仍需独立审核/);
  expect(api.importFinal).toHaveBeenCalledTimes(1); expect(open).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "到资料库检查并确认" }));
  expect(open).toHaveBeenCalledExactlyOnceWith({ documentId: "doc", projectId: "project" });
});
