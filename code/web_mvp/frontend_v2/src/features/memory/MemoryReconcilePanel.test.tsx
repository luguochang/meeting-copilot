import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { MemoryReconcileApi, MemoryReconcileJob, MemoryReconcileSettings } from "../../api/memoryReconcile";
import { MemoryReconcilePanel } from "./MemoryReconcilePanel";

const settings: MemoryReconcileSettings = { projectId: "project", mode: "explicit_only", revision: 2, updatedAtMs: 100 };
const job: MemoryReconcileJob = { id: "job", projectId: "project", documentId: "document", documentTitle: "术语更正", sourceVersionId: "version3", sourceVersion: 3, status: "pending", errorCode: null, errorMessage: null, createdAtMs: 100, updatedAtMs: 100, attempts: 0, resultMemoryIds: [], canRetry: true, requiresResume: true };
function fixture(): MemoryReconcileApi { return { settings: vi.fn().mockResolvedValue(settings), configure: vi.fn().mockResolvedValue({ ...settings, mode: "off", revision: 3 }), list: vi.fn().mockResolvedValue({ items: [job], hasMore: false, nextCursor: null }), request: vi.fn(), retry: vi.fn().mockResolvedValue(job), cancel: vi.fn().mockResolvedValue({ ...job, status: "cancelled" }) }; }
describe("background memory candidate control", () => {
  it("shows old pending as awaiting the user and never resumes on mount", async () => {
    const api = fixture(); render(<MemoryReconcilePanel api={api} projectId="project" onChanged={vi.fn()} />); await screen.findByText(/等待你恢复/); expect(api.retry).not.toHaveBeenCalled(); expect(api.request).not.toHaveBeenCalled(); expect(screen.getByRole("button", { name: "继续核对此版本" })).toBeEnabled();
  });
  it("turns off with the loaded revision and prevents retries while off", async () => {
    const api = fixture(); const user = userEvent.setup(); render(<MemoryReconcilePanel api={api} projectId="project" onChanged={vi.fn()} />); await screen.findByText(/等待你恢复/); vi.mocked(api.settings).mockResolvedValue({ ...settings, mode: "off", revision: 3 }); await user.selectOptions(screen.getByRole("combobox", { name: "后台记忆整理模式" }), "off"); await waitFor(() => expect(api.configure).toHaveBeenCalledWith("project", "off", 2)); await waitFor(() => expect(screen.getByRole("button", { name: "继续核对此版本" })).toBeDisabled()); expect(api.retry).not.toHaveBeenCalled();
  });
  it("retries only the explicit current source version and preserves conflict message", async () => {
    const api = fixture(); vi.mocked(api.retry).mockRejectedValue(new Error("原资料已更新，请重新检查。")); const user = userEvent.setup(); render(<MemoryReconcilePanel api={api} projectId="project" onChanged={vi.fn()} />); await user.click(await screen.findByRole("button", { name: "继续核对此版本" })); await screen.findByRole("alert"); expect(api.retry).toHaveBeenCalledWith("job", "version3"); expect(screen.getByText(/原资料已更新/)).toBeVisible();
  });
  it("refreshes candidates after success without activating any memory", async () => {
    const api = fixture(); vi.mocked(api.list).mockResolvedValue({ items: [{ ...job, status: "succeeded", resultMemoryIds: ["candidate"], canRetry: false, requiresResume: false }], hasMore: false, nextCursor: null }); const changed = vi.fn(); const user = userEvent.setup(); render(<MemoryReconcilePanel api={api} projectId="project" onChanged={changed} />); await screen.findByText(/仍待你确认/); expect(changed).toHaveBeenCalledTimes(1); await user.click(screen.getByRole("button", { name: "刷新整理记录" })); await screen.findByText(/仍待你确认/); expect(changed).toHaveBeenCalledTimes(1); expect(api.retry).not.toHaveBeenCalled();
  });
  it("does not display previous-project jobs when a scope request is still resolving", async () => {
    const api = fixture(); const changed = vi.fn(); const view = render(<MemoryReconcilePanel api={api} projectId="project" onChanged={changed} />); await screen.findByText("术语更正"); vi.mocked(api.settings).mockReturnValue(new Promise(() => {})); vi.mocked(api.list).mockReturnValue(new Promise(() => {})); view.rerender(<MemoryReconcilePanel api={api} projectId="other" onChanged={changed} />); await waitFor(() => expect(screen.queryByText("术语更正")).not.toBeInTheDocument()); expect(screen.getByRole("combobox", { name: "后台记忆整理模式" })).toBeDisabled(); expect(api.retry).not.toHaveBeenCalled();
  });
});
