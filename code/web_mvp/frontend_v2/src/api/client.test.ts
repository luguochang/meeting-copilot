import { HttpMeetingApi } from "./client";
import { ContractError, parseMeetingSnapshot } from "./schema";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("HttpMeetingApi", () => {
  it("creates the durable V2 meeting before audio capture starts", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ meeting: { id: "rec_new" } }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.createMeeting("rec_new");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          meeting_id: "rec_new",
          expected_duration_seconds: 3_600,
          track_count: 1,
        }),
      }),
    );
  });

  it("uploads a recording as multipart without overriding the browser boundary", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ meeting_id: "imported-meeting" }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();
    const file = new File(["audio"], "meeting.m4a", { type: "audio/mp4" });

    await expect(api.importRecording(file)).resolves.toEqual({ meetingId: "imported-meeting" });

    const request = fetchSpy.mock.calls[0][1] as RequestInit;
    expect(fetchSpy.mock.calls[0][0]).toBe("/v2/meetings/import-audio");
    expect(request.method).toBe("POST");
    expect(request.headers).toEqual({ Accept: "application/json" });
    expect(request.body).toBeInstanceOf(FormData);
  });

  it("deletes a meeting through the durable cleanup endpoint", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ deleted: true }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.deleteMeeting("meeting/old");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting%2Fold",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("parses the current snake-case V2 snapshot contract", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      response({
        meeting_id: "meeting-1",
        last_seq: 3,
        segments: [
          {
            meeting_id: "meeting-1",
            segment_id: "segment-1",
            final_id: "final-1",
            transcript_seq: 1,
            text: "原始文字",
            normalized_text: "修正文字",
            started_at_ms: 100,
            ended_at_ms: 900,
            revision: 2,
            evidence_hash: "hash-1",
            created_at_ms: 1_000,
            updated_at_ms: 1_200,
          },
        ],
        suggestions: [],
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi("http://localhost:8767/");

    const snapshot = await api.getSnapshot("meeting-1");

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://localhost:8767/v2/meetings/meeting-1/snapshot",
      expect.objectContaining({ headers: expect.objectContaining({ Accept: "application/json" }) }),
    );
    expect(snapshot).toMatchObject({ meetingId: "meeting-1", lastSeq: 3 });
    expect(snapshot.segments[0]).toMatchObject({ normalizedText: "修正文字", revision: 2 });
  });

  it("uses after_seq for incremental event polling", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      response({
        meeting_id: "meeting-1",
        after_seq: 7,
        last_seq: 7,
        events: [],
        has_more: false,
        next_after_seq: 7,
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.getEvents("meeting-1", 7);
    expect(fetchSpy.mock.calls[0][0]).toBe("/v2/meetings/meeting-1/events?after_seq=7");
  });

  it("rejects malformed fact arrays instead of inventing display data", () => {
    expect(() => parseMeetingSnapshot({ meeting_id: "meeting-1", last_seq: 0, segments: null, suggestions: [] }))
      .toThrow(ContractError);
  });

  it("posts a typed ui-rendered trace receipt", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ trace_id: "job-1" }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi("http://localhost:8767/");

    await api.markUiRendered("job/1", 9.8, 3.2);

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://localhost:8767/v2/traces/job%2F1/ui-rendered",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ event_seq: 9, draft_seq: 3 }),
      }),
    );
  });
});
