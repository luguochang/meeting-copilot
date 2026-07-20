import { act, renderHook, waitFor } from "@testing-library/react";
import { useMeetingMicrophone } from "./useMeetingMicrophone";

interface NativeResponse {
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
  source?: string;
  fallback_source?: string | null;
  requested_mode?: string;
  active_mode?: string;
  active_track_count?: number;
  microphone?: NativeResponse;
  system_audio?: NativeResponse;
}

function response(overrides: Partial<NativeResponse> = {}): NativeResponse {
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
    ...overrides,
  };
}

describe("useMeetingMicrophone", () => {
  afterEach(() => {
    delete window.__TAURI__;
    delete window.__TAURI_INTERNALS__;
  });

  it("uses Tauri native capture for the complete lifecycle when the packaged helper exists", async () => {
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "mic_adapter_prepare") return response({ captures_audio: false });
      if (command === "mic_adapter_stop") return response({ status: "stopped", captures_audio: false });
      if (command === "mic_adapter_pause") return response({ status: "paused", captures_audio: false });
      if (command === "mic_adapter_resume") return response({ captures_audio: false });
      return response();
    });
    const invoke = <T,>(command: string, args?: Record<string, unknown>) => (
      invokeMock(command, args) as Promise<T>
    );
    window.__TAURI__ = { core: { invoke } };
    const getUserMedia = vi.fn().mockRejectedValue(new Error("browser capture must not run"));
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });

    const { result } = renderHook(() => useMeetingMicrophone());
    await act(async () => result.current.start("meeting_native"));

    expect(getUserMedia).not.toHaveBeenCalled();
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_prepare", undefined);
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_start", { sessionId: "meeting_native" });
    expect(result.current.state.phase).toBe("recording");
    expect(result.current.state.inputLevelAvailable).toBe(false);

    act(() => result.current.togglePause());
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith("mic_adapter_pause", undefined));
    expect(result.current.state.phase).toBe("paused");

    act(() => result.current.togglePause());
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith("mic_adapter_resume", undefined));
    expect(result.current.state.phase).toBe("recording");

    await act(async () => result.current.end());
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_stop", { sessionId: "meeting_native" });
    expect(result.current.state.phase).toBe("ended");
  });

  it("keeps the browser capture fallback when no Tauri bridge is available", async () => {
    const getUserMedia = vi.fn().mockRejectedValue(new Error("browser fallback selected"));
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    const { result } = renderHook(() => useMeetingMicrophone());

    let capturedError: unknown;
    await act(async () => {
      try {
        await result.current.start("meeting_browser");
      } catch (error) {
        capturedError = error;
      }
    });
    expect(capturedError).toEqual(new Error("browser fallback selected"));
    expect(getUserMedia).toHaveBeenCalledOnce();
    await waitFor(() => expect(result.current.state.phase).toBe("error"));
  });

  it("routes an explicit system-audio source without probing or starting microphone capture", async () => {
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "system_audio_adapter_prepare") {
        return response({
          status: "not_started",
          captures_audio: false,
          permission_status: "not_checked",
          source: "system_audio",
          fallback_source: null,
        });
      }
      if (command === "system_audio_adapter_collect_events") {
        return response({ captures_audio: false, events: [] });
      }
      if (command === "system_audio_adapter_status") {
        return response({
          permission_status: "authorized",
          source: "system_audio",
          fallback_source: null,
        });
      }
      if (command === "system_audio_adapter_stop") {
        return response({ status: "stopped", captures_audio: false });
      }
      return response({
        permission_status: "authorized",
        source: "system_audio",
        fallback_source: null,
      });
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };
    const getUserMedia = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    const { result } = renderHook(() => useMeetingMicrophone());

    await act(async () => result.current.start("meeting_system", { inputSource: "system_audio" }));

    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_start", {
      sessionId: "meeting_system",
      requestPermission: true,
    });
    expect(invokeMock.mock.calls.some(([command]) => String(command).startsWith("mic_adapter_"))).toBe(false);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(result.current.supportsPause).toBe(false);

    await act(async () => result.current.end());
    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_stop", {
      sessionId: "meeting_system",
    });
  });

  it("routes dual-track through its atomic Tauri lifecycle without starting another capture owner", async () => {
    const track = () => response({ status: "recording", captures_audio: true });
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "dual_track_adapter_start" || command === "dual_track_adapter_status") {
        return response({
          requested_mode: "dual_track",
          active_mode: "dual_track",
          active_track_count: 2,
          microphone: track(),
          system_audio: response({ ...track(), fallback_source: null }),
        });
      }
      if (command === "dual_track_adapter_collect_events") {
        return response({
          requested_mode: "dual_track",
          microphone: response({ events: [] }),
          system_audio: response({ events: [] }),
        });
      }
      if (command === "dual_track_adapter_stop") {
        return response({
          status: "stopped",
          requested_mode: "dual_track",
          active_mode: "none",
          active_track_count: 0,
          microphone: response({ status: "stopped", captures_audio: false }),
          system_audio: response({ status: "stopped", captures_audio: false }),
        });
      }
      if (command === "dual_track_adapter_cleanup") {
        return response({
          status: "cleaned",
          requested_mode: "dual_track",
          active_mode: "none",
          active_track_count: 0,
          microphone: response({ status: "cleaned", captures_audio: false }),
          system_audio: response({ status: "cleaned", captures_audio: false }),
        });
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
    const getUserMedia = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    const { result } = renderHook(() => useMeetingMicrophone());

    await act(async () => result.current.start("meeting_dual", { inputSource: "dual_track" }));

    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_start", {
      sessionId: "meeting_dual",
      requestSystemAudioPermission: true,
    });
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(invokeMock.mock.calls.some(([command]) => String(command).startsWith("mic_adapter_"))).toBe(false);
    expect(invokeMock.mock.calls.some(([command]) => String(command).startsWith("system_audio_adapter_"))).toBe(false);
    expect(result.current.inputSource).toBe("dual_track");
    expect(result.current.supportsPause).toBe(false);
    expect(result.current.state.phase).toBe("recording");

    await act(async () => result.current.end());

    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_stop", { sessionId: "meeting_dual" });
    expect(invokeMock).toHaveBeenCalledWith("dual_track_adapter_cleanup", { sessionId: "meeting_dual" });
    expect(result.current.state.phase).toBe("ended");
  });

  it("projects native partial ASR events into the live transcript state", async () => {
    let collected = false;
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "mic_adapter_prepare") return response({ captures_audio: false });
      if (command === "mic_adapter_collect_events" && !collected) {
        collected = true;
        return response({
          captures_audio: false,
          events: [{
            event_type: "partial",
            segment_id: "native_partial_1",
            text: "正在讨论灰度发布",
            start_ms: 1200,
          }],
        });
      }
      if (command === "mic_adapter_status") return response({ captures_audio: false });
      if (command === "mic_adapter_stop") return response({ status: "stopped", captures_audio: false });
      return response();
    });
    window.__TAURI__ = { core: { invoke: <T,>(command: string, args?: Record<string, unknown>) => invokeMock(command, args) as Promise<T> } };

    const { result } = renderHook(() => useMeetingMicrophone());
    await act(async () => result.current.start("meeting_native_partial"));

    await waitFor(() => expect(result.current.state.activePartial).toMatchObject({
      segmentId: "native_partial_1",
      text: "正在讨论灰度发布",
      startedAtMs: 1200,
    }));
    await act(async () => result.current.end());
  });

  it("automatically reconnects the same native meeting after an unexpected helper stop", async () => {
    let statusChecks = 0;
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "mic_adapter_prepare") return response({ captures_audio: false });
      if (command === "mic_adapter_collect_events") return response({ captures_audio: false, events: [] });
      if (command === "mic_adapter_status") {
        statusChecks += 1;
        return response({ status: statusChecks === 1 ? "stopped" : "recording", captures_audio: false });
      }
      if (command === "mic_adapter_stop") return response({ status: "stopped", captures_audio: false });
      return response();
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };

    const { result } = renderHook(() => useMeetingMicrophone());
    await act(async () => result.current.start("meeting_native_reconnect"));

    await waitFor(() => {
      expect(invokeMock.mock.calls.filter(([command]) => command === "mic_adapter_start")).toHaveLength(2);
      expect(result.current.state.phase).toBe("recording");
      expect(result.current.state.statusMessage).toContain("已自动恢复");
    });
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_start", {
      sessionId: "meeting_native_reconnect",
    });
    await act(async () => result.current.end());
  });
});
