import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("../features/live-meeting/LiveMeetingWorkbench", () => ({
  LiveMeetingWorkbench: ({
    meetingId,
    onBackToMeetings,
    onOpenMeeting,
  }: {
    meetingId: string | null;
    onBackToMeetings?: () => void;
    onOpenMeeting?: (meetingId: string) => void;
  }) => (
    <main>
      <output data-testid="meeting-route">{meetingId ?? "list"}</output>
      <button type="button" onClick={onBackToMeetings}>返回会议列表</button>
      <button type="button" onClick={() => onOpenMeeting?.("meeting-next")}>打开会议</button>
    </main>
  ),
}));

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});

describe("App route state", () => {
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
});
