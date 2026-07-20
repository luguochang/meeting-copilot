import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { MeetingApi } from "../../api/client";
import type { MeetingHistoryItem } from "../../domain/events";
import { MeetingHistory } from "./MeetingHistory";

function meeting(id: string, title: string, updatedAtMs: number): MeetingHistoryItem {
  return {
    meetingId: id,
    title,
    phase: "ended",
    startedAtMs: updatedAtMs - 1_000,
    endedAtMs: updatedAtMs,
    createdAtMs: updatedAtMs - 1_000,
    updatedAtMs,
    segmentCount: 3,
    suggestionCount: 1,
    audioDurationMs: 60_000,
    hasMinutes: true,
  };
}

function apiWithPages() {
  const listMeetingsPage = vi.fn()
    .mockResolvedValueOnce({
      meetings: [meeting("meeting-2", "支付评审", 2_000)],
      hasMore: true,
      nextCursor: { beforeUpdatedAtMs: 2_000, beforeMeetingId: "meeting-2" },
    })
    .mockResolvedValueOnce({
      meetings: [meeting("meeting-1", "数据库评审", 1_000)],
      hasMore: false,
      nextCursor: null,
    });
  return {
    listMeetingsPage,
    listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
    deleteMeeting: vi.fn(),
    retryImportJob: vi.fn(),
  } as unknown as MeetingApi;
}

