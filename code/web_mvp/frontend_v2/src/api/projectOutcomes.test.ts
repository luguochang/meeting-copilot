import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpProjectOutcomesApi } from "./projectOutcomes";
const source = { source_type: "meeting_entity", source_id: "entity/a", project_id: "p/a", meeting_id: "meeting", version: 3, content_hash: "h", quote: "人工确认", locator: { type: "meeting_entity", kind: "decision" } };
const item = { id: "item/a", project_id: "p/a", type: "decision", text: "确认方案", status: "active", owner: null, due: null, source_meeting_id: "meeting", source_refs: [source], origin: "user", revision: 3, supersedes_id: null };
const proposal = { id: "proposal/a", project_id: "p/a", meeting_id: "meeting", origin: "user", base_revision: 9, changes: [{ action: "create", type: "decision", text: "确认方案", source_refs: [source] }], status: "pending", revision: 1, is_stale: false };
const brief = { project_id: "p/a", project_name: "项目甲", project_revision: 9, folder_id: "folder/a", document_ids: null, generated_at_ms: 1000, decisions: [item], unfinished_tasks: [], open_questions: [], needs_review: [], needs_review_documents: [], truncated: false };
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
afterEach(() => vi.unstubAllGlobals());
describe("project outcomes API", () => {
  it("accepts real-shaped frozen document evidence without inventing an ordinal, then reads its actual position", async () => {
    const frozen = { source_type: "document", source_id: "span/frozen", document_id: "document/frozen", project_id: "p/a", title: "模拟人工终稿", version_id: "version-1", version: 1, content_hash: "frozen-hash", context_revision: 3, quote: "尚待核对重复处理边界", locator: { type: "review_document", paragraph: 4, offset: 0, source_id: "review-final", source_version: 1, source_hash: "source-hash" } };
    const wireProposal = { ...proposal, origin: "ai", changes: [{ action: "create", type: "question", text: "确认订单重复回调边界", source_refs: [frozen] }] };
    const fetcher = vi.fn().mockResolvedValueOnce(response({ items: [wireProposal], has_more: false, next_cursor: null })).mockResolvedValueOnce(response({ source: { ...frozen, ordinal: 3 } }));
    vi.stubGlobal("fetch", fetcher);
    const api = new HttpProjectOutcomesApi();
    const parsed = (await api.listProposals("p/a")).items[0].changes[0].sourceRefs[0];
    expect(parsed).toMatchObject({ sourceType: "document", version: 1, contentHash: "frozen-hash", versionId: "version-1", locator: frozen.locator });
    expect(parsed).not.toHaveProperty("ordinal");
    expect(await api.getSource(parsed)).toMatchObject({ ...parsed, ordinal: 3 });
    expect(fetcher.mock.calls.every(([, init]) => init.method === undefined)).toBe(true);
  });
  it.each([-1, "3", 0.5])("still rejects an invalid supplied source ordinal %s", async (ordinal) => {
    const badSource = { ...source, source_type: "document", document_id: "doc", title: "模拟资料", version_id: "v1", ordinal };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ items: [{ ...proposal, changes: [{ action: "create", source_refs: [badSource] }] }], has_more: false, next_cursor: null })));
    await expect(new HttpProjectOutcomesApi().listProposals("p/a")).rejects.toThrow("项目来源位置无效");
  });
  it("preserves opaque cursors, versions and confirmed source types", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ items: [item], project_revision: 9, next_cursor: "opaque+/=", has_more: true })).mockResolvedValueOnce(response({ source })); vi.stubGlobal("fetch", fetcher); const api = new HttpProjectOutcomesApi("http://localhost:9000/"); const page = await api.listState("p/a", "active", "opaque+/="); expect(page.items[0].sourceRefs[0].sourceType).toBe("meeting_entity"); expect(new URL(fetcher.mock.calls[0][0]).searchParams.get("cursor")).toBe("opaque+/="); const reference = await api.getSource(page.items[0].sourceRefs[0]); expect(reference.version).toBe(3); expect(new URL(fetcher.mock.calls[1][0]).searchParams.get("meeting_id")).toBe("meeting");
  });
  it("sends independent item, project and proposal revision guards", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ item, project_revision: 10 })).mockResolvedValueOnce(response({ proposal, items: [item], project_revision: 11 })).mockResolvedValueOnce(response({ proposal })); vi.stubGlobal("fetch", fetcher); const api = new HttpProjectOutcomesApi(); await api.updateState(item.id, { text: "修正", owner: null }, 3, 9); await api.confirmProposal(proposal.id, 1, 10); await api.rejectProposal(proposal.id, 2); expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ text: "修正", owner: null, expected_revision: 3, expected_project_revision: 9 }); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_revision: 1, expected_project_revision: 10 }); expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ expected_revision: 2 });
  });
  it("keeps user-proposed changes pending and carries actual source versions", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ source })).mockResolvedValueOnce(response({ proposal })); vi.stubGlobal("fetch", fetcher); const api = new HttpProjectOutcomesApi(); const ref = await api.getSource({ sourceType: "meeting_entity", sourceId: source.source_id, projectId: source.project_id, meetingId: "meeting" }); await api.createProposal("p/a", [{ action: "create", type: "decision", text: "确认方案", sourceRefs: [ref] }], 9, "meeting"); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({ origin: "user", expected_project_revision: 9, changes: [{ source_refs: [source] }] }); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("reports public conflict errors and rejects malformed document references", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ detail: { message: "项目已更新，请核对。" } }, 409)).mockResolvedValueOnce(response({ source: { ...source, source_type: "document", meeting_id: null } })); vi.stubGlobal("fetch", fetcher); const api = new HttpProjectOutcomesApi(); await expect(api.confirmProposal("proposal", 1, 9)).rejects.toThrow("项目已更新"); await expect(api.getSource({ sourceType: "document", sourceId: "span", projectId: "p", meetingId: null })).rejects.toThrow("项目文本字段无效");
  });
  it("previews the exact pre-meeting scope with a real revision and no invented meeting ID", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(brief)).mockResolvedValueOnce(response({ ...brief, document_ids: [], decisions: [] })); vi.stubGlobal("fetch", fetcher);
    const api = new HttpProjectOutcomesApi(); const controller = new AbortController();
    const full = await api.previewBrief({ projectId: "p/a", folderId: "folder/a", documentIds: null }, controller.signal);
    const empty = await api.previewBrief({ projectId: "p/a", folderId: "folder/a", documentIds: [] });
    expect(full.revision).toBe(9); expect(full.decisions[0].sourceRefs[0].version).toBe(3); expect(empty.decisions).toEqual([]);
    expect(fetcher.mock.calls[0][0]).toBe("/v2/knowledge/projects/p%2Fa/brief-preview");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ folder_id: "folder/a", document_ids: null, limit: 20 });
    expect(JSON.parse(fetcher.mock.calls[1][1].body).document_ids).toEqual([]); expect(fetcher.mock.calls[0][1].signal).toBe(controller.signal);
  });
  it.each([
    { ...brief, folder_id: "different" },
    { ...brief, document_ids: [] },
    { ...brief, decisions: [{ ...item, status: "needs_review" }] },
    { ...brief, decisions: [{ ...item, project_id: "other" }] },
  ])("rejects mismatched scope or invalid current conclusions in a brief", async (result) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(result)));
    await expect(new HttpProjectOutcomesApi().previewBrief({ projectId: "p/a", folderId: "folder/a", documentIds: null })).rejects.toThrow(/范围不一致|未确认或过期/);
  });
});
