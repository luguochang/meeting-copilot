import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { parsePreparationJob, type PreparationBriefApi, type PreparationInput, type PreparationJob } from "../../api/preparationBrief";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";
import { rawPreparationJob } from "../../api/preparationBrief.fixtures";
import { PreparationBriefPanel } from "./PreparationBriefPanel";

const input: PreparationInput = { selection: { projectId: "project", folderId: null, documentIds: null }, goal: "确认上线条件", role: "项目负责人", focus: ["风险"], scene: "project" };
function fixture(job = parsePreparationJob(rawPreparationJob())) {
  const api = { list: vi.fn().mockResolvedValue({ items: [job], hasMore: false, nextCursor: null }), get: vi.fn().mockResolvedValue(job), start: vi.fn().mockResolvedValue(job), cancel: vi.fn().mockResolvedValue({ ...job, status: "cancelled" }), retry: vi.fn().mockResolvedValue(job) } satisfies PreparationBriefApi;
  const projectApi = { getWiki: vi.fn().mockResolvedValue({ projectId: "project", revision: 9 }) } as unknown as ProjectOutcomesApi;
  return { api, projectApi };
}
describe("meeting target preparation", () => {
  it("shows goal planning and does not submit a job on mount", async () => {
    const f = fixture(); render(<PreparationBriefPanel {...f} input={input} open />);
    expect(await screen.findByText("哪些条件还没有完成？")).toBeInTheDocument();
    expect(screen.getAllByText("按本次目标建议，待会议核实。")).toHaveLength(2);
    expect(f.api.start).not.toHaveBeenCalled(); expect(f.api.retry).not.toHaveBeenCalled();
  });
  it("immediately hides a result after target or scope changes", async () => {
    const f = fixture(); const view = render(<PreparationBriefPanel {...f} input={input} open />);
    await screen.findByText("哪些条件还没有完成？");
    view.rerender(<PreparationBriefPanel {...f} input={{ ...input, selection: { ...input.selection, documentIds: [] } }} open />);
    expect(screen.queryByText("哪些条件还没有完成？")).not.toBeInTheDocument();
    expect(screen.getByText("这份任务使用其他目标或资料范围，结果未带入当前准备。")).toBeInTheDocument();
    expect(f.api.start).not.toHaveBeenCalled();
  });
  it("requires explicit resume and uses the current project revision", async () => {
    const job: PreparationJob = { ...parsePreparationJob(rawPreparationJob()), status: "pending", result: null, completedAtMs: null, requiresResume: true };
    const f = fixture(job); render(<PreparationBriefPanel {...f} input={input} open />);
    fireEvent.click(await screen.findByRole("button", { name: "明确恢复准备" }));
    await waitFor(() => expect(f.api.retry).toHaveBeenCalledWith(job, 9));
    expect(f.api.start).not.toHaveBeenCalled();
  });
  it("keeps stale preparation out of the current advice", async () => {
    const f = fixture({ ...parsePreparationJob(rawPreparationJob()), evidenceStatus: "stale" });
    render(<PreparationBriefPanel {...f} input={input} open />);
    await screen.findByText(/来源或项目状态已变化/);
    expect(screen.queryByText("哪些条件还没有完成？")).not.toBeInTheDocument();
  });
  it("does not start without a meeting target", async () => {
    const f = fixture(); render(<PreparationBriefPanel {...f} input={{ ...input, goal: "" }} open />);
    expect(screen.getByRole("button", { name: "让 Pi 按本次目标准备" })).toBeDisabled();
    await screen.findByText(/这份任务使用其他目标/);
    expect(f.api.start).not.toHaveBeenCalled();
  });
  it("cancels an old pending job with a single explicit action", async () => {
    const job: PreparationJob = { ...parsePreparationJob(rawPreparationJob()), status: "pending", result: null, completedAtMs: null, requiresResume: true };
    const f = fixture(job); render(<PreparationBriefPanel {...f} input={input} open />);
    fireEvent.click(await screen.findByRole("button", { name: "取消准备" }));
    await waitFor(() => expect(f.api.cancel).toHaveBeenCalledWith("prep"));
    expect(f.api.retry).not.toHaveBeenCalled();
  });
});