describe("MeetingHistory", () => {
  it("loads additional history through the server cursor instead of slicing a local list", async () => {
    const user = userEvent.setup();
    const api = apiWithPages();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    expect(await screen.findByText("支付评审")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "加载更多" }));

    expect(await screen.findByText("数据库评审")).toBeVisible();
    expect(api.listMeetingsPage).toHaveBeenLastCalledWith(
      expect.objectContaining({
        cursor: { beforeUpdatedAtMs: 2_000, beforeMeetingId: "meeting-2" },
        limit: 12,
      }),
      undefined,
    );
  });

  it("sends the search text and status filter to the server", async () => {
    const api = {
      listMeetingsPage: vi.fn().mockResolvedValue({ meetings: [], hasMore: false, nextCursor: null }),
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting: vi.fn(),
      retryImportJob: vi.fn(),
    } as unknown as MeetingApi;
    const user = userEvent.setup();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);
    await screen.findByText("完成的会议会出现在这里");

    await user.type(screen.getByPlaceholderText("搜索会议名称"), "发布");
    await user.selectOptions(screen.getByLabelText("按状态筛选"), "processing");

    await waitFor(() => expect(api.listMeetingsPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "发布", status: "processing", cursor: null }),
      expect.any(AbortSignal),
    ));
  });

  it("uses the explicit deletion dialog and refreshes after a scoped deletion", async () => {
    const currentMeeting = meeting("meeting-2", "支付评审", 2_000);
    const listMeetingsPage = vi.fn().mockResolvedValue({
      meetings: [currentMeeting],
      hasMore: false,
      nextCursor: null,
    });
    const deleteMeeting = vi.fn().mockResolvedValue(undefined);
    const api = {
      listMeetingsPage,
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting,
      retryImportJob: vi.fn(),
    } as unknown as MeetingApi;
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "管理本地数据：支付评审" }));
    expect(screen.getByRole("dialog", { name: "删除会议数据" })).toBeVisible();
    expect(screen.getByText("删除原始录音和音频切片，保留会议文字、AI 整理和历史记录。")).toBeVisible();
    await user.click(screen.getByRole("radio", { name: /仅 AI 整理/ }));
    await user.click(screen.getByRole("button", { name: "删除仅 AI 整理" }));

    await waitFor(() => expect(deleteMeeting).toHaveBeenCalledWith("meeting-2", "derived"));
    await waitFor(() => expect(listMeetingsPage).toHaveBeenCalledTimes(2));
    expect(screen.getByText("支付评审")).toBeVisible();
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("removes the history row only when the entire meeting is deleted", async () => {
    const listMeetingsPage = vi.fn().mockResolvedValue({
      meetings: [meeting("meeting-2", "支付评审", 2_000)],
      hasMore: false,
      nextCursor: null,
    });
    const deleteMeeting = vi.fn().mockResolvedValue(undefined);
    const api = {
      listMeetingsPage,
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting,
      retryImportJob: vi.fn(),
    } as unknown as MeetingApi;
    const user = userEvent.setup();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "管理本地数据：支付评审" }));
    await user.click(screen.getByRole("radio", { name: /整场会议/ }));
    await user.click(screen.getByRole("button", { name: "删除整场会议" }));

    await waitFor(() => expect(deleteMeeting).toHaveBeenCalledWith("meeting-2", "all"));
    await waitFor(() => expect(screen.queryByText("支付评审")).not.toBeInTheDocument());
    expect(listMeetingsPage).toHaveBeenCalledTimes(1);
  });

  it("keeps the deletion dialog open with an actionable error when deletion fails", async () => {
    const api = {
      listMeetingsPage: vi.fn().mockResolvedValue({
        meetings: [meeting("meeting-2", "支付评审", 2_000)],
        hasMore: false,
        nextCursor: null,
      }),
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting: vi.fn().mockRejectedValue(new Error("录音文件正被占用")),
      retryImportJob: vi.fn(),
    } as unknown as MeetingApi;
    const user = userEvent.setup();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "管理本地数据：支付评审" }));
    await user.click(screen.getByRole("button", { name: "删除仅录音" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("录音文件正被占用");
    expect(screen.getByRole("dialog", { name: "删除会议数据" })).toBeVisible();
    expect(screen.getByRole("button", { name: "删除仅录音" })).toBeEnabled();
  });

  it("loads and saves the local retention policy", async () => {
    const getDataGovernanceSettings = vi.fn().mockResolvedValue({
      retentionPolicy: "local_until_user_deletes",
      updatedAtMs: 1_000,
    });
    const updateDataGovernanceSettings = vi.fn().mockResolvedValue({
      retentionPolicy: "90_days",
      updatedAtMs: 2_000,
    });
    const api = {
      listMeetingsPage: vi.fn().mockResolvedValue({ meetings: [], hasMore: false, nextCursor: null }),
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting: vi.fn(),
      retryImportJob: vi.fn(),
      getDataGovernanceSettings,
      updateDataGovernanceSettings,
    } as unknown as MeetingApi;
    const user = userEvent.setup();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "本地数据" }));
    const policy = await screen.findByRole("combobox", { name: "会议数据保留时间" });
    expect(policy).toHaveValue("local_until_user_deletes");
    await user.selectOptions(policy, "90_days");
    await user.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => expect(updateDataGovernanceSettings).toHaveBeenCalledWith("90_days"));
    expect(await screen.findByText("保留策略已保存")).toBeVisible();
    expect(policy).toHaveValue("90_days");
  });

  it("can retry a failed retention-policy load before enabling save", async () => {
    const getDataGovernanceSettings = vi.fn()
      .mockRejectedValueOnce(new Error("本地服务暂不可用"))
      .mockResolvedValueOnce({ retentionPolicy: "30_days", updatedAtMs: 3_000 });
    const api = {
      listMeetingsPage: vi.fn().mockResolvedValue({ meetings: [], hasMore: false, nextCursor: null }),
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting: vi.fn(),
      retryImportJob: vi.fn(),
      getDataGovernanceSettings,
      updateDataGovernanceSettings: vi.fn(),
    } as unknown as MeetingApi;
    const user = userEvent.setup();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "本地数据" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本地服务暂不可用");
    expect(screen.getByRole("button", { name: "保存设置" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重试" }));

    const policy = await screen.findByRole("combobox", { name: "会议数据保留时间" });
    await waitFor(() => expect(policy).toHaveValue("30_days"));
    expect(screen.getByRole("button", { name: "保存设置" })).toBeEnabled();
  });
});
