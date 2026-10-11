import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecoveryPanel } from "./RecoveryPanel";
import { RecoveryApiError } from "../../api/transcriptRecovery";
import { recoveryApi, recoveryJob, recoverySnapshot } from "../../test/transcriptRecoveryFixtures";

async function open(api = recoveryApi(), onMerged = vi.fn()) {
  render(<RecoveryPanel api={api} meetingId="meeting-recovery" onMerged={onMerged} />);
  await userEvent.click(screen.getByRole("button", { name: "查看文字恢复范围" }));
  await screen.findByRole("button", { name: /批次 2.*候选已生成/ });
  return { api, onMerged };
}
it("does not read or start any task until opened, then read alone creates nothing", async () => {
  const api = recoveryApi(); render(<RecoveryPanel api={api} meetingId="meeting-recovery" />);
  expect(api.get).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "查看文字恢复范围" }));
  await screen.findByRole("button", { name: "开始本地补识别" });
  expect(api.create).not.toHaveBeenCalled(); expect(api.retry).not.toHaveBeenCalled(); expect(api.merge).not.toHaveBeenCalled();
});
it("creates the selected track, epoch, exact samples and source digest only", async () => {
  const { api } = await open();
  await userEvent.click(screen.getByRole("button", { name: "开始本地补识别" }));
  expect(api.create).toHaveBeenCalledWith("meeting-recovery", expect.objectContaining({ track: "system_audio", epoch: 2, startSample: 16_000, endSample: 48_000, audioDigest: "a".repeat(64), coverageDigest: "c".repeat(64) }), expect.any(AbortSignal));
  expect(api.merge).not.toHaveBeenCalled();
});
it("keeps per-job edits and original ASR while switching jobs, then merges only the chosen draft", async () => {
  const api = recoveryApi(recoverySnapshot({ jobs: [recoveryJob(), recoveryJob({ id: "job-2", track: "microphone", epoch: 1, originalCandidateText: "另一段原ASR", candidateText: "另一段原ASR" })] }));
  const onMerged = vi.fn(); await open(api, onMerged);
  await userEvent.click(screen.getByRole("button", { name: /会议声音.*候选已生成/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "补识别候选文字" }), { target: { value: "预算还需复核。" } });
  await userEvent.click(screen.getByRole("button", { name: /麦克风.*候选已生成/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "补识别候选文字" }), { target: { value: "另一任务草稿" } });
  await userEvent.click(screen.getByRole("button", { name: /会议声音.*候选已生成/ }));
  expect(screen.getByRole("textbox", { name: "补识别候选文字" })).toHaveValue("预算还需复核。");
  await userEvent.click(screen.getByText("查看原始 ASR 候选")); expect(screen.getByText("预算仍待核对。")).toBeVisible();
  expect(api.merge).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "确认并合并到识别原文" }));
  await waitFor(() => expect(onMerged).toHaveBeenCalledOnce());
  expect(api.merge).toHaveBeenCalledWith("meeting-recovery", "job-1", 1, "预算还需复核。", expect.any(AbortSignal));
  expect(screen.getByText(/补识别原文已合并，未做 AI 校对/)).toBeVisible();
});
it("blocks merge while recording even when a candidate is ready", async () => {
  const { api } = await open(recoveryApi(recoverySnapshot({ mergeAllowed: false })));
  await userEvent.click(screen.getByRole("button", { name: /候选已生成/ }));
  expect(screen.getByRole("button", { name: "确认并合并到识别原文" })).toBeDisabled();
  expect(screen.getByText(/请等待会议结束且没有活跃录音/)).toBeVisible(); expect(api.merge).not.toHaveBeenCalled();
});
it("preserves a draft across candidate-version refresh and requires explicit replacement", async () => {
  const api = recoveryApi(); await open(api);
  await userEvent.click(screen.getByRole("button", { name: /候选已生成/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "补识别候选文字" }), { target: { value: "保留我的修正" } });
  vi.mocked(api.get).mockResolvedValue(recoverySnapshot({ jobs: [recoveryJob({ version: 4, candidateVersion: 2, candidateText: "新版候选", originalCandidateText: "新版候选" })] }));
  await userEvent.click(screen.getByRole("button", { name: "刷新恢复状态" }));
  await screen.findByText(/候选版本已更新，你的修改仍保留/);
  expect(screen.getByRole("textbox", { name: "补识别候选文字" })).toHaveValue("保留我的修正");
  expect(screen.getByRole("button", { name: "确认并合并到识别原文" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "使用最新候选并放弃本任务草稿" }));
  expect(screen.getByRole("textbox", { name: "补识别候选文字" })).toHaveValue("新版候选"); expect(api.merge).not.toHaveBeenCalled();
});
it("keeps edits after a 409 and never renders an arbitrary exception", async () => {
  const api = recoveryApi(); vi.mocked(api.merge).mockRejectedValue(new RecoveryApiError(409, "recovery_version_conflict")); await open(api);
  await userEvent.click(screen.getByRole("button", { name: /候选已生成/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "补识别候选文字" }), { target: { value: "冲突后仍保留" } });
  await userEvent.click(screen.getByRole("button", { name: "确认并合并到识别原文" }));
  await screen.findByRole("alert"); expect(screen.getByRole("textbox", { name: "补识别候选文字" })).toHaveValue("冲突后仍保留");
});
it("shows cancellation awaiting actual execution end and does not offer premature retry", async () => {
  const api = recoveryApi(recoverySnapshot({ jobs: [recoveryJob({ status: "running", executionActive: true })] }));
  vi.mocked(api.cancel).mockResolvedValue(recoveryJob({ status: "cancelled", executionActive: true, version: 4 }));
  render(<RecoveryPanel api={api} meetingId="meeting-recovery" />); await userEvent.click(screen.getByRole("button", { name: "查看文字恢复范围" }));
  await userEvent.click(await screen.findByRole("button", { name: /本地补识别中/ }));
  await userEvent.click(screen.getByRole("button", { name: "取消本次补识别" }));
  await screen.findByText(/已请求取消，仍在等待本次执行结束/);
  expect(api.cancel).toHaveBeenCalledWith("meeting-recovery", "job-1", 3, expect.any(AbortSignal));
  expect(screen.queryByRole("button", { name: "手动重试本次补识别" })).not.toBeInTheDocument(); expect(api.retry).not.toHaveBeenCalled();
});
it("does not re-enable merging or overwrite latest job from a late pre-mutation read", async () => {
  const api = recoveryApi(); await open(api); await userEvent.click(screen.getByRole("button", { name: /候选已生成/ }));
  let resolveOld!: (value: ReturnType<typeof recoverySnapshot>) => void;
  vi.mocked(api.get).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; })).mockResolvedValue(recoverySnapshot({ jobs: [recoveryJob({ status: "merged", version: 4 })] }));
  await userEvent.click(screen.getByRole("button", { name: "刷新恢复状态" }));
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  await userEvent.click(screen.getByRole("button", { name: "确认并合并到识别原文" }));
  await screen.findByText(/补识别原文已合并/); resolveOld(recoverySnapshot());
  await waitFor(() => expect(screen.queryByRole("button", { name: "确认并合并到识别原文" })).not.toBeInTheDocument());
  expect(api.merge).toHaveBeenCalledOnce();
});
it("only manual retry uses latest task version; exhausted/stale jobs do not retry", async () => {
  const api = recoveryApi(recoverySnapshot({ jobs: [recoveryJob({ status: "failed", errorCode: "asr_capacity_unavailable" })] }));
  render(<RecoveryPanel api={api} meetingId="meeting-recovery" />); await userEvent.click(screen.getByRole("button", { name: "查看文字恢复范围" }));
  await userEvent.click(await screen.findByRole("button", { name: /补识别未完成/ }));
  expect(api.retry).not.toHaveBeenCalled(); await userEvent.click(screen.getByRole("button", { name: "手动重试本次补识别" }));
  expect(api.retry).toHaveBeenCalledWith("meeting-recovery", "job-1", 3, expect.any(AbortSignal));
});
it.each(["stale", "failed"] as const)("prevents unavailable stale/exhausted work: %s", async (status) => {
  const api = recoveryApi(recoverySnapshot({ jobs: [recoveryJob({ status, attempts: 3 })], sources: [], unavailableSources: [{ track: "microphone", epoch: 1, errorCode: "recovery_timeline_unknown" }] }));
  render(<RecoveryPanel api={api} meetingId="meeting-recovery" />); await userEvent.click(screen.getByRole("button", { name: "查看文字恢复范围" }));
  await userEvent.click(await screen.findByRole("button", { name: status === "stale" ? /候选已失效/ : /补识别未完成/ }));
  expect(screen.queryByRole("button", { name: "确认并合并到识别原文" })).not.toBeInTheDocument(); expect(screen.queryByRole("button", { name: "手动重试本次补识别" })).not.toBeInTheDocument();
  expect(screen.getByText(/时间映射尚不明确/)).toBeVisible();
});
it("resets visible scope before a late old meeting response and never submits old ranges to a new meeting", async () => {
  const api = recoveryApi(); let finishOld!: (value: ReturnType<typeof recoverySnapshot>) => void;
  vi.mocked(api.get).mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }));
  const { rerender } = render(<RecoveryPanel api={api} meetingId="meeting-recovery" />);
  await userEvent.click(screen.getByRole("button", { name: "查看文字恢复范围" }));
  await waitFor(() => expect(api.get).toHaveBeenCalledOnce());
  rerender(<RecoveryPanel api={api} meetingId="another-meeting" />);
  expect(screen.queryByRole("button", { name: "开始本地补识别" })).not.toBeInTheDocument();
  finishOld(recoverySnapshot());
  expect(screen.queryByText(/预算仍待核对/)).not.toBeInTheDocument();
  vi.mocked(api.get).mockResolvedValue(recoverySnapshot({ meetingId: "another-meeting", sources: [], jobs: [] }));
  await userEvent.click(screen.getByRole("button", { name: "查看文字恢复范围" }));
  await screen.findByText(/暂无可恢复的已保存音频范围/);
  expect(api.create).not.toHaveBeenCalled(); expect(api.merge).not.toHaveBeenCalled();
});
