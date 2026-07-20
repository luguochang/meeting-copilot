import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingPreflightDialog } from "./MeetingPreflightDialog";

class FakeAnalyser {
  fftSize = 1_024;

  getFloatTimeDomainData(values: Float32Array) {
    values.fill(FakeProbeAudioContext.sampleValue);
  }
}

class FakeProbeAudioContext {
  static sampleValue = 0.05;
  readonly analyser = new FakeAnalyser();
  close = vi.fn().mockResolvedValue(undefined);
  createAnalyser = vi.fn(() => this.analyser as unknown as AnalyserNode);
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn() }) as unknown as MediaStreamAudioSourceNode);
}

const stopTrack = vi.fn();

async function finishBrowserProbe() {
  await act(async () => {
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(2_600);
  });
}


function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("MeetingPreflightDialog", () => {
  beforeEach(() => {
    delete window.__TAURI__;
    delete window.__TAURI_INTERNALS__;
    FakeProbeAudioContext.sampleValue = 0.05;
    stopTrack.mockReset();
    vi.stubGlobal("AudioContext", FakeProbeAudioContext);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v2/storage/preflight")) {
        return Promise.resolve(response({
          allowed: true,
          writable_capacity_bytes: 4 * 1024 ** 3,
          estimated_meeting_bytes: 110 * 1024 ** 2,
        }));
      }
      if (url.endsWith("/providers/status")) {
        return Promise.resolve(response({
          configured: true,
          runtime_synced: true,
          probe_status: "succeeded",
          model: "gpt-5.5",
        }));
      }
      return Promise.resolve(response({
        llm: { configured: true, provider: "relay", model: "gpt-5.5" },
        asr: { realtime_asr_available: true, realtime_providers: ["funasr_realtime"] },
        cost_policy: { remote_asr_default_enabled: false, raw_audio_uploaded_by_default: false },
      }));
    }));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        enumerateDevices: vi.fn().mockResolvedValue([
          { kind: "audioinput", deviceId: "mic-1", label: "MacBook Microphone", groupId: "group-1", toJSON: () => ({}) },
        ]),
        getUserMedia: vi.fn().mockResolvedValue({
          getAudioTracks: () => [{ readyState: "live" }],
          getTracks: () => [{ stop: stopTrack }],
        }),
      },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    delete window.__TAURI__;
    delete window.__TAURI_INTERNALS__;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("collects consent, device and meeting-scoped technical terms before start", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={onStart} />);

    expect(await screen.findByText("本地中文实时识别可用")).toBeVisible();
    expect(screen.getByText("AI 已连接 · gpt-5.5")).toBeVisible();
    expect(screen.getByText("本地可写 4.0 GB · 本场预计 110 MB")).toBeVisible();
    fireEvent.change(screen.getByPlaceholderText(/checkout-service/), {
      target: { value: "P99，checkout-service\np99" },
    });
    fireEvent.click(screen.getByLabelText("我已告知参会者并确认可以录音"));
    fireEvent.click(screen.getByRole("button", { name: "开始会议" }));

    await waitFor(() => expect(onStart).toHaveBeenCalledWith({
      hotwords: ["P99", "checkout-service"],
      inputSource: "microphone",
      inputDeviceId: "mic-1",
      inputDeviceName: "MacBook Microphone",
      noticeAcknowledged: true,
    }));
  });

  it("keeps system audio hidden in the Web runtime", async () => {
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);

    expect(await screen.findByText("本地中文实时识别可用")).toBeVisible();
    expect(screen.queryByRole("radiogroup", { name: "会议声音来源" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "系统音频" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "双轨" })).not.toBeInTheDocument();
  });

  it("offers dual-track only when the packaged Tauri capability is available", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined);
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "dual_track_adapter_status") {
        return {
          command_status: "ok",
          status: "not_recording",
          requested_mode: "dual_track",
          active_mode: "none",
          active_track_count: 0,
          microphone: { command_status: "ok", status: "not_started", helper_present: true, captures_audio: false, errors: [] },
          system_audio: { command_status: "ok", status: "not_started", helper_present: true, captures_audio: false, fallback_source: null, errors: [] },
        };
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };

    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={onStart} />);

    const dualTrack = await screen.findByRole("radio", { name: "双轨" });
    fireEvent.click(dualTrack);
    expect(dualTrack).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("同时采集麦克风和系统音频；任一轨失败都会中止本次采集。")).toBeVisible();

    fireEvent.click(screen.getByLabelText("我已告知参会者并确认可以录音"));
    fireEvent.click(screen.getByRole("button", { name: "开始会议" }));

    await waitFor(() => expect(onStart).toHaveBeenCalledWith({
      hotwords: [],
      inputSource: "dual_track",
      inputDeviceId: null,
      inputDeviceName: "麦克风 + 系统音频",
      noticeAcknowledged: true,
    }));
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_status", undefined);
  });

  it("hides dual-track when the Tauri command or either native helper is unavailable", async () => {
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "dual_track_adapter_status") {
        return {
          command_status: "partial",
          status: "not_recording",
          requested_mode: "dual_track",
          active_mode: "none",
          active_track_count: 0,
          microphone: { command_status: "ok", status: "not_started", helper_present: true, captures_audio: false, errors: [] },
          system_audio: { command_status: "blocked", status: "error", helper_present: false, captures_audio: false, errors: ["helper missing"] },
        };
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };

    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);

    expect(await screen.findByText("本地中文实时识别可用")).toBeVisible();
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_status", undefined));
    expect(screen.queryByRole("radio", { name: "双轨" })).not.toBeInTheDocument();
  });

  it("checks packaged system audio availability and saves the selected source", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined);
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "system_audio_adapter_prepare") {
        return {
          command_status: "ok",
          status: "not_started",
          source: "system_audio",
          helper_present: true,
          captures_audio: false,
          fallback_source: null,
          errors: [],
        };
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };

    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={onStart} />);
    expect(await screen.findByRole("radiogroup", { name: "会议声音来源" })).toBeVisible();
    expect(screen.getByRole("radio", { name: "麦克风" })).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("radio", { name: "系统音频" }));

    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_prepare", undefined));
    expect(screen.getByRole("radio", { name: "系统音频" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("将采集本机播放的会议声音，不会同时启动麦克风。")).toBeVisible();
    expect(screen.getByLabelText("系统音频启动检查项")).toHaveTextContent("传输开始时验证PCM开始时验证声音启动后检测识别独立就绪");

    fireEvent.click(screen.getByLabelText("我已告知参会者并确认可以录音"));
    fireEvent.click(screen.getByRole("button", { name: "开始会议" }));

    await waitFor(() => expect(onStart).toHaveBeenCalledWith({
      hotwords: [],
      inputSource: "system_audio",
      inputDeviceId: null,
      inputDeviceName: "系统音频",
      noticeAcknowledged: true,
    }));
    expect(invokeMock.mock.calls.map(([command]) => command)).toEqual([
      "provider_config_status",
      "dual_track_adapter_status",
      "system_audio_adapter_prepare",
    ]);
  });

  it("keeps microphone selected when packaged system audio is unavailable", async () => {
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command !== "system_audio_adapter_prepare") {
        throw new Error(`unexpected command: ${command}`);
      }
      return {
        command_status: "blocked",
        status: "error",
        source: "system_audio",
        helper_present: false,
        captures_audio: false,
        fallback_source: null,
        errors: ["native helper missing"],
      };
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("本地中文实时识别可用");

    fireEvent.click(screen.getByRole("radio", { name: "系统音频" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("系统音频采集不可用");
    expect(screen.getByRole("radio", { name: "麦克风" })).toHaveAttribute("aria-checked", "true");
    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_prepare", undefined);
  });

  it("keeps the dialog open and shows an explicit capture startup failure", async () => {
    const onStart = vi.fn().mockRejectedValue(new Error("系统音频未收到 PCM 数据，已阻止开始会议"));
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={onStart} />);
    await screen.findByText("本地中文实时识别可用");

    fireEvent.click(screen.getByLabelText("我已告知参会者并确认可以录音"));
    fireEvent.click(screen.getByRole("button", { name: "开始会议" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("系统音频未收到 PCM 数据，已阻止开始会议");
    expect(screen.getByRole("dialog", { name: "准备开始会议" })).toBeVisible();
  });

  it("checks the browser microphone on explicit user action", async () => {
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("本地中文实时识别可用");
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "检查麦克风" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(150);
    });
    expect(screen.getByLabelText("RMS 输入电平 5.0%")).toHaveAttribute("data-probe-status", "checking");
    await finishBrowserProbe();

    expect(screen.getByText("正常收到声音，麦克风可用")).toBeVisible();
    expect(screen.getByLabelText("RMS 输入电平 5.0%")).toHaveAttribute("data-probe-status", "receiving_audio");
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: { deviceId: { exact: "mic-1" } },
      video: false,
    });
    expect(stopTrack).toHaveBeenCalledOnce();
  });

  it("shows a sampled RMS level and a muted warning when the browser input is silent", async () => {
    FakeProbeAudioContext.sampleValue = 0;
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("本地中文实时识别可用");

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "检查麦克风" }));
    await finishBrowserProbe();

    expect(screen.getByLabelText("RMS 输入电平 0.0%")).toHaveAttribute("data-probe-status", "silent");
    expect(screen.getByRole("alert")).toHaveTextContent("未检测到声音，请检查麦克风是否静音");
    expect(stopTrack).toHaveBeenCalledOnce();
  });

  it.each([
    ["NotAllowedError", "permission_denied", "麦克风权限被拒绝，请在系统或浏览器设置中允许访问"],
    ["NotFoundError", "no_device", "没有可用的麦克风设备"],
  ])("shows the browser %s failure from an explicit probe", async (name, probeStatus, expectedMessage) => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(new DOMException("failed", name));
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("本地中文实时识别可用");
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "检查麦克风" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(expectedMessage);
    expect(screen.getByLabelText("检查后显示 RMS 输入电平")).toHaveAttribute("data-probe-status", probeStatus);
  });

  it("treats a stream without a live audio track as no_device", async () => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValueOnce({
      getAudioTracks: () => [],
      getTracks: () => [{ stop: stopTrack }],
    } as unknown as MediaStream);
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("本地中文实时识别可用");

    fireEvent.click(screen.getByRole("button", { name: "检查麦克风" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("没有可用的麦克风设备");
    expect(screen.getByLabelText("检查后显示 RMS 输入电平")).toHaveAttribute("data-probe-status", "no_device");
    expect(stopTrack).toHaveBeenCalledOnce();
  });

  it("uses the dedicated native probe result instead of treating prepare as a microphone check", async () => {
    const invokeMock = vi.fn((command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "mic_adapter_probe") {
        return Promise.resolve({
          command_status: "ok",
          probe_status: "receiving_audio",
          sampled: true,
          rms: 0.04,
          peak_rms: 0.08,
          level: 0.48,
          duration_ms: 2_500,
          helper_present: true,
          errors: [],
        });
      }
      if (command === "mic_adapter_prepare") {
        return Promise.resolve({ command_status: "ok", helper_present: true });
      }
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("本地中文实时识别可用");
    expect(invokeMock).toHaveBeenCalledTimes(2);
    expect(invokeMock).toHaveBeenCalledWith("provider_config_status", undefined);
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_status", undefined);

    fireEvent.click(screen.getByRole("button", { name: "检查麦克风" }));

    expect(await screen.findByText("正常收到声音，麦克风可用")).toBeVisible();
    expect(screen.getByLabelText("RMS 输入电平 4.0%")).toHaveAttribute("data-probe-status", "receiving_audio");
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_probe", undefined);
    expect(invokeMock).not.toHaveBeenCalledWith("mic_adapter_prepare", undefined);
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("does not treat helper presence as a completed native microphone sample", async () => {
    const invokeMock = vi.fn().mockResolvedValue({
      command_status: "ok",
      helper_present: true,
      errors: [],
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("本地中文实时识别可用");

    fireEvent.click(screen.getByRole("button", { name: "检查麦克风" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("麦克风检查失败");
    expect(screen.queryByText("正常收到声音，麦克风可用")).not.toBeInTheDocument();
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_probe", undefined);
  });

  it.each([
    ["silent", "未检测到声音，请检查麦克风是否静音"],
    ["permission_denied", "麦克风权限被拒绝，请在系统设置中允许访问"],
    ["no_device", "没有可用的麦克风设备"],
  ])("shows native %s probe status", async (probeStatus, expectedMessage) => {
    const invokeMock = vi.fn().mockResolvedValue({
      command_status: probeStatus === "silent" ? "ok" : "blocked",
      probe_status: probeStatus,
      sampled: probeStatus === "silent",
      rms: 0,
      peak_rms: 0,
      level: 0,
      duration_ms: probeStatus === "silent" ? 2_500 : 0,
      helper_present: true,
      errors: ["native probe failed"],
    });
    window.__TAURI__ = { core: { invoke: invokeMock } };
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("本地中文实时识别可用");

    fireEvent.click(screen.getByRole("button", { name: "检查麦克风" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(expectedMessage);
    if (probeStatus === "silent") {
      expect(screen.getByLabelText("RMS 输入电平 0.0%")).toHaveAttribute("data-probe-status", "silent");
    } else {
      expect(screen.getByLabelText("检查后显示 RMS 输入电平")).toHaveAttribute("data-probe-status", probeStatus);
    }
  });

  it("allows local recording when AI is unconfigured but blocks unavailable local ASR", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v2/storage/preflight")) {
        return Promise.resolve(response({
          allowed: true,
          writable_capacity_bytes: 4 * 1024 ** 3,
          estimated_meeting_bytes: 110 * 1024 ** 2,
        }));
      }
      return Promise.resolve(response({
        llm: { configured: false },
        asr: { realtime_asr_available: false, realtime_providers: [] },
      }));
    });
    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);

    expect(await screen.findByText("AI 未配置，会议仍可录音和转写")).toBeVisible();
    fireEvent.click(screen.getByLabelText("我已告知参会者并确认可以录音"));
    expect(screen.getByRole("button", { name: "开始会议" })).toBeDisabled();
    expect(screen.getByText("本地实时识别不可用")).toBeVisible();
  });

  it("uses provider status instead of treating configured health as a connected runtime", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v2/storage/preflight")) {
        return Promise.resolve(response({
          allowed: true,
          writable_capacity_bytes: 4 * 1024 ** 3,
          estimated_meeting_bytes: 110 * 1024 ** 2,
        }));
      }
      if (url.endsWith("/providers/status")) {
        return Promise.resolve(response({
          configured: true,
          runtime_synced: false,
          probe_status: "not_run",
          model: "gpt-5.5",
        }));
      }
      return Promise.resolve(response({
        llm: { configured: true, provider: "relay", model: "gpt-5.5" },
        asr: { realtime_asr_available: true, realtime_providers: ["funasr_realtime"] },
      }));
    });

    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);

    expect(await screen.findByText("gpt-5.5 已保存，AI 待连接")).toBeVisible();
    expect(screen.queryByText("gpt-5.5 已配置")).not.toBeInTheDocument();
  });

  it("explicitly syncs saved desktop AI config and refreshes preflight health without probing", async () => {
    let healthReads = 0;
    let resolveSync!: (value: unknown) => void;
    const syncResult = new Promise((resolve) => {
      resolveSync = resolve;
    });
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v2/storage/preflight")) {
        return Promise.resolve(response({
          allowed: true,
          writable_capacity_bytes: 4 * 1024 ** 3,
          estimated_meeting_bytes: 110 * 1024 ** 2,
        }));
      }
      if (url.endsWith("/providers/health")) {
        healthReads += 1;
        return Promise.resolve(response({
          llm: healthReads > 1
            ? { configured: true, provider: "relay", model: "gpt-5.5" }
            : { configured: false },
          asr: { realtime_asr_available: true, realtime_providers: ["funasr_realtime"] },
        }));
      }
      if (url.endsWith("/providers/status")) {
        return Promise.resolve(response({
          configured: healthReads > 1,
          runtime_synced: healthReads > 1,
          probe_status: "not_run",
          model: healthReads > 1 ? "gpt-5.5" : null,
        }));
      }
      return Promise.resolve(response({}, 404));
    });
    const invokeMock = vi.fn((command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "provider_config_sync") return syncResult;
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };

    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);

    expect(await screen.findByText("AI 未配置，会议仍可录音和转写")).toBeVisible();
    fireEvent.click(screen.getByLabelText("我已告知参会者并确认可以录音"));
    expect(screen.getByRole("button", { name: "开始会议" })).toBeEnabled();
    expect(invokeMock).toHaveBeenCalledTimes(2);
    expect(invokeMock).toHaveBeenCalledWith("provider_config_status", undefined);
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_status", undefined);
    expect(invokeMock).not.toHaveBeenCalledWith("provider_config_sync", undefined);

    fireEvent.click(screen.getByRole("button", { name: "连接 AI" }));
    expect(invokeMock).toHaveBeenCalledWith("provider_config_sync", undefined);
    expect(screen.getByRole("button", { name: "正在连接 AI" })).toBeDisabled();

    resolveSync({
      command_status: "ok",
      configured: true,
      runtime_synced: true,
      errors: [],
    });

    expect(await screen.findByText("gpt-5.5 已同步，连接尚未测试")).toBeVisible();
    expect(screen.getByText("AI 运行时已同步")).toBeVisible();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/v2/storage/preflight"))).toHaveLength(2);
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/providers/health"))).toHaveLength(2);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/providers/llm/probe"))).toBe(false);
  });

  it("shows desktop AI sync errors without blocking recording and transcription", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v2/storage/preflight")) {
        return Promise.resolve(response({
          allowed: true,
          writable_capacity_bytes: 4 * 1024 ** 3,
          estimated_meeting_bytes: 110 * 1024 ** 2,
        }));
      }
      return Promise.resolve(response({
        llm: { configured: false },
        asr: { realtime_asr_available: true, realtime_providers: ["funasr_realtime"] },
      }));
    });
    const invokeMock = vi.fn().mockResolvedValue({
      command_status: "error",
      configured: true,
      runtime_synced: false,
      errors: ["系统凭据未授权"],
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };

    render(<MeetingPreflightDialog open busy={false} onCancel={vi.fn()} onStart={vi.fn()} />);
    await screen.findByText("AI 已保存，AI 待连接");
    fireEvent.click(screen.getByLabelText("我已告知参会者并确认可以录音"));

    fireEvent.click(screen.getByRole("button", { name: "连接 AI" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("系统凭据未授权");
    expect(screen.getByRole("button", { name: "重试连接 AI" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "开始会议" })).toBeEnabled();
    expect(invokeMock).toHaveBeenCalledWith("provider_config_sync", undefined);
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/providers/health"))).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/providers/llm/probe"))).toBe(false);
  });
});
