import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ImportJob } from "../../domain/events";
import { ImportRecordingDialog } from "./ImportRecordingDialog";

function job(overrides: Partial<ImportJob> = {}): ImportJob {
  return {
    id: "import-job-1",
    meetingId: "import-meeting-1",
    status: "pending",
    stage: "reading",
    progress: 5,
    errorClass: null,
    errorMessage: null,
    retryable: false,
    updatedAtMs: 1,
    ...overrides,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("ImportRecordingDialog", () => {
  it("keeps reading the durable background job and shows its real stage", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let resolveCompleted!: (value: ImportJob) => void;
    const completed = new Promise<ImportJob>((resolve) => {
      resolveCompleted = resolve;
    });
    const onReadImportJob = vi.fn()
      .mockResolvedValueOnce(job({ status: "running", stage: "transcribing", progress: 62 }))
      .mockReturnValue(completed);
    const onImport = vi.fn().mockResolvedValue({
      meetingId: "import-meeting-1",
      job: job(),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <ImportRecordingDialog
        open
        onClose={vi.fn()}
        onImport={onImport}
        onReadImportJob={onReadImportJob}
        onRetryImport={vi.fn()}
        onOpenMeeting={vi.fn()}
      />,
    );

    await user.upload(
      screen.getByLabelText("选择要导入的录音文件"),
      new File([new Uint8Array(16)], "技术评审.m4a", { type: "audio/mp4" }),
    );
    await user.click(screen.getByRole("button", { name: "开始导入" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("本地中文转写 · 62%"));
    expect(within(screen.getByLabelText("录音导入步骤")).getByText("本地中文转写").closest("li"))
      .toHaveClass("is-active");

    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    await act(async () => resolveCompleted(job({ status: "succeeded", stage: "completed", progress: 100 })));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("录音导入完成"));
    expect(onReadImportJob).toHaveBeenCalledWith("import-meeting-1");
  });

  it("retries a preserved failed import from the same dialog", async () => {
    const onRetryImport = vi.fn().mockResolvedValue(job({ status: "pending", stage: "reading", progress: 0 }));
    const user = userEvent.setup();
    render(
      <ImportRecordingDialog
        open
        onClose={vi.fn()}
        onImport={vi.fn().mockResolvedValue({
          meetingId: "import-meeting-1",
          job: job({
            status: "failed",
            stage: "transcribing",
            progress: 45,
            errorClass: "file_asr_component_missing",
            errorMessage: "本地文件转写组件未安装，原始录音已保留。",
            retryable: true,
          }),
        })}
        onReadImportJob={vi.fn()}
        onRetryImport={onRetryImport}
        onOpenMeeting={vi.fn()}
      />,
    );

    await user.upload(
      screen.getByLabelText("选择要导入的录音文件"),
      new File([new Uint8Array(16)], "失败样本.wav", { type: "audio/wav" }),
    );
    await user.click(screen.getByRole("button", { name: "开始导入" }));
    expect(await screen.findByText("本地文件转写组件未安装，原始录音已保留。")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "重试导入" }));
    await waitFor(() => expect(onRetryImport).toHaveBeenCalledWith("import-meeting-1"));
    expect(screen.getByRole("status")).toHaveTextContent("读取文件 · 0%");
  });
});
