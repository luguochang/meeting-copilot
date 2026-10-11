import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpKnowledgeApi } from "./knowledge";

function downloadFixture(response: Response) {
  const createObjectURL = vi.fn().mockReturnValue("blob:project-export");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", class extends URL { static createObjectURL = createObjectURL; static revokeObjectURL = revokeObjectURL; });
  const clicked: { filename: string; href: string }[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicked.push({ filename: this.download, href: this.href }); });
  const fetcher = vi.fn().mockResolvedValue(response); vi.stubGlobal("fetch", fetcher);
  return { createObjectURL, revokeObjectURL, clicked, fetcher };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("project export download", () => {
  it("downloads one read-only project ZIP and releases its object URL and temporary link", async () => {
    const fixture = downloadFixture(new Response("PK test archive", { headers: { "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="project-1234567890abcdef.zip"' } }));
    await new HttpKnowledgeApi("http://localhost:8766/").exportProject("项目/a");
    expect(fixture.fetcher).toHaveBeenCalledExactlyOnceWith("http://localhost:8766/v2/knowledge/projects/%E9%A1%B9%E7%9B%AE%2Fa/export", { headers: { Accept: "application/zip" }, signal: expect.any(AbortSignal) });
    expect(fixture.clicked).toEqual([{ filename: "project-1234567890abcdef.zip", href: "blob:project-export" }]);
    expect(fixture.revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:project-export");
    expect(document.querySelector('a[href="blob:project-export"]')).toBeNull();
  });
  it("does not save JSON errors as ZIP files and preserves the public size-limit explanation", async () => {
    const fixture = downloadFixture(new Response(JSON.stringify({ detail: { code: "project_export_too_large", message: "项目资料超出导出上限。" } }), { status: 413, headers: { "Content-Type": "application/json" } }));
    await expect(new HttpKnowledgeApi().exportProject("project-1")).rejects.toThrow("项目资料超出导出上限。");
    expect(fixture.createObjectURL).not.toHaveBeenCalled(); expect(fixture.clicked).toEqual([]);
  });
  it("rejects an HTML fallback even when the server returned status 200", async () => {
    const fixture = downloadFixture(new Response("<html>workbench</html>", { headers: { "Content-Type": "text/html" } }));
    await expect(new HttpKnowledgeApi().exportProject("project-1")).rejects.toThrow("项目资料导出格式错误");
    expect(fixture.clicked).toEqual([]);
  });
  it("uses a safe ZIP filename if the response filename contains an unexpected path", async () => {
    const fixture = downloadFixture(new Response("PK", { headers: { "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="../unsafe.exe"' } }));
    await new HttpKnowledgeApi().exportProject("project-1");
    expect(fixture.clicked[0].filename).toBe("talktrace-project.zip");
  });
  it("bounds a stalled request without starting a second download", async () => {
    vi.useFakeTimers(); const fetcher = vi.fn().mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => { init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true }); }));
    vi.stubGlobal("fetch", fetcher);
    const result = expect(new HttpKnowledgeApi().exportProject("project-1")).rejects.toThrow("项目资料导出超时");
    await vi.advanceTimersByTimeAsync(60_000); await result;
    expect(fetcher).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
  });
  it("forwards caller cancellation and clears its deadline", async () => {
    vi.useFakeTimers(); const controller = new AbortController();
    vi.stubGlobal("fetch", vi.fn().mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => { init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true }); })));
    const result = expect(new HttpKnowledgeApi().exportProject("project-1", controller.signal)).rejects.toThrow("aborted");
    controller.abort(); await result; expect(vi.getTimerCount()).toBe(0);
  });
});
