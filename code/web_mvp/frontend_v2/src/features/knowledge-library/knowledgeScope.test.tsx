import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { KnowledgeApi } from "../../api/knowledge";
import { HttpMaterialsApi } from "../../api/materials";
import { parseDocumentSourceEvidence } from "../../api/sourceEvidence";
import type { DocumentSourceEvidence } from "../../domain/events";
import { KnowledgeScopePicker } from "./KnowledgeScopePicker";
import { MeetingKnowledgeScope } from "./MeetingKnowledgeScope";
import { SourceEvidenceList } from "./SourceEvidenceList";

const context = { meetingId: "meeting", projectId: "project", folderId: null, documentIds: null, selectionMode: "project", revision: 3 };
const reference: DocumentSourceEvidence = { sourceType: "document", sourceId: "span", documentId: "doc", projectId: "project", title: "确认预算", versionId: "version", version: 2, contentHash: "hash", quote: "预算为三万元", locator: { type: "pdf_page", page: 2 }, contextRevision: 3 };
function apiFixture() { return { listProjects: vi.fn().mockResolvedValue({ items: [{ id: "project", name: "产品", revision: 1, archivedAtMs: null }], hasMore: false, nextCursor: null }), listFolders: vi.fn().mockResolvedValue({ items: [], hasMore: false, nextCursor: null }), listDocuments: vi.fn().mockResolvedValue({ items: [{ id: "doc", title: "确认预算", status: "active", activeVersionId: "version" }, { id: "draft", title: "未检查预算", status: "draft", activeVersionId: null }], hasMore: false, nextCursor: null }), getMeetingContext: vi.fn().mockResolvedValue(context), saveMeetingContext: vi.fn().mockResolvedValue({ ...context, revision: 4 }), getMeetingSource: vi.fn().mockResolvedValue({ ...reference, ordinal: 0 }) } as unknown as KnowledgeApi; }
describe("meeting knowledge scope", () => {
  it("makes all / explicit empty / chosen documents distinct and skips unconfirmed sources", async () => {
    const user = userEvent.setup(); const api = apiFixture(); const change = vi.fn(); const initial = { projectId: "project", folderId: null, documentIds: null }; const view = render(<KnowledgeScopePicker api={api} value={initial} onChange={change} />);
    await screen.findByRole("option", { name: "产品" }); await user.selectOptions(screen.getByLabelText("会议资料选择方式"), "none"); expect(change).toHaveBeenLastCalledWith({ ...initial, documentIds: [] });
    await user.selectOptions(screen.getByLabelText("会议资料选择方式"), "selected"); await screen.findByLabelText(/确认预算已确认/); expect(screen.getByLabelText(/未检查预算暂不可用/)).toBeDisabled();
    await user.click(screen.getByLabelText(/确认预算已确认/)); expect(change).toHaveBeenLastCalledWith({ ...initial, documentIds: ["doc"] });
    view.rerender(<KnowledgeScopePicker api={api} value={{ ...initial, documentIds: ["doc"] }} onChange={change} />); await user.selectOptions(screen.getByLabelText("会议资料项目"), ""); expect(change).toHaveBeenLastCalledWith({ projectId: null, folderId: null, documentIds: null });
  });
  it("saves the SQLite scope revision explicitly and preserves drafts on conflict", async () => {
    const user = userEvent.setup(); const api = apiFixture(); vi.mocked(api.saveMeetingContext).mockRejectedValueOnce(new Error("范围已更新，请重新读取。")); const saved = vi.fn(); render(<MeetingKnowledgeScope api={api} meetingId="meeting" onSaved={saved} />);
    await screen.findByLabelText("会议资料选择方式"); await user.selectOptions(screen.getByLabelText("会议资料选择方式"), "none"); expect(screen.getByText("范围已修改，尚未保存。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存资料范围" })); await screen.findByRole("alert"); expect(saved).not.toHaveBeenCalled(); expect(screen.getByLabelText("会议资料选择方式")).toHaveValue("none");
    await user.click(screen.getByRole("button", { name: "保存资料范围" })); await waitFor(() => expect(api.saveMeetingContext).toHaveBeenLastCalledWith("meeting", expect.objectContaining({ projectId: "project", documentIds: [] }), 3)); expect(saved).toHaveBeenCalledOnce();
  });
  it.each(["needs_review", "deleted"])("does not present %s sources as confirmed or permit adding them", async (sourceStatus) => {
    const api = apiFixture(); const change = vi.fn();
    vi.mocked(api.listDocuments).mockResolvedValue({ items: [{ id: "stale", title: "旧关联资料", status: "active", activeVersionId: "version", sourceStatus }], hasMore: false, nextCursor: null } as never);
    render(<KnowledgeScopePicker api={api} value={{ projectId: "project", folderId: null, documentIds: ["another"] }} onChange={change} />);
    const checkbox = await screen.findByRole("checkbox", { name: /旧关联资料/ });
    expect(checkbox).toBeDisabled(); expect(checkbox).not.toHaveAccessibleName(/已确认/);
    await userEvent.click(checkbox); expect(change).not.toHaveBeenCalled();
  });
  it.each([
    { status: "active", sourceStatus: "needs_review" },
    { status: "active", sourceStatus: "deleted" },
    { status: "disabled", sourceStatus: "current" },
    { status: "deleted", sourceStatus: "current" },
  ])("keeps an already bound unavailable source removable (%s)", async (state) => {
    const api = apiFixture(); const change = vi.fn();
    vi.mocked(api.listDocuments).mockResolvedValue({ items: [{ id: "old", title: "已选旧资料", activeVersionId: "version", ...state }], hasMore: false, nextCursor: null } as never);
    const initial = { projectId: "project", folderId: null, documentIds: ["old"] };
    const view = render(<KnowledgeScopePicker api={api} value={initial} onChange={change} />);
    const checkbox = await screen.findByRole("checkbox", { name: /已选旧资料/ });
    expect(checkbox).toBeChecked(); expect(checkbox).toBeEnabled();
    await userEvent.click(checkbox); expect(change).toHaveBeenLastCalledWith({ ...initial, documentIds: [] });
    view.rerender(<KnowledgeScopePicker api={api} value={{ ...initial, documentIds: [] }} onChange={change} />);
    expect(screen.getByRole("checkbox", { name: /已选旧资料/ })).toBeDisabled();
  });
  it("allows removing selected documents hidden by paging or deletion without silently dropping the binding", async () => {
    const api = apiFixture(); const change = vi.fn(); const initial = { projectId: "project", folderId: null, documentIds: ["no-longer-listed"] };
    render(<KnowledgeScopePicker api={api} value={initial} onChange={change} />);
    await screen.findByRole("checkbox", { name: /确认预算/ });
    expect(screen.getByLabelText("当前页之外的所选资料")).toHaveTextContent(/已从当前范围移除/);
    expect(change).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "取消这份资料" }));
    expect(change).toHaveBeenLastCalledWith({ ...initial, documentIds: [] });
  });
  it("refreshes a previously available selection when its source changes and still permits cancelling", async () => {
    const api = apiFixture(); const change = vi.fn();
    const doc = { id: "doc", title: "确认预算", status: "active", activeVersionId: "version", sourceStatus: "current" };
    vi.mocked(api.listDocuments).mockResolvedValueOnce({ items: [doc], hasMore: false, nextCursor: null } as never).mockResolvedValueOnce({ items: [{ ...doc, sourceStatus: "needs_review" }], hasMore: false, nextCursor: null } as never);
    render(<KnowledgeScopePicker api={api} value={{ projectId: "project", folderId: null, documentIds: ["doc"] }} onChange={change} />);
    await screen.findByRole("checkbox", { name: /确认预算.*已确认/ });
    await userEvent.click(screen.getByRole("button", { name: "刷新资料状态" }));
    const checkbox = await screen.findByRole("checkbox", { name: /确认预算.*来源已修改，待复核/ });
    expect(checkbox).toBeEnabled(); expect(checkbox).toBeChecked();
    await userEvent.click(checkbox); expect(change).toHaveBeenLastCalledWith({ projectId: "project", folderId: null, documentIds: [] });
  });
});
describe("typed source evidence", () => {
  it.each(["attachment_scope_or_version_changed", "unknown_reason_with_private_diagnostics", null])("opens the exact historical attachment with a safe Chinese notice for reason=%s", async (reason) => {
    const rawSource = { source_type: "meeting_attachment", source_id: "material-span", set_id: "material-set", meeting_id: "meeting", attachment_id: "attachment", title: "公开合成通知需求", version_id: "v1", version: 1, content_hash: "v1-hash", locator: { paragraph: 3 }, quote: "通知允许在权益成功后补发。", context_revision: 1, ordinal: 2, source_state: "stale", stale_reason: reason };
    const source = parseDocumentSourceEvidence(rawSource);
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ source: rawSource }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    try {
      const materials = new HttpMaterialsApi("http://localhost:8770");
      const api = { getMeetingAttachmentSource: (meetingId: string, sourceId: string) => materials.source(meetingId, sourceId) } as unknown as KnowledgeApi;
      const opened = vi.fn();
      render(<SourceEvidenceList evidence={[source]} api={api} meetingId="meeting" onTranscript={vi.fn()} onDocument={opened} stale />);
      fireEvent.click(screen.getByText(/查看全部依据/));
      fireEvent.click(screen.getByRole("button", { name: /本场附件 · 公开合成通知需求 · 第 1 版/ }));
      const notice = await screen.findByRole("alert");
      expect(notice).toHaveTextContent(reason === "attachment_scope_or_version_changed" ? "使用范围或版本已变化" : "已不用于当前建议");
      expect(notice).toHaveTextContent(/历史原文.*核对/);
      expect(notice).not.toHaveTextContent(/attachment_scope_or_version_changed|unknown_reason_with_private_diagnostics/);
      await waitFor(() => expect(opened).toHaveBeenCalledWith(expect.objectContaining({ sourceType: "meeting_attachment", sourceId: "material-span", versionId: "v1", contentHash: "v1-hash", ordinal: 2, sourceState: "stale", quote: rawSource.quote }), "meeting"));
      expect(fetcher).toHaveBeenCalledOnce();
      expect(fetcher.mock.calls[0][0]).toBe("http://localhost:8770/v2/meetings/meeting/sources/material-span?allow_historical=true");
      expect(fetcher.mock.calls[0][1].method).toBeUndefined();
    } finally { vi.unstubAllGlobals(); }
  });
  it("keeps transcript and document navigation separate, validating the bound document version", async () => {
    const user = userEvent.setup(); const api = apiFixture(); const transcript = vi.fn(); const document = vi.fn(); render(<SourceEvidenceList evidence={[{ segmentId: "segment", transcriptSeq: 1, startMs: null, endMs: null, quote: "会议中说的话" }, reference]} api={api} meetingId="meeting" onTranscript={transcript} onDocument={document} />);
    await user.click(screen.getByText(/查看全部依据/)); await user.click(screen.getByRole("button", { name: /会议原话/ })); expect(transcript).toHaveBeenCalledWith("segment");
    await user.click(screen.getByRole("button", { name: /资料 · 确认预算 · 第 2 版 · 第 2 页/ })); await waitFor(() => expect(document).toHaveBeenCalledWith(reference, "meeting")); expect(api.getMeetingSource).toHaveBeenCalledWith("span", "meeting"); expect(transcript).toHaveBeenCalledOnce();
  });
  it.each(["context", "version", "backend"])("never opens a latest replacement when %s has changed", async (failure) => {
    const api = apiFixture(); if (failure === "context") vi.mocked(api.getMeetingContext).mockResolvedValue({ ...context, revision: 4 } as never); else if (failure === "version") vi.mocked(api.getMeetingSource).mockResolvedValue({ ...reference, ordinal: 0, versionId: "new-version", contentHash: "new-hash", locator: { type: "pdf_page", page: 2 } }); else vi.mocked(api.getMeetingSource).mockRejectedValue(new Error("资料已经禁用。"));
    const document = vi.fn(); render(<SourceEvidenceList evidence={[reference]} api={api} meetingId="meeting" onTranscript={vi.fn()} onDocument={document} />); fireEvent.click(screen.getByText(/查看全部依据/)); fireEvent.click(screen.getByRole("button", { name: /资料 · 确认预算/ })); expect(await screen.findByRole("alert")).toHaveTextContent(/过期|禁用/); expect(document).not.toHaveBeenCalled();
  });
});
