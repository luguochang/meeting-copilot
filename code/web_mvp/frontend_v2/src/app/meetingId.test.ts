import { createMeetingId, resolveMeetingId } from "./meetingId";

describe("meeting identifiers", () => {
  it("creates a backend-safe, bounded recording session identifier", () => {
    const meetingId = createMeetingId(1_720_000_000_000, new Uint8Array([1, 2, 3, 4, 5, 6]));

    expect(meetingId).toMatch(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/);
    expect(meetingId).toBe("rec_ly5nl9ts_010203040506");
  });

  it("resolves the canonical meeting_id query parameter", () => {
    expect(resolveMeetingId("?meeting_id=rec_test&session=legacy")).toBe("rec_test");
  });
});
