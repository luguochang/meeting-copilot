import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HttpMeetingApi } from "../api/client";
import type { MeetingHistoryItem } from "../domain/events";
import { App } from "./App";
import { discoverMeetingNavigation } from "./meetingNavigationState";

vi.mock("../features/live-meeting/LiveMeetingWorkbench", () => ({
  LiveMeetingWorkbench: ({
    meetingId,
    onBackToMeetings,
    onCreateMeeting,
    onOpenMeeting,
    onOpenNotes,
    onMeetingStarted,
    onMeetingSessionStateChange,
    activeMeeting,
  }: {
    meetingId: string | null;
    onBackToMeetings?: () => void;
    onCreateMeeting?: () => string;
    onOpenMeeting?: (meetingId: string) => void;
    onOpenNotes?: () => void;
    onMeetingStarted?: (meetingId: string) => void;
    onMeetingSessionStateChange?: (meetingId: string, state: "recoverable" | "capturing_elsewhere") => void;
    activeMeeting?: { meetingId: string; state: string } | null;
  }) => (
    <main>
      <output data-testid="meeting-route">{meetingId ?? "list"}</output>
      <button type="button" onClick={onBackToMeetings}>返回会议列表</button>
      <button type="button" onClick={() => onCreateMeeting?.()}>预留会议编号</button>
      <button type="button" onClick={() => onOpenMeeting?.("meeting-next")}>打开会议</button>
      <button type="button" onClick={() => onMeetingStarted?.("meeting-active")}>模拟开始采集</button>
      <button type="button" onClick={() => onMeetingSessionStateChange?.("meeting-recoverable", "recoverable")}>模拟待恢复会议</button>
      <button type="button" onClick={() => onMeetingSessionStateChange?.("meeting-external", "capturing_elsewhere")}>模拟其他窗口采集</button>
      <button type="button" onClick={onOpenNotes}>打开笔记</button>
      <output data-testid="active-meeting">{activeMeeting?.meetingId ?? "none"}</output>
      <output data-testid="active-meeting-state">{activeMeeting?.state ?? "none"}</output>
    </main>
  ),
}));

vi.mock("../features/notes/NotesCenter", () => ({
  NotesCenter: ({
    activeMeeting,
    onOpenActiveMeeting,
  }: {
    activeMeeting?: { meetingId: string } | null;
    onOpenActiveMeeting?: () => void;
  }) => (
    <main>
      <output data-testid="notes-active-meeting">{activeMeeting?.meetingId ?? "none"}</output>
      <button type="button" onClick={onOpenActiveMeeting}>返回正在会议</button>
    </main>
  ),
}));

function historyMeeting(
  meetingId: string,
  capture: MeetingHistoryItem["capture"],
): MeetingHistoryItem {
  return {
    meetingId,
    title: meetingId,
    phase: "live",
    startedAtMs: 1_000,
    endedAtMs: null,
    createdAtMs: 1_000,
    updatedAtMs: 2_000,
    segmentCount: 0,
    suggestionCount: 0,
    audioDurationMs: 0,
    hasMinutes: false,
    capture,
  };
}

