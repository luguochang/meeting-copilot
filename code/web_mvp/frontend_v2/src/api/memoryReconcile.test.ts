import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpMemoryReconcileApi } from "./memoryReconcile";

const job = { id: "job/a", project_id: "project", document_id: "doc/a", document_title: "名称纠正", source_version_id: "version/a", source_version: 3, status: "failed", error_code: "execution_interrupted", error_message: "执行中断，请手动重试。", created_at_ms: 100, updated_at_ms: 200, attempts: 1, result_memory_ids: [], can_retry: true, requires_resume: false };
const settings = { project_id: "project", mode: "explicit_only", revision: 0, updated_at_ms: 0 };
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
afterEach(() => vi.unstubAllGlobals());
describe("memory reconciliation API", () => {
  it("reads settings and history without starting jobs and preserves resumable state", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ settings })).mockResolvedValueOnce(response({ items: [{ ...job, status: "pending", attempts: 0, requires_resume: true }], has_more: true, next_cursor: "opaque+/=" })); vi.stubGlobal("fetch", fetcher);
    const api = new HttpMemoryReconcileApi("http://localhost:9000/"); expect((await api.settings("project")).revision).toBe(0); const history = await api.list("project", "opaque+/="); expect(history.items[0].requiresResume).toBe(true); expect(history.items[0].attempts).toBe(0); expect(new URL(fetcher.mock.calls[1][0]).searchParams.get("cursor")).toBe("opaque+/="); expect(fetcher.mock.calls.every((call) => !call[1].method)).toBe(true);
  });
  it("guards mode updates and targets the exact version for explicit retry", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ settings: { ...settings, mode: "off", revision: 2 } })).mockResolvedValueOnce(response({ job: { ...job, status: "pending" } })).mockResolvedValueOnce(response({ job: { ...job, status: "cancelled" } })); vi.stubGlobal("fetch", fetcher); const api = new HttpMemoryReconcileApi();
    await api.configure("project", "off", 1); expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ mode: "off", expected_revision: 1 }); await api.retry("job/a", "version/a"); expect(fetcher.mock.calls[1][0]).toContain("job%2Fa/retry"); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_version_id: "version/a" }); expect((await api.cancel("job/a")).status).toBe("cancelled");
  });
  it("rejects ambiguous work status and surfaces version conflicts", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ items: [{ ...job, requires_resume: undefined }], has_more: false, next_cursor: null })).mockResolvedValueOnce(response({ detail: "资料版本已变化，请重新确认。" }, 409)); vi.stubGlobal("fetch", fetcher); const api = new HttpMemoryReconcileApi(); await expect(api.list("project")).rejects.toThrow("状态字段错误"); await expect(api.request("doc/a", "version/a")).rejects.toThrow("资料版本已变化");
  });
});
