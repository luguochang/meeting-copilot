import { describe, expect, it } from "vitest";
import { fallbackMeetingTitle } from "./meetingTitle";

describe("fallbackMeetingTitle", () => {
  it("keeps the default title compact enough for narrow headers", () => {
    const timestamp = new Date(2026, 6, 21, 15, 2).getTime();

    expect(fallbackMeetingTitle(timestamp, "meeting-1")).toBe("7月21日 15:02 会议");
  });
});