beforeEach(() => {
  vi.spyOn(HttpMeetingApi.prototype, "listMeetingsPage").mockResolvedValue({
    meetings: [],
    hasMore: false,
    nextCursor: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
  window.history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});

describe("App route state", () => {
  it("classifies fresh and expired capture leases for global navigation", () => {
    const nowMs = 10_000;
    expect(discoverMeetingNavigation([
      historyMeeting("meeting-external", {
        state: "active",
        activeTrackCount: 1,
        trackCount: 1,
        lastHeartbeatAtMs: 9_000,
        leaseUntilMs: 20_000,
      }),
      historyMeeting("meeting-stale", {
        state: "active",
        activeTrackCount: 1,
        trackCount: 1,
        lastHeartbeatAtMs: 8_000,
        leaseUntilMs: 9_500,
      }),
    ], null, nowMs)).toEqual({
      externalMeetingId: "meeting-external",
      recoverableMeetingId: "meeting-stale",
    });
  });

  it("discovers an active meeting from the list before any detail page is opened", async () => {
    vi.mocked(HttpMeetingApi.prototype.listMeetingsPage).mockResolvedValue({
      meetings: [historyMeeting("meeting-external", {
        state: "active",
        activeTrackCount: 1,
        trackCount: 1,
        lastHeartbeatAtMs: Date.now(),
        leaseUntilMs: Date.now() + 30_000,
      })],
      hasMore: false,
      nextCursor: null,
    });
    window.history.replaceState(null, "", "/");
    render(<App />);

    await waitFor(() => expect(screen.getByTestId("active-meeting")).toHaveTextContent("meeting-external"));
    expect(screen.getByTestId("active-meeting-state")).toHaveTextContent("external");
  });

  it("changes the global entry from external capture to recoverable after the next freshness poll", async () => {
    vi.useFakeTimers();
    const nowMs = Date.now();
    vi.mocked(HttpMeetingApi.prototype.listMeetingsPage)
      .mockResolvedValueOnce({
        meetings: [historyMeeting("meeting-external", {
          state: "active",
          activeTrackCount: 1,
          trackCount: 1,
          lastHeartbeatAtMs: nowMs,
          leaseUntilMs: nowMs + 30_000,
        })],
        hasMore: false,
        nextCursor: null,
      })
      .mockResolvedValue({
        meetings: [historyMeeting("meeting-external", {
          state: "recoverable",
          activeTrackCount: 0,
          trackCount: 1,
          lastHeartbeatAtMs: nowMs,
          leaseUntilMs: nowMs - 1,
        })],
        hasMore: false,
        nextCursor: null,
      });
    window.history.replaceState(null, "", "/");
    render(<App />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("active-meeting-state")).toHaveTextContent("external");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(screen.getByTestId("active-meeting-state")).toHaveTextContent("recoverable");
  });

  it("does not expose a new meeting route before the backend creates it", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/");
    render(<App />);

    await user.click(screen.getByRole("button", { name: "预留会议编号" }));

    expect(screen.getByTestId("meeting-route")).toHaveTextContent("list");
    expect(window.location.search).toBe("");
  });

  it("clears every meeting query alias when returning to the meeting list", async () => {
    const user = userEvent.setup();
    window.history.replaceState(
      null,
      "",
      "/?meeting_id=meeting-review&meeting=legacy&session_id=old&session=older",
    );

    render(<App />);
    expect(screen.getByTestId("meeting-route")).toHaveTextContent("meeting-review");

    await user.click(screen.getByRole("button", { name: "返回会议列表" }));

    expect(screen.getByTestId("meeting-route")).toHaveTextContent("list");
    expect(window.location.search).toBe("");
  });

  it("follows browser popstate and keeps opened meetings canonical", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/");
    render(<App />);

    await user.click(screen.getByRole("button", { name: "打开会议" }));
    expect(window.location.search).toBe("?meeting_id=meeting-next");
    expect(screen.getByTestId("meeting-route")).toHaveTextContent("meeting-next");

    window.history.pushState(null, "", "/?session_id=meeting-from-history");
    window.dispatchEvent(new PopStateEvent("popstate"));

    await waitFor(() => expect(screen.getByTestId("meeting-route")).toHaveTextContent("meeting-from-history"));
  });

  it("keeps the active meeting recoverable while visiting another product page", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/?meeting_id=meeting-active");
    render(<App />);

    await user.click(screen.getByRole("button", { name: "模拟开始采集" }));
    expect(screen.getByTestId("active-meeting")).toHaveTextContent("meeting-active");
    await user.click(screen.getByRole("button", { name: "打开笔记" }));

    expect(window.location.search).toBe("?view=notes");
    expect(screen.getByTestId("notes-active-meeting")).toHaveTextContent("meeting-active");
    await user.click(screen.getByRole("button", { name: "返回正在会议" }));
    expect(window.location.search).toBe("?meeting_id=meeting-active");
    expect(screen.getByTestId("meeting-route")).toHaveTextContent("meeting-active");
  });

  it("keeps a backend-live recoverable meeting reachable after leaving its page", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/?meeting_id=meeting-recoverable");
    render(<App />);

    await user.click(screen.getByRole("button", { name: "模拟待恢复会议" }));
    expect(screen.getByTestId("active-meeting")).toHaveTextContent("meeting-recoverable");
    await user.click(screen.getByRole("button", { name: "打开笔记" }));

    expect(screen.getByTestId("notes-active-meeting")).toHaveTextContent("meeting-recoverable");
    await user.click(screen.getByRole("button", { name: "返回正在会议" }));
    expect(window.location.search).toBe("?meeting_id=meeting-recoverable");
    expect(screen.getByTestId("meeting-route")).toHaveTextContent("meeting-recoverable");
  });

  it("keeps a fresh capture from another window reachable without claiming local ownership", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/?meeting_id=meeting-external");
    render(<App />);

    await user.click(screen.getByRole("button", { name: "模拟其他窗口采集" }));
    expect(screen.getByTestId("active-meeting")).toHaveTextContent("meeting-external");
    expect(screen.getByTestId("active-meeting-state")).toHaveTextContent("external");
    await user.click(screen.getByRole("button", { name: "打开笔记" }));

    expect(screen.getByTestId("notes-active-meeting")).toHaveTextContent("meeting-external");
    await user.click(screen.getByRole("button", { name: "返回正在会议" }));
    expect(window.location.search).toBe("?meeting_id=meeting-external");
  });
});
