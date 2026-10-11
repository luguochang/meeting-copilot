import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpKnowledgeApi } from "./knowledge";

const doc = { id: "资料/a", project_id: "项目/a", folder_id: null, kind: "upload", title: "预算资料", source_meeting_id: null, filename: "预算.pdf", active_version_id: null, status: "draft", import_status: "extracted", error_code: null, revision: 2 };
const version = { id: "版本/a", document_id: doc.id, number: 1, content: "预算尚未确定。", content_hash: "h", origin: "extracted", confirmed_at_ms: null, spans: [{ id: "引用/a", version_id: "版本/a", ordinal: 0, text: "预算尚未确定。", locator: { type: "pdf_page", page: 3 } }] };
function respond(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
afterEach(() => vi.unstubAllGlobals());
describe("HttpKnowledgeApi", () => {
  it("preserves opaque pagination, Chinese names and scopes in API queries", async () => {
    const fetcher = vi.fn().mockResolvedValue(respond({ items: [doc], has_more: true, next_cursor: "opaque+/=" })); vi.stubGlobal("fetch", fetcher);
    const result = await new HttpKnowledgeApi("http://localhost:9000/").listDocuments({ projectId: "项目/a", folderId: "目录/a", query: "预算", cursor: "opaque+/=" });
    const url = new URL(fetcher.mock.calls[0][0]); expect(url.pathname).toBe("/v2/knowledge/documents"); expect(url.searchParams.get("cursor")).toBe("opaque+/="); expect(url.searchParams.get("project_id")).toBe("项目/a"); expect(url.searchParams.get("folder_id")).toBe("目录/a"); expect(result.nextCursor).toBe("opaque+/="); expect(result.items[0].id).toBe(doc.id);
  });
  it("uploads multipart original without a fabricated Content-Type or confirmation", async () => {
    const fetcher = vi.fn().mockResolvedValue(respond({ document: { ...doc, import_status: "pending" }, job: { id: "job", document_id: doc.id, project_id: doc.project_id, type: "import", status: "queued", progress: "等待转换", attempt: 0, error_code: null, result_version_id: null } }, 202)); vi.stubGlobal("fetch", fetcher);
    const file = new File(["data"], "预算方案.docx"); await new HttpKnowledgeApi().importDocument(file, doc.project_id, "folder", "已修正方案");
    const [url, init] = fetcher.mock.calls[0]; expect(url).toBe("/v2/knowledge/documents"); expect(init.body).toBeInstanceOf(FormData); expect(init.body.get("file").name).toBe(file.name); expect(init.body.get("project_id")).toBe(doc.project_id); expect(init.headers["Content-Type"]).toBeUndefined(); expect(fetcher).toHaveBeenCalledOnce();
  });
  it("reads metadata-only version pagination and exact typed source locators", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(respond({ items: [version], next_before_number: 1 })).mockResolvedValueOnce(respond({ version })).mockResolvedValueOnce(respond({ source: { source_type: "document", source_id: "引用/a", document_id: doc.id, project_id: doc.project_id, title: doc.title, version_id: version.id, version: 1, content_hash: "h", ordinal: 0, quote: version.content, locator: version.spans[0].locator } })); vi.stubGlobal("fetch", fetcher);
    const api = new HttpKnowledgeApi(); const page = await api.listVersions(doc.id); expect(page.hasMore).toBe(true); expect(page.items[0]).not.toHaveProperty("content");
    expect((await api.getVersion(doc.id, version.id)).spans[0].locator.page).toBe(3); const source = await api.getSource("引用/a", doc.project_id); expect(source.sourceType).toBe("document"); expect(source.versionId).toBe(version.id); expect(source.quote).toBe(version.content);
    expect(fetcher.mock.calls[1][0]).toContain(encodeURIComponent(doc.id)); expect(fetcher.mock.calls[2][0]).toContain(encodeURIComponent("引用/a"));
  });
  it("sends expected revisions for editing, confirmation and deletion", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(respond({ document: { ...doc, revision: 3 }, version })).mockResolvedValueOnce(respond({ document: { ...doc, revision: 4, status: "active" } })).mockResolvedValueOnce(respond({ document: { ...doc, revision: 5, status: "deleted" } })); vi.stubGlobal("fetch", fetcher);
    const api = new HttpKnowledgeApi(); await api.saveVersion(doc.id, "修正文稿", 2); await api.confirmVersion(doc.id, version.id, 3); await api.updateDocument(doc.id, 4, { status: "deleted" });
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ content: "修正文稿", expected_revision: 2 }); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ version_id: version.id, expected_revision: 3 }); expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ status: "deleted", expected_revision: 4 });
  });
  it("shows backend public conflict messages and rejects invalid contracts", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(respond({ detail: { code: "revision_conflict", message: "资料已被更新，请刷新后再操作。" } }, 409)).mockResolvedValueOnce(respond({ items: [doc], has_more: "yes" })); vi.stubGlobal("fetch", fetcher);
    const api = new HttpKnowledgeApi(); await expect(api.confirmVersion(doc.id, version.id, 2)).rejects.toThrow("资料已被更新"); await expect(api.listDocuments({ projectId: doc.project_id })).rejects.toThrow("资料分页返回格式错误");
  });
  it("imports exact saved source versions idempotently and keeps edited preview provenance", async () => {
    const bound = { ...doc, source_status: "current", source_binding: { source_kind: "note", source_id: "笔记/a" } };
    const result = { document: bound, version: { ...version, source_status: "current" }, created: false, already_imported: true };
    const fetcher = vi.fn().mockResolvedValueOnce(respond(result, 201)).mockResolvedValueOnce(respond(result, 201)).mockResolvedValueOnce(respond({ document: { ...bound, source_status: "needs_review" }, version: { ...version, source_status: "needs_review" } })); vi.stubGlobal("fetch", fetcher);
    const api = new HttpKnowledgeApi(); const imported = await api.importNote("笔记/a", { projectId: doc.project_id, expectedSourceVersion: 3, folderId: "分类/a", expectedDocumentRevision: 2 });
    expect(imported.alreadyImported).toBe(true); expect(imported.document.sourceBinding).toEqual({ sourceKind: "note", sourceId: "笔记/a" });
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ project_id: doc.project_id, expected_source_version: 3, folder_id: "分类/a", expected_document_revision: 2 });
    await api.importFinal("会议/a", { documentKind: "minutes", expectedSourceVersion: 4, expectedDocumentRevision: 2 }); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ document_kind: "minutes", expected_source_version: 4, expected_document_revision: 2 });
    const edited = await api.saveVersion(doc.id, "保留修正版", 2, version.id); expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ content: "保留修正版", expected_revision: 2, base_version_id: version.id }); expect(edited.version.sourceStatus).toBe("needs_review"); expect(fetcher.mock.calls.every(([, init]) => init.method === "POST")).toBe(true);
  });
});
