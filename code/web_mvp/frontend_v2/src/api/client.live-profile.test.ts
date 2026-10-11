import { HttpMeetingApi } from "./client";
import { ContractError, parseMeetingSnapshot } from "./schema";

const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
const preparation = { hotwords: [], inputSource: "microphone" as const, inputDeviceId: null, inputDeviceName: null, noticeAcknowledged: true as const };
const snapshot = { meeting_id: "existing", last_seq: 0, segments: [], suggestions: [] };

describe("per-meeting live help profile HTTP contract", () => {
  it.each(["participation", "legacy"] as const)("saves explicit %s before capture using the preparation field", async (profile) => {
    const fetcher = vi.fn().mockResolvedValue(response({ meeting_id: "existing" })); vi.stubGlobal("fetch", fetcher);
    await new HttpMeetingApi().saveMeetingPreparation("existing", { ...preparation, liveAnalysisProfile: profile });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toBe("/v2/meetings/existing/preparation");
    expect(fetcher.mock.calls[0][1].method).toBe("PUT");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ live_analysis_profile: profile });
  });
  it("omits an unspecified profile so another context edit cannot replace the stored mode", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ meeting_id: "existing" })); vi.stubGlobal("fetch", fetcher);
    await new HttpMeetingApi().saveMeetingPreparation("existing", preparation);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).not.toHaveProperty("live_analysis_profile");
  });
  it("reads current and historical preparations without changing mode; missing old fields stay legacy", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ meeting_id: "existing", live_analysis_profile: "participation" })).mockResolvedValueOnce(response({ versions: [{ meeting_id: "existing", live_analysis_profile: "participation" }, { meeting_id: "existing" }] }));
    vi.stubGlobal("fetch", fetcher); const api = new HttpMeetingApi();
    expect((await api.getMeetingPreparation("existing")).liveAnalysisProfile).toBe("participation");
    expect((await api.getMeetingPreparationVersions("existing")).map((item) => item.liveAnalysisProfile)).toEqual(["participation", "legacy"]);
    expect(fetcher.mock.calls.every(([, options]) => options?.method === undefined)).toBe(true);
  });
  it("hydrates an old snapshot as legacy and accepts an explicitly persisted participation snapshot", () => {
    expect(parseMeetingSnapshot(snapshot).liveAnalysisProfile).toBe("legacy");
    expect(parseMeetingSnapshot({ ...snapshot, live_analysis_profile: "participation" }).liveAnalysisProfile).toBe("participation");
    expect(parseMeetingSnapshot({ ...snapshot, liveAnalysisProfile: "legacy" }).liveAnalysisProfile).toBe("legacy");
    expect(parseMeetingSnapshot({ ...snapshot, live_analysis_profile_locked: true }).liveAnalysisProfileLocked).toBe(true);
    expect(parseMeetingSnapshot(snapshot).liveAnalysisProfileLocked).toBeUndefined();
    expect(() => parseMeetingSnapshot({ ...snapshot, live_analysis_profile_locked: "false" })).toThrow(ContractError);
  });
  it.each([null, "automatic", 1])("rejects an unknown profile %s instead of silently switching an existing meeting", async (profile) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ meeting_id: "existing", live_analysis_profile: profile })));
    await expect(new HttpMeetingApi().getMeetingPreparation("existing")).rejects.toBeInstanceOf(ContractError);
    expect(() => parseMeetingSnapshot({ ...snapshot, live_analysis_profile: profile })).toThrow(ContractError);
  });
});
