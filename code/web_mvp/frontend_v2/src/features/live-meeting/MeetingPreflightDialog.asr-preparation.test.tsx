import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingPreflightDialog } from "./MeetingPreflightDialog";
import { correctionCapabilityFixture, requestedCorrectionMode } from "../../test/correctionCapabilityFixture";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function runtime(options: { online?: boolean; offline?: boolean; resident?: boolean; mode?: string } = {}) {
  return { schema_version: "asr_runtime_status.v1", realtime_available: true,
    resident_enabled: options.resident ?? true,
    resident: { spawned: true, process_running: true, process_ready: options.online ?? true },
    offline_refinement: { capability: { status: "ready", realtime_policy: { mode: options.mode ?? "prewarm", prewarm_enabled: (options.mode ?? "prewarm") === "prewarm" } },
      worker: { spawned: true, process_running: true, process_ready: options.offline ?? true } } };
}

let runtimeReply: () => Promise<Response>;
let prepareReply: () => Promise<Response>;
let capabilityReply: (url: string) => Promise<Response>;
let componentAvailable: boolean;
let fetchMock: ReturnType<typeof vi.fn>;
const onStart = vi.fn();
const onCancel = vi.fn();

beforeEach(() => {
  delete window.__TAURI__; delete window.__TAURI_INTERNALS__;
  sessionStorage.clear(); localStorage.clear();
  componentAvailable = true;
  runtimeReply = async () => response(runtime({ offline: false }));
  prepareReply = async () => response({ ok: true });
  capabilityReply = async (url) => response(correctionCapabilityFixture(requestedCorrectionMode(url)));
  onStart.mockReset().mockResolvedValue(undefined); onCancel.mockReset();
  fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/providers/asr/correction-capability?")) return capabilityReply(url);
    if (url === "/providers/asr/runtime") return runtimeReply();
    if (url === "/providers/asr/prewarm" && init?.method === "POST") return prepareReply();
    if (url === "/v2/storage/preflight") return Promise.resolve(response({ allowed: true, writable_capacity_bytes: 4 * 1024 ** 3, estimated_meeting_bytes: 110 * 1024 ** 2 }));
    if (url === "/providers/status") return Promise.resolve(response({ configured: true, runtime_synced: true, probe_status: "not_run", model: "gpt-test" }));
    if (url === "/providers/health") return Promise.resolve(response({ llm: { configured: true }, asr: { realtime_asr_available: componentAvailable } }));
    throw new Error(`Unexpected mock request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {
    enumerateDevices: vi.fn().mockResolvedValue([{ kind: "audioinput", deviceId: "mic-1", label: "Test microphone" }]),
    getUserMedia: vi.fn(() => { throw new Error("Preparation must not request audio"); }),
  } });
});

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function panel() {
  return render(<MeetingPreflightDialog open busy={false} onCancel={onCancel} onStart={onStart} />);
}

function postCalls() { return fetchMock.mock.calls.filter(([url, init]) => url === "/providers/asr/prewarm" && init?.method === "POST"); }

describe("explicit local ASR preparation", () => {
  it("shows actual memory thresholds and the LLM-only fallback instead of claiming local readiness", async () => {
    capabilityReply = async () => response({ ...correctionCapabilityFixture(), effective_mode: "llm_only", recommended_mode: "llm_only", fallback_reason: "asr_capacity_unavailable",
      local_refinement: { available: false, worker_ready: false, reason: "asr_capacity_unavailable" },
      memory: { ...correctionCapabilityFixture().memory, commit_available_bytes: 7_200_000_000, physical_available_bytes: 600_000_000 } });
    panel();
    expect(await screen.findByText("本场将使用：仅大模型校对")).toBeVisible();
    expect(screen.getByText(/可用内存未达到本地精修要求/)).toBeVisible();
    expect(screen.getByText("可用提交内存 6.71 GiB / 离线精修门槛 8.00 GiB")).toBeVisible();
    expect(screen.getByText(/可用物理内存 0.56 GiB \/ 离线精修门槛 1.00 GiB/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "准备离线识别" })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "本地精修＋大模型校对" })).toHaveAttribute("aria-checked", "true");
    expect(postCalls()).toHaveLength(0); expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("switches mode through read-only preflight and uses correction-specific configuration", async () => {
    capabilityReply = async (url) => response({ ...correctionCapabilityFixture(requestedCorrectionMode(url)), llm_correction: { enabled: true, configured: false } });
    panel(); await screen.findByRole("button", { name: "准备离线识别" });
    fireEvent.click(screen.getByRole("radio", { name: "仅大模型校对" }));
    expect(await screen.findByText("本场将使用：仅大模型校对")).toBeVisible();
    expect(screen.getByText(/大模型校对缺少配置/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "准备离线识别" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("correction_mode=llm_only"))).toBe(true);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("keeps failed resource observation explicit without falsely enabling a mode", async () => {
    capabilityReply = async () => response({}, 503); panel();
    expect(await screen.findByText("未取得校对模式和资源检查结果，请重新检查识别状态。")).toBeVisible();
    expect(screen.getByRole("button", { name: "开始会议" })).toBeDisabled();
    expect(screen.queryByText(/本场将使用：/)).not.toBeInTheDocument();
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("does not disguise an unknown memory probe as a capacity fallback", async () => {
    capabilityReply = async (url) => {
      const fixture = correctionCapabilityFixture(requestedCorrectionMode(url));
      return response(fixture.requested_mode === "llm_only" ? fixture : { ...fixture,
        local_refinement: { available: false, worker_ready: false, reason: "asr_resource_probe_failed" },
        memory: { ...fixture.memory, checked: false, commit_available_bytes: null, physical_available_bytes: null } });
    };
    panel();
    expect(await screen.findByText(/暂时无法确认本机可用内存；请重新检查/)).toBeVisible();
    expect(screen.queryByText("本场将使用：仅大模型校对")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始会议" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "仅大模型校对" }));
    expect(await screen.findByText("本场将使用：仅大模型校对")).toBeVisible();
    expect(screen.queryByText(/暂时无法确认本机可用内存；请重新检查/)).not.toBeInTheDocument();
    expect(postCalls()).toHaveLength(0);
  });

  it("preserves the stored LLM-only choice when an existing meeting is locked", async () => {
    render(<MeetingPreflightDialog open busy={false} initialCorrectionMode="llm_only" liveAnalysisProfileLocked onCancel={onCancel} onStart={onStart} />);
    expect(await screen.findByText("本场将使用：仅大模型校对")).toBeVisible();
    expect(screen.getByRole("radio", { name: "仅大模型校对" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "本地精修＋大模型校对" })).toBeDisabled();
    expect(postCalls()).toHaveLength(0);
  });

  it("requires explicit recovery for a latched local worker failure", async () => {
    capabilityReply = async () => response({ ...correctionCapabilityFixture(), local_refinement: {
      available: true, worker_ready: false, recovery_required: true, last_error_code: "asr_capacity_unavailable", reason: null,
    } });
    panel();
    const retry = await screen.findByRole("button", { name: "重新准备" });
    expect(screen.getByText("上次本地精修准备未完成，请点击重新准备。")).toBeVisible();
    expect(postCalls()).toHaveLength(0);
    fireEvent.click(retry);
    await waitFor(() => expect(postCalls()).toHaveLength(1));
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("separates available components from unprepared processes without activating audio", async () => {
    runtimeReply = async () => response(runtime({ online: false, offline: false }));
    panel();
    await screen.findByText("本地中文实时识别可用");
    expect(await screen.findByText("实时识别进程尚未就绪")).toBeVisible();
    expect(screen.getByText("离线精修进程尚未就绪")).toBeVisible();
    expect(screen.queryByText("实时识别进程已就绪")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始会议" })).toBeDisabled();
    expect(postCalls()).toHaveLength(0);
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("makes only one explicit offline POST then reads actual readiness, with no recording or model calls", async () => {
    let finish!: (reply: Response) => void;
    prepareReply = () => new Promise((resolve) => { finish = resolve; });
    panel();
    const button = await screen.findByRole("button", { name: "准备离线识别" });
    expect(screen.getByText("此操作只准备本地识别，不会启动录音。")).toBeVisible();
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(postCalls()).toHaveLength(1));
    const [, init] = postCalls()[0];
    expect(new Headers(init.headers).get("x-meeting-copilot-verification")).toBe("1");
    expect(screen.getByRole("button", { name: "正在准备离线识别…" })).toBeDisabled();
    runtimeReply = async () => response(runtime());
    await act(async () => finish(response({ ok: true })));
    expect(await screen.findByText("离线精修进程已就绪")).toBeVisible();
    expect(fetchMock.mock.calls.filter(([url]) => url === "/providers/asr/runtime")).toHaveLength(2);
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(onStart).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.every(([url]) => String(url).startsWith("/providers/asr/correction-capability?") || ["/providers/asr/runtime", "/providers/asr/prewarm", "/providers/health", "/providers/status", "/v2/storage/preflight"].includes(String(url)))).toBe(true);
  });

  it("does not mistake successful offline preparation for online recovery", async () => {
    runtimeReply = async () => response(runtime({ online: false }));
    panel(); fireEvent.click(await screen.findByRole("button", { name: "准备离线识别" }));
    await waitFor(() => expect(postCalls()).toHaveLength(1));
    expect(await screen.findByText("实时识别进程尚未就绪")).toBeVisible();
    expect(screen.getByText("离线精修进程已就绪")).toBeVisible();
    expect(screen.getByRole("button", { name: "开始会议" })).toBeDisabled();
  });

  it("keeps failure visible and retries only on another explicit click", async () => {
    prepareReply = async () => response({ detail: { error: "refiner_not_ready", error_code: "worker_start_timeout", message: "private secret should not be shown" } }, 503);
    panel(); fireEvent.click(await screen.findByRole("button", { name: "准备离线识别" }));
    const retry = await screen.findByRole("button", { name: "重新准备" });
    expect(screen.queryByText(/private secret/)).not.toBeInTheDocument();
    expect(screen.getByText(/离线识别准备失败/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重新检查识别状态" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === "/providers/asr/runtime").length).toBeGreaterThan(1));
    expect(postCalls()).toHaveLength(1);
    await waitFor(() => expect(retry).toBeEnabled());
    prepareReply = async () => response({ ok: true }); runtimeReply = async () => response(runtime());
    fireEvent.click(retry);
    await screen.findByText("离线精修进程已就绪");
    expect(postCalls()).toHaveLength(2);
  });

  it("refreshes actual readiness after failed preparation instead of retaining the old ready label", async () => {
    runtimeReply = async () => response(runtime());
    prepareReply = async () => {
      runtimeReply = async () => response(runtime({ offline: false }));
      return response({ detail: { error: "refiner_not_ready", error_code: "worker_start_timeout" } }, 503);
    };
    panel(); await screen.findByText("离线精修进程已就绪");
    fireEvent.click(screen.getByRole("button", { name: "准备离线识别" }));
    await screen.findByRole("button", { name: "重新准备" });
    expect(screen.getByText("离线精修进程尚未就绪")).toBeVisible();
    expect(screen.queryByText("离线精修进程已就绪")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => url === "/providers/asr/runtime")).toHaveLength(2);
    expect(postCalls()).toHaveLength(1);
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("reports actionable allowlisted resource failure without exposing arbitrary diagnostics or retrying", async () => {
    prepareReply = async () => response({ detail: { error: "refiner_not_ready", error_code: "worker_out_of_memory", message: "private machine path / key", last_exit_code: "private exception" } }, 503);
    panel(); fireEvent.click(await screen.findByRole("button", { name: "准备离线识别" }));
    expect(await screen.findByText(/本机内存不足，请释放内存后重新检查识别状态/)).toBeVisible();
    expect(screen.queryByText(/private/)).not.toBeInTheDocument();
    expect(postCalls()).toHaveLength(1);
    expect(onStart).not.toHaveBeenCalled(); expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("closes observation without stopping the shared worker or applying a stale response after reopening", async () => {
    let finish!: (reply: Response) => void;
    prepareReply = () => new Promise((resolve) => { finish = resolve; });
    const view = panel();
    fireEvent.click(await screen.findByRole("button", { name: "准备离线识别" }));
    await waitFor(() => expect(postCalls()).toHaveLength(1));
    fireEvent.click(screen.getAllByRole("button", { name: "关闭会前检查", hidden: true })[0]);
    expect(onCancel).toHaveBeenCalledOnce();
    expect((postCalls()[0][1].signal as AbortSignal).aborted).toBe(true);
    view.rerender(<MeetingPreflightDialog open={false} busy={false} onCancel={onCancel} onStart={onStart} />);
    view.rerender(<MeetingPreflightDialog open busy={false} onCancel={onCancel} onStart={onStart} />);
    await screen.findByText("离线精修进程尚未就绪");
    await act(async () => finish(response({ ok: true })));
    expect(screen.queryByText("离线精修进程已就绪")).not.toBeInTheDocument();
    expect(postCalls()).toHaveLength(1);
    expect(onStart).not.toHaveBeenCalled();
  });

  it.each(["on_demand", "online_only"])("does not request offline preparation under %s", async (mode) => {
    runtimeReply = async () => response(runtime({ offline: false, mode })); panel();
    expect(await screen.findByText(mode === "on_demand" ? "离线精修将在首个正式段按需加载" : "离线精修未启用")).toBeVisible();
    expect(screen.queryByRole("button", { name: "准备离线识别" })).not.toBeInTheDocument();
    expect(postCalls()).toHaveLength(0);
  });

  it("marks nonresident preparation as inapplicable", async () => {
    runtimeReply = async () => response(runtime({ resident: false, online: false, offline: false })); panel();
    expect(await screen.findByText("当前识别方式无需提前准备")).toBeVisible();
    expect(screen.queryByRole("button", { name: "准备离线识别" })).not.toBeInTheDocument();
  });

  it.each(["404", "unknown", "missing", "network"])("reports %s preparation status as unknown rather than ready", async (kind) => {
    runtimeReply = async () => {
      if (kind === "network") throw new TypeError("offline");
      return response(kind === "unknown" ? { schema_version: "future.v8" } : kind === "missing" ? { schema_version: "asr_runtime_status.v1" } : {}, kind === "404" ? 404 : 200);
    }; panel();
    expect(await screen.findByText("识别准备状态未知；将沿用当前识别方式。")).toBeVisible();
    expect(screen.queryByText("实时识别进程已就绪")).not.toBeInTheDocument();
    expect(postCalls()).toHaveLength(0);
  });
});
