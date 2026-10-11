import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HttpMeetingApi, type MeetingApi } from "../../api/client";
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

async function openDataManagementDialog(
  user: ReturnType<typeof userEvent.setup>,
  title: string,
) {
  const trigger = await screen.findByRole("button", { name: `会议操作：${title}` });
  await user.click(trigger);
  await user.click(screen.getByRole("menuitem", { name: `管理本地数据：${title}` }));
  return trigger;
}

describe("MeetingHistory", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("keeps ended records readable without claiming unavailable audio or nonexistent review products through the actual HTTP client", async () => {
    const items = [
      { id: "record-only", title: "只有会议记录", state: "ended", segment_count: 3, has_minutes: false },
      { id: "audio-failed", title: "录音未保存", state: "ended", segment_count: 3, has_minutes: false, audio_status: "failed" },
      { id: "audio-saved", title: "录音已实际保存", state: "ended", segment_count: 3, has_minutes: false, audio_status: "saved" },
      { id: "review-ready", title: "已有会议纪要", state: "ended", segment_count: 3, has_minutes: true },
    ];
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ meetings: items, has_more: false, next_cursor: null }), { status: 200, headers: { "content-type": "application/json" } }));
    const onOpenMeeting = vi.fn();
    const user = userEvent.setup();
    render(<MeetingHistory api={new HttpMeetingApi()} onOpenMeeting={onOpenMeeting} />);

    const recordOnly = await screen.findByRole("button", { name: "打开会议：只有会议记录" });
    expect(within(recordOnly).getByText("会议记录已保存")).toBeVisible();
    expect(recordOnly).not.toHaveTextContent("录音已保存");
    expect(recordOnly).not.toHaveTextContent("复盘已就绪");
    expect(screen.getByRole("button", { name: "打开会议：录音未保存" })).toHaveTextContent("会议记录已保存");
    expect(screen.getByRole("button", { name: "打开会议：录音已实际保存" })).toHaveTextContent("文字和录音已保存");
    expect(screen.getByRole("button", { name: "打开会议：已有会议纪要" })).toHaveTextContent("复盘已就绪");
    const readySummary = within(screen.getByLabelText("会议记录统计")).getByText("已完成").parentElement!;
    expect(readySummary).toHaveTextContent("4");
    expect(within(readySummary).getByText("可回看")).toBeVisible();
    expect(screen.queryByText("复盘就绪")).not.toBeInTheDocument();

    await user.click(recordOnly);
    expect(onOpenMeeting).toHaveBeenCalledWith("record-only");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith("/v2/meetings?limit=12&status=all", expect.objectContaining({ headers: { Accept: "application/json" } }));
    expect(fetcher.mock.calls[0][1]?.method ?? "GET").toBe("GET");
  });

  it("preserves failed-review handling without asserting that missing or saved audio was retained", async () => {
    const items = [
      { id: "review-failed", title: "纪要生成失败", state: "ended", has_minutes: false, review_jobs: { minutes: { status: "failed" } } },
      { id: "review-cancelled", title: "方案任务取消", state: "ended", has_minutes: false, audio_status: "saved", review_jobs: { approach: { status: "cancelled" } } },
      { id: "review-working", title: "纪要仍在处理", state: "ended", has_minutes: false, review_jobs: { minutes: { status: "running" } } },
    ];
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ meetings: items, has_more: false, next_cursor: null }), { status: 200, headers: { "content-type": "application/json" } }));
    const user = userEvent.setup();
    render(<MeetingHistory api={new HttpMeetingApi()} onOpenMeeting={vi.fn()} />);

    const failed = await screen.findByRole("button", { name: "打开会议：纪要生成失败" });
    expect(failed).toHaveTextContent("会议记录已保留 · 纪要失败");
    expect(failed).not.toHaveTextContent("录音已保留");
    expect(screen.getByRole("button", { name: "打开会议：方案任务取消" })).toHaveTextContent("会议记录已保留 · 方案失败");
    expect(screen.getByRole("button", { name: "打开会议：纪要仍在处理" })).toHaveTextContent("会后整理中");
    expect(within(screen.getByLabelText("会议记录统计")).getByText("已完成").parentElement).toHaveTextContent("0");

    await user.selectOptions(screen.getByLabelText("按状态筛选"), "failed");
    await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith("/v2/meetings?limit=12&status=failed", expect.objectContaining({ headers: { Accept: "application/json" } })));
    expect(fetcher.mock.calls.every(([, options]) => (options?.method ?? "GET") === "GET")).toBe(true);
    expect(screen.getByLabelText("按状态筛选")).toHaveValue("failed");
  });

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

  it("restores search, filter, sort order and scroll after returning from a meeting", async () => {
    window.sessionStorage.setItem("talktrace.meeting-history.view.v1", JSON.stringify({
      query: "支付",
      filter: "ready",
      sortOrder: "oldest",
      scrollY: 420,
    }));
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const api = {
      listMeetingsPage: vi.fn().mockResolvedValue({
        meetings: [meeting("meeting-2", "支付评审", 2_000)],
        hasMore: false,
        nextCursor: null,
      }),
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting: vi.fn(),
      retryImportJob: vi.fn(),
    } as unknown as MeetingApi;

    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    expect(await screen.findByDisplayValue("支付")).toBeVisible();
    expect(screen.getByLabelText("按状态筛选")).toHaveValue("ready");
    expect(screen.getByLabelText("会议排序")).toHaveValue("oldest");
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 420, behavior: "auto" }));
  });

  it("distinguishes local, cross-window, recoverable, and never-started live meetings", async () => {
    const active = { ...meeting("meeting-active", "正在录制", 3_000), phase: "live" as const };
    const recoverable = { ...meeting("meeting-stale", "待恢复会议", 2_000), phase: "live" as const };
    const external = {
      ...meeting("meeting-external", "其他窗口会议", 1_500),
      phase: "live" as const,
      capture: { state: "active" as const, activeTrackCount: 1, trackCount: 1, lastHeartbeatAtMs: 1_400, leaseUntilMs: 31_400 },
    };
    const inactive = {
      ...meeting("meeting-inactive", "尚未开始会议", 1_000),
      phase: "live" as const,
      capture: { state: "inactive" as const, activeTrackCount: 0, trackCount: 0, lastHeartbeatAtMs: null, leaseUntilMs: null },
    };
    const api = {
      listMeetingsPage: vi.fn().mockResolvedValue({
        meetings: [active, recoverable, external, inactive],
        hasMore: false,
        nextCursor: null,
      }),
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting: vi.fn(),
      retryImportJob: vi.fn(),
    } as unknown as MeetingApi;

    render(
      <MeetingHistory
        api={api}
        activeMeetingId="meeting-active"
        onOpenMeeting={vi.fn()}
      />,
    );

    expect(await screen.findByText("会议进行中")).toBeVisible();
    expect(screen.getByText("会议未结束 · 可恢复")).toBeVisible();
    expect(screen.getByText("另一窗口录音中")).toBeVisible();
    expect(screen.getByText("会议未结束 · 可开始")).toBeVisible();
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

    await openDataManagementDialog(user, "支付评审");
    expect(screen.getByRole("dialog", { name: "删除会议数据" })).toBeVisible();
    expect(screen.getByText("删除原始录音和音频切片，保留会议文字、AI 整理和历史记录。")).toBeVisible();
    await user.click(screen.getByRole("radio", { name: /仅 AI 整理/ }));
    await user.click(screen.getByRole("button", { name: "删除仅 AI 整理" }));

    await waitFor(() => expect(deleteMeeting).toHaveBeenCalledWith("meeting-2", "derived"));
    await waitFor(() => expect(listMeetingsPage).toHaveBeenCalledTimes(2));
    expect(screen.getByText("支付评审")).toBeVisible();
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("focuses the safe deletion action, closes with Escape, and restores the row action", async () => {
    const api = {
      listMeetingsPage: vi.fn().mockResolvedValue({
        meetings: [meeting("meeting-2", "支付评审", 2_000)],
        hasMore: false,
        nextCursor: null,
      }),
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting: vi.fn(),
      retryImportJob: vi.fn(),
    } as unknown as MeetingApi;
    const user = userEvent.setup();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    const trigger = await screen.findByRole("button", { name: "会议操作：支付评审" });
    await user.click(trigger);
    expect(screen.getByRole("menu", { name: "会议操作：支付评审" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "会议操作：支付评审" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());

    await openDataManagementDialog(user, "支付评审");
    await waitFor(() => expect(screen.getByRole("button", { name: "取消" })).toHaveFocus());

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "删除会议数据" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
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

    await openDataManagementDialog(user, "支付评审");
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

    await openDataManagementDialog(user, "支付评审");
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
    const policy = await screen.findByRole("radio", { name: /手动删除/ });
    expect(policy).toBeChecked();
    await user.click(screen.getByRole("radio", { name: /90 天/ }));
    await user.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => expect(updateDataGovernanceSettings).toHaveBeenCalledWith("90_days"));
    expect(await screen.findByText("保留策略已保存")).toBeVisible();
    expect(screen.getByRole("radio", { name: /90 天/ })).toBeChecked();
  });

  it("does not silently discard an unsaved retention-policy change", async () => {
    const api = {
      listMeetingsPage: vi.fn().mockResolvedValue({ meetings: [], hasMore: false, nextCursor: null }),
      listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
      deleteMeeting: vi.fn(),
      retryImportJob: vi.fn(),
      getDataGovernanceSettings: vi.fn().mockResolvedValue({
        retentionPolicy: "local_until_user_deletes",
        updatedAtMs: 1_000,
      }),
      updateDataGovernanceSettings: vi.fn(),
    } as unknown as MeetingApi;
    const user = userEvent.setup();
    render(<MeetingHistory api={api} onOpenMeeting={vi.fn()} />);

    const trigger = await screen.findByRole("button", { name: "本地数据" });
    await user.click(trigger);
    await user.click(await screen.findByRole("radio", { name: /30 天/ }));
    await user.click(screen.getByRole("button", { name: "关闭" }));

    expect(screen.getByText("放弃未保存的修改？")).toBeVisible();
    expect(screen.getByRole("dialog", { name: "数据保留策略" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "继续编辑" }));
    expect(screen.queryByText("放弃未保存的修改？")).not.toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.getByText("放弃未保存的修改？")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.queryByRole("dialog", { name: "数据保留策略" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
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

    const policy = await screen.findByRole("radio", { name: /30 天/ });
    await waitFor(() => expect(policy).toBeChecked());
    expect(screen.getByRole("button", { name: "保存设置" })).toBeEnabled();
  });
});
