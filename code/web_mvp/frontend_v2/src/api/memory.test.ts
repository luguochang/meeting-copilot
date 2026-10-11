import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpMemoryApi } from "./memory";
const memory = { id: "m/a", scope: "global", scope_id: "", scene: "", kind: "preference", memory_key: "response_length", value: "brief", aliases: [], source_refs: [], origin: "user", status: "candidate", revision: 1 };
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
afterEach(() => vi.unstubAllGlobals());
describe("memory API", () => {
  it("keeps host-computed candidate conflicts and reconciliation provenance", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ items: [{ ...memory, origin: "ai", conflicts_with_ids: ["confirmed"], reconciliation_job_id: "job" }], next_cursor: null, has_more: false })); vi.stubGlobal("fetch", fetcher);
    const result = await new HttpMemoryApi().list("global", ""); expect(result.items[0]).toMatchObject({ origin: "ai", status: "candidate", conflictingMemoryIds: ["confirmed"], reconciliationJobId: "job" });
  });
  it("preserves scope and cursor and creates explicit user candidates", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ items: [memory], next_cursor: "opaque+/=", has_more: true })).mockResolvedValueOnce(response(memory)); vi.stubGlobal("fetch", fetcher); const api = new HttpMemoryApi("http://localhost:9000/"); const page = await api.list("global", "", "opaque+/="); expect(page.items[0].status).toBe("candidate"); expect(new URL(fetcher.mock.calls[0][0]).searchParams.get("scope_id")).toBe(""); expect(new URL(fetcher.mock.calls[0][0]).searchParams.get("cursor")).toBe("opaque+/="); await api.create({ scope: "global", scopeId: "", scene: "", kind: "preference", key: "response_length", value: "brief", aliases: [] }); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ scope: "global", scope_id: "", scene: "", kind: "preference", memory_key: "response_length", value: "brief", aliases: [], source_refs: [], origin: "user" });
  });
  it("guards replacement revisions and supports edited new candidate IDs", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ ...memory, id: "new-candidate" })).mockResolvedValueOnce(response({ ...memory, status: "active", revision: 2 })); vi.stubGlobal("fetch", fetcher); const api = new HttpMemoryApi(); const edited = await api.update("m/a", 1, { value: "standard", aliases: [] }); expect(edited.id).toBe("new-candidate"); await api.update(edited.id, 1, { status: "active", replaceActiveId: "old", replaceActiveRevision: 4 }); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_revision: 1, status: "active", replace_active_id: "old", replace_active_revision: 4 }); expect(fetcher.mock.calls[0][0]).toContain(encodeURIComponent("m/a"));
  });
  it("uses read-only project/scene preview and rejects free-form policies", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ project_id: "project", scene: "interview", preferences: { suggestion_policy: "off" }, sources: [{ id: "m", revision: 2, scope: "project", scene: "" }] })).mockResolvedValueOnce(response({ project_id: null, scene: "general", preferences: { suggestion_policy: "arbitrary prompt" }, sources: [] })); vi.stubGlobal("fetch", fetcher); const api = new HttpMemoryApi("http://localhost:9000"); expect((await api.preview("project", "interview")).preferences.suggestion_policy).toBe("off"); const q = new URL(fetcher.mock.calls[0][0]).searchParams; expect(q.get("project_id")).toBe("project"); expect(q.get("scene")).toBe("interview"); await expect(api.preview(null, "general")).rejects.toThrow("建议频率偏好无效");
  });
  it("surfaces conflicts and validates export contents", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ detail: { message: "已存在确认值，请明确替换。" } }, 409)).mockResolvedValueOnce(response({ format: "talktrace.memory.v1", scope: "global", scope_id: "", items: [memory] })); vi.stubGlobal("fetch", fetcher); const api = new HttpMemoryApi(); await expect(api.update("m/a", 1, { status: "active" })).rejects.toThrow("请明确替换"); expect(await api.export("global", "")).toMatchObject({ format: "talktrace.memory.v1", items: [memory] });
  });
});
