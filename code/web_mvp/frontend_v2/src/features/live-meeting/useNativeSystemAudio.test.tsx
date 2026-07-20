import { act, renderHook, waitFor } from "@testing-library/react";
import { useNativeSystemAudio } from "./useNativeSystemAudio";

interface SystemAudioResponse {
  command_status: string;
  status: string;
  permission_status: string;
  source: string;
  helper_present: boolean;
  captures_audio: boolean;
  transport_ready: boolean;
  pcm_seen: boolean;
  audible_pcm_seen: boolean;
  asr_ready: boolean;
  fallback_source: string | null;
  errors: string[];
  events?: unknown[];
}

function response(overrides: Partial<SystemAudioResponse> = {}): SystemAudioResponse {
  return {
    command_status: "ok",
    status: "recording",
    permission_status: "authorized",
    source: "system_audio",
    helper_present: true,
    captures_audio: true,
    transport_ready: true,
    pcm_seen: true,
    audible_pcm_seen: true,
    asr_ready: true,
    fallback_source: null,
    errors: [],
    events: [],
    ...overrides,
  };
}

describe("useNativeSystemAudio", () => {
  afterEach(() => {
    delete window.__TAURI__;
    delete window.__TAURI_INTERNALS__;
  });

  it("uses only the packaged system-audio commands for start, events, status and stop", async () => {
    let collected = false;
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "system_audio_adapter_prepare") {
        return response({
          status: "not_started",
          captures_audio: false,
          transport_ready: false,
          pcm_seen: false,
          audible_pcm_seen: false,
          asr_ready: false,
        });
      }
      if (command === "system_audio_adapter_collect_events" && !collected) {
        collected = true;
        return response({
          captures_audio: false,
          events: [{
            event_type: "partial",
            segment_id: "system-partial-1",
            text: "正在讨论系统音频采集",
            start_ms: 900,
          }],
        });
      }
      if (command === "system_audio_adapter_status") {
        return response({ captures_audio: true });
      }
      if (command === "system_audio_adapter_stop") {
        return response({ status: "stopped", captures_audio: false });
      }
      return response();
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };
    const { result } = renderHook(() => useNativeSystemAudio());

    await act(async () => result.current.start("meeting_system_audio"));
    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_prepare", undefined);
    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_start", {
      sessionId: "meeting_system_audio",
      requestPermission: true,
    });
    expect(invokeMock.mock.calls.some(([command]) => String(command).startsWith("mic_adapter_"))).toBe(false);

    await waitFor(() => expect(result.current.state.activePartial).toMatchObject({
      segmentId: "system-partial-1",
      text: "正在讨论系统音频采集",
      startedAtMs: 900,
    }));
    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_collect_events", {
      sessionId: "meeting_system_audio",
    });
    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_status", undefined);

    await act(async () => result.current.end());
    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_stop", {
      sessionId: "meeting_system_audio",
    });
    expect(result.current.state.phase).toBe("ended");
  });

  it("surfaces permission denial in Chinese without falling back to microphone", async () => {
    const getUserMedia = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "system_audio_adapter_prepare") {
        return response({
          status: "not_started",
          captures_audio: false,
          transport_ready: false,
          pcm_seen: false,
          audible_pcm_seen: false,
          asr_ready: false,
        });
      }
      return response({
        command_status: "blocked",
        status: "permission_denied",
        permission_status: "denied",
        captures_audio: false,
        transport_ready: false,
        pcm_seen: false,
        audible_pcm_seen: false,
        asr_ready: false,
        errors: ["screen recording permission denied"],
      });
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };
    const { result } = renderHook(() => useNativeSystemAudio());

    let capturedError: unknown;
    await act(async () => {
      try {
        await result.current.start("meeting_system_denied");
      } catch (error) {
        capturedError = error;
      }
    });

    expect(capturedError).toEqual(new Error("系统音频权限被拒绝，请在系统设置的“屏幕与系统音频录制”中允许访问"));
    expect(result.current.state).toMatchObject({
      phase: "error",
      error: "系统音频权限被拒绝，请在系统设置的“屏幕与系统音频录制”中允许访问",
    });
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(invokeMock.mock.calls.some(([command]) => String(command).startsWith("mic_adapter_"))).toBe(false);
  });

  it.each([
    [
      { transport_ready: false, pcm_seen: false },
      "系统音频传输未就绪，已阻止开始会议",
    ],
    [
      { transport_ready: true, pcm_seen: false },
      "系统音频未收到 PCM 数据，已阻止开始会议",
    ],
  ])("blocks startup when layered health fails: %o", async (health, expectedMessage) => {
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "system_audio_adapter_prepare") {
        return response({ status: "not_started" });
      }
      if (command === "system_audio_adapter_start") return response(health);
      if (command === "system_audio_adapter_stop") {
        return response({ status: "stopped" });
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
    const { result } = renderHook(() => useNativeSystemAudio());

    let capturedError: unknown;
    await act(async () => {
      try {
        await result.current.start("meeting_layered_failure");
      } catch (error) {
        capturedError = error;
      }
    });

    expect(capturedError).toEqual(new Error(expectedMessage));
    expect(result.current.state).toMatchObject({ phase: "error", error: expectedMessage });
    expect(invokeMock).toHaveBeenCalledWith("system_audio_adapter_stop", {
      sessionId: "meeting_layered_failure",
    });
  });

  it("keeps a silent PCM stream connected and reports it separately from ASR readiness", async () => {
    const invokeMock = vi.fn(async (command: string) => {
      if (command === "system_audio_adapter_prepare") return response({ status: "not_started" });
      if (command === "system_audio_adapter_start") {
        return response({ audible_pcm_seen: false, asr_ready: false });
      }
      if (command === "system_audio_adapter_collect_events") return response({ events: [] });
      if (command === "system_audio_adapter_status") {
        return response({ audible_pcm_seen: false, asr_ready: false });
      }
      if (command === "system_audio_adapter_stop") return response({ status: "stopped" });
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: { invoke: <T,>(command: string) => invokeMock(command) as Promise<T> },
    };
    const { result } = renderHook(() => useNativeSystemAudio());

    await act(async () => result.current.start("meeting_silent_system_audio"));

    expect(result.current.state).toMatchObject({
      phase: "recording",
      asrReady: false,
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
