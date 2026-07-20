import { act, renderHook } from "@testing-library/react";
import { useNativeDualTrack } from "./useNativeDualTrack";

interface TrackResponse {
  command_status: string;
  status: string;
  helper_present: boolean;
  captures_audio: boolean;
  transport_ready: boolean;
  pcm_seen: boolean;
  audible_pcm_seen: boolean;
  asr_ready: boolean;
  errors: string[];
  events?: unknown[];
  permission_status?: string;
  fallback_source?: string | null;
}

function track(overrides: Partial<TrackResponse> = {}): TrackResponse {
  return {
    command_status: "ok",
    status: "recording",
    helper_present: true,
    captures_audio: true,
    transport_ready: true,
    pcm_seen: true,
    audible_pcm_seen: true,
    asr_ready: true,
    errors: [],
    events: [],
    fallback_source: null,
    ...overrides,
  };
}

function dual(overrides: Record<string, unknown> = {}) {
  return {
    command_status: "ok",
    status: "recording",
    requested_mode: "dual_track",
    active_mode: "dual_track",
    active_track_count: 2,
    microphone: track(),
    system_audio: track(),
    ...overrides,
  };
}

describe("useNativeDualTrack", () => {
  afterEach(() => {
    vi.useRealTimers();
    delete window.__TAURI__;
    delete window.__TAURI_INTERNALS__;
  });

  it("collects microphone and system-audio events into one meeting controller", async () => {
    vi.useFakeTimers();
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "dual_track_adapter_start" || command === "dual_track_adapter_status") return dual();
      if (command === "dual_track_adapter_collect_events") {
        return {
          command_status: "ok",
          requested_mode: "dual_track",
          microphone: track({ events: [
            { event_type: "asr_ready", ready: true, source: "microphone" },
            { event_type: "input_level", level: 0.2, source: "microphone" },
            { event_type: "partial", segment_id: "mic-1", text: "我的意见", source: "microphone" },
          ] }),
          system_audio: track({ events: [
            { event_type: "asr_ready", ready: true, source: "system_audio" },
            { event_type: "pcm", rms: 0.08, source: "system_audio" },
            { event_type: "partial", segment_id: "sys-1", text: "远端发言", source: "system_audio" },
          ] }),
        };
      }
      if (command === "dual_track_adapter_stop") {
        return dual({ status: "stopped", active_mode: "none", active_track_count: 0 });
      }
      if (command === "dual_track_adapter_cleanup") {
        return dual({ status: "cleaned", active_mode: "none", active_track_count: 0 });
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: { invoke: <T,>(command: string, args?: Record<string, unknown>) => invokeMock(command, args) as Promise<T> },
    };
    const { result } = renderHook(() => useNativeDualTrack());

    await act(async () => result.current.start("dual_events"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    expect(result.current.state.activePartial).toMatchObject({
      segmentId: "sys-1",
      text: "远端发言",
    });
    expect(result.current.state.asrReady).toBe(true);
    expect(result.current.state.inputLevelAvailable).toBe(true);
    expect(result.current.state.inputLevel).toBeCloseTo(0.48);
    expect(result.current.state.systemAudioHealth).toEqual({
      transportReady: true,
      pcmSeen: true,
      audiblePcmSeen: true,
      asrReady: true,
    });
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_collect_events", { sessionId: "dual_events" });
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_status", undefined);

    await act(async () => result.current.end());
  });

  it("rejects a partial startup, names the failed track, and cleans up both owners", async () => {
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "dual_track_adapter_start") {
        return dual({
          command_status: "partial",
          status: "degraded_single_track",
          active_mode: "single_track",
          active_track_count: 1,
          system_audio: track({
            command_status: "blocked",
            status: "permission_denied",
            permission_status: "denied",
            captures_audio: false,
            transport_ready: false,
            pcm_seen: false,
            audible_pcm_seen: false,
            asr_ready: false,
            errors: ["screen capture permission denied"],
          }),
        });
      }
      if (command === "dual_track_adapter_stop") {
        return dual({ command_status: "partial", status: "stop_incomplete", active_mode: "none", active_track_count: 0 });
      }
      if (command === "dual_track_adapter_cleanup") {
        return dual({ status: "cleaned", active_mode: "none", active_track_count: 0 });
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: { invoke: <T,>(command: string, args?: Record<string, unknown>) => invokeMock(command, args) as Promise<T> },
    };
    const { result } = renderHook(() => useNativeDualTrack());

    let capturedError: unknown;
    await act(async () => {
      try {
        await result.current.start("dual_tcc_denied");
      } catch (error) {
        capturedError = error;
      }
    });

    expect(capturedError).toBeInstanceOf(Error);
    expect((capturedError as Error).message).toContain("系统音频轨道");
    expect((capturedError as Error).message).toContain("屏幕与系统音频录制");
    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.statusMessage).toContain("双轨采集不完整");
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_stop", { sessionId: "dual_tcc_denied" });
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_cleanup", { sessionId: "dual_tcc_denied" });
  });

  it.each([
    [
      { transport_ready: false, pcm_seen: false },
      "系统音频轨道传输未就绪，已阻止开始会议",
    ],
    [
      { transport_ready: true, pcm_seen: false },
      "系统音频轨道未收到 PCM 数据，已阻止开始会议",
    ],
  ])("blocks dual-track startup when system-audio health fails: %o", async (health, expectedMessage) => {
    const invokeMock = vi.fn(async (command: string) => {
      if (command === "dual_track_adapter_start") {
        return dual({ system_audio: track(health) });
      }
      if (command === "dual_track_adapter_stop") {
        return dual({ status: "stopped", active_mode: "none", active_track_count: 0 });
      }
      if (command === "dual_track_adapter_cleanup") {
        return dual({ status: "cleaned", active_mode: "none", active_track_count: 0 });
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: { invoke: <T,>(command: string) => invokeMock(command) as Promise<T> },
    };
    const { result } = renderHook(() => useNativeDualTrack());

    let capturedError: unknown;
    await act(async () => {
      try {
        await result.current.start("dual_health_failure");
      } catch (error) {
        capturedError = error;
      }
    });

    expect(capturedError).toBeInstanceOf(Error);
    expect((capturedError as Error).message).toContain(expectedMessage);
    expect(result.current.state.phase).toBe("error");
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_stop");
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_cleanup");
  });

  it("keeps dual-track recording active while system PCM is silent", async () => {
    const invokeMock = vi.fn(async (command: string) => {
      if (command === "dual_track_adapter_start") {
        return dual({
          system_audio: track({ audible_pcm_seen: false, asr_ready: false }),
        });
      }
      if (command === "dual_track_adapter_collect_events") {
        return {
          command_status: "ok",
          requested_mode: "dual_track",
          microphone: track({ events: [] }),
          system_audio: track({
            audible_pcm_seen: false,
            asr_ready: false,
            events: [],
          }),
        };
      }
      if (command === "dual_track_adapter_status") {
        return dual({
          system_audio: track({ audible_pcm_seen: false, asr_ready: false }),
        });
      }
      if (command === "dual_track_adapter_stop") {
        return dual({ status: "stopped", active_mode: "none", active_track_count: 0 });
      }
      if (command === "dual_track_adapter_cleanup") {
        return dual({ status: "cleaned", active_mode: "none", active_track_count: 0 });
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: { invoke: <T,>(command: string) => invokeMock(command) as Promise<T> },
    };
    const { result } = renderHook(() => useNativeDualTrack());

    await act(async () => result.current.start("dual_silent_system_audio"));

    expect(result.current.state).toMatchObject({
      phase: "recording",
      statusMessage: "已连接但当前无系统声音",
      systemAudioHealth: {
        transportReady: true,
        pcmSeen: true,
        audiblePcmSeen: false,
        asrReady: false,
      },
    });
    await act(async () => result.current.end());
  });
});
