import { checkAsrCapturePreparation, fetchMeetingCorrectionCapability, HttpMeetingApi } from "./client";
import { ContractError, parseMeetingCorrectionCapability } from "./schema";
import { correctionCapabilityFixture } from "../test/correctionCapabilityFixture";

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const preparation = { hotwords: [], inputSource: "microphone" as const, inputDeviceId: null, inputDeviceName: null, noticeAcknowledged: true as const };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("per-meeting correction mode contract", () => {
  it.each(["local_and_llm", "llm_only"] as const)("persists explicit %s and reads the same current/history value", async (mode) => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({})).mockResolvedValueOnce(response({ meeting_id: "existing", correction_mode: mode }))
      .mockResolvedValueOnce(response({ versions: [{ meeting_id: "existing", correction_mode: mode }] }));
    vi.stubGlobal("fetch", fetcher); const api = new HttpMeetingApi();
    await api.saveMeetingPreparation("existing", { ...preparation, correctionMode: mode });
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ correction_mode: mode });
    expect((await api.getMeetingPreparation("existing")).correctionMode).toBe(mode);
    expect((await api.getMeetingPreparationVersions("existing"))[0].correctionMode).toBe(mode);
  });
  it("omits unspecified mode on context edits, and reads old preparations as local plus LLM", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => response({ meeting_id: "existing" })); vi.stubGlobal("fetch", fetcher);
    const api = new HttpMeetingApi(); await api.saveMeetingPreparation("existing", preparation);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).not.toHaveProperty("correction_mode");
    expect((await api.getMeetingPreparation("existing")).correctionMode).toBe("local_and_llm");
  });
  it("rejects unknown stored modes rather than changing an existing meeting", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ meeting_id: "existing", correction_mode: "automatic" })));
    await expect(new HttpMeetingApi().getMeetingPreparation("existing")).rejects.toBeInstanceOf(ContractError);
  });
  it("does not infer resources or configuration from a missing or future capability", () => {
    expect(parseMeetingCorrectionCapability({ schema_version: "future.v2" })).toBeNull();
    expect(parseMeetingCorrectionCapability({ ...correctionCapabilityFixture(), memory: {} })).toBeNull();
    expect(parseMeetingCorrectionCapability({ ...correctionCapabilityFixture(), llm_correction: { configured: true } })).toBeNull();
  });
  it("uses persisted meeting preflight for LLM-only without a prewarm POST", async () => {
    const fetcher = vi.fn<typeof fetch>(async (input) => String(input).includes("correction-capability")
      ? response(correctionCapabilityFixture("llm_only")) : response({ schema_version: "asr_runtime_status.v1", resident_enabled: true,
        resident: { spawned: true, process_running: true, process_ready: true }, offline_refinement: { capability: { realtime_policy: { mode: "prewarm" } }, worker: { spawned: false } } }));
    vi.stubGlobal("fetch", fetcher);
    await expect(checkAsrCapturePreparation("llm_only", "existing")).resolves.toBeUndefined();
    expect(fetcher.mock.calls.map(([url]) => String(url))).toContain("/providers/asr/correction-capability?correction_mode=llm_only&meeting_id=existing");
    expect(fetcher.mock.calls.every(([, init]) => !init || init.method === "GET")).toBe(true);
  });
  it("does not start when mode/resource preflight fails or stored mode differs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({}, 503)));
    await expect(fetchMeetingCorrectionCapability("llm_only")).resolves.toBeNull();
    await expect(checkAsrCapturePreparation("llm_only")).rejects.toThrow("未取得本场校对模式");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => response(String(input).includes("correction-capability") ? correctionCapabilityFixture("local_and_llm") : {})));
    await expect(checkAsrCapturePreparation("llm_only", "existing")).rejects.toThrow("本场校对模式已变化");
  });
});
