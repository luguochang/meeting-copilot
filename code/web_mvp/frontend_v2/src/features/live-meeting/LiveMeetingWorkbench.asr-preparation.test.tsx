import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MeetingApi } from "../../api/client";
import type { MeetingEventTransport } from "../../api/eventTransport";
import type { MaterialsApi, MaterialSet } from "../../api/materials";
import type { BrowserMicrophoneController } from "./useBrowserMicrophone";
import { LiveMeetingWorkbench } from "./LiveMeetingWorkbench";
import { MATERIAL_START_KEY } from "./materialStartAttempt";
import { correctionCapabilityFixture, requestedCorrectionMode } from "../../test/correctionCapabilityFixture";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
function runtime(options: { offline?: boolean; resident?: boolean; mode?: string } = {}) {
  return { schema_version: "asr_runtime_status.v1", realtime_available: true, resident_enabled: options.resident ?? true,
    resident: { spawned: true, process_running: true, process_ready: true },
    offline_refinement: { capability: { status: "ready", realtime_policy: { mode: options.mode ?? "prewarm", prewarm_enabled: (options.mode ?? "prewarm") === "prewarm" } },
      worker: { spawned: true, process_running: true, process_ready: options.offline ?? true } } };
}
class ProbeAudioContext {
  state = "running";
  resume = vi.fn().mockResolvedValue(undefined); close = vi.fn().mockResolvedValue(undefined);
  createAnalyser = vi.fn(() => ({ fftSize: 1024, getFloatTimeDomainData: (values: Float32Array) => values.fill(0.05) }));
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn() }));
}
let runtimeReply: () => Promise<Response>;
let componentAvailable: boolean;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  delete window.__TAURI__; delete window.__TAURI_INTERNALS__;
  sessionStorage.clear(); localStorage.clear(); componentAvailable = true;
  runtimeReply = async () => response(runtime());
  fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/providers/asr/correction-capability?")) return Promise.resolve(response(correctionCapabilityFixture(requestedCorrectionMode(url))));
    if (url === "/providers/asr/runtime") return runtimeReply();
    if (url === "/v2/storage/preflight") return Promise.resolve(response({ allowed: true, writable_capacity_bytes: 4 * 1024 ** 3, estimated_meeting_bytes: 110 * 1024 ** 2 }));
    if (url === "/providers/status") return Promise.resolve(response({ configured: true, runtime_synced: true, probe_status: "not_run", model: "gpt-test" }));
    if (url === "/providers/health") return Promise.resolve(response({ llm: { configured: true }, asr: { realtime_asr_available: componentAvailable } }));
    throw new Error(`Unexpected mock request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock); vi.stubGlobal("AudioContext", ProbeAudioContext);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {
    enumerateDevices: vi.fn().mockResolvedValue([{ kind: "audioinput", deviceId: "mic-1", label: "Test microphone" }]),
    getUserMedia: vi.fn().mockResolvedValue({ getAudioTracks: () => [{ readyState: "live" }], getTracks: () => [{ stop: vi.fn() }] }),
  } });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function dependencies(materials = false) {
  const api = { createMeeting: vi.fn().mockResolvedValue(undefined), saveMeetingPreparation: vi.fn().mockResolvedValue(undefined),
    deleteMeeting: vi.fn().mockResolvedValue(undefined), listMeetings: vi.fn().mockResolvedValue({ meetings: [] }) } as unknown as MeetingApi;
  const capture: BrowserMicrophoneController = { state: { phase: "idle", asrReady: false, inputLevel: 0, elapsedMs: null, activePartial: null, error: null, statusMessage: "尚未开始录音", droppedFrames: 0 },
    start: vi.fn().mockResolvedValue(undefined), togglePause: vi.fn(), end: vi.fn().mockResolvedValue(undefined), acknowledgeCommitted: vi.fn() };
  const transport = { kind: "sse", subscribe: vi.fn(() => () => {}) } as unknown as MeetingEventTransport;
  const createId = vi.fn(() => "rec_asr_prepared"); const opened = vi.fn();
  const draft: MaterialSet = { id: "prepared-set", state: "draft", meeting_id: null, revision: 4, context_revision: 1 };
  const materialsApi = { get: vi.fn(async () => ({ material_set: draft, attachments: [] })), forMeeting: vi.fn(async (id) => ({ ...draft, state: "bound", meeting_id: id, revision: 5 })),
    getForMeeting: vi.fn(async () => null), create: vi.fn(), upload: vi.fn(), detail: vi.fn(), spans: vi.fn(), save: vi.fn(), use: vi.fn(), retry: vi.fn(), cancel: vi.fn(), remove: vi.fn(), removeDraft: vi.fn(), source: vi.fn() } as MaterialsApi;
  if (materials) sessionStorage.setItem("talktrace-material-draft", "prepared-set");
  render(<LiveMeetingWorkbench meetingId={null} api={api} transport={transport} microphoneController={capture} onCreateMeeting={createId} onOpenMeeting={opened} {...(materials ? { materialsApi } : {})} />);
  return { api, capture, createId, opened, materialsApi };
}
async function prepareDialog() {
  fireEvent.click(screen.getByRole("button", { name: "开始会议" }));
  const dialog = await screen.findByRole("dialog", { name: "准备开始会议" });
  await within(dialog).findByText("本地中文实时识别可用");
  vi.useFakeTimers(); fireEvent.click(within(dialog).getByRole("button", { name: "检查麦克风" }));
  await act(async () => { await Promise.resolve(); await vi.advanceTimersByTimeAsync(2600); }); vi.useRealTimers();
  expect(within(dialog).getByText("正常收到声音，麦克风可用")).toBeVisible();
  fireEvent.click(within(dialog).getByLabelText("我已告知参会者并确认可以录音"));
  return dialog;
}
describe("fresh ASR checks at actual capture entry", () => {
  it("saves LLM-only before capture and checks the persisted setting without loading offline refinement", async () => {
    const { api, capture } = dependencies(); const dialog = await prepareDialog();
    runtimeReply = async () => response(runtime({ offline: false }));
    fireEvent.click(within(dialog).getByRole("radio", { name: "仅大模型校对" }));
    await within(dialog).findByText("本场将使用：仅大模型校对");
    fireEvent.click(within(dialog).getByRole("button", { name: "开始会议" }));
    await waitFor(() => expect(capture.start).toHaveBeenCalledOnce());
    expect(api.saveMeetingPreparation).toHaveBeenCalledWith("rec_asr_prepared", expect.objectContaining({ correctionMode: "llm_only" }));
    expect(vi.mocked(api.saveMeetingPreparation).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(capture.start).mock.invocationCallOrder[0]);
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/providers/asr/correction-capability?correction_mode=llm_only&meeting_id=rec_asr_prepared")).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => url === "/providers/asr/prewarm")).toBe(false);
  });

  it("checks stale readiness before creating a meeting or material start receipt", async () => {
    const { api, capture, createId } = dependencies(true); const dialog = await prepareDialog();
    runtimeReply = async () => response(runtime({ offline: false }));
    fireEvent.click(within(dialog).getByRole("button", { name: "开始会议" }));
    await within(dialog).findByText(/本地识别尚未准备完成/);
    expect(createId).not.toHaveBeenCalled(); expect(api.createMeeting).not.toHaveBeenCalled(); expect(capture.start).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(MATERIAL_START_KEY)).toBeNull();
    expect(sessionStorage.getItem("talktrace-material-draft")).toBe("prepared-set");
  });

  it("checks again after asynchronous saves, preserves materials, and retries the same created meeting", async () => {
    const { api, capture, createId } = dependencies(true); const dialog = await prepareDialog();
    vi.mocked(api.saveMeetingPreparation).mockImplementationOnce(async () => { runtimeReply = async () => response(runtime({ offline: false })); });
    fireEvent.click(within(dialog).getByRole("button", { name: "开始会议" }));
    await within(dialog).findByText(/资料已保留，请重试同一会议/);
    expect(api.createMeeting).toHaveBeenCalledOnce(); expect(capture.start).not.toHaveBeenCalled(); expect(api.deleteMeeting).not.toHaveBeenCalled();
    const originalReceipt = sessionStorage.getItem(MATERIAL_START_KEY); expect(originalReceipt).toContain("rec_asr_prepared");
    runtimeReply = async () => response(runtime());
    await prepareDialogProbeAgain(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "开始会议" }));
    await waitFor(() => expect(capture.start).toHaveBeenCalledOnce());
    expect(capture.start).toHaveBeenCalledWith("rec_asr_prepared", { inputDeviceId: "mic-1", inputSource: "microphone" });
    expect(api.createMeeting).toHaveBeenCalledOnce(); expect(createId).toHaveBeenCalledOnce(); expect(sessionStorage.getItem(MATERIAL_START_KEY)).toBeNull();
  });

  it("does not create twice while the first fresh readiness request is pending", async () => {
    const { api, capture } = dependencies(); const dialog = await prepareDialog();
    let finish!: (reply: Response) => void;
    runtimeReply = () => new Promise((resolve) => { finish = resolve; });
    const button = within(dialog).getByRole("button", { name: "开始会议" }); fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === "/providers/asr/runtime").length).toBe(2));
    expect(api.createMeeting).not.toHaveBeenCalled();
    runtimeReply = async () => response(runtime()); await act(async () => finish(response(runtime())));
    await waitFor(() => expect(capture.start).toHaveBeenCalledOnce()); expect(api.createMeeting).toHaveBeenCalledOnce();
  });

  it.each(["404", "unknown", "network", "nonresident", "on_demand", "online_only"])("preserves legal start for %s without preparing offline", async (kind) => {
    runtimeReply = async () => {
      if (kind === "network") throw new TypeError("network unavailable");
      if (kind === "404") return response({}, 404);
      if (kind === "unknown") return response({ schema_version: "future.v2" });
      return response(runtime({ offline: false, resident: kind !== "nonresident", mode: ["on_demand", "online_only"].includes(kind) ? kind : "prewarm" }));
    };
    const { api, capture } = dependencies(); const dialog = await prepareDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "开始会议" }));
    await waitFor(() => expect(capture.start).toHaveBeenCalledOnce());
    expect(api.createMeeting).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls.some(([url]) => url === "/providers/asr/prewarm")).toBe(false);
  });

  it("keeps component and microphone confirmation gates when the new endpoint is unknown", async () => {
    runtimeReply = async () => response({}, 404); componentAvailable = false;
    const { api, capture } = dependencies(); fireEvent.click(screen.getByRole("button", { name: "开始会议" }));
    const dialog = await screen.findByRole("dialog", { name: "准备开始会议" });
    await within(dialog).findByText("本地实时识别不可用");
    expect(within(dialog).getByRole("button", { name: "开始会议" })).toBeDisabled();
    expect(api.createMeeting).not.toHaveBeenCalled(); expect(capture.start).not.toHaveBeenCalled();
  });
});

async function prepareDialogProbeAgain(dialog: HTMLElement) {
  vi.useFakeTimers(); fireEvent.click(within(dialog).getByRole("button", { name: "检查麦克风" }));
  await act(async () => { await Promise.resolve(); await vi.advanceTimersByTimeAsync(2600); }); vi.useRealTimers();
}
